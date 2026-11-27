"""Proves supplier-mcp's sandbox adapter wiring (M7): swapping
``SUPPLIER_BUSINESS_SYSTEM_ADAPTER=sandbox`` makes supplier-mcp's tools
talk to a real ``supplier-sandbox-erp`` server instead of the in-process
mock, with no change to the MCP tool surface, and that a transient fault
from that business system never surfaces as a false ``"confirmed"`` or
otherwise-trusted result.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp_integration import McpToolError, call_tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from supplier_mcp.app import create_app
from supplier_mcp.models import SupplierMcpOrderRecord
from supplier_mcp.settings import get_settings

from database import Base, new_engine
from identity import issue_service_credential

ORG = "org_fictional_wholesale_01"

#: Real-socket server harness, duplicated per this repo's existing
#: per-app test convention (see apps/supplier-mcp/tests/test_app.py).


@dataclass
class _RunningServer:
    base_url: str
    _server: uvicorn.Server
    _thread: threading.Thread

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server_thread(server: uvicorn.Server) -> None:
    asyncio.run(server.serve())


def _start_server(app: FastAPI, *, host: str = "127.0.0.1", port: int) -> _RunningServer:
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=_run_server_thread, args=(server,), daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=0.5)
            if response.status_code == 200:
                return _RunningServer(base_url=base_url, _server=server, _thread=thread)
        except httpx.HTTPError:
            pass
        time.sleep(0.1)

    server.should_exit = True
    thread.join(timeout=5)
    raise RuntimeError(f"Server on {base_url} did not become healthy in time.")


@contextlib.contextmanager
def running_server(app: FastAPI, *, port: int) -> Iterator[_RunningServer]:
    handle = _start_server(app, port=port)
    try:
        yield handle
    finally:
        handle.stop()


def _configure_sandbox_env(*, erp_base_url: str, erp_token: str) -> None:
    os.environ["SUPPLIER_BUSINESS_SYSTEM_ADAPTER"] = "sandbox"
    os.environ["SANDBOX_ERP_BASE_URL"] = erp_base_url
    os.environ["SANDBOX_ERP_SERVICE_CREDENTIAL"] = erp_token


def _reset_to_mock_adapter() -> None:
    os.environ["SUPPLIER_BUSINESS_SYSTEM_ADAPTER"] = "mock"


@contextlib.asynccontextmanager
async def _running_sandbox_erp():
    from supplier_sandbox_erp.app import create_app as create_erp_app
    from supplier_sandbox_erp.settings import get_settings as get_erp_settings

    db_file = os.path.join(tempfile.gettempdir(), "aimo_sandbox_erp_for_supplier_mcp.sqlite3")
    with contextlib.suppress(FileNotFoundError):
        os.remove(db_file)
    os.environ["SUPPLIER_SANDBOX_ERP_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_file}"
    os.environ.setdefault("SUPPLIER_SANDBOX_ERP_ORGANIZATION_ID", ORG)
    os.environ.setdefault("ENVIRONMENT", "test")
    get_erp_settings.cache_clear()
    erp_settings = get_erp_settings()

    engine = new_engine(erp_settings.supplier_sandbox_erp_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    erp_app = create_erp_app()
    erp_port = find_free_port()
    with running_server(erp_app, port=erp_port):
        base_url = f"http://127.0.0.1:{erp_port}"
        token = issue_service_credential(
            organization_id=ORG,
            scope="use_sandbox_erp",
            signing_secret=erp_settings.service_credential_signing_secret,
        )
        yield base_url, token


async def _fresh_supplier_mcp_app():
    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.supplier_mcp_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return create_app(), settings


@pytest.mark.asyncio
async def test_search_catalogue_through_sandbox_adapter() -> None:
    async with _running_sandbox_erp() as (erp_base_url, erp_token):
        _configure_sandbox_env(erp_base_url=erp_base_url, erp_token=erp_token)
        try:
            app, settings = await _fresh_supplier_mcp_app()
            port = find_free_port()
            with running_server(app, port=port):
                result = await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=issue_service_credential(
                        organization_id=ORG,
                        scope="use_supplier_tools",
                        signing_secret=settings.service_credential_signing_secret,
                    ),
                    tool_name="search_catalogue",
                    arguments={"query": "oat milk"},
                )
        finally:
            _reset_to_mock_adapter()
    assert any(p["product_ref"] == "sandbox_sku_oat_milk" for p in result["result"])
    # The sandbox adapter does not embed price/forecast stock in a
    # catalogue search -- unlike the mock adapter's merged shape.
    assert result["result"][0]["unit_price"] is None


@pytest.mark.asyncio
async def test_execute_order_surfaces_sandbox_transient_fault_as_tool_error() -> None:
    async with _running_sandbox_erp() as (erp_base_url, erp_token):
        _configure_sandbox_env(erp_base_url=erp_base_url, erp_token=erp_token)
        try:
            app, settings = await _fresh_supplier_mcp_app()
            port = find_free_port()
            with running_server(app, port=port):
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{erp_base_url}/admin/fault-injection/arm",
                        json={"scenario": "transient_error"},
                    )
                execution_token = issue_service_credential(
                    organization_id=ORG,
                    scope="execute_order",
                    signing_secret=settings.service_credential_signing_secret,
                )
                with pytest.raises(McpToolError):
                    await call_tool(
                        server_url=f"http://127.0.0.1:{port}/mcp",
                        token=execution_token,
                        tool_name="execute_order",
                        arguments={
                            "idempotency_key": "idem_sandbox_fault",
                            "agreement_id": "txn_01",
                            "content_hash": "sha256:abc",
                            "buyer_organization_id": "org_fictional_bistro_01",
                            "lines": [],
                        },
                    )
                # Never left ambiguous in supplier-mcp's own durable
                # store: the failed attempt wrote no row at all, so a
                # retry with the same key is a clean first attempt, not
                # stuck behind a phantom "confirmed" or "failed" record.
                status = await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=execution_token,
                    tool_name="get_order_status",
                    arguments={"idempotency_key": "idem_sandbox_fault"},
                )
                assert status["found"] is False
        finally:
            _reset_to_mock_adapter()


@pytest.mark.asyncio
async def test_execute_order_reports_pending_for_delayed_ack() -> None:
    async with _running_sandbox_erp() as (erp_base_url, erp_token):
        _configure_sandbox_env(erp_base_url=erp_base_url, erp_token=erp_token)
        try:
            app, settings = await _fresh_supplier_mcp_app()
            port = find_free_port()
            with running_server(app, port=port):
                key = "idem_sandbox_pending"
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{erp_base_url}/admin/fault-injection/arm",
                        json={"scenario": "delayed_ack", "idempotency_key": key},
                    )
                execution_token = issue_service_credential(
                    organization_id=ORG,
                    scope="execute_order",
                    signing_secret=settings.service_credential_signing_secret,
                )
                result = await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=execution_token,
                    tool_name="execute_order",
                    arguments={
                        "idempotency_key": key,
                        "agreement_id": "txn_01",
                        "content_hash": "sha256:abc",
                        "buyer_organization_id": "org_fictional_bistro_01",
                        "lines": [],
                    },
                )
                assert result["status"] == "pending"
                assert result["external_order_id"] is None

                async with httpx.AsyncClient() as client:
                    await client.post(f"{erp_base_url}/admin/fault-injection/resolve/{key}")

                status = await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=execution_token,
                    tool_name="get_order_status",
                    arguments={"idempotency_key": key},
                )
                assert status["status"] == "confirmed"
                assert status["external_order_id"] is not None
        finally:
            _reset_to_mock_adapter()


async def _get_order_directly_from_erp(
    *, erp_base_url: str, erp_token: str, idempotency_key: str
) -> dict:
    """Independent confirmation: reads the order straight from
    sandbox-erp's own record, bypassing supplier-mcp entirely -- the
    actual source of truth for whether an ERP order exists at all."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{erp_base_url}/orders/{idempotency_key}",
            headers={"Authorization": f"Bearer {erp_token}"},
        )
    response.raise_for_status()
    return response.json()


