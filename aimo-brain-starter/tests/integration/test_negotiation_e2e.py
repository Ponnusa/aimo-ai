"""M4 acceptance test: full nonbinding RFQ -> quote -> counteroffer ->
proposed-agreement negotiation over real A2A HTTP.

Six independent real HTTP servers are booted (network-api, registry-api,
buyer-mcp, supplier-mcp, buyer-agent, supplier-agent), each its own
uvicorn instance on its own port with its own SQLite file — the same
harness pattern as ``test_registry_discovery_e2e.py``. The flow:

1. Buyer discovers supplier through the M2 registry.
2. Buyer obtains its requirements and budget through its own MCP service.
3. Buyer sends an RFQ over A2A.
4. Supplier checks catalogue/inventory/pricing through its own MCP
   service and returns a versioned quote.
5. Buyer evaluates the quote against its MCP-sourced budget and, if it
   exceeds budget, sends a counteroffer.
6. Supplier evaluates the counteroffer and returns revised terms.
7. Both sides converge on an identical NONBINDING proposed agreement.
8. Both organizations independently persist their own negotiation
   history (``buyer_negotiations``/``buyer_proposals`` vs.
   ``supplier_negotiations``/``supplier_proposals``).

Nothing in this test, or the code it exercises, creates an order,
reservation or payment — see ``tests/security/test_negotiation_security.py``
for the explicit check.
"""

from __future__ import annotations

import os
import tempfile
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import new_engine
from identity import issue_service_credential

from .a2a_server_harness import (
    find_free_port,
    generate_and_register_signing_key,
    running_server,
)

BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"
NEGOTIATE_SKILL_ID = "food.request_quote"

SIGNING_SECRET = "e2e-negotiation-signing-secret"
ADMIN_KEY = "e2e-negotiation-admin-key"
INTERNAL_KEY = "e2e-negotiation-internal-key"
BUYER_KID = "e2e-buyer-k1"
SUPPLIER_KID = "e2e-supplier-k1"


def _fresh_sqlite_url(label: str) -> str:
    path = os.path.join(tempfile.gettempdir(), f"aimo_m4_{label}_{uuid.uuid4().hex}.sqlite3")
    return f"sqlite+aiosqlite:///{path}"


async def _create_tables(engine, *models) -> None:
    from database import Base

    tables = [model.__table__ for model in models]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)


def _build_network_app(*, database_url: str):
    import network_api.app as network_app_module
    import network_api.settings as network_settings_module
    from network_api.models import (
        OrganizationRecord,
        OrganizationSigningKeyRecord,
        TradingRelationshipRecord,
    )

    os.environ["DATABASE_URL"] = database_url
    os.environ["NETWORK_ADMIN_API_KEY"] = ADMIN_KEY
    os.environ["NETWORK_INTERNAL_SERVICE_KEY"] = INTERNAL_KEY
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET
    network_settings_module.get_settings.cache_clear()

    app = network_app_module.create_app()
    return app, (OrganizationRecord, TradingRelationshipRecord, OrganizationSigningKeyRecord)


def _build_registry_app(*, database_url: str, network_api_url: str):
    import registry_api.app as registry_app_module
    import registry_api.settings as registry_settings_module
    from registry_api.models import (
        RegistryAgentCapabilityRecord,
        RegistryAgentRecord,
        RegistryAuditEventRecord,
        RegistryOrganizationPolicyRecord,
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
        RegistryOrganizationPolicyRecord,
    )


def _build_supplier_mcp_app(*, database_url: str):
    import supplier_mcp.app as supplier_mcp_app_module
    import supplier_mcp.settings as supplier_mcp_settings_module
    from supplier_mcp.models import SupplierMcpToolInvocationRecord

    os.environ["SUPPLIER_MCP_DATABASE_URL"] = database_url
    os.environ["SUPPLIER_MCP_ORGANIZATION_ID"] = SUPPLIER_ORG
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET
    supplier_mcp_settings_module.get_settings.cache_clear()

    return supplier_mcp_app_module.create_app(), SupplierMcpToolInvocationRecord


def _build_buyer_mcp_app(*, database_url: str):
    import buyer_mcp.app as buyer_mcp_app_module
    import buyer_mcp.settings as buyer_mcp_settings_module
    from buyer_mcp.models import BuyerMcpToolInvocationRecord

    os.environ["BUYER_MCP_DATABASE_URL"] = database_url
    os.environ["BUYER_MCP_ORGANIZATION_ID"] = BUYER_ORG
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET
    buyer_mcp_settings_module.get_settings.cache_clear()

    return buyer_mcp_app_module.create_app(), BuyerMcpToolInvocationRecord


