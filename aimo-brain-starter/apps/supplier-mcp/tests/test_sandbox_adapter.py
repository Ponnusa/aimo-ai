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
from supplier_mcp.app import create_app
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
