"""supplier-agent FastAPI application.

M1 scope: a real, independent A2A server (official SDK, mounted on this
same FastAPI app), with its own Agent Card, its own database-backed task
store, and its own liveness/readiness endpoints. There is still no
inter-agent import and no direct function call into buyer-agent anywhere
in this package — only the A2A wire protocol crosses the boundary.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import DatabaseTaskStore
from agent_runtime import get_llm_adapter
from fastapi import FastAPI, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import new_engine
from supplier_agent.a2a_card import build_agent_card
from supplier_agent.a2a_executor import SupplierAgentExecutor
from supplier_agent.settings import get_settings


class HealthOut(BaseModel):
    status: str
    service: str
    llm_provider: str


class ReadyOut(BaseModel):
    status: str
    service: str
    database: str


def create_app() -> FastAPI:
    settings = get_settings()
    engine = new_engine(settings.supplier_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = get_llm_adapter(settings.llm_provider)

    agent_card = build_agent_card(settings)
    # table_name left at its default ("tasks"): the A2A SDK's built-in
    # TaskModel class is reused as-is rather than dynamically declaring a
    # new mapped class per call (create_task_model), which is required
    # since create_app() may run more than once per process (tests boot
    # fresh apps repeatedly) and SQLAlchemy forbids redefining a table
    # against the same MetaData. "tasks" never collides with this
    # service's own "supplier_tasks" business task-log table.
    task_store = DatabaseTaskStore(engine)
    executor = SupplierAgentExecutor(settings=settings, session_factory=session_factory)
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=agent_card,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await task_store.initialize()
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="AIMO BRAIN Supplier Agent", version="0.1.0", lifespan=lifespan)

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card),
        # Mounted at the root path to match the Agent Card's advertised
        # interface URL (settings.supplier_a2a_url, the base URL itself).
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url="/"),
    )

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        return HealthOut(status="ok", service="supplier-agent", llm_provider=llm.model_name)

    @app.get("/ready", response_model=ReadyOut)
    async def ready() -> ReadyOut:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return ReadyOut(status="ok", service="supplier-agent", database="reachable")

    # --- Registry endpoint-ownership verification (M2) -----------------
    # registry-api issues this agent's operator a one-time challenge
    # value; the operator (here, this same process, acting as its own
    # operator) must expose it at this well-known path before registry-api
    # will mark the agent "active". This is NOT agent-to-agent
    # communication — it is the agent's own operator configuring their own
    # server, the same way domain-validated TLS certificates work.
    app.state.ownership_challenge = None

    @app.put("/internal/ownership-challenge")
    async def set_ownership_challenge(body: dict[str, str]) -> dict[str, str]:
        app.state.ownership_challenge = body["challenge"]
        return {"status": "ok"}

    @app.get("/.well-known/aimo-brain-challenge")
    async def serve_ownership_challenge() -> Response:
        if app.state.ownership_challenge is None:
            return Response(status_code=404)
        return Response(content=app.state.ownership_challenge, media_type="text/plain")

    return app


app = create_app()