def _build_supplier_app(
    *,
    a2a_url: str,
    database_url: str,
    supplier_mcp_url: str,
    network_api_url: str,
    registry_api_url: str,
    signing_private_key_pem: str,
):
    import supplier_agent.app as supplier_app_module
    import supplier_agent.settings as supplier_settings_module
    from supplier_agent.models import (
        SupplierNegotiationRecord,
        SupplierProcessedArtifactRecord,
        SupplierProposalRecord,
        SupplierSeenMessageProofRecord,
        SupplierTaskRecord,
    )

    os.environ["SUPPLIER_A2A_URL"] = a2a_url
    os.environ["SUPPLIER_DATABASE_URL"] = database_url
    os.environ["SUPPLIER_ORGANIZATION_ID"] = SUPPLIER_ORG
    os.environ["SUPPLIER_MCP_URL"] = supplier_mcp_url
    os.environ["SUPPLIER_MCP_SERVICE_CREDENTIAL"] = issue_service_credential(
        organization_id=SUPPLIER_ORG, scope="use_supplier_tools", signing_secret=SIGNING_SECRET
    )
    os.environ["NETWORK_API_URL"] = network_api_url
    os.environ["REGISTRY_API_URL"] = registry_api_url
    os.environ["SUPPLIER_SIGNING_PRIVATE_KEY_PEM"] = signing_private_key_pem
    os.environ["SUPPLIER_SIGNING_KID"] = SUPPLIER_KID
    supplier_settings_module.get_settings.cache_clear()

    app = supplier_app_module.create_app()
    return app, (
        SupplierTaskRecord,
        SupplierNegotiationRecord,
        SupplierProposalRecord,
        SupplierProcessedArtifactRecord,
        SupplierSeenMessageProofRecord,
    )


