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
from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import new_engine
from identity import ServiceCredentialError, verify_service_credential
from supplier_agent.a2a_card import build_agent_card
from supplier_agent.a2a_executor import SupplierAgentExecutor
from supplier_agent.management_api import MANAGE_ORGANIZATION_SCOPE, create_management_router
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

    # --- Management API (M6) --------------------------------------------
    app.include_router(create_management_router(settings=settings, session_factory=session_factory))

    # --- Registry endpoint-ownership verification (M2) -----------------
    # registry-api issues this agent's operator a one-time challenge
    # value; the operator (here, this same process, acting as its own
    # operator) must expose it at this well-known path before registry-api
    # will mark the agent "active". This is NOT agent-to-agent
    # communication — it is the agent's own operator configuring their own
    # server, the same way domain-validated TLS certificates work.
    #
    # The GET below is deliberately public with no credential — that is
    # the whole point (registry-api, an unauthenticated third party from
    # this service's point of view, must be able to fetch it). The PUT is
    # a different concern entirely: it is the WRITE side, and was found
    # (2026-11-27 staging exercise) to have no authentication at all,
    # meaning any unauthenticated caller on the internet could overwrite
    # or clear what gets served here mid-verification — a real
    # unauthenticated-write/DoS gap on this one step, not a weakness in
    # the challenge itself (registry-api's own nonce+signature format
    # still protects against a forged challenge value being *accepted*;
    # this gap was about who may *set* the value this agent serves).
    app.state.ownership_challenge = None

    def _require_organization_management(authorization: str | None) -> None:
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer service credential.")
        try:
            claims = verify_service_credential(
                authorization.removeprefix("Bearer "),
                signing_secret=settings.service_credential_signing_secret,
                required_scope=MANAGE_ORGANIZATION_SCOPE,
            )
        except ServiceCredentialError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if claims.organization_id != settings.supplier_organization_id:
            raise HTTPException(
                status_code=403,
                detail="Credential does not grant management of this organization.",
            )

    @app.put("/internal/ownership-challenge")
    async def set_ownership_challenge(
        body: dict[str, str], authorization: str | None = Header(default=None)
    ) -> dict[str, str]:
        _require_organization_management(authorization)
        app.state.ownership_challenge = body["challenge"]
        return {"status": "ok"}

    @app.get("/.well-known/aimo-brain-challenge")
    async def serve_ownership_challenge() -> Response:
        if app.state.ownership_challenge is None:
            return Response(status_code=404)
        return Response(content=app.state.ownership_challenge, media_type="text/plain")

    return app


app = create_app()
