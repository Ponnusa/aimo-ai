"""network-api FastAPI application.

M0 scope: a health endpoint proving the service boots independently with
its own settings/database engine, plus a read-only organizations listing
endpoint backed by seeded fictional data. Registry/discovery/verification
endpoints (FR-01, FR-02) land in M2.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import select

from database import new_engine, new_session_factory
from network_api.models import OrganizationRecord
from network_api.settings import get_settings


class OrganizationOut(BaseModel):
    organization_id: str
    display_name: str
    verified: bool


class HealthOut(BaseModel):
    status: str
    service: str


def create_app() -> FastAPI:
    settings = get_settings()
    engine = new_engine(settings.network_database_url)
    session_factory = new_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="AIMO BRAIN Network API", version="0.1.0", lifespan=lifespan)

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        return HealthOut(status="ok", service="network-api")

    @app.get("/organizations", response_model=list[OrganizationOut])
    async def list_organizations() -> list[OrganizationOut]:
        async with session_factory() as session:
            rows = (await session.execute(select(OrganizationRecord))).scalars().all()
            return [
                OrganizationOut(
                    organization_id=row.organization_id,
                    display_name=row.display_name,
                    verified=row.verified,
                )
                for row in rows
            ]

    return app


app = create_app()
