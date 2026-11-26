from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from network_api.app import create_app
from network_api.models import OrganizationRecord
from network_api.settings import get_settings

from database import Base, new_engine


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
async def test_add_member_requires_admin_key() -> None:
    app, engine, settings = await _fresh_app_with_orgs("org_a")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/organizations/org_a/members",
            json={
                "oidc_subject": "sub_1",
                "email": "a@example.com",
                "display_name": "A",
                "role": "approver",
            },
        )
    assert response.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_add_member_rejects_unknown_role() -> None:
    app, engine, settings = await _fresh_app_with_orgs("org_a")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/organizations/org_a/members",
            json={
                "oidc_subject": "sub_1",
                "email": "a@example.com",
                "display_name": "A",
                "role": "superuser",
            },
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
    assert response.status_code == 400
    await engine.dispose()


@pytest.mark.asyncio
async def test_add_list_and_revoke_member() -> None:
    app, engine, settings = await _fresh_app_with_orgs("org_a")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        add_response = await client.post(
            "/organizations/org_a/members",
            json={
                "oidc_subject": "sub_1",
                "email": "a@example.com",
                "display_name": "A",
                "role": "approver",
            },
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert add_response.status_code == 200
        member_id = add_response.json()["id"]

        list_response = await client.get(
            "/organizations/org_a/members",
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert list_response.status_code == 200
        assert [m["oidc_subject"] for m in list_response.json()] == ["sub_1"]

        lookup_response = await client.get(
            "/members/lookup",
            params={"organization_id": "org_a", "oidc_subject": "sub_1", "role": "approver"},
            headers={"X-Internal-Service-Key": settings.network_internal_service_key},
        )
        assert lookup_response.status_code == 200
        assert lookup_response.json()["active"] is True

        revoke_response = await client.post(
            f"/organizations/org_a/members/{member_id}/revoke",
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert revoke_response.status_code == 200
        assert revoke_response.json()["status"] == "revoked"

        lookup_after = await client.get(
            "/members/lookup",
            params={"organization_id": "org_a", "oidc_subject": "sub_1", "role": "approver"},
            headers={"X-Internal-Service-Key": settings.network_internal_service_key},
        )
        assert lookup_after.json()["active"] is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_member_lookup_requires_internal_service_key() -> None:
    app, engine, settings = await _fresh_app_with_orgs("org_a")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/members/lookup",
            params={"organization_id": "org_a", "oidc_subject": "sub_1", "role": "approver"},
        )
    assert response.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_oidc_mapping_set_and_resolve_by_tenant() -> None:
    app, engine, settings = await _fresh_app_with_orgs("org_a")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        set_response = await client.post(
            "/organizations/org_a/oidc-mapping",
            json={
                "oidc_issuer": "https://login.microsoftonline.com/tenant-a/v2.0",
                "oidc_tenant_id": "tenant-a",
            },
            headers={"X-Operator-Key": settings.network_operator_key},
        )
        assert set_response.status_code == 200

        resolve_response = await client.get(
            "/organizations/by-oidc-tenant",
            params={
                "oidc_issuer": "https://login.microsoftonline.com/tenant-a/v2.0",
                "oidc_tenant_id": "tenant-a",
            },
            headers={"X-Internal-Service-Key": settings.network_internal_service_key},
        )
        assert resolve_response.status_code == 200
        assert resolve_response.json()["organization_id"] == "org_a"

        unmapped_response = await client.get(
            "/organizations/by-oidc-tenant",
            params={
                "oidc_issuer": "https://login.microsoftonline.com/x/v2.0",
                "oidc_tenant_id": "x",
            },
            headers={"X-Internal-Service-Key": settings.network_internal_service_key},
        )
        assert unmapped_response.status_code == 404
    await engine.dispose()


@pytest.mark.asyncio
async def test_service_credential_agent_id_restricted_to_portal_admin_sentinel() -> None:
    app, engine, settings = await _fresh_app_with_orgs("org_a")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        allowed = await client.post(
            "/organizations/org_a/service-credentials",
            json={"scope": "manage_organization", "agent_id": "portal-admin-action"},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert allowed.status_code == 200

        wrong_value = await client.post(
            "/organizations/org_a/service-credentials",
            json={"scope": "manage_organization", "agent_id": "something-else"},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert wrong_value.status_code == 400

        wrong_scope = await client.post(
            "/organizations/org_a/service-credentials",
            json={"scope": "manage_agents", "agent_id": "portal-admin-action"},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert wrong_scope.status_code == 400
    await engine.dispose()


@pytest.mark.asyncio
async def test_relationships_for_organization_includes_both_sides() -> None:
    app, engine, settings = await _fresh_app_with_orgs("org_buyer", "org_supplier")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/relationships",
            json={"buyer_organization_id": "org_buyer", "supplier_organization_id": "org_supplier"},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        for org_id in ("org_buyer", "org_supplier"):
            response = await client.get(
                f"/organizations/{org_id}/relationships",
                headers={"X-Internal-Service-Key": settings.network_internal_service_key},
            )
            assert response.status_code == 200
            assert len(response.json()) == 1
    await engine.dispose()
