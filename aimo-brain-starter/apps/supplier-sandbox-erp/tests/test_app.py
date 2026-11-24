from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from supplier_sandbox_erp.app import create_app
from supplier_sandbox_erp.settings import get_settings

from database import Base, new_engine
from identity import issue_service_credential

#: Real-socket server harness, duplicated from
#: ``tests/integration/a2a_server_harness.py`` per this repo's existing
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


ORG = "org_fictional_wholesale_01"
MILK_REF = "sandbox_sku_milk_lactose_free"
BUTTER_REF = "sandbox_sku_butter_unsalted"


async def _fresh_app():
    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.supplier_sandbox_erp_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return create_app(), settings


def _auth_headers(settings) -> dict[str, str]:
    token = issue_service_credential(
        organization_id=ORG,
        scope="use_sandbox_erp",
        signing_secret=settings.service_credential_signing_secret,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_search_catalogue_finds_seeded_product() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.get(
                "/catalogue/search",
                params={"query": "lactose-free milk"},
                headers=_auth_headers(settings),
            )
    assert response.status_code == 200
    assert any(p["product_ref"] == MILK_REF for p in response.json())


@pytest.mark.asyncio
async def test_missing_credential_rejected() -> None:
    app, _settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.get("/catalogue/search", params={"query": "milk"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_wrong_scope_credential_rejected() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    token = issue_service_credential(
        organization_id=ORG,
        scope="use_supplier_tools",
        signing_secret=settings.service_credential_signing_secret,
    )
    with running_server(app, port=port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.get(
                "/catalogue/search",
                params={"query": "milk"},
                headers={"Authorization": f"Bearer {token}"},
            )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_customer_price_falls_back_to_base_price_for_unknown_customer() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.get(
                f"/customers/org_unknown_buyer/products/{MILK_REF}/price",
                headers=_auth_headers(settings),
            )
    body = response.json()
    assert body["found"] is True
    assert body["unit_price"] == "1.25"


@pytest.mark.asyncio
async def test_customer_price_uses_customer_specific_price() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.get(
                f"/customers/org_fictional_bistro_01/products/{MILK_REF}/price",
                headers=_auth_headers(settings),
            )
    assert response.json()["unit_price"] == "1.19"


@pytest.mark.asyncio
async def test_submit_order_is_idempotent() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    key = f"idem_{uuid.uuid4().hex}"
    body = {
        "idempotency_key": key,
        "buyer_organization_id": "org_fictional_bistro_01",
        "lines": [{"product_ref": MILK_REF, "quantity": "24", "unit": "L", "unit_price": "1.19"}],
    }
    with running_server(app, port=port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            first = (
                await client.post("/orders", json=body, headers=_auth_headers(settings))
            ).json()
            second = (
                await client.post("/orders", json=body, headers=_auth_headers(settings))
            ).json()
    assert first["duplicate"] is False
    assert first["status"] == "confirmed"
    assert second["duplicate"] is True
    assert second["external_order_id"] == first["external_order_id"]


@pytest.mark.asyncio
async def test_submitted_order_decrements_stock() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            before = (
                await client.get(
                    f"/products/{MILK_REF}/availability",
                    params={"quantity": "24"},
                    headers=_auth_headers(settings),
                )
            ).json()
            await client.post(
                "/orders",
                json={
                    "idempotency_key": f"idem_{uuid.uuid4().hex}",
                    "buyer_organization_id": "org_fictional_bistro_01",
                    "lines": [
                        {
                            "product_ref": MILK_REF,
                            "quantity": "24",
                            "unit": "L",
                            "unit_price": "1.19",
                        }
                    ],
                },
                headers=_auth_headers(settings),
            )
            after = (
                await client.get(
                    f"/products/{MILK_REF}/availability",
                    params={"quantity": "24"},
                    headers=_auth_headers(settings),
                )
            ).json()
    assert float(after["quantity_available"]) == float(before["quantity_available"]) - 24


@pytest.mark.asyncio
async def test_delivery_cutoff_flagged_for_butter() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.get(
                f"/products/{BUTTER_REF}/delivery-options",
                params={"quantity": "10"},
                headers=_auth_headers(settings),
            )
    options = response.json()
    assert options[0]["restrictions"] != []


@pytest.mark.asyncio
async def test_substitution_offered_below_moq_with_configured_substitute() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.get(
                f"/products/{MILK_REF}/availability",
                params={"quantity": "1"},
                headers=_auth_headers(settings),
            )
    body = response.json()
    assert body["substituted_for_product_ref"] == "sandbox_sku_oat_milk"


@pytest.mark.asyncio
async def test_fault_injection_transient_error() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    with running_server(app, port=port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await client.post("/admin/fault-injection/arm", json={"scenario": "transient_error"})
            response = await client.get(
                "/catalogue/search", params={"query": "milk"}, headers=_auth_headers(settings)
            )
            assert response.status_code == 503
            # one-shot: the next call succeeds.
            response = await client.get(
                "/catalogue/search", params={"query": "milk"}, headers=_auth_headers(settings)
            )
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_fault_injection_delayed_ack_then_resolve() -> None:
    app, settings = await _fresh_app()
    port = find_free_port()
    key = f"idem_{uuid.uuid4().hex}"
    body = {
        "idempotency_key": key,
        "buyer_organization_id": "org_fictional_bistro_01",
        "lines": [{"product_ref": MILK_REF, "quantity": "24", "unit": "L", "unit_price": "1.19"}],
    }
    with running_server(app, port=port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await client.post(
                "/admin/fault-injection/arm",
                json={"scenario": "delayed_ack", "idempotency_key": key},
            )
            submitted = (
                await client.post("/orders", json=body, headers=_auth_headers(settings))
            ).json()
            assert submitted["status"] == "pending"
            assert submitted["external_order_id"] is None

            still_pending = (
                await client.get(f"/orders/{key}", headers=_auth_headers(settings))
            ).json()
            assert still_pending["status"] == "pending"

            await client.post(f"/admin/fault-injection/resolve/{key}")

            resolved = (await client.get(f"/orders/{key}", headers=_auth_headers(settings))).json()
            assert resolved["status"] == "confirmed"
            assert resolved["external_order_id"] is not None
