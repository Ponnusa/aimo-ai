"""M2 acceptance test: registry-mediated discovery followed by a direct,
real A2A exchange.

Four independent real HTTP servers are booted (network-api, registry-api,
supplier-agent, buyer-agent), each its own process-equivalent uvicorn
instance on its own port with its own SQLite file. The flow exercised is
exactly the one described in docs/DECISIONS.md ADR-008:

1. network-api mints a short-lived, organization-scoped service credential
   and records a buyer<->supplier trading relationship.
2. registry-api fetches and validates the supplier's real Agent Card
   (over the SSRF-safe transport), registers it as "pending".
3. registry-api issues an ownership-verification challenge; the supplier
   agent's own process serves it at its well-known path; registry-api
   confirms it and marks the agent "active".
4. The buyer agent discovers the supplier through registry-api (never a
   hardcoded URL) and then talks to it directly over the official A2A
   SDK — registry-api is not in that second conversation at all.
"""

from __future__ import annotations

import os
import tempfile
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import new_engine

from .a2a_server_harness import find_free_port, running_server

BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"
SKILL_ID = "food.check_product_availability"

SIGNING_SECRET = "e2e-test-signing-secret"
ADMIN_KEY = "e2e-test-admin-key"
INTERNAL_KEY = "e2e-test-internal-key"


def _fresh_sqlite_url(label: str) -> str:
    path = os.path.join(tempfile.gettempdir(), f"aimo_m2_{label}_{uuid.uuid4().hex}.sqlite3")
    return f"sqlite+aiosqlite:///{path}"


async def _create_tables(engine, *models) -> None:
    from database import Base

    tables = [model.__table__ for model in models]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)


def _build_network_app(*, database_url: str):
    import network_api.app as network_app_module
    import network_api.settings as network_settings_module
    from network_api.models import OrganizationRecord, TradingRelationshipRecord

    os.environ["DATABASE_URL"] = database_url
    os.environ["NETWORK_ADMIN_API_KEY"] = ADMIN_KEY
    os.environ["NETWORK_INTERNAL_SERVICE_KEY"] = INTERNAL_KEY
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET
    network_settings_module.get_settings.cache_clear()

    app = network_app_module.create_app()
    return app, (OrganizationRecord, TradingRelationshipRecord)


def _build_registry_app(*, database_url: str, network_api_url: str):
    import registry_api.app as registry_app_module
    import registry_api.settings as registry_settings_module
    from registry_api.models import (
        RegistryAgentCapabilityRecord,
        RegistryAgentRecord,
        RegistryAuditEventRecord,
    )

    os.environ["REGISTRY_DATABASE_URL"] = database_url
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET
    os.environ["NETWORK_API_URL"] = network_api_url
    os.environ["NETWORK_INTERNAL_SERVICE_KEY"] = INTERNAL_KEY
    os.environ["REGISTRY_ALLOW_PRIVATE_NETWORK_CARD_TARGETS"] = "true"
    registry_settings_module.get_settings.cache_clear()

    app = registry_app_module.create_app()
    return app, (
        RegistryAgentRecord,
        RegistryAgentCapabilityRecord,
        RegistryAuditEventRecord,
    )


def _build_supplier_app(*, a2a_url: str, database_url: str):
    import supplier_agent.app as supplier_app_module
    import supplier_agent.settings as supplier_settings_module
    from supplier_agent.models import SupplierTaskRecord

    os.environ["SUPPLIER_A2A_URL"] = a2a_url
    os.environ["SUPPLIER_DATABASE_URL"] = database_url
    os.environ["SUPPLIER_ORGANIZATION_ID"] = SUPPLIER_ORG
    supplier_settings_module.get_settings.cache_clear()

    app = supplier_app_module.create_app()
    return app, SupplierTaskRecord


