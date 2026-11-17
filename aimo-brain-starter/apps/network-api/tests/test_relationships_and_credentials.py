from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from network_api.app import create_app
from network_api.models import OrganizationRecord
from network_api.settings import get_settings

from database import Base, new_engine
from identity import verify_service_credential


async def _fresh_app_with_orgs(*orgs: str):
    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.network_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        for org_id in orgs:
            await session.merge(
                OrganizationRecord(organization_id=org_id, display_name=org_id, verified=True)
            )
        await session.commit()

    app = create_app()
    return app, engine, settings


@pytest.mark.asyncio
async def test_issuing_credential_for_unknown_org_is_rejected() -> None:
    app, engine, settings = await _fresh_app_with_orgs()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/organizations/org_does_not_exist/service-credentials",
            json={},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
    assert response.status_code == 404
    await engine.dispose()


@pytest.mark.asyncio
async def test_issuing_credential_without_admin_key_is_rejected() -> None:
    app, engine, settings = await _fresh_app_with_orgs("org_fictional_bistro_01")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/organizations/org_fictional_bistro_01/service-credentials", json={}
        )
    assert response.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_issued_credential_verifies_and_scopes_to_the_org() -> None:
    app, engine, settings = await _fresh_app_with_orgs("org_fictional_bistro_01")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/organizations/org_fictional_bistro_01/service-credentials",
            json={"ttl_seconds": 120},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == "org_fictional_bistro_01"
    assert body["scope"] == "manage_agents"

    claims = verify_service_credential(
        body["token"],
        signing_secret=settings.service_credential_signing_secret,
        required_scope="manage_agents",
    )
    assert claims.organization_id == "org_fictional_bistro_01"
    await engine.dispose()


@pytest.mark.asyncio
async def test_relationship_lifecycle_and_internal_lookup() -> None:
    app, engine, settings = await _fresh_app_with_orgs(
        "org_fictional_bistro_01", "org_fictional_wholesale_01"
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create = await client.post(
            "/relationships",
            json={
                "buyer_organization_id": "org_fictional_bistro_01",
                "supplier_organization_id": "org_fictional_wholesale_01",
            },
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert create.status_code == 200
        relationship_id = create.json()["id"]

        lookup = await client.get(
            "/relationships",
            params={
                "buyer_organization_id": "org_fictional_bistro_01",
                "supplier_organization_id": "org_fictional_wholesale_01",
            },
            headers={"X-Internal-Service-Key": settings.network_internal_service_key},
        )
        assert lookup.status_code == 200
        assert lookup.json()[0]["status"] == "active"

        suspend = await client.post(
            f"/relationships/{relationship_id}/suspend",
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert suspend.status_code == 200
        assert suspend.json()["status"] == "suspended"

        lookup_after_suspend = await client.get(
            "/relationships",
            params={
                "buyer_organization_id": "org_fictional_bistro_01",
                "supplier_organization_id": "org_fictional_wholesale_01",
            },
            headers={"X-Internal-Service-Key": settings.network_internal_service_key},
        )
        assert lookup_after_suspend.json()[0]["status"] == "suspended"

        reactivate = await client.post(
            f"/relationships/{relationship_id}/reactivate",
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert reactivate.json()["status"] == "active"

        revoke = await client.post(
            f"/relationships/{relationship_id}/revoke",
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert revoke.json()["status"] == "revoked"
    await engine.dispose()


@pytest.mark.asyncio
async def test_relationship_lookup_without_internal_key_is_rejected() -> None:
    app, engine, settings = await _fresh_app_with_orgs()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/relationships",
            params={
                "buyer_organization_id": "org_fictional_bistro_01",
                "supplier_organization_id": "org_fictional_wholesale_01",
            },
        )
    assert response.status_code == 401
    await engine.dispose()
