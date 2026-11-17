"""M1 acceptance tests: the real buyer<->supplier A2A exchange.

Every test here talks over genuine HTTP to genuine, independently
started uvicorn servers (see ``a2a_server_harness``) — never a direct
Python function call between ``buyer_agent`` and ``supplier_agent`` code,
and never ``ASGITransport``. Each test uses its own fresh per-service
SQLite file and its own free port, so tests never share state with each
other or with other test modules.
"""

from __future__ import annotations

import os
import tempfile
import uuid

import httpx
import pytest
from a2a.client import A2ACardResolver
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import new_engine

from .a2a_server_harness import find_free_port, running_server


def _fresh_sqlite_url(label: str) -> str:
    path = os.path.join(tempfile.gettempdir(), f"aimo_m1_{label}_{uuid.uuid4().hex}.sqlite3")
    return f"sqlite+aiosqlite:///{path}"


async def _create_tables(engine, model) -> None:
    from database import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[model.__table__])


def _build_supplier_app(*, a2a_url: str, database_url: str):
    import supplier_agent.app as supplier_app_module
    import supplier_agent.settings as supplier_settings_module
    from supplier_agent.models import SupplierTaskRecord

    os.environ["SUPPLIER_A2A_URL"] = a2a_url
    os.environ["SUPPLIER_DATABASE_URL"] = database_url
    supplier_settings_module.get_settings.cache_clear()

    app = supplier_app_module.create_app()
    return app, SupplierTaskRecord


def _build_buyer_app(*, a2a_url: str, database_url: str, supplier_a2a_url: str):
    import buyer_agent.app as buyer_app_module
    import buyer_agent.settings as buyer_settings_module
    from buyer_agent.models import BuyerTaskRecord

    os.environ["BUYER_A2A_URL"] = a2a_url
    os.environ["BUYER_DATABASE_URL"] = database_url
    os.environ["SUPPLIER_A2A_URL"] = supplier_a2a_url
    buyer_settings_module.get_settings.cache_clear()

    app = buyer_app_module.create_app()
    return app, BuyerTaskRecord


@pytest.fixture
async def supplier_server():
    port = find_free_port()
    a2a_url = f"http://127.0.0.1:{port}"
    database_url = _fresh_sqlite_url("supplier")
    app, model = _build_supplier_app(a2a_url=a2a_url, database_url=database_url)

    engine = new_engine(database_url)
    await _create_tables(engine, model)
    await engine.dispose()

    with running_server(app, port=port) as server:
        yield server, database_url


@pytest.fixture
async def buyer_server(supplier_server):
    supplier, _supplier_db_url = supplier_server
    port = find_free_port()
    a2a_url = f"http://127.0.0.1:{port}"
    database_url = _fresh_sqlite_url("buyer")
    app, model = _build_buyer_app(
        a2a_url=a2a_url, database_url=database_url, supplier_a2a_url=supplier.base_url
    )

    engine = new_engine(database_url)
    await _create_tables(engine, model)
    await engine.dispose()

    with running_server(app, port=port) as server:
        yield server, database_url, supplier


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_card_discovery_for_both_agents(buyer_server) -> None:
    buyer, _buyer_db_url, supplier = buyer_server

    async with httpx.AsyncClient() as client:
        resolver = A2ACardResolver(httpx_client=client, base_url=supplier.base_url)
        supplier_card = await resolver.get_agent_card()
        resolver2 = A2ACardResolver(httpx_client=client, base_url=buyer.base_url)
        buyer_card = await resolver2.get_agent_card()

    assert supplier_card.name == "AIMO BRAIN Fictional Supplier Agent"
    assert any(s.id == "food.check_product_availability" for s in supplier_card.skills)
    assert buyer_card.name == "AIMO BRAIN Fictional Buyer Agent"
    assert any(s.id == "food.buyer_agent_status" for s in buyer_card.skills)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_product_availability_exchange(buyer_server) -> None:

    from buyer_agent.client import request_product_availability
    from buyer_agent.settings import get_settings

    buyer, buyer_db_url, _supplier = buyer_server
    settings = get_settings()
    engine = new_engine(buyer_db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    response = await request_product_availability(
        settings=settings,
        session_factory=session_factory,
        lines=[{"product_query": "lactose-free milk", "quantity": "100", "unit": "L"}],
    )

    assert response["type"] == "product_availability_response"
    assert response["results"][0]["matched"] is True
    assert response["results"][0]["lines"][0]["product_ref"] == "sku_fictional_milk_lactose_free"

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_message_handling_yields_failed_task(buyer_server) -> None:
    """Sending something that is not a valid data payload must fail the
    task cleanly, not crash the supplier or silently succeed."""
    from a2a.client import ClientConfig, create_client
    from a2a.helpers import new_text_message
    from a2a.types import Role, SendMessageRequest, TaskState

    buyer, _buyer_db_url, supplier = buyer_server

    async with httpx.AsyncClient() as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=supplier.base_url)
        card = await resolver.get_agent_card()

        async with httpx.AsyncClient() as client_httpx:
            client = await create_client(
                agent=card, client_config=ClientConfig(streaming=False, httpx_client=client_httpx)
            )
            message = new_text_message("not a structured availability request", role=Role.ROLE_USER)
            final_task = None
            async for chunk in client.send_message(SendMessageRequest(message=message)):
                if chunk.HasField("task"):
                    final_task = chunk.task

    assert final_task is not None
    assert final_task.status.state == TaskState.TASK_STATE_FAILED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_unavailable_raises_clear_error() -> None:
    """Pointing the buyer at a port nothing is listening on must raise a
    clear, typed error — not hang, and not silently return nothing."""
    from buyer_agent.client import AgentUnavailableError, discover_supplier_agent_card

    dead_port = find_free_port()
    with pytest.raises(AgentUnavailableError):
        await discover_supplier_agent_card(f"http://127.0.0.1:{dead_port}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_requests_each_succeed_independently(buyer_server) -> None:
    """Two independent messages with identical content must each be
    processed as their own task, without crashing or corrupting state.
    Business-level idempotency (same intent deduplicated) is explicit
    M5 scope — this only proves no crash/corruption at the A2A layer."""

    from buyer_agent.client import request_product_availability
    from buyer_agent.settings import get_settings

    buyer, buyer_db_url, _supplier = buyer_server
    settings = get_settings()
    engine = new_engine(buyer_db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    lines = [{"product_query": "lactose-free milk", "quantity": "100", "unit": "L"}]
    first = await request_product_availability(
        settings=settings, session_factory=session_factory, lines=lines
    )
    second = await request_product_availability(
        settings=settings, session_factory=session_factory, lines=lines
    )

    assert first["results"][0]["matched"] is True
    assert second["results"][0]["matched"] is True

    await engine.dispose()
