"""buyer-mcp FastAPI application.

M3 scope: mounts the official MCP SDK's Streamable HTTP ASGI app (built by
``buyer_mcp.server.create_mcp_server``) under this FastAPI app, alongside
``/health``/``/ready``, matching the pattern every other AIMO BRAIN
service uses. Only buyer-agent (via a "use_buyer_tools" service
credential minted by network-api) can successfully call these tools —
see ``mcp_integration.server_auth``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from buyer_mcp.server import create_mcp_server
from buyer_mcp.settings import get_settings
from database import new_engine


class HealthOut(BaseModel):
    status: str
    service: str


class ReadyOut(BaseModel):
    status: str
    service: str
    database: str


def create_app() -> FastAPI:
    settings = get_settings()
    engine = new_engine(settings.buyer_mcp_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    mcp_server = create_mcp_server(settings=settings, session_factory=session_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_server.session_manager.run():
            try:
                yield
            finally:
                await engine.dispose()

    app = FastAPI(title="AIMO BRAIN Buyer MCP", version="0.1.0", lifespan=lifespan)
    app.mount("/mcp", mcp_server.streamable_http_app())

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        return HealthOut(status="ok", service="buyer-mcp")

    @app.get("/ready", response_model=ReadyOut)
    async def ready() -> ReadyOut:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return ReadyOut(status="ok", service="buyer-mcp", database="reachable")

    return app


app = create_app()
