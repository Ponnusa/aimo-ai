from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from registry_api.app import create_app
from registry_api.settings import get_settings

from database import Base, new_engine
from identity import issue_service_credential

SIGNING_SECRET = "test-signing-secret"
BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"
OTHER_ORG = "org_fictional_other_01"


def _fake_card(
    name: str = "Fictional Supplier Agent",
    skill_ids: tuple[str, ...] = ("food.check_product_availability",),
):
    return SimpleNamespace(
        name=name,
        skills=[SimpleNamespace(id=skill_id, name=skill_id) for skill_id in skill_ids],
    )


async def _fresh_app(monkeypatch, *, fake_card=None):
    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.registry_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    if fake_card is not None:
        import registry_api.app as app_module

        async def _fake_fetch(url, **kwargs):
            return fake_card

        monkeypatch.setattr(app_module, "fetch_and_validate_agent_card", _fake_fetch)

    app = create_app()
    return app, engine, settings


def _bearer(organization_id: str, *, signing_secret: str, scope: str = "manage_agents") -> dict:
    token = issue_service_credential(
        organization_id=organization_id, scope=scope, signing_secret=signing_secret
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_health_and_ready() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        ready = await client.get("/ready")
    assert health.json() == {"status": "ok", "service": "registry-api"}
    assert ready.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_register_agent_requires_bearer(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
        )
    assert response.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_register_agent_rejects_credential_for_a_different_org(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=_bearer(OTHER_ORG, signing_secret=settings.service_credential_signing_secret),
        )
    assert response.status_code == 403
    await engine.dispose()


@pytest.mark.asyncio
async def test_register_agent_succeeds_and_is_pending(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=_bearer(
                SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret
            ),
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["capabilities"] == ["food.check_product_availability"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_registering_same_agent_id_under_different_org_is_rejected(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/agents",
            json={
                "agent_id": "agent_shared_id",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=_bearer(
                SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret
            ),
        )
        assert first.status_code == 200

        second = await client.post(
            "/agents",
            json={
                "agent_id": "agent_shared_id",
                "organization_id": OTHER_ORG,
                "a2a_card_url": "http://127.0.0.1:9998",
            },
            headers=_bearer(OTHER_ORG, signing_secret=settings.service_credential_signing_secret),
        )
        assert second.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_verification_lifecycle(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=headers,
        )

        start = await client.post(
            "/agents/agent_fictional_supplier_01/verification/start",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert start.status_code == 200
        challenge = start.json()["challenge"]

        import registry_api.app as app_module

        async def _fake_fetch_text(url, **kwargs):
            return challenge

        monkeypatch.setattr(app_module, "fetch_text_safely", _fake_fetch_text)

        confirm = await client.post(
            "/agents/agent_fictional_supplier_01/verification/confirm",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert confirm.status_code == 200
        assert confirm.json()["status"] == "active"
    await engine.dispose()


@pytest.mark.asyncio
async def test_verification_confirm_rejects_mismatched_challenge(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=headers,
        )
        await client.post(
            "/agents/agent_fictional_supplier_01/verification/start",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )

        import registry_api.app as app_module

        async def _fake_fetch_text_wrong(url, **kwargs):
            return "not-the-right-challenge"

        monkeypatch.setattr(app_module, "fetch_text_safely", _fake_fetch_text_wrong)

        confirm = await client.post(
            "/agents/agent_fictional_supplier_01/verification/confirm",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert confirm.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_lifecycle_suspend_reactivate_revoke_is_terminal(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=headers,
        )

        suspend = await client.post(
            "/agents/agent_fictional_supplier_01/suspend",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert suspend.json()["status"] == "suspended"

        reactivate = await client.post(
            "/agents/agent_fictional_supplier_01/reactivate",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert reactivate.json()["status"] == "active"

        revoke = await client.post(
            "/agents/agent_fictional_supplier_01/revoke",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert revoke.json()["status"] == "revoked"

        cannot_reactivate = await client.post(
            "/agents/agent_fictional_supplier_01/reactivate",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert cannot_reactivate.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_credential_scoped_to_other_org_cannot_find_or_manage_agent(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    supplier_headers = _bearer(
        SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret
    )
    other_headers = _bearer(OTHER_ORG, signing_secret=settings.service_credential_signing_secret)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=supplier_headers,
        )
        response = await client.post(
            "/agents/agent_fictional_supplier_01/suspend",
            params={"organization_id": OTHER_ORG},
            headers=other_headers,
        )
    # 404, not 403: the response must not confirm the agent exists under
    # a different organization at all.
    assert response.status_code == 404
    await engine.dispose()


@pytest.mark.asyncio
async def test_discovery_excludes_pending_agents_and_raw_card_data(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=headers,
        )

        # Pending (unverified) agents are not discoverable, even by their
        # own organization's buyer counterpart id.
        discovery = await client.get("/agents", params={"requesting_organization_id": SUPPLIER_ORG})
        assert discovery.json() == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_discovery_requires_active_relationship_across_orgs(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    transport = ASGITransport(app=app)

    import registry_api.app as app_module

    async def _no_relationship(**kwargs):
        return False

    monkeypatch.setattr(app_module, "has_active_relationship", _no_relationship)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=headers,
        )
        # Manually activate for this test (bypassing the challenge flow).
        from registry_api.models import RegistryAgentRecord
        from sqlalchemy.ext.asyncio import async_sessionmaker

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            record = await session.get(RegistryAgentRecord, "agent_fictional_supplier_01")
            record.status = "active"
            await session.commit()

        no_relationship_discovery = await client.get(
            "/agents", params={"requesting_organization_id": BUYER_ORG}
        )
        assert no_relationship_discovery.json() == []

        async def _has_relationship(**kwargs):
            return True

        monkeypatch.setattr(app_module, "has_active_relationship", _has_relationship)

        with_relationship_discovery = await client.get(
            "/agents", params={"requesting_organization_id": BUYER_ORG}
        )
        results = with_relationship_discovery.json()
        assert len(results) == 1
        assert results[0]["agent_id"] == "agent_fictional_supplier_01"
        assert results[0]["a2a_endpoint_url"] == "http://127.0.0.1:9999"
        assert set(results[0].keys()) == {
            "agent_id",
            "organization_id",
            "display_name",
            "status",
            "capabilities",
            "a2a_endpoint_url",
        }
    await engine.dispose()


@pytest.mark.asyncio
async def test_self_discovery_never_requires_a_relationship(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    transport = ASGITransport(app=app)

    import registry_api.app as app_module

    async def _blow_up(**kwargs):
        raise AssertionError("must not call network-api for self-discovery")

    monkeypatch.setattr(app_module, "has_active_relationship", _blow_up)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=headers,
        )
        from registry_api.models import RegistryAgentRecord
        from sqlalchemy.ext.asyncio import async_sessionmaker

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            record = await session.get(RegistryAgentRecord, "agent_fictional_supplier_01")
            record.status = "active"
            await session.commit()

        own_discovery = await client.get(
            "/agents", params={"requesting_organization_id": SUPPLIER_ORG}
        )
        assert len(own_discovery.json()) == 1
    await engine.dispose()
