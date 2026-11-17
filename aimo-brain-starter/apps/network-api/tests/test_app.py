import pytest
from httpx import ASGITransport, AsyncClient
from network_api.app import create_app
from network_api.models import OrganizationRecord
from network_api.settings import get_settings

from database import Base, new_engine


@pytest.mark.asyncio
async def test_health_endpoint_reports_ok() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "network-api"}


@pytest.mark.asyncio
async def test_list_organizations_returns_seeded_records() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.network_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()
    transport = ASGITransport(app=app)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await session.merge(
            OrganizationRecord(
                organization_id="org_test_01",
                display_name="Fictional Test Org",
                verified=True,
            )
        )
        await session.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/organizations")

    assert response.status_code == 200
    body = response.json()
    assert any(o["organization_id"] == "org_test_01" for o in body)

    await engine.dispose()
