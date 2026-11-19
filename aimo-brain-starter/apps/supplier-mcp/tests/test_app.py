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
from fastapi import FastAPI
from mcp_integration import McpUnauthorizedError, call_tool
from supplier_mcp.app import create_app
from supplier_mcp.settings import get_settings

from database import Base, new_engine
from identity import issue_service_credential

#: Real-socket server harness, duplicated from
#: ``tests/integration/a2a_server_harness.py`` — see the matching
#: comment in ``apps/buyer-mcp/tests/test_app.py``.


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


ORG = "org_fictional_wholesale_01"
OTHER_ORG = "org_fictional_other_01"
MILK_REF = "sku_fictional_milk_lactose_free"


async def _fresh_app():
    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.supplier_mcp_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return create_app(), settings


def _token(
    organization_id: str, *, signing_secret: str, scope: str = "use_supplier_tools", **kwargs
) -> str:
    return issue_service_credential(
        organization_id=organization_id,
        scope=scope,
        signing_secret=signing_secret,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_search_catalogue_finds_fictional_milk() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        result = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(ORG, signing_secret=settings.service_credential_signing_secret),
            tool_name="search_catalogue",
            arguments={"query": "lactose-free milk"},
        )
    assert len(result["result"]) == 1
    assert result["result"][0]["product_ref"] == MILK_REF


@pytest.mark.asyncio
async def test_check_inventory_known_product() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        result = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(ORG, signing_secret=settings.service_credential_signing_secret),
            tool_name="check_inventory",
            arguments={"product_ref": MILK_REF},
        )
    assert result["found"] is True
    assert result["stock_today"] == "80"


@pytest.mark.asyncio
async def test_check_inventory_unknown_product() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        result = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(ORG, signing_secret=settings.service_credential_signing_secret),
            tool_name="check_inventory",
            arguments={"product_ref": "sku_does_not_exist"},
        )
    assert result["found"] is False


@pytest.mark.asyncio
async def test_get_customer_price_applies_discount() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        result = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(ORG, signing_secret=settings.service_credential_signing_secret),
            tool_name="get_customer_price",
            arguments={
                "product_ref": MILK_REF,
                "buyer_organization_id": "org_fictional_bistro_01",
            },
        )
    assert result["found"] is True
    assert result["unit_price"] == "1.19"


@pytest.mark.asyncio
async def test_get_delivery_options_known_product() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        result = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(ORG, signing_secret=settings.service_credential_signing_secret),
            tool_name="get_delivery_options",
            arguments={"product_ref": MILK_REF, "quantity": "10"},
        )
    assert len(result["result"]) == 2


@pytest.mark.asyncio
async def test_missing_credential_is_rejected() -> None:
    app, _settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token="not-a-real-token",
            tool_name="search_catalogue",
            arguments={"query": "milk"},
        )


@pytest.mark.asyncio
async def test_buyer_scoped_credential_cannot_call_supplier_tools() -> None:
    """Cross-tenant/cross-role rejection: a credential minted for the
    buyer org's own tools must not work here, even if replayed at the
    right organization id — the scope itself differs."""
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(
                ORG,
                signing_secret=settings.service_credential_signing_secret,
                scope="use_buyer_tools",
            ),
            tool_name="search_catalogue",
            arguments={"query": "milk"},
        )


@pytest.mark.asyncio
async def test_wrong_organization_credential_is_rejected() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(OTHER_ORG, signing_secret=settings.service_credential_signing_secret),
            tool_name="search_catalogue",
            arguments={"query": "milk"},
        )


@pytest.mark.asyncio
async def test_execute_order_creates_an_order_and_status_lookup_finds_it() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        execution_token = _token(
            ORG, signing_secret=settings.service_credential_signing_secret, scope="execute_order"
        )
        result = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=execution_token,
            tool_name="execute_order",
            arguments={
                "idempotency_key": "idem_order_01",
                "agreement_id": "txn_01",
                "content_hash": "sha256:abc",
                "buyer_organization_id": "org_fictional_bistro_01",
            },
        )
        assert result["status"] == "confirmed"
        assert result["duplicate"] is False
        assert result["external_order_id"].startswith("mock_order_")

        status = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=execution_token,
            tool_name="get_order_status",
            arguments={"idempotency_key": "idem_order_01"},
        )
        assert status["found"] is True
        assert status["external_order_id"] == result["external_order_id"]


@pytest.mark.asyncio
async def test_execute_order_is_idempotent_for_the_same_key() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        execution_token = _token(
            ORG, signing_secret=settings.service_credential_signing_secret, scope="execute_order"
        )
        first = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=execution_token,
            tool_name="execute_order",
            arguments={
                "idempotency_key": "idem_order_dup",
                "agreement_id": "txn_01",
                "content_hash": "sha256:abc",
                "buyer_organization_id": "org_fictional_bistro_01",
            },
        )
        second = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=execution_token,
            tool_name="execute_order",
            arguments={
                "idempotency_key": "idem_order_dup",
                "agreement_id": "txn_01",
                "content_hash": "sha256:abc",
                "buyer_organization_id": "org_fictional_bistro_01",
            },
        )
    assert first["external_order_id"] == second["external_order_id"]
    assert first["duplicate"] is False
    assert second["duplicate"] is True


@pytest.mark.asyncio
async def test_get_order_status_unknown_key_not_found() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        result = await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(
                ORG,
                signing_secret=settings.service_credential_signing_secret,
                scope="execute_order",
            ),
            tool_name="get_order_status",
            arguments={"idempotency_key": "idem_never_seen"},
        )
    assert result["found"] is False


@pytest.mark.asyncio
async def test_read_only_credential_cannot_call_execute_order() -> None:
    """Narrow-scope enforcement (M5): a credential that only grants
    ``use_supplier_tools`` must not be able to invoke execute_order."""
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(ORG, signing_secret=settings.service_credential_signing_secret),
            tool_name="execute_order",
            arguments={
                "idempotency_key": "idem_should_fail",
                "agreement_id": "txn_01",
                "content_hash": "sha256:abc",
                "buyer_organization_id": "org_fictional_bistro_01",
            },
        )


@pytest.mark.asyncio
async def test_execution_credential_cannot_call_read_only_tools() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port), pytest.raises(McpUnauthorizedError):
        await call_tool(
            server_url=f"http://127.0.0.1:{port}/mcp",
            token=_token(
                ORG,
                signing_secret=settings.service_credential_signing_secret,
                scope="execute_order",
            ),
            tool_name="search_catalogue",
            arguments={"query": "milk"},
        )