@pytest.mark.asyncio
async def test_duplicate_submission_never_creates_a_second_erp_order() -> None:
    """Acceptance criterion: duplicate submission. Calling execute_order
    twice with the same idempotency_key must return the exact same
    order both times, confirmed independently against sandbox-erp's own
    record as exactly one order, never two."""
    async with _running_sandbox_erp() as (erp_base_url, erp_token):
        _configure_sandbox_env(erp_base_url=erp_base_url, erp_token=erp_token)
        try:
            app, settings = await _fresh_supplier_mcp_app()
            port = find_free_port()
            with running_server(app, port=port):
                key = "idem_sandbox_duplicate"
                execution_token = issue_service_credential(
                    organization_id=ORG,
                    scope="execute_order",
                    signing_secret=settings.service_credential_signing_secret,
                )
                first = await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=execution_token,
                    tool_name="execute_order",
                    arguments={
                        "idempotency_key": key,
                        "agreement_id": "txn_01",
                        "content_hash": "sha256:abc",
                        "buyer_organization_id": "org_fictional_bistro_01",
                        "lines": [],
                    },
                )
                assert first["duplicate"] is False
                assert first["external_order_id"] is not None

                second = await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=execution_token,
                    tool_name="execute_order",
                    arguments={
                        "idempotency_key": key,
                        "agreement_id": "txn_01",
                        "content_hash": "sha256:abc",
                        "buyer_organization_id": "org_fictional_bistro_01",
                        "lines": [],
                    },
                )
                assert second["duplicate"] is True
                assert second["external_order_id"] == first["external_order_id"]

                erp_record = await _get_order_directly_from_erp(
                    erp_base_url=erp_base_url, erp_token=erp_token, idempotency_key=key
                )
                assert erp_record["found"] is True
                assert erp_record["external_order_id"] == first["external_order_id"]
        finally:
            _reset_to_mock_adapter()


