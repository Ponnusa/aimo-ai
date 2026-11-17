"""M3 required security test cases (docs/SECURITY.md): unauthorized tool
calls, cross-tenant access, invalid tool parameters, expired credentials,
MCP server unavailability/timeouts, and malicious/malformed tool
responses.

Every test here talks to a real MCP server over a real localhost socket
(never ``ASGITransport``), consistent with every other cross-process
boundary test in this repository.
"""

from __future__ import annotations

import asyncio
import os
import socket
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from buyer_mcp.app import create_app as create_buyer_mcp_app
from buyer_mcp.settings import get_settings as get_buyer_mcp_settings
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp_integration import (
    McpInvalidArgumentsError,
    McpMalformedResponseError,
    McpServerUnavailableError,
    McpUnauthorizedError,
    call_tool,
)
from supplier_agent.mcp_client import fetch_availability_matches
from supplier_agent.settings import SupplierAgentSettings
from supplier_mcp.app import create_app as create_supplier_mcp_app
from supplier_mcp.settings import get_settings as get_supplier_mcp_settings

from database import Base, new_engine
from identity import issue_service_credential

_SIGNING_SECRET = "security-test-signing-secret"
BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"


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
            if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                return _RunningServer(base_url=base_url, _server=server, _thread=thread)
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    server.should_exit = True
    thread.join(timeout=5)
    raise RuntimeError(f"Server on {base_url} did not become healthy in time.")


@contextmanager
def _running_server(app: FastAPI, *, port: int):
    handle = _start_server(app, port=port)
    try:
        yield handle
    finally:
        handle.stop()


def _fresh_sqlite_url(label: str) -> str:
    path = os.path.join(tempfile.gettempdir(), f"aimo_m3_sec_{label}_{uuid.uuid4().hex}.sqlite3")
    return f"sqlite+aiosqlite:///{path}"


async def _fresh_supplier_mcp_app():
    os.environ["SUPPLIER_MCP_DATABASE_URL"] = _fresh_sqlite_url("supplier_mcp")
    os.environ["SUPPLIER_MCP_ORGANIZATION_ID"] = SUPPLIER_ORG
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = _SIGNING_SECRET
    get_supplier_mcp_settings.cache_clear()
    settings = get_supplier_mcp_settings()
    engine = new_engine(settings.supplier_mcp_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return create_supplier_mcp_app()


async def _fresh_buyer_mcp_app():
    os.environ["BUYER_MCP_DATABASE_URL"] = _fresh_sqlite_url("buyer_mcp")
    os.environ["BUYER_MCP_ORGANIZATION_ID"] = BUYER_ORG
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = _SIGNING_SECRET
    get_buyer_mcp_settings.cache_clear()
    settings = get_buyer_mcp_settings()
    engine = new_engine(settings.buyer_mcp_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return create_buyer_mcp_app()


def _token(organization_id: str, scope: str, **kwargs) -> str:
    return issue_service_credential(
        organization_id=organization_id, scope=scope, signing_secret=_SIGNING_SECRET, **kwargs
    )


@pytest.mark.security
@pytest.mark.asyncio
async def test_unauthorized_tool_call_is_rejected() -> None:
    """SECURITY-2: agent cannot use a missing/invalid credential."""
    app = await _fresh_supplier_mcp_app()
    port = _find_free_port()
    with _running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token="garbage",
            tool_name="search_catalogue",
            arguments={"query": "milk"},
        )


@pytest.mark.security
@pytest.mark.asyncio
async def test_cross_tenant_tool_access_is_rejected() -> None:
    """SECURITY-1: buyer cannot call supplier's private tools, even with
    a validly-signed credential for a different organization/scope."""
    app = await _fresh_supplier_mcp_app()
    port = _find_free_port()
    with _running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(BUYER_ORG, "use_buyer_tools"),
            tool_name="search_catalogue",
            arguments={"query": "milk"},
        )


@pytest.mark.security
@pytest.mark.asyncio
async def test_expired_credential_cannot_call_tools() -> None:
    """SECURITY-2: agent cannot use an expired credential."""
    app = await _fresh_supplier_mcp_app()
    port = _find_free_port()
    with _running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(SUPPLIER_ORG, "use_supplier_tools", ttl_seconds=10, now=1_000_000),
            tool_name="search_catalogue",
            arguments={"query": "milk"},
        )


@pytest.mark.security
@pytest.mark.asyncio
async def test_invalid_tool_parameters_are_rejected() -> None:
    """Malformed arguments (a non-decimal amount) must fail clearly, not
    silently coerce or crash the server."""
    app = await _fresh_buyer_mcp_app()
    port = _find_free_port()
    with _running_server(app, port=port), pytest.raises(McpInvalidArgumentsError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(BUYER_ORG, "use_buyer_tools"),
            tool_name="request_purchase_approval",
            arguments={"organization_id": BUYER_ORG, "amount": "not-a-number", "currency": "EUR"},
        )


@pytest.mark.security
@pytest.mark.asyncio
async def test_mcp_server_unavailable_raises_clear_bounded_error() -> None:
    """SECURITY-7-adjacent: an unreachable MCP server must raise a clear,
    typed error within bounded retries — never hang indefinitely."""
    dead_port = _find_free_port()
    with pytest.raises(McpServerUnavailableError):
        await call_tool(
            server_url=f"http://127.0.0.1:{dead_port}/mcp",
            token="irrelevant",
            tool_name="search_catalogue",
            arguments={"query": "milk"},
            request_timeout_seconds=1.0,
            max_attempts=2,
            retry_backoff_seconds=0.05,
        )


@pytest.mark.security
@pytest.mark.asyncio
async def test_malicious_tool_response_is_rejected_not_silently_trusted() -> None:
    """A compromised/malicious supplier-mcp that returns a catalogue match
    missing required fields must not crash supplier_agent opaquely or be
    silently trusted — it must surface as a clear, typed error."""
    from contextlib import asynccontextmanager

    rogue = FastMCP(name="rogue-supplier-mcp", streamable_http_path="/")

    @rogue.tool()
    def search_catalogue(query: str) -> dict[str, list[dict[str, str]]]:
        # Deliberately missing "product_ref", "unit", "stock_today", etc.
        return {"result": [{"name": "Suspicious Product"}]}

    rogue_mcp_asgi_app = rogue.streamable_http_app()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        async with rogue.session_manager.run():
            yield

    rogue_app = FastAPI(lifespan=_lifespan)
    rogue_app.mount("/mcp", rogue_mcp_asgi_app)

    @rogue_app.get("/health")
    async def _health() -> dict:
        return {"status": "ok"}

    port = _find_free_port()
    with _running_server(rogue_app, port=port):
        settings = SupplierAgentSettings(
            supplier_mcp_url=f"http://127.0.0.1:{port}/mcp",
            supplier_mcp_service_credential="irrelevant-rogue-server-does-not-check-auth",
        )
        with pytest.raises(McpMalformedResponseError):
            await fetch_availability_matches(
                settings=settings,
                lines=[{"product_query": "milk", "quantity": "10", "unit": "L"}],
                buyer_organization_id=BUYER_ORG,
            )
