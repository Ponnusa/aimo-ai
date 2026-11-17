"""M0 integration smoke test: seeding + reading fictional organizations
through the network-api's own database engine (no other service involved).
"""

import pytest
from network_api.app import create_app
from network_api.seed import FICTIONAL_ORGANIZATIONS
from network_api.settings import get_settings

from database import Base, new_engine


@pytest.mark.integration
@pytest.mark.asyncio
async def test_seeded_fictional_organizations_are_listable() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.network_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        for org in FICTIONAL_ORGANIZATIONS:
            await session.merge(org)
        await session.commit()

    from httpx import ASGITransport, AsyncClient

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/organizations")

    body = response.json()
    seeded_ids = {org.organization_id for org in FICTIONAL_ORGANIZATIONS}
    returned_ids = {o["organization_id"] for o in body}
    assert seeded_ids.issubset(returned_ids)

    await engine.dispose()
