"""M5 acceptance test: independent OPA policy authorization, evidence
exchange and idempotent commitment over real A2A/MCP HTTP.

Extends the M4 harness (``test_negotiation_e2e.py``) with: a buyer OPA
bundle, a supplier OPA bundle, agent-scoped ``commit`` credentials for
both agents, and supplier-mcp's narrow ``execute_order`` credential. The
flow, all over real HTTP across seven independently booted servers
(network-api, registry-api, buyer-mcp, supplier-mcp, buyer-agent,
supplier-agent, plus two real ``opa eval`` subprocess invocations):

1. Negotiation converges on a NONBINDING proposed_agreement (M4, reused
   verbatim).
2. Buyer independently evaluates its own OPA policy and finalizes its
   own authorization evidence (no human approval needed at this price).
3. Buyer sends that evidence to the supplier; the supplier independently
   evaluates its own OPA policy and replies with its own evidence.
4. Both organizations now hold matching, allowed evidence for the exact
   same content_hash -> mutually authorized.
5. Buyer sends an idempotent order_commit_request; the supplier calls
   its own supplier-mcp ``execute_order`` tool (the approved mock order
   adapter) and replies with a CONFIRMED order_confirmation.
6. A retried commit with the same idempotency_key returns the exact same
   external_order_id -- never a second order.

See ``tests/security/test_authorization_commitment_security.py`` for the
adversarial cases (denial, pending approval, spoofed/cross-tenant
evidence, unauthorized direct execute_order call).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import new_engine
from identity import issue_service_credential

from .a2a_server_harness import find_free_port, running_server

pytestmark = pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed")

BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"
BUYER_AGENT_ID = "agent_fictional_buyer_01"
SUPPLIER_AGENT_ID = "agent_fictional_supplier_01"

SIGNING_SECRET = "e2e-m5-signing-secret"
ADMIN_KEY = "e2e-m5-admin-key"
INTERNAL_KEY = "e2e-m5-internal-key"


def _fresh_sqlite_url(label: str) -> str:
    path = os.path.join(tempfile.gettempdir(), f"aimo_m5_{label}_{uuid.uuid4().hex}.sqlite3")
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


def _build_supplier_mcp_app(*, database_url: str):
    import supplier_mcp.app as supplier_mcp_app_module
    import supplier_mcp.settings as supplier_mcp_settings_module
    from supplier_mcp.models import SupplierMcpOrderRecord, SupplierMcpToolInvocationRecord

    os.environ["SUPPLIER_MCP_DATABASE_URL"] = database_url
    os.environ["SUPPLIER_MCP_ORGANIZATION_ID"] = SUPPLIER_ORG
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET
    supplier_mcp_settings_module.get_settings.cache_clear()

    return supplier_mcp_app_module.create_app(), (
        SupplierMcpToolInvocationRecord,
        SupplierMcpOrderRecord,
    )


def _build_buyer_mcp_app(*, database_url: str):
    import buyer_mcp.app as buyer_mcp_app_module
    import buyer_mcp.settings as buyer_mcp_settings_module
    from buyer_mcp.models import BuyerMcpToolInvocationRecord

    os.environ["BUYER_MCP_DATABASE_URL"] = database_url
    os.environ["BUYER_MCP_ORGANIZATION_ID"] = BUYER_ORG
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET
    buyer_mcp_settings_module.get_settings.cache_clear()

    return buyer_mcp_app_module.create_app(), BuyerMcpToolInvocationRecord


def _build_supplier_app(*, a2a_url: str, database_url: str, supplier_mcp_url: str):
    import supplier_agent.app as supplier_app_module
    import supplier_agent.settings as supplier_settings_module
    from supplier_agent.models import (
        SupplierApprovalRecord,
        SupplierAuthorizationEvidenceRecord,
        SupplierNegotiationRecord,
        SupplierOrderRecord,
        SupplierProcessedArtifactRecord,
        SupplierProposalRecord,
        SupplierTaskRecord,
    )

    os.environ["SUPPLIER_A2A_URL"] = a2a_url
    os.environ["SUPPLIER_DATABASE_URL"] = database_url
    os.environ["SUPPLIER_ORGANIZATION_ID"] = SUPPLIER_ORG
    os.environ["SUPPLIER_AGENT_ID"] = SUPPLIER_AGENT_ID
    os.environ["SUPPLIER_MCP_URL"] = supplier_mcp_url
    os.environ["SUPPLIER_MCP_SERVICE_CREDENTIAL"] = issue_service_credential(
        organization_id=SUPPLIER_ORG, scope="use_supplier_tools", signing_secret=SIGNING_SECRET
    )
    os.environ["SUPPLIER_MCP_EXECUTION_CREDENTIAL"] = issue_service_credential(
        organization_id=SUPPLIER_ORG, scope="execute_order", signing_secret=SIGNING_SECRET
    )
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET
    os.environ["SUPPLIER_COMMIT_SERVICE_CREDENTIAL"] = issue_service_credential(
        organization_id=SUPPLIER_ORG,
        scope="commit",
        signing_secret=SIGNING_SECRET,
        agent_id=SUPPLIER_AGENT_ID,
    )
    supplier_settings_module.get_settings.cache_clear()

    app = supplier_app_module.create_app()
    return app, (
        SupplierTaskRecord,
        SupplierNegotiationRecord,
        SupplierProposalRecord,
        SupplierProcessedArtifactRecord,
        SupplierApprovalRecord,
        SupplierAuthorizationEvidenceRecord,
        SupplierOrderRecord,
    )


def _build_buyer_app(*, a2a_url: str, database_url: str, registry_api_url: str, buyer_mcp_url: str):
    import buyer_agent.app as buyer_app_module
    import buyer_agent.settings as buyer_settings_module
    from buyer_agent.models import (
        BuyerApprovalRecord,
        BuyerAuthorizationEvidenceRecord,
        BuyerNegotiationRecord,
        BuyerOrderRecord,
        BuyerProposalRecord,
        BuyerTaskRecord,
    )

    os.environ["BUYER_A2A_URL"] = a2a_url
    os.environ["BUYER_DATABASE_URL"] = database_url
    os.environ["BUYER_ORGANIZATION_ID"] = BUYER_ORG
    os.environ["BUYER_AGENT_ID"] = BUYER_AGENT_ID
    os.environ["REGISTRY_API_URL"] = registry_api_url
    os.environ["BUYER_MCP_URL"] = buyer_mcp_url
    os.environ["BUYER_MCP_SERVICE_CREDENTIAL"] = issue_service_credential(
        organization_id=BUYER_ORG, scope="use_buyer_tools", signing_secret=SIGNING_SECRET
    )
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET
    os.environ["BUYER_COMMIT_SERVICE_CREDENTIAL"] = issue_service_credential(
        organization_id=BUYER_ORG,
        scope="commit",
        signing_secret=SIGNING_SECRET,
        agent_id=BUYER_AGENT_ID,
    )
    buyer_settings_module.get_settings.cache_clear()

    app = buyer_app_module.create_app()
    return app, (
        BuyerTaskRecord,
        BuyerNegotiationRecord,
        BuyerProposalRecord,
        BuyerApprovalRecord,
        BuyerAuthorizationEvidenceRecord,
        BuyerOrderRecord,
    )


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
async def supplier_server():
    mcp_database_url = _fresh_sqlite_url("supplier_mcp")
    mcp_app, mcp_models = _build_supplier_mcp_app(database_url=mcp_database_url)
    mcp_engine = new_engine(mcp_database_url)
    await _create_tables(mcp_engine, *mcp_models)
    await mcp_engine.dispose()

    mcp_port = find_free_port()
    with running_server(mcp_app, port=mcp_port) as mcp_server:
        port = find_free_port()
        a2a_url = f"http://127.0.0.1:{port}"
        database_url = _fresh_sqlite_url("supplier")
        app, models = _build_supplier_app(
            a2a_url=a2a_url,
            database_url=database_url,
            supplier_mcp_url=f"{mcp_server.base_url}/mcp",
        )

        engine = new_engine(database_url)
        await _create_tables(engine, *models)
        await engine.dispose()

        with running_server(app, port=port) as server:
            server.database_url = database_url  # type: ignore[attr-defined]
            yield server


@pytest.fixture
async def buyer_server(registry_server):
    mcp_database_url = _fresh_sqlite_url("buyer_mcp")
    mcp_app, mcp_model = _build_buyer_mcp_app(database_url=mcp_database_url)
    mcp_engine = new_engine(mcp_database_url)
    await _create_tables(mcp_engine, mcp_model)
    await mcp_engine.dispose()

    mcp_port = find_free_port()
    with running_server(mcp_app, port=mcp_port) as mcp_server:
        port = find_free_port()
        a2a_url = f"http://127.0.0.1:{port}"
        database_url = _fresh_sqlite_url("buyer")
        app, models = _build_buyer_app(
            a2a_url=a2a_url,
            database_url=database_url,
            registry_api_url=registry_server.base_url,
            buyer_mcp_url=f"{mcp_server.base_url}/mcp",
        )

        engine = new_engine(database_url)
        await _create_tables(engine, *models)
        await engine.dispose()

        with running_server(app, port=port) as server:
            server.database_url = database_url  # type: ignore[attr-defined]
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
                "agent_id": SUPPLIER_AGENT_ID,
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": supplier_server.base_url,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert register.status_code == 200, register.text

        start = await client.post(
            f"{registry_server.base_url}/agents/{SUPPLIER_AGENT_ID}/verification/start",
            params={"organization_id": SUPPLIER_ORG},
            headers={"Authorization": f"Bearer {token}"},
        )
        challenge = start.json()["challenge"]

        await client.put(
            f"{supplier_server.base_url}/internal/ownership-challenge",
            json={"challenge": challenge},
        )

        confirm = await client.post(
            f"{registry_server.base_url}/agents/{SUPPLIER_AGENT_ID}/verification/confirm",
            params={"organization_id": SUPPLIER_ORG},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["status"] == "active"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_authorization_and_commitment_flow(
    network_server, registry_server, supplier_server, buyer_server
) -> None:
    token = await _mint_credential(network_server, SUPPLIER_ORG)
    await _create_relationship(network_server)
    await _register_and_verify_supplier(
        registry_server=registry_server, supplier_server=supplier_server, token=token
    )

    from buyer_agent.client import negotiate_purchase_via_registry
    from buyer_agent.commitment import run_authorization_and_commitment
    from buyer_agent.settings import get_settings

    settings = get_settings()
    buyer_engine = new_engine(os.environ["BUYER_DATABASE_URL"])
    buyer_session_factory = async_sessionmaker(buyer_engine, expire_on_commit=False)

    # Low total (50L * ~1.19 EUR/L ~= 60 EUR): under both organizations'
    # human_approval_threshold (200 EUR) and budget_limit (500 EUR) -- no
    # human approval needed on either side for this acceptance path.
    agreement = await negotiate_purchase_via_registry(
        settings=settings,
        session_factory=buyer_session_factory,
        lines=[{"product_query": "lactose-free milk", "quantity": "50", "unit": "L"}],
        delivery_location_ref="site_01",
    )
    assert agreement["type"] == "proposed_agreement"
    negotiation_id = agreement["transaction_id"]

    confirmation = await run_authorization_and_commitment(
        negotiation_id, settings=settings, session_factory=buyer_session_factory
    )
    assert confirmation["type"] == "order_confirmation"
    assert confirmation["payload"]["status"] == "CONFIRMED"
    external_order_id = confirmation["payload"]["external_order_id"]
    assert external_order_id

    # A second, independent call for the same negotiation is idempotent:
    # same idempotency_key, same external_order_id, no second order.
    confirmation_again = await run_authorization_and_commitment(
        negotiation_id, settings=settings, session_factory=buyer_session_factory
    )
    assert confirmation_again["payload"]["external_order_id"] == external_order_id

    # Both organizations independently persisted evidence and an order
    # record for the exact same content_hash, in their own databases.
    from buyer_agent.models import BuyerAuthorizationEvidenceRecord, BuyerOrderRecord
    from supplier_agent.models import SupplierAuthorizationEvidenceRecord, SupplierOrderRecord

    async with buyer_session_factory() as session:
        buyer_evidence = (
            (
                await session.execute(
                    select(BuyerAuthorizationEvidenceRecord).where(
                        BuyerAuthorizationEvidenceRecord.negotiation_id == negotiation_id
                    )
                )
            )
            .scalars()
            .all()
        )
        buyer_orders = (
            (
                await session.execute(
                    select(BuyerOrderRecord).where(
                        BuyerOrderRecord.negotiation_id == negotiation_id
                    )
                )
            )
            .scalars()
            .all()
        )
    await buyer_engine.dispose()
    assert {row.source for row in buyer_evidence} == {"own", "counterparty"}
    assert all(row.allow for row in buyer_evidence)
    assert len(buyer_orders) == 1
    assert buyer_orders[0].state == "CONFIRMED"

    supplier_engine = new_engine(supplier_server.database_url)  # type: ignore[attr-defined]
    supplier_session_factory = async_sessionmaker(supplier_engine, expire_on_commit=False)
    async with supplier_session_factory() as session:
        supplier_evidence = (
            (
                await session.execute(
                    select(SupplierAuthorizationEvidenceRecord).where(
                        SupplierAuthorizationEvidenceRecord.negotiation_id == negotiation_id
                    )
                )
            )
            .scalars()
            .all()
        )
        supplier_orders = (
            (
                await session.execute(
                    select(SupplierOrderRecord).where(
                        SupplierOrderRecord.negotiation_id == negotiation_id
                    )
                )
            )
            .scalars()
            .all()
        )
    await supplier_engine.dispose()
    assert {row.source for row in supplier_evidence} == {"own", "counterparty"}
    assert all(row.allow for row in supplier_evidence)
    assert len(supplier_orders) == 1
    assert supplier_orders[0].external_order_id == external_order_id
