"""M4 required security test cases (docs/SECURITY.md,
docs/ROADMAP.md M4): stale/conflicting negotiation updates,
duplicate-message replay, cross-organization negotiation hijacking, and
the explicit guarantee that no M4 code path can create a binding order
or payment.

Only ``supplier-mcp`` and ``supplier-agent`` are booted as real HTTP
servers — a raw A2A client sends hand-built negotiation artifacts
directly, so these tests exercise the supplier's own defenses without
depending on buyer-agent's (already-correct) behavior.
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
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Base, new_engine
from identity import issue_service_credential

_SIGNING_SECRET = "negotiation-security-test-signing-secret"
BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"
OTHER_ORG = "org_fictional_other_01"


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


def _build_supplier_mcp_app(*, database_url: str):
    import supplier_mcp.app as supplier_mcp_app_module
    import supplier_mcp.settings as supplier_mcp_settings_module
    from supplier_mcp.models import SupplierMcpToolInvocationRecord

    os.environ["SUPPLIER_MCP_DATABASE_URL"] = database_url
    os.environ["SUPPLIER_MCP_ORGANIZATION_ID"] = SUPPLIER_ORG
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = _SIGNING_SECRET
    supplier_mcp_settings_module.get_settings.cache_clear()

    return supplier_mcp_app_module.create_app(), SupplierMcpToolInvocationRecord


def _build_supplier_app(*, a2a_url: str, database_url: str, supplier_mcp_url: str):
    import supplier_agent.app as supplier_app_module
    import supplier_agent.settings as supplier_settings_module
    from supplier_agent.models import (
        SupplierNegotiationRecord,
        SupplierProcessedArtifactRecord,
        SupplierProposalRecord,
        SupplierTaskRecord,
    )

    os.environ["SUPPLIER_A2A_URL"] = a2a_url
    os.environ["SUPPLIER_DATABASE_URL"] = database_url
    os.environ["SUPPLIER_ORGANIZATION_ID"] = SUPPLIER_ORG
    os.environ["SUPPLIER_MCP_URL"] = supplier_mcp_url
    os.environ["SUPPLIER_MCP_SERVICE_CREDENTIAL"] = issue_service_credential(
        organization_id=SUPPLIER_ORG, scope="use_supplier_tools", signing_secret=_SIGNING_SECRET
    )
    supplier_settings_module.get_settings.cache_clear()

    app = supplier_app_module.create_app()
    return app, (
        SupplierTaskRecord,
        SupplierNegotiationRecord,
        SupplierProposalRecord,
        SupplierProcessedArtifactRecord,
    )


@pytest.fixture
async def supplier_server():
    mcp_database_url = _fresh_sqlite_url("mcp")
    mcp_app, mcp_model = _build_supplier_mcp_app(database_url=mcp_database_url)
    mcp_engine = new_engine(mcp_database_url)
    await _create_tables(mcp_engine, mcp_model)
    await mcp_engine.dispose()

    with _running_server(mcp_app, port=_find_free_port()) as mcp_server:
        port = _find_free_port()
        a2a_url = f"http://127.0.0.1:{port}"
        database_url = _fresh_sqlite_url("agent")
        app, models = _build_supplier_app(
            a2a_url=a2a_url,
            database_url=database_url,
            supplier_mcp_url=f"{mcp_server.base_url}/mcp",
        )
        engine = new_engine(database_url)
        await _create_tables(engine, *models)
        await engine.dispose()

        with _running_server(app, port=port) as server:
            yield server, database_url


async def _send(supplier, artifact: dict) -> tuple[TaskState, dict | None]:
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


def _rfq(*, buyer_agent_id: str = "agent_buyer_01", buyer_org: str = BUYER_ORG) -> dict:
    return build_rfq(
        artifact_id=str(uuid.uuid4()),
        transaction_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        buyer_agent_id=buyer_agent_id,
        supplier_agent_id="agent_fictional_supplier_01",
        buyer_organization_id=buyer_org,
        proposal_id=str(uuid.uuid4()),
        lines=[{"product_query": "lactose-free milk", "quantity": "50", "unit": "L"}],
        delivery_location_ref="site_01",
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stale_counterproposal_is_rejected(supplier_server) -> None:
    supplier, _db_url = supplier_server
    rfq = _rfq()
    state, quote = await _send(supplier, rfq)
    assert state == "TASK_STATE_COMPLETED"
    assert quote is not None and quote["type"] == "quote"

    stale_counter = build_counterproposal(
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
    )
    state, _response = await _send(supplier, stale_counter)
    assert state == "TASK_STATE_FAILED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_rfq_artifact_is_not_reprocessed(supplier_server) -> None:
    supplier, db_url = supplier_server
    rfq = _rfq()

    state1, quote1 = await _send(supplier, rfq)
    state2, quote2 = await _send(supplier, rfq)  # exact same artifact_id, replayed
    assert state1 == state2 == "TASK_STATE_COMPLETED"
    assert quote1 == quote2  # identical cached response, not a freshly re-evaluated one

    from supplier_agent.models import SupplierProposalRecord

    engine = new_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        from sqlalchemy import select

        rows = (
            (
                await session.execute(
                    select(SupplierProposalRecord).where(
                        SupplierProposalRecord.negotiation_id == rfq["transaction_id"]
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
async def test_cross_organization_cannot_hijack_anothers_negotiation(supplier_server) -> None:
    """An attacker who learns another organization's negotiation_id
    (artifact IDs/negotiation IDs are not secrets — they travel in plain
    artifacts) must not be able to inject a counterproposal or acceptance
    into it by simply claiming a different organization_id."""
    supplier, _db_url = supplier_server
    rfq = _rfq(buyer_org=BUYER_ORG)
    state, quote = await _send(supplier, rfq)
    assert state == "TASK_STATE_COMPLETED"
    assert quote is not None

    hijack_counter = build_counterproposal(
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
    state, _response = await _send(supplier, hijack_counter)
    assert state == "TASK_STATE_FAILED"

    hijack_accept = build_accept_proposal(
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
    state, _response = await _send(supplier, hijack_accept)
    assert state == "TASK_STATE_FAILED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mismatched_accept_hash_is_rejected(supplier_server) -> None:
    """``accept_proposal`` must bind to the exact content_hash this
    supplier itself recorded — a forged/altered hash is rejected, not
    silently treated as an acceptance of whatever was actually quoted."""
    supplier, _db_url = supplier_server
    rfq = _rfq()
    _state, quote = await _send(supplier, rfq)
    assert quote is not None

    forged_accept = build_accept_proposal(
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
