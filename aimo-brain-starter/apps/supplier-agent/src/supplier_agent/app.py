"""supplier-agent FastAPI application.

M0 scope: a health endpoint proving this service boots independently, with
its own database engine and its own settings. There is no A2A endpoint,
no inter-agent call path and no buyer import anywhere in this package —
that is M1 scope, implemented with the official A2A SDK, never with a
direct function call into buyer-agent.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from agent_runtime import get_llm_adapter
from fastapi import FastAPI
from pydantic import BaseModel
from supplier_agent.settings import get_settings

from database import new_engine


class HealthOut(BaseModel):
    status: str
    service: str
    llm_provider: str


def create_app() -> FastAPI:
    settings = get_settings()
    engine = new_engine(settings.supplier_database_url)
    llm = get_llm_adapter(settings.llm_provider)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="AIMO BRAIN Supplier Agent", version="0.1.0", lifespan=lifespan)

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        return HealthOut(status="ok", service="supplier-agent", llm_provider=llm.model_name)

    return app


app = create_app()