def _build_buyer_app(
    *,
    a2a_url: str,
    database_url: str,
    registry_api_url: str,
    buyer_mcp_url: str,
    network_api_url: str,
    signing_private_key_pem: str,
):
    import buyer_agent.app as buyer_app_module
    import buyer_agent.settings as buyer_settings_module
    from buyer_agent.models import (
        BuyerNegotiationRecord,
        BuyerProposalRecord,
        BuyerSeenMessageProofRecord,
        BuyerTaskRecord,
    )

    os.environ["BUYER_A2A_URL"] = a2a_url
    os.environ["BUYER_DATABASE_URL"] = database_url
    os.environ["BUYER_ORGANIZATION_ID"] = BUYER_ORG
    os.environ["REGISTRY_API_URL"] = registry_api_url
    os.environ["BUYER_MCP_URL"] = buyer_mcp_url
    os.environ["BUYER_MCP_SERVICE_CREDENTIAL"] = issue_service_credential(
        organization_id=BUYER_ORG, scope="use_buyer_tools", signing_secret=SIGNING_SECRET
    )
    os.environ["NETWORK_API_URL"] = network_api_url
    os.environ["BUYER_SIGNING_PRIVATE_KEY_PEM"] = signing_private_key_pem
    os.environ["BUYER_SIGNING_KID"] = BUYER_KID
    buyer_settings_module.get_settings.cache_clear()

    app = buyer_app_module.create_app()
    return app, (
        BuyerTaskRecord,
        BuyerNegotiationRecord,
        BuyerProposalRecord,
        BuyerSeenMessageProofRecord,
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
async def supplier_server(registry_server, network_server):
    mcp_database_url = _fresh_sqlite_url("supplier_mcp")
    mcp_app, mcp_model = _build_supplier_mcp_app(database_url=mcp_database_url)
    mcp_engine = new_engine(mcp_database_url)
    await _create_tables(mcp_engine, mcp_model)
    await mcp_engine.dispose()

    signing_private_key_pem = await generate_and_register_signing_key(
        network_api_base_url=network_server.base_url,
        organization_id=SUPPLIER_ORG,
        admin_key=ADMIN_KEY,
        kid=SUPPLIER_KID,
    )

    mcp_port = find_free_port()
    with running_server(mcp_app, port=mcp_port) as mcp_server:
        port = find_free_port()
        a2a_url = f"http://127.0.0.1:{port}"
        database_url = _fresh_sqlite_url("supplier")
        app, models = _build_supplier_app(
            a2a_url=a2a_url,
            database_url=database_url,
            supplier_mcp_url=f"{mcp_server.base_url}/mcp",
            network_api_url=network_server.base_url,
            registry_api_url=registry_server.base_url,
            signing_private_key_pem=signing_private_key_pem,
        )

        engine = new_engine(database_url)
        await _create_tables(engine, *models)
        await engine.dispose()

        with running_server(app, port=port) as server:
            server.database_url = database_url  # type: ignore[attr-defined]
            yield server


@pytest.fixture
async def buyer_server(registry_server, network_server):
    mcp_database_url = _fresh_sqlite_url("buyer_mcp")
    mcp_app, mcp_model = _build_buyer_mcp_app(database_url=mcp_database_url)
    mcp_engine = new_engine(mcp_database_url)
    await _create_tables(mcp_engine, mcp_model)
    await mcp_engine.dispose()

    signing_private_key_pem = await generate_and_register_signing_key(
        network_api_base_url=network_server.base_url,
        organization_id=BUYER_ORG,
        admin_key=ADMIN_KEY,
        kid=BUYER_KID,
    )

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
            network_api_url=network_server.base_url,
            signing_private_key_pem=signing_private_key_pem,
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


async def _register_and_verify_agent(
    *, registry_server, agent_server, organization_id: str, agent_id: str, token: str
) -> None:
    """Registers and endpoint-ownership-verifies one agent in
    registry-api. As of M5.1, *every* agent sending a signed message
    must be a registered, active agent — not just the supplier that
    gets discovered — since the receiving side's
    ``verify_agent_is_registered_and_related`` checks every verified
    sender this way, buyer included."""
    async with httpx.AsyncClient() as client:
        register = await client.post(
            f"{registry_server.base_url}/agents",
            json={
                "agent_id": agent_id,
                "organization_id": organization_id,
                "a2a_card_url": agent_server.base_url,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert register.status_code == 200, register.text

        start = await client.post(
            f"{registry_server.base_url}/agents/{agent_id}/verification/start",
            params={"organization_id": organization_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        challenge = start.json()["endpoint_challenge"]

        await client.put(
            f"{agent_server.base_url}/internal/ownership-challenge",
            json={"challenge": challenge},
        )

        confirm = await client.post(
            f"{registry_server.base_url}/agents/{agent_id}/verification/confirm",
            params={"organization_id": organization_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["status"] == "active"


async def _register_and_verify_supplier(*, registry_server, supplier_server, token: str) -> None:
    await _register_and_verify_agent(
        registry_server=registry_server,
        agent_server=supplier_server,
        organization_id=SUPPLIER_ORG,
        agent_id="agent_fictional_supplier_01",
        token=token,
    )


async def _register_and_verify_buyer(*, registry_server, buyer_server, token: str) -> None:
    await _register_and_verify_agent(
        registry_server=registry_server,
        agent_server=buyer_server,
        organization_id=BUYER_ORG,
        agent_id="agent_fictional_buyer_01",
        token=token,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_negotiation_converges_on_nonbinding_proposed_agreement(
    network_server, registry_server, supplier_server, buyer_server
) -> None:
    token = await _mint_credential(network_server, SUPPLIER_ORG)
    await _create_relationship(network_server)
    await _register_and_verify_supplier(
        registry_server=registry_server, supplier_server=supplier_server, token=token
    )
    buyer_token = await _mint_credential(network_server, BUYER_ORG)
    await _register_and_verify_buyer(
        registry_server=registry_server, buyer_server=buyer_server, token=buyer_token
    )

    from buyer_agent.client import negotiate_purchase_via_registry
    from buyer_agent.settings import get_settings

    settings = get_settings()
    engine = new_engine(os.environ["BUYER_DATABASE_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    result = await negotiate_purchase_via_registry(
        settings=settings,
        session_factory=session_factory,
        lines=[{"product_query": "lactose-free milk", "quantity": "50", "unit": "L"}],
        delivery_location_ref="site_01",
    )

    assert result["type"] == "proposed_agreement"
    assert result["payload"]["status"] == "NONBINDING"
    await engine.dispose()

    # Both organizations independently persisted their own negotiation
    # history, in their own databases, converging on the same terms hash.
    from buyer_agent.models import BuyerProposalRecord
    from supplier_agent.models import SupplierProposalRecord

    buyer_engine = new_engine(os.environ["BUYER_DATABASE_URL"])
    buyer_sessions = async_sessionmaker(buyer_engine, expire_on_commit=False)
    async with buyer_sessions() as session:
        buyer_rows = (
            (
                await session.execute(
                    select(BuyerProposalRecord).where(
                        BuyerProposalRecord.artifact_type == "proposed_agreement"
                    )
                )
            )
            .scalars()
            .all()
        )
    await buyer_engine.dispose()

    supplier_engine = new_engine(supplier_server.database_url)  # type: ignore[attr-defined]
    supplier_sessions = async_sessionmaker(supplier_engine, expire_on_commit=False)
    async with supplier_sessions() as session:
        supplier_rows = (
            (
                await session.execute(
                    select(SupplierProposalRecord).where(
                        SupplierProposalRecord.artifact_type == "proposed_agreement"
                    )
                )
            )
            .scalars()
            .all()
        )
    await supplier_engine.dispose()

    assert len(buyer_rows) == 1
    assert len(supplier_rows) == 1
    assert buyer_rows[0].content_hash == supplier_rows[0].content_hash == result["content_hash"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_negotiation_exercises_a_real_counteroffer_round(
    network_server, registry_server, supplier_server, buyer_server
) -> None:
    """Quantity chosen so the initial quote exceeds the buyer's MCP-
    sourced budget (EUR 500.00): the buyer must send a genuine
    ``counterproposal`` (not just accept the first quote), and the
    supplier's deterministic evaluation must accept it once it clears
    its own margin floor — exercising acceptance-scenario steps 6-7."""
    token = await _mint_credential(network_server, SUPPLIER_ORG)
    await _create_relationship(network_server)
    await _register_and_verify_supplier(
        registry_server=registry_server, supplier_server=supplier_server, token=token
    )
    buyer_token = await _mint_credential(network_server, BUYER_ORG)
    await _register_and_verify_buyer(
        registry_server=registry_server, buyer_server=buyer_server, token=buyer_token
    )

    from buyer_agent.client import negotiate_purchase_via_registry
    from buyer_agent.models import BuyerProposalRecord
    from buyer_agent.settings import get_settings

    settings = get_settings()
    engine = new_engine(os.environ["BUYER_DATABASE_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    result = await negotiate_purchase_via_registry(
        settings=settings,
        session_factory=session_factory,
        lines=[{"product_query": "lactose-free milk", "quantity": "430", "unit": "L"}],
        delivery_location_ref="site_01",
    )

    assert result["type"] == "proposed_agreement"
    assert result["payload"]["status"] == "NONBINDING"

    async with session_factory() as session:
        counterproposals = (
            (
                await session.execute(
                    select(BuyerProposalRecord).where(
                        BuyerProposalRecord.artifact_type == "counterproposal"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(counterproposals) >= 1, "Buyer should have sent at least one real counteroffer."
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_negotiation_recoverable_after_buyer_restart(
    network_server, registry_server, supplier_server, buyer_server
) -> None:
    """A fresh buyer-agent process (same database) can still look up a
    negotiation another process started — restart recovery, not just
    in-memory continuation."""
    token = await _mint_credential(network_server, SUPPLIER_ORG)
    await _create_relationship(network_server)
    await _register_and_verify_supplier(
        registry_server=registry_server, supplier_server=supplier_server, token=token
    )
    buyer_token = await _mint_credential(network_server, BUYER_ORG)
    await _register_and_verify_buyer(
        registry_server=registry_server, buyer_server=buyer_server, token=buyer_token
    )

    from buyer_agent.client import negotiate_purchase_via_registry
    from buyer_agent.settings import get_settings

    settings = get_settings()
    engine = new_engine(os.environ["BUYER_DATABASE_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    result = await negotiate_purchase_via_registry(
        settings=settings,
        session_factory=session_factory,
        lines=[{"product_query": "lactose-free milk", "quantity": "50", "unit": "L"}],
        delivery_location_ref="site_01",
    )
    negotiation_id = result["transaction_id"]
    await engine.dispose()

    # Simulate a restart: a brand new engine/session factory against the
    # same persisted database, as a fresh process would create.
    from buyer_agent import negotiation_store

    fresh_engine = new_engine(os.environ["BUYER_DATABASE_URL"])
    fresh_sessions = async_sessionmaker(fresh_engine, expire_on_commit=False)
    record = await negotiation_store.get_negotiation(fresh_sessions, negotiation_id)
    assert record is not None
    assert record.state == "PENDING_AUTHORIZATION"
    await fresh_engine.dispose()
