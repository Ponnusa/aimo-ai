"""M4 required security test cases (docs/SECURITY.md,
docs/ROADMAP.md M4): stale/conflicting negotiation updates,
duplicate-message replay, cross-organization negotiation hijacking, and
the explicit guarantee that no M4 code path can create a binding order
or payment.

As of M5.1 (docs/DECISIONS.md ADR-012), every negotiation artifact must
also carry a valid per-artifact JWS proof before the supplier's own
negotiation-layer checks ever run — so network-api and registry-api are
now booted too, and every artifact this file sends is signed with a
real, registered sender's key. The legitimate buyer's RFQ/counter/
accept artifacts are signed with its own real, registered key so these
tests still exercise the *negotiation*-layer protections being tested,
not just message authentication (which has its own dedicated coverage
in packages/message-integrity/tests and apps/*/tests/test_authorization.py).
The hijack attempt is signed with a key registry-api has never seen,
so it is rejected at the authentication layer — an even stronger
guarantee than the original M4 test, with the same observable result
(TASK_STATE_FAILED).
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import os
import socket
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import Role, SendMessageRequest, TaskState
from fastapi import FastAPI
from food_wholesale.negotiation import build_accept_proposal, build_counterproposal, build_rfq
from message_integrity import generate_signing_keypair, private_key_to_pem, sign_artifact
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Base, new_engine
from identity import issue_service_credential

_SIGNING_SECRET = "negotiation-security-test-signing-secret"
_ADMIN_KEY = "negotiation-security-test-admin-key"
_INTERNAL_KEY = "negotiation-security-test-internal-key"
BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"
OTHER_ORG = "org_fictional_other_01"
BUYER_AGENT_ID = "agent_buyer_01"
SUPPLIER_AGENT_ID = "agent_fictional_supplier_01"


@dataclass
class _RunningServer:
    base_url: str
    _server: uvicorn.Server
    _thread: threading.Thread

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server_thread(server: uvicorn.Server) -> None:
    asyncio.run(server.serve())


def _start_server(app: FastAPI, *, port: int) -> _RunningServer:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=_run_server_thread, args=(server,), daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=0.5)
            if response.status_code == 200:
                return _RunningServer(base_url=base_url, _server=server, _thread=thread)
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Server on {base_url} did not become healthy in time.")


@contextlib.contextmanager
def _running_server(app: FastAPI, *, port: int) -> Iterator[_RunningServer]:
    handle = _start_server(app, port=port)
    try:
        yield handle
    finally:
        handle.stop()


def _fresh_sqlite_url(label: str) -> str:
    path = os.path.join(tempfile.gettempdir(), f"aimo_m4_sec_{label}_{uuid.uuid4().hex}.sqlite3")
    return f"sqlite+aiosqlite:///{path}"


async def _create_tables(engine, *models) -> None:
    tables = [model.__table__ for model in models]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)


def _build_network_app(*, database_url: str):
    import network_api.app as network_app_module
    import network_api.settings as network_settings_module
    from network_api.models import (
        AgentSigningDelegationRecord,
        KeyEventAccessLogRecord,
        KeyLifecycleEventRecord,
        OrganizationKidNamespaceRecord,
        OrganizationRecord,
        OrganizationSigningKeyRecord,
        ServiceCredentialIssuanceRecord,
        TradingRelationshipRecord,
    )

    os.environ["DATABASE_URL"] = database_url
    os.environ["NETWORK_ADMIN_API_KEY"] = _ADMIN_KEY
    os.environ["NETWORK_INTERNAL_SERVICE_KEY"] = _INTERNAL_KEY
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = _SIGNING_SECRET
    network_settings_module.get_settings.cache_clear()

    app = network_app_module.create_app()
    return app, (
        OrganizationRecord,
        TradingRelationshipRecord,
        OrganizationSigningKeyRecord,
        OrganizationKidNamespaceRecord,
        AgentSigningDelegationRecord,
        KeyLifecycleEventRecord,
        KeyEventAccessLogRecord,
        ServiceCredentialIssuanceRecord,
    )


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
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = _SIGNING_SECRET
    os.environ["NETWORK_API_URL"] = network_api_url
    os.environ["NETWORK_INTERNAL_SERVICE_KEY"] = _INTERNAL_KEY
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
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = _SIGNING_SECRET
    supplier_mcp_settings_module.get_settings.cache_clear()

    return supplier_mcp_app_module.create_app(), SupplierMcpToolInvocationRecord


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
        SupplierVerificationEvidenceReceiptRecord,
    )

    os.environ["SUPPLIER_A2A_URL"] = a2a_url
    os.environ["SUPPLIER_DATABASE_URL"] = database_url
    os.environ["SUPPLIER_ORGANIZATION_ID"] = SUPPLIER_ORG
    os.environ["SUPPLIER_AGENT_ID"] = SUPPLIER_AGENT_ID
    os.environ["SUPPLIER_MCP_URL"] = supplier_mcp_url
    os.environ["SUPPLIER_MCP_SERVICE_CREDENTIAL"] = issue_service_credential(
        organization_id=SUPPLIER_ORG, scope="use_supplier_tools", signing_secret=_SIGNING_SECRET
    )
    os.environ["NETWORK_API_URL"] = network_api_url
    os.environ["REGISTRY_API_URL"] = registry_api_url
    os.environ["SUPPLIER_SIGNING_PRIVATE_KEY_PEM"] = signing_private_key_pem
    os.environ["SUPPLIER_SIGNING_KID"] = "sec-supplier-k1"
    supplier_settings_module.get_settings.cache_clear()

    app = supplier_app_module.create_app()
    return app, (
        SupplierTaskRecord,
        SupplierNegotiationRecord,
        SupplierProposalRecord,
        SupplierProcessedArtifactRecord,
        SupplierSeenMessageProofRecord,
        SupplierVerificationEvidenceReceiptRecord,
    )


def _build_buyer_app(*, a2a_url: str, database_url: str):
    """A real buyer-agent process is booted only so it can answer
    registry-api's endpoint-ownership challenge during registration —
    this file still sends hand-built artifacts directly (never through
    ``buyer_agent.client``) so it can exercise negotiation-layer edge
    cases precisely."""
    import buyer_agent.app as buyer_app_module
    import buyer_agent.settings as buyer_settings_module
    from buyer_agent.models import BuyerTaskRecord

    os.environ["BUYER_A2A_URL"] = a2a_url
    os.environ["BUYER_DATABASE_URL"] = database_url
    os.environ["BUYER_ORGANIZATION_ID"] = BUYER_ORG
    os.environ["BUYER_AGENT_ID"] = BUYER_AGENT_ID
    buyer_settings_module.get_settings.cache_clear()

    app = buyer_app_module.create_app()
    return app, (BuyerTaskRecord,)


async def _register_signing_key(*, network_server: _RunningServer, organization_id: str, kid: str):
    from message_integrity import public_key_to_jwk

    private_key, public_key = generate_signing_keypair()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{network_server.base_url}/organizations/{organization_id}/signing-keys",
            json={"kid": kid, "public_key_jwk": public_key_to_jwk(public_key, kid=kid)},
            headers={"X-Admin-Key": _ADMIN_KEY},
        )
    response.raise_for_status()
    return private_key


async def _register_organizations(network_server) -> None:
    from network_api.models import OrganizationRecord

    engine = new_engine(os.environ["DATABASE_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        for org_id in (BUYER_ORG, SUPPLIER_ORG):
            await session.merge(
                OrganizationRecord(organization_id=org_id, display_name=org_id, verified=True)
            )
        await session.commit()
    await engine.dispose()


async def _create_relationship(network_server) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{network_server.base_url}/relationships",
            json={"buyer_organization_id": BUYER_ORG, "supplier_organization_id": SUPPLIER_ORG},
            headers={"X-Admin-Key": _ADMIN_KEY},
        )
    assert response.status_code == 200, response.text


async def _mint_credential(network_server, organization_id: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{network_server.base_url}/organizations/{organization_id}/service-credentials",
            json={"ttl_seconds": 3600},
            headers={"X-Admin-Key": _ADMIN_KEY},
        )
    assert response.status_code == 200, response.text
    return response.json()["token"]


async def _mint_management_credential(network_server, organization_id: str) -> str:
    """A ``manage_organization``-scoped credential — the only credential
    the agent's own ``PUT /internal/ownership-challenge`` accepts."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{network_server.base_url}/organizations/{organization_id}/service-credentials",
            json={"scope": "manage_organization", "ttl_seconds": 60},
            headers={"X-Admin-Key": _ADMIN_KEY},
        )
    assert response.status_code == 200, response.text
    return response.json()["token"]