def _build_buyer_app(*, a2a_url: str, database_url: str, registry_api_url: str):
    import buyer_agent.app as buyer_app_module
    import buyer_agent.settings as buyer_settings_module
    from buyer_agent.models import BuyerTaskRecord

    os.environ["BUYER_A2A_URL"] = a2a_url
    os.environ["BUYER_DATABASE_URL"] = database_url
    os.environ["BUYER_ORGANIZATION_ID"] = BUYER_ORG
    os.environ["REGISTRY_API_URL"] = registry_api_url
    buyer_settings_module.get_settings.cache_clear()

    app = buyer_app_module.create_app()
    return app, BuyerTaskRecord


@pytest.fixture
async def network_server():
    database_url = _fresh_sqlite_url("network")
    app, models = _build_network_app(database_url=database_url)
    engine = new_engine(database_url)
    await _create_tables(engine, *models)

    from network_api.models import OrganizationRecord

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        for org_id in (BUYER_ORG, SUPPLIER_ORG):
            await session.merge(
                OrganizationRecord(organization_id=org_id, display_name=org_id, verified=True)
            )
        await session.commit()
    await engine.dispose()

    port = find_free_port()
    with running_server(app, port=port) as server:
        yield server


@pytest.fixture
async def supplier_server():
    port = find_free_port()
    a2a_url = f"http://127.0.0.1:{port}"
    database_url = _fresh_sqlite_url("supplier")
    app, model = _build_supplier_app(a2a_url=a2a_url, database_url=database_url)

    engine = new_engine(database_url)
    await _create_tables(engine, model)
    await engine.dispose()

    with running_server(app, port=port) as server:
        yield server


@pytest.fixture
async def registry_server(network_server):
    database_url = _fresh_sqlite_url("registry")
    app, models = _build_registry_app(
        database_url=database_url, network_api_url=network_server.base_url
    )
    engine = new_engine(database_url)
    await _create_tables(engine, *models)
    await engine.dispose()

    port = find_free_port()
    with running_server(app, port=port) as server:
        yield server


@pytest.fixture
async def buyer_server(registry_server):
    port = find_free_port()
    a2a_url = f"http://127.0.0.1:{port}"
    database_url = _fresh_sqlite_url("buyer")
    app, model = _build_buyer_app(
        a2a_url=a2a_url, database_url=database_url, registry_api_url=registry_server.base_url
    )

    engine = new_engine(database_url)
    await _create_tables(engine, model)
    await engine.dispose()

    with running_server(app, port=port) as server:
        yield server


async def _mint_credential(network_server, organization_id: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{network_server.base_url}/organizations/{organization_id}/service-credentials",
            json={"ttl_seconds": 3600},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
    assert response.status_code == 200, response.text
    return response.json()["token"]


async def _create_relationship(network_server) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{network_server.base_url}/relationships",
            json={
                "buyer_organization_id": BUYER_ORG,
                "supplier_organization_id": SUPPLIER_ORG,
            },
            headers={"X-Admin-Key": ADMIN_KEY},
        )
    assert response.status_code == 200, response.text


