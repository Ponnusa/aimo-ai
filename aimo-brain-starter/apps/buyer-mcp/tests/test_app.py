from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from buyer_mcp.app import create_app
from buyer_mcp.settings import get_settings
from fastapi import FastAPI
from mcp_integration import McpUnauthorizedError, call_tool

from database import Base, new_engine
from identity import issue_service_credential

#: Real-socket server harness, duplicated from
#: ``tests/integration/a2a_server_harness.py`` rather than imported
#: across the top-level ``tests`` package (no stable import path from
#: this per-service test directory without an ``__init__.py`` chain).
#: MCP Streamable HTTP is a real streaming protocol — these tests
#: exercise it over a genuine localhost socket, matching the A2A tests'
#: own rule against simulating a real-protocol exchange.


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


ORG = "org_fictional_bistro_01"
OTHER_ORG = "org_fictional_other_01"


async def _fresh_app():
    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.buyer_mcp_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return create_app(), settings


def _token(organization_id: str, *, scope: str = "use_buyer_tools", **kwargs) -> str:
    return issue_service_credential(
        organization_id=organization_id,
        scope=scope,
        signing_secret="test-signing-secret",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_get_purchase_requirements_with_valid_credential() -> None:
    app, _settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        result = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(ORG),
            tool_name="get_purchase_requirements",
        )
    assert result is not None
    assert len(result["result"]) == 1


@pytest.mark.asyncio
async def test_get_budget_returns_fictional_budget() -> None:
    app, _settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        result = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(ORG),
            tool_name="get_budget",
            arguments={"organization_id": ORG},
        )
    assert result["currency"] == "EUR"
    assert result["remaining_amount"] == "500.00"


@pytest.mark.asyncio
async def test_request_purchase_approval_within_budget() -> None:
    app, _settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        result = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(ORG),
            tool_name="request_purchase_approval",
            arguments={"organization_id": ORG, "amount": "100.00", "currency": "EUR"},
        )
    assert result["decision"] == "approved"


@pytest.mark.asyncio
async def test_missing_credential_is_rejected() -> None:
    app, _settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token="not-a-real-token",
            tool_name="get_purchase_requirements",
        )


@pytest.mark.asyncio
async def test_wrong_organization_credential_is_rejected() -> None:
    app, _settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(OTHER_ORG),
            tool_name="get_purchase_requirements",
        )


@pytest.mark.asyncio
async def test_wrong_scope_credential_is_rejected() -> None:
    app, _settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(ORG, scope="use_supplier_tools"),
            tool_name="get_purchase_requirements",
        )


@pytest.mark.asyncio
async def test_expired_credential_is_rejected() -> None:
    app, _settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(ORG, ttl_seconds=10, now=1_000_000),
            tool_name="get_purchase_requirements",
        )