@pytest.mark.asyncio
async def test_get_order_status_recovers_an_order_accepted_after_the_client_timed_out() -> None:
    """Acceptance criterion: timeout after possible ERP acceptance,
    recovery, and independent confirmation. ``execute_order`` writes no
    local row on an adapter timeout (it never learned an
    external_order_id) -- but sandbox-erp's own "timeout" fault keeps
    processing the request after its client gives up (a real,
    deliberately-reproduced race, not a simulated shortcut: the sandbox
    adapter's HTTP client has a fixed 3s timeout, the fault sleeps
    longer, and the server keeps running after the client disconnects
    exactly as a real ERP's backend would). Without reconciling against
    the business system directly when no local row exists, that order
    would be stuck in RECONCILIATION_REQUIRED forever despite genuinely
    having been accepted. Confirms recovery, and independently confirms
    against sandbox-erp's own record that exactly one order exists."""
    async with _running_sandbox_erp() as (erp_base_url, erp_token):
        _configure_sandbox_env(erp_base_url=erp_base_url, erp_token=erp_token)
        try:
            app, settings = await _fresh_supplier_mcp_app()
            port = find_free_port()
            with running_server(app, port=port):
                key = "idem_sandbox_timeout_then_recover"
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{erp_base_url}/admin/fault-injection/arm",
                        json={"scenario": "timeout", "idempotency_key": key},
                    )
                execution_token = issue_service_credential(
                    organization_id=ORG,
                    scope="execute_order",
                    signing_secret=settings.service_credential_signing_secret,
                )
                with pytest.raises(McpToolError):
                    await call_tool(
                        server_url=f"http://127.0.0.1:{port}/mcp",
                        token=execution_token,
                        tool_name="execute_order",
                        arguments={
                            "idempotency_key": key,
                            "agreement_id": "txn_01",
                            "content_hash": "sha256:abc",
                            "buyer_organization_id": "org_fictional_bistro_01",
                            "lines": [],
                        },
                    )
                # No local row yet -- execute_order's own timeout path
                # never learned an external_order_id to record.
                immediately_after = await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=execution_token,
                    tool_name="get_order_status",
                    arguments={"idempotency_key": key},
                )
                assert immediately_after["found"] is False

                # sandbox-erp's own "timeout" fault keeps running past
                # the client's 3s giveup (default
                # fault_injection_timeout_seconds=5.0 > the adapter's
                # fixed 3.0s client timeout) -- wait past that so the
                # order genuinely exists server-side before reconciling.
                await asyncio.sleep(3.0)

                recovered = await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=execution_token,
                    tool_name="get_order_status",
                    arguments={"idempotency_key": key},
                )
                assert recovered["found"] is True
                assert recovered["status"] == "confirmed"
                assert recovered["external_order_id"] is not None

                erp_record = await _get_order_directly_from_erp(
                    erp_base_url=erp_base_url, erp_token=erp_token, idempotency_key=key
                )
                assert erp_record["found"] is True
                assert erp_record["external_order_id"] == recovered["external_order_id"]

                # A genuine retry after recovery must still never create
                # a second ERP order.
                retried = await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=execution_token,
                    tool_name="execute_order",
                    arguments={
                        "idempotency_key": key,
                        "agreement_id": "txn_01",
                        "content_hash": "sha256:abc",
                        "buyer_organization_id": "org_fictional_bistro_01",
                        "lines": [],
                    },
                )
                assert retried["duplicate"] is True
                assert retried["external_order_id"] == recovered["external_order_id"]
        finally:
            _reset_to_mock_adapter()