async def _register_and_verify_supplier(*, registry_server, supplier_server, token: str) -> None:
    async with httpx.AsyncClient() as client:
        register = await client.post(
            f"{registry_server.base_url}/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": supplier_server.base_url,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert register.status_code == 200, register.text
        assert register.json()["status"] == "pending"

        start = await client.post(
            f"{registry_server.base_url}/agents/agent_fictional_supplier_01/verification/start",
            params={"organization_id": SUPPLIER_ORG},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert start.status_code == 200, start.text
        challenge = start.json()["challenge"]

        put_response = await client.put(
            f"{supplier_server.base_url}/internal/ownership-challenge",
            json={"challenge": challenge},
        )
        assert put_response.status_code == 200

        confirm = await client.post(
            f"{registry_server.base_url}/agents/agent_fictional_supplier_01/verification/confirm",
            params={"organization_id": SUPPLIER_ORG},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["status"] == "active"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_registry_discovery_then_direct_a2a_exchange(
    network_server, registry_server, supplier_server, buyer_server
) -> None:
    token = await _mint_credential(network_server, SUPPLIER_ORG)
    await _create_relationship(network_server)
    await _register_and_verify_supplier(
        registry_server=registry_server, supplier_server=supplier_server, token=token
    )

    from buyer_agent.client import request_product_availability_via_registry
    from buyer_agent.settings import get_settings

    settings = get_settings()
    buyer_db_url = os.environ["BUYER_DATABASE_URL"]
    engine = new_engine(buyer_db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    response = await request_product_availability_via_registry(
        settings=settings,
        session_factory=session_factory,
        lines=[{"product_query": "lactose-free milk", "quantity": "50", "unit": "L"}],
    )

    assert response["type"] == "product_availability_response"
    assert response["results"][0]["matched"] is True

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discovery_without_relationship_is_not_found(
    network_server, registry_server, supplier_server
) -> None:
    """No trading relationship exists yet: discovery must find nothing,
    not fall back to treating the agent as public."""
    token = await _mint_credential(network_server, SUPPLIER_ORG)
    await _register_and_verify_supplier(
        registry_server=registry_server, supplier_server=supplier_server, token=token
    )

    async with httpx.AsyncClient() as client:
        discovery = await client.get(
            f"{registry_server.base_url}/agents",
            params={"requesting_organization_id": BUYER_ORG, "skill_id": SKILL_ID},
        )
    assert discovery.json() == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_suspended_relationship_removes_discoverability(
    network_server, registry_server, supplier_server
) -> None:
    token = await _mint_credential(network_server, SUPPLIER_ORG)
    await _create_relationship(network_server)
    await _register_and_verify_supplier(
        registry_server=registry_server, supplier_server=supplier_server, token=token
    )

    async with httpx.AsyncClient() as client:
        before = await client.get(
            f"{registry_server.base_url}/agents",
            params={"requesting_organization_id": BUYER_ORG, "skill_id": SKILL_ID},
        )
        assert len(before.json()) == 1

        relationships = await client.get(
            f"{network_server.base_url}/relationships",
            params={
                "buyer_organization_id": BUYER_ORG,
                "supplier_organization_id": SUPPLIER_ORG,
            },
            headers={"X-Internal-Service-Key": INTERNAL_KEY},
        )
        relationship_id = relationships.json()[0]["id"]

        await client.post(
            f"{network_server.base_url}/relationships/{relationship_id}/suspend",
            headers={"X-Admin-Key": ADMIN_KEY},
        )

        after = await client.get(
            f"{registry_server.base_url}/agents",
            params={"requesting_organization_id": BUYER_ORG, "skill_id": SKILL_ID},
        )
        assert after.json() == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_revoked_agent_is_not_discoverable(
    network_server, registry_server, supplier_server
) -> None:
    token = await _mint_credential(network_server, SUPPLIER_ORG)
    await _create_relationship(network_server)
    await _register_and_verify_supplier(
        registry_server=registry_server, supplier_server=supplier_server, token=token
    )

    async with httpx.AsyncClient() as client:
        await client.post(
            f"{registry_server.base_url}/agents/agent_fictional_supplier_01/revoke",
            params={"organization_id": SUPPLIER_ORG},
            headers={"Authorization": f"Bearer {token}"},
        )
        discovery = await client.get(
            f"{registry_server.base_url}/agents",
            params={"requesting_organization_id": BUYER_ORG, "skill_id": SKILL_ID},
        )
        assert discovery.json() == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_malicious_scheme_card_url_is_rejected_before_any_registration(
    network_server, registry_server
) -> None:
    """Scheme validation is unconditional — unlike the IP-range check, it
    is never skipped by the local-dev bypass this test harness otherwise
    enables (``REGISTRY_ALLOW_PRIVATE_NETWORK_CARD_TARGETS=true``, needed
    so supplier-agent's real 127.0.0.1 endpoint can register at all). A
    non-http(s) scheme must still be rejected outright, over real HTTP,
    before anything is stored — not even as 'pending'."""
    token = await _mint_credential(network_server, SUPPLIER_ORG)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{registry_server.base_url}/agents",
            json={
                "agent_id": "agent_malicious",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "file:///etc/passwd",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400

        owned = await client.get(
            f"{registry_server.base_url}/organizations/{SUPPLIER_ORG}/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert owned.json() == []