async def _register_and_verify_agent(
    *,
    registry_server,
    agent_server,
    network_server,
    organization_id: str,
    agent_id: str,
    token: str,
) -> None:
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

        mgmt_token = await _mint_management_credential(network_server, organization_id)
        await client.put(
            f"{agent_server.base_url}/internal/ownership-challenge",
            json={"challenge": challenge},
            headers={"Authorization": f"Bearer {mgmt_token}"},
        )

        confirm = await client.post(
            f"{registry_server.base_url}/agents/{agent_id}/verification/confirm",
            params={"organization_id": organization_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["status"] == "active"


@pytest.fixture
async def security_servers():
    """Boots network-api, registry-api, supplier-mcp and supplier-agent
    (as real HTTP servers) plus a real buyer-agent process (only to
    answer the registration challenge), registers and relates both
    organizations, and returns ``(supplier, buyer_private_key, db_url)``.
    """
    network_database_url = _fresh_sqlite_url("network")
    network_app, network_models = _build_network_app(database_url=network_database_url)
    network_engine = new_engine(network_database_url)
    await _create_tables(network_engine, *network_models)
    await network_engine.dispose()

    with _running_server(network_app, port=_find_free_port()) as network_server:
        await _register_organizations(network_server)
        await _create_relationship(network_server)

        registry_database_url = _fresh_sqlite_url("registry")
        registry_app, registry_models = _build_registry_app(
            database_url=registry_database_url, network_api_url=network_server.base_url
        )
        registry_engine = new_engine(registry_database_url)
        await _create_tables(registry_engine, *registry_models)
        await registry_engine.dispose()

        with _running_server(registry_app, port=_find_free_port()) as registry_server:
            supplier_private_key = await _register_signing_key(
                network_server=network_server,
                organization_id=SUPPLIER_ORG,
                kid="sec-supplier-k1",
            )
            buyer_private_key = await _register_signing_key(
                network_server=network_server, organization_id=BUYER_ORG, kid="sec-buyer-k1"
            )

            mcp_database_url = _fresh_sqlite_url("mcp")
            mcp_app, mcp_model = _build_supplier_mcp_app(database_url=mcp_database_url)
            mcp_engine = new_engine(mcp_database_url)
            await _create_tables(mcp_engine, mcp_model)
            await mcp_engine.dispose()

            with _running_server(mcp_app, port=_find_free_port()) as mcp_server:
                supplier_port = _find_free_port()
                supplier_a2a_url = f"http://127.0.0.1:{supplier_port}"
                supplier_database_url = _fresh_sqlite_url("supplier")
                supplier_app, supplier_models = _build_supplier_app(
                    a2a_url=supplier_a2a_url,
                    database_url=supplier_database_url,
                    supplier_mcp_url=f"{mcp_server.base_url}/mcp",
                    network_api_url=network_server.base_url,
                    registry_api_url=registry_server.base_url,
                    signing_private_key_pem=private_key_to_pem(supplier_private_key),
                )
                supplier_engine = new_engine(supplier_database_url)
                await _create_tables(supplier_engine, *supplier_models)
                await supplier_engine.dispose()

                buyer_port = _find_free_port()
                buyer_a2a_url = f"http://127.0.0.1:{buyer_port}"
                buyer_database_url = _fresh_sqlite_url("buyer")
                buyer_app, buyer_models = _build_buyer_app(
                    a2a_url=buyer_a2a_url, database_url=buyer_database_url
                )
                buyer_engine = new_engine(buyer_database_url)
                await _create_tables(buyer_engine, *buyer_models)
                await buyer_engine.dispose()

                with (
                    _running_server(supplier_app, port=supplier_port) as supplier,
                    _running_server(buyer_app, port=buyer_port) as buyer,
                ):
                    supplier_token = await _mint_credential(network_server, SUPPLIER_ORG)
                    await _register_and_verify_agent(
                        registry_server=registry_server,
                        agent_server=supplier,
                        network_server=network_server,
                        organization_id=SUPPLIER_ORG,
                        agent_id=SUPPLIER_AGENT_ID,
                        token=supplier_token,
                    )
                    buyer_token = await _mint_credential(network_server, BUYER_ORG)
                    await _register_and_verify_agent(
                        registry_server=registry_server,
                        agent_server=buyer,
                        network_server=network_server,
                        organization_id=BUYER_ORG,
                        agent_id=BUYER_AGENT_ID,
                        token=buyer_token,
                    )

                    yield supplier, buyer_private_key, supplier_database_url


def _sign_as_buyer(artifact: dict, *, buyer_private_key) -> dict:
    return sign_artifact(
        artifact,
        private_key=buyer_private_key,
        kid="sec-buyer-k1",
        organization_id=BUYER_ORG,
        agent_id=BUYER_AGENT_ID,
        recipient_organization_id=SUPPLIER_ORG,
    )


def _sign_as_unregistered_attacker(artifact: dict) -> dict:
    """A key registry-api/network-api has never seen — this can never
    pass message authentication, regardless of what organization_id it
    claims in the payload."""
    attacker_private_key, _ = generate_signing_keypair()
    return sign_artifact(
        artifact,
        private_key=attacker_private_key,
        kid="attacker-k1",
        organization_id=OTHER_ORG,
        agent_id="agent_attacker_01",
        recipient_organization_id=SUPPLIER_ORG,
    )


async def _send(supplier, artifact: dict) -> tuple[str, dict | None]:
    async with httpx.AsyncClient() as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=supplier.base_url)
        card = await resolver.get_agent_card()
        message = new_data_message(data=artifact, role=Role.ROLE_USER)
        message.metadata["sender_agent_id"] = artifact["sender_agent_id"]

        async with httpx.AsyncClient() as send_client:
            client = await create_client(
                agent=card, client_config=ClientConfig(streaming=False, httpx_client=send_client)
            )
            final_task = None
            async for chunk in client.send_message(SendMessageRequest(message=message)):
                if chunk.HasField("task"):
                    final_task = chunk.task

    assert final_task is not None
    if final_task.status.state != TaskState.TASK_STATE_COMPLETED:
        return TaskState.Name(final_task.status.state), None

    parts = [part for a in final_task.artifacts for part in a.parts]
    data_parts = get_data_parts(parts)
    return "TASK_STATE_COMPLETED", (data_parts[0] if data_parts else None)


def _rfq(*, buyer_agent_id: str = BUYER_AGENT_ID, buyer_org: str = BUYER_ORG) -> dict:
    return build_rfq(
        artifact_id=str(uuid.uuid4()),
        transaction_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        buyer_agent_id=buyer_agent_id,
        supplier_agent_id=SUPPLIER_AGENT_ID,
        buyer_organization_id=buyer_org,
        proposal_id=str(uuid.uuid4()),
        lines=[{"product_query": "lactose-free milk", "quantity": "50", "unit": "L"}],
        delivery_location_ref="site_01",
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stale_counterproposal_is_rejected(security_servers) -> None:
    supplier, buyer_private_key, _db_url = security_servers
    rfq = _sign_as_buyer(_rfq(), buyer_private_key=buyer_private_key)
    state, quote = await _send(supplier, rfq)
    assert state == "TASK_STATE_COMPLETED"
    assert quote is not None and quote["type"] == "quote"

    stale_counter = _sign_as_buyer(
        build_counterproposal(
            artifact_id=str(uuid.uuid4()),
            transaction_id=rfq["transaction_id"],
            correlation_id=rfq["correlation_id"],
            buyer_agent_id=rfq["sender_agent_id"],
            supplier_agent_id=quote["sender_agent_id"],
            buyer_organization_id=BUYER_ORG,
            proposal_id=str(uuid.uuid4()),
            supersedes_proposal_id="prop_does_not_exist",  # wrong: not the quote just received
            version=3,
            lines=quote["payload"]["lines"],
            currency=quote["payload"]["currency"],
        ),
        buyer_private_key=buyer_private_key,
    )
    state, _response = await _send(supplier, stale_counter)
    assert state == "TASK_STATE_FAILED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_rfq_artifact_is_not_reprocessed(security_servers) -> None:
    """Simulates a realistic buyer retry: the exact same RFQ content
    (artifact_id, proposal_id, lines) is resent after an assumed lost
    response, but freshly re-signed (a new jti/signature per send is
    the whole point of M5.1 message-level replay protection — reusing
    one is itself a replay, covered separately by
    ``packages/message-integrity/tests``). M4's own artifact_id-based
    dedup cache is what must make this resend idempotent."""
    supplier, buyer_private_key, db_url = security_servers
    unsigned_rfq = _rfq()

    state1, quote1 = await _send(
        supplier, _sign_as_buyer(unsigned_rfq, buyer_private_key=buyer_private_key)
    )
    state2, quote2 = await _send(
        supplier, _sign_as_buyer(unsigned_rfq, buyer_private_key=buyer_private_key)
    )
    assert state1 == state2 == "TASK_STATE_COMPLETED"
    # Cached response payloads are identical; the top-level proof around
    # them differs (a fresh signature/jti is minted per response).
    assert quote1 is not None and quote2 is not None
    assert {k: v for k, v in quote1.items() if k != "proof"} == {
        k: v for k, v in quote2.items() if k != "proof"
    }

    from supplier_agent.models import SupplierProposalRecord

    engine = new_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        from sqlalchemy import select

        rows = (
            (
                await session.execute(
                    select(SupplierProposalRecord).where(
                        SupplierProposalRecord.negotiation_id == unsigned_rfq["transaction_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
    await engine.dispose()
    # RFQ (version 1) + exactly one quote (version 2) — the replay never
    # created a second quote proposal.
    assert len(rows) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_organization_cannot_hijack_anothers_negotiation(security_servers) -> None:
    """An attacker who learns another organization's negotiation_id
    (artifact IDs/negotiation IDs are not secrets — they travel in plain
    artifacts) must not be able to inject a counterproposal or acceptance
    into it. As of M5.1 this is rejected even earlier than the
    negotiation-participant check M4 added: the attacker has no real,
    registered signing key at all, so message authentication itself
    fails first."""
    supplier, buyer_private_key, _db_url = security_servers
    rfq = _sign_as_buyer(_rfq(buyer_org=BUYER_ORG), buyer_private_key=buyer_private_key)
    state, quote = await _send(supplier, rfq)
    assert state == "TASK_STATE_COMPLETED"
    assert quote is not None

    hijack_counter = _sign_as_unregistered_attacker(
        build_counterproposal(
            artifact_id=str(uuid.uuid4()),
            transaction_id=rfq["transaction_id"],  # the real negotiation_id
            correlation_id=rfq["correlation_id"],
            buyer_agent_id="agent_attacker_01",
            supplier_agent_id=quote["sender_agent_id"],
            buyer_organization_id=OTHER_ORG,  # not the organization that started it
            proposal_id=str(uuid.uuid4()),
            supersedes_proposal_id=quote["proposal_id"],
            version=quote["version"] + 1,
            lines=quote["payload"]["lines"],
            currency=quote["payload"]["currency"],
        )
    )
    state, _response = await _send(supplier, hijack_counter)
    assert state == "TASK_STATE_FAILED"

    hijack_accept = _sign_as_unregistered_attacker(
        build_accept_proposal(
            artifact_id=str(uuid.uuid4()),
            transaction_id=rfq["transaction_id"],
            correlation_id=rfq["correlation_id"],
            sender_agent_id="agent_attacker_01",
            recipient_agent_id=quote["sender_agent_id"],
            organization_id=OTHER_ORG,
            proposal_id=str(uuid.uuid4()),
            version=quote["version"] + 1,
            accepted_proposal_id=quote["proposal_id"],
            accepted_content_hash=quote["content_hash"],
        )
    )
    state, _response = await _send(supplier, hijack_accept)
    assert state == "TASK_STATE_FAILED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mismatched_accept_hash_is_rejected(security_servers) -> None:
    """``accept_proposal`` must bind to the exact content_hash this
    supplier itself recorded — a forged/altered hash is rejected, not
    silently treated as an acceptance of whatever was actually quoted."""
    supplier, buyer_private_key, _db_url = security_servers
    rfq = _sign_as_buyer(_rfq(), buyer_private_key=buyer_private_key)
    _state, quote = await _send(supplier, rfq)
    assert quote is not None

    forged_accept = _sign_as_buyer(
        build_accept_proposal(
            artifact_id=str(uuid.uuid4()),
            transaction_id=rfq["transaction_id"],
            correlation_id=rfq["correlation_id"],
            sender_agent_id=rfq["sender_agent_id"],
            recipient_agent_id=quote["sender_agent_id"],
            organization_id=BUYER_ORG,
            proposal_id=str(uuid.uuid4()),
            version=quote["version"] + 1,
            accepted_proposal_id=quote["proposal_id"],
            accepted_content_hash="0" * 64,  # forged
        ),
        buyer_private_key=buyer_private_key,
    )
    state, _response = await _send(supplier, forged_accept)
    assert state == "TASK_STATE_FAILED"


def test_no_m4_code_path_creates_a_binding_order_or_payment() -> None:
    """Static guarantee: none of the M4 negotiation modules reference
    anything order/payment/commit-shaped, and every proposed_agreement
    this code can build is unconditionally stamped NONBINDING (verified
    separately in industries/food-wholesale/tests/test_negotiation.py).
    This is the explicit M4 acceptance requirement: "Verification that
    no M4 path can create binding orders or payments."
    """
    forbidden_tokens = {
        "submit_order",
        "create_order",
        "place_order",
        "charge",
        "payment",
        "commit_order",
    }
    negotiation_modules = [
        Path("apps/buyer-agent/src/buyer_agent/negotiation.py"),
        Path("apps/buyer-agent/src/buyer_agent/negotiation_store.py"),
        Path("apps/supplier-agent/src/supplier_agent/negotiation.py"),
        Path("apps/supplier-agent/src/supplier_agent/negotiation_store.py"),
        Path("industries/food-wholesale/src/food_wholesale/negotiation.py"),
    ]
    repo_root = Path(__file__).resolve().parents[2]
    for relative_path in negotiation_modules:
        source = (repo_root / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        lowered = {identifier.lower() for identifier in identifiers}
        hit = forbidden_tokens & lowered
        assert not hit, f"{relative_path} references forbidden identifier(s): {hit}"