async def _arm_timeout_and_recover(
    *, erp_base_url: str, port: int, execution_token: str, key: str
) -> dict:
    """Shared setup for the three tests below: reproduces the same
    timeout-then-recover race as the test above, leaving a row with
    ``recovered_without_request_context=True`` and no known
    agreement_id/buyer_organization_id/content_hash, then returns the
    recovered ``get_order_status`` result."""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{erp_base_url}/admin/fault-injection/arm",
            json={"scenario": "timeout", "idempotency_key": key},
        )
    with pytest.raises(McpToolError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=execution_token,
            tool_name="execute_order",
            arguments={
                "idempotency_key": key,
                "agreement_id": "txn_01",
                "content_hash": "sha256:abc",
                "buyer_organization_id": "org_fictional_bistro_01",
                "lines": [],
            },
        )
    await asyncio.sleep(3.0)
    recovered = await call_tool(
        server_url=f"http://127.0.0.1:{port}/mcp",
        token=execution_token,
        tool_name="get_order_status",
        arguments={"idempotency_key": key},
    )
    assert recovered["found"] is True
    return recovered


async def _read_order_row(*, database_url: str, idempotency_key: str):
    engine = new_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return (
                await session.execute(
                    select(SupplierMcpOrderRecord).where(
                        SupplierMcpOrderRecord.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()


async def _count_backfill_audit_hits(*, database_url: str, idempotency_key: str) -> int:
    """Counts how many ``execute_order`` audit entries for this key
    actually logged "(backfilled transaction identity)" -- i.e. how
    many of this key's UPDATE attempts actually matched/wrote a row
    (``rowcount > 0``), not merely how many *attempted* one. With the
    ``recovered_without_request_context.is_(True)`` guard in place,
    exactly one concurrent retry's UPDATE can ever match (the row's
    flag can only read True for one winning commit); without it, every
    retry's UPDATE matches unconditionally by idempotency_key alone, so
    this would count every racer that attempted a backfill, not just
    the single legitimate winner -- unlike the final row's content
    (which a single atomic UPDATE always writes as one consistent set
    of values regardless of whether the guard exists, so it alone
    cannot distinguish first-commit-wins from last-write-wins), the
    *count* of successful backfills is the one observable that only the
    atomic guard keeps at exactly one."""
    from supplier_mcp.models import SupplierMcpToolInvocationRecord

    engine = new_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            rows = (
                (
                    await session.execute(
                        select(SupplierMcpToolInvocationRecord).where(
                            SupplierMcpToolInvocationRecord.tool_name == "execute_order",
                            SupplierMcpToolInvocationRecord.success.is_(True),
                        )
                    )
                )
                .scalars()
                .all()
            )
    finally:
        await engine.dispose()
    return sum(
        1
        for row in rows
        if idempotency_key in row.detail and "backfilled transaction identity" in row.detail
    )


@pytest.mark.asyncio
async def test_valid_backfill_fills_in_transaction_identity_after_recovery() -> None:
    """Acceptance criterion: backfill from a valid retry. After
    recovery leaves a row with no known transaction identity, the first
    genuine ``execute_order`` retry for that key safely backfills
    agreement_id/buyer_organization_id/content_hash and clears
    ``recovered_without_request_context`` -- never left permanently
    unlinked when real identity is available."""
    async with _running_sandbox_erp() as (erp_base_url, erp_token):
        _configure_sandbox_env(erp_base_url=erp_base_url, erp_token=erp_token)
        try:
            app, settings = await _fresh_supplier_mcp_app()
            port = find_free_port()
            with running_server(app, port=port):
                key = "idem_sandbox_valid_backfill"
                execution_token = issue_service_credential(
                    organization_id=ORG,
                    scope="execute_order",
                    signing_secret=settings.service_credential_signing_secret,
                )
                await _arm_timeout_and_recover(
                    erp_base_url=erp_base_url, port=port, execution_token=execution_token, key=key
                )
                row = await _read_order_row(
                    database_url=settings.supplier_mcp_database_url, idempotency_key=key
                )
                assert row.recovered_without_request_context is True
                assert row.agreement_id == ""

                await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=execution_token,
                    tool_name="execute_order",
                    arguments={
                        "idempotency_key": key,
                        "agreement_id": "txn_backfill_01",
                        "content_hash": "sha256:backfilled",
                        "buyer_organization_id": "org_fictional_bistro_01",
                        "lines": [],
                    },
                )
                row_after = await _read_order_row(
                    database_url=settings.supplier_mcp_database_url, idempotency_key=key
                )
                assert row_after.recovered_without_request_context is False
                assert row_after.agreement_id == "txn_backfill_01"
                assert row_after.content_hash == "sha256:backfilled"
                assert row_after.buyer_organization_id == "org_fictional_bistro_01"
        finally:
            _reset_to_mock_adapter()


@pytest.mark.asyncio
async def test_conflicting_retry_arriving_after_a_committed_backfill_never_overwrites_it() -> None:
    """Acceptance criterion: a conflicting retry (sequential case). Once
    one retry's backfill has already committed, a second, later retry
    presenting different agreement_id/content_hash for the same key
    must never overwrite it -- ordinary idempotency, not the race case
    below. (This alone does not exercise the UPDATE's own
    recovered_without_request_context guard, since by the time the
    second call runs, the flag is already False and the guard is never
    reached -- see test_conflicting_retry_racing_for_the_same_backfill
    for that.) The business system never sees a second submission
    either way."""
    async with _running_sandbox_erp() as (erp_base_url, erp_token):
        _configure_sandbox_env(erp_base_url=erp_base_url, erp_token=erp_token)
        try:
            app, settings = await _fresh_supplier_mcp_app()
            port = find_free_port()
            with running_server(app, port=port):
                key = "idem_sandbox_conflicting_retry"
                execution_token = issue_service_credential(
                    organization_id=ORG,
                    scope="execute_order",
                    signing_secret=settings.service_credential_signing_secret,
                )
                recovered = await _arm_timeout_and_recover(
                    erp_base_url=erp_base_url, port=port, execution_token=execution_token, key=key
                )

                first = await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=execution_token,
                    tool_name="execute_order",
                    arguments={
                        "idempotency_key": key,
                        "agreement_id": "txn_first",
                        "content_hash": "sha256:first",
                        "buyer_organization_id": "org_fictional_bistro_01",
                        "lines": [],
                    },
                )
                second = await call_tool(
                    server_url=f"http://127.0.0.1:{port}/mcp",
                    token=execution_token,
                    tool_name="execute_order",
                    arguments={
                        "idempotency_key": key,
                        "agreement_id": "txn_second_conflicting",
                        "content_hash": "sha256:second_conflicting",
                        "buyer_organization_id": "org_fictional_different_buyer_99",
                        "lines": [],
                    },
                )
                assert first["duplicate"] is True
                assert second["duplicate"] is True
                assert first["external_order_id"] == recovered["external_order_id"]
                assert second["external_order_id"] == recovered["external_order_id"]

                row = await _read_order_row(
                    database_url=settings.supplier_mcp_database_url, idempotency_key=key
                )
                # The first retry's identity won; the second's never
                # got written anywhere -- no merge, no overwrite.
                assert row.recovered_without_request_context is False
                assert row.agreement_id == "txn_first"
                assert row.content_hash == "sha256:first"
                assert row.buyer_organization_id == "org_fictional_bistro_01"

                # No second ERP submission either way.
                erp_record = await _get_order_directly_from_erp(
                    erp_base_url=erp_base_url, erp_token=erp_token, idempotency_key=key
                )
                assert erp_record["found"] is True
                assert erp_record["external_order_id"] == recovered["external_order_id"]
        finally:
            _reset_to_mock_adapter()


@pytest.mark.asyncio
async def test_conflicting_retry_racing_for_the_same_backfill() -> None:
    """Acceptance criterion: a conflicting retry (the actual race). Two
    differently-worded execute_order retries for the same recovered,
    unlinked key, launched genuinely concurrently via ``asyncio.gather``
    rather than one after the other, must still only ever let exactly
    one of them actually backfill -- this is the scenario that
    exercises the UPDATE's ``recovered_without_request_context.is_(True)``
    WHERE-clause guard (not just the outer Python ``if``), because both
    calls can observe the flag as still True before either commits.

    The decisive check is the count of audit entries that logged
    "(backfilled transaction identity)" (see
    ``_count_backfill_audit_hits``'s own docstring for why): the final
    row's content alone cannot tell a guarded, first-commit-wins UPDATE
    apart from an unguarded one, because either way a single atomic SQL
    UPDATE statement always writes one internally-consistent set of
    values, never a column-level mixture -- removing the guard would
    simply let the row end up as *whichever retry's UPDATE statement
    happens to run last*, still "one call's values in full." Without
    the guard, idempotency_key alone matches the row unconditionally,
    so *every* concurrent retry's UPDATE would match and count as a
    backfill, not just one; this is what actually distinguishes the two
    (confirmed directly: an independent review of an earlier version of
    this test found it passed identically even with the guard entirely
    removed, for exactly this reason)."""
    async with _running_sandbox_erp() as (erp_base_url, erp_token):
        _configure_sandbox_env(erp_base_url=erp_base_url, erp_token=erp_token)
        try:
            app, settings = await _fresh_supplier_mcp_app()
            port = find_free_port()
            with running_server(app, port=port):
                key = "idem_sandbox_racing_conflicting_retry"
                execution_token = issue_service_credential(
                    organization_id=ORG,
                    scope="execute_order",
                    signing_secret=settings.service_credential_signing_secret,
                )
                recovered = await _arm_timeout_and_recover(
                    erp_base_url=erp_base_url, port=port, execution_token=execution_token, key=key
                )

                async def _retry(
                    *, agreement_id: str, content_hash: str, buyer_organization_id: str
                ):
                    return await call_tool(
                        server_url=f"http://127.0.0.1:{port}/mcp",
                        token=execution_token,
                        tool_name="execute_order",
                        arguments={
                            "idempotency_key": key,
                            "agreement_id": agreement_id,
                            "content_hash": content_hash,
                            "buyer_organization_id": buyer_organization_id,
                            "lines": [],
                        },
                    )

                racer_a, racer_b = await asyncio.gather(
                    _retry(
                        agreement_id="txn_racer_a",
                        content_hash="sha256:racer_a",
                        buyer_organization_id="org_fictional_bistro_01",
                    ),
                    _retry(
                        agreement_id="txn_racer_b",
                        content_hash="sha256:racer_b",
                        buyer_organization_id="org_fictional_different_buyer_99",
                    ),
                )
                assert racer_a["duplicate"] is True
                assert racer_b["duplicate"] is True
                assert racer_a["external_order_id"] == recovered["external_order_id"]
                assert racer_b["external_order_id"] == recovered["external_order_id"]

                row = await _read_order_row(
                    database_url=settings.supplier_mcp_database_url, idempotency_key=key
                )
                assert row.recovered_without_request_context is False
                # Exactly one racer's values won -- in full, never a
                # mixture of both (which a non-atomic check-then-write
                # could produce: e.g. racer_a's agreement_id alongside
                # racer_b's content_hash).
                won_a = (row.agreement_id, row.content_hash, row.buyer_organization_id) == (
                    "txn_racer_a",
                    "sha256:racer_a",
                    "org_fictional_bistro_01",
                )
                won_b = (row.agreement_id, row.content_hash, row.buyer_organization_id) == (
                    "txn_racer_b",
                    "sha256:racer_b",
                    "org_fictional_different_buyer_99",
                )
                assert won_a or won_b, (
                    f"row has a mixture of both racers' values, not either one cleanly: "
                    f"{row.agreement_id!r}, {row.content_hash!r}, {row.buyer_organization_id!r}"
                )

                # The decisive check: exactly one of the two concurrent
                # retries' UPDATEs actually matched/wrote a row. Without
                # the recovered_without_request_context.is_(True) guard,
                # BOTH would match unconditionally on idempotency_key
                # alone and both would log a successful backfill here.
                backfill_hits = await _count_backfill_audit_hits(
                    database_url=settings.supplier_mcp_database_url, idempotency_key=key
                )
                assert backfill_hits == 1, (
                    f"expected exactly one of the two concurrent retries to actually "
                    f"backfill (the atomic guard enforces first-commit-wins); got "
                    f"{backfill_hits} -- the guard did not limit this to a single writer."
                )

                erp_record = await _get_order_directly_from_erp(
                    erp_base_url=erp_base_url, erp_token=erp_token, idempotency_key=key
                )
                assert erp_record["found"] is True
                assert erp_record["external_order_id"] == recovered["external_order_id"]
        finally:
            _reset_to_mock_adapter()
