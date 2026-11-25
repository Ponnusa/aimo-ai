from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from message_integrity.keys import generate_signing_keypair, public_key_to_jwk
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
    import sys

    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.registry_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text as _text

        try:
            count = (await conn.execute(_text("SELECT COUNT(*) FROM registry_agents"))).scalar_one()
            print(
                f"[DIAG] url={settings.registry_database_url!r} "
                f"registry_agents rowcount after create_all={count}",
                file=sys.stderr,
            )
        except Exception as diag_exc:  # noqa: BLE001
            print(f"[DIAG] rowcount probe failed: {diag_exc!r}", file=sys.stderr)

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
        challenge = start.json()["endpoint_challenge"]

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


@pytest.mark.asyncio
async def test_verification_with_valid_key_proof_activates(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    private_key, public_key = generate_signing_keypair()
    jwk = public_key_to_jwk(public_key, kid="org_supplier-k1")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        register = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
                "signing_public_key_jwk": jwk,
            },
            headers=headers,
        )
        assert register.json()["has_signing_key"] is True

        start = await client.post(
            "/agents/agent_fictional_supplier_01/verification/start",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        challenge = start.json()["endpoint_challenge"]
        key_challenge = start.json()["key_challenge"]
        assert key_challenge is not None and key_challenge != challenge

        import registry_api.app as app_module

        async def _fake_fetch_text(url, **kwargs):
            return challenge

        monkeypatch.setattr(app_module, "fetch_text_safely", _fake_fetch_text)

        signature = private_key.sign(key_challenge.encode("utf-8"))
        confirm = await client.post(
            "/agents/agent_fictional_supplier_01/verification/confirm",
            params={"organization_id": SUPPLIER_ORG},
            json={"key_proof_signature": base64.b64encode(signature).decode("ascii")},
            headers=headers,
        )
        assert confirm.status_code == 200
        assert confirm.json()["status"] == "active"
    await engine.dispose()


@pytest.mark.asyncio
async def test_verification_missing_key_proof_rejected_when_key_registered(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    _, public_key = generate_signing_keypair()
    jwk = public_key_to_jwk(public_key, kid="org_supplier-k1")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
                "signing_public_key_jwk": jwk,
            },
            headers=headers,
        )
        start = await client.post(
            "/agents/agent_fictional_supplier_01/verification/start",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        challenge = start.json()["endpoint_challenge"]

        import registry_api.app as app_module

        async def _fake_fetch_text(url, **kwargs):
            return challenge

        monkeypatch.setattr(app_module, "fetch_text_safely", _fake_fetch_text)

        confirm = await client.post(
            "/agents/agent_fictional_supplier_01/verification/confirm",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert confirm.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_verification_wrong_key_signature_rejected(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    _, public_key = generate_signing_keypair()
    attacker_private_key, _ = generate_signing_keypair()
    jwk = public_key_to_jwk(public_key, kid="org_supplier-k1")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
                "signing_public_key_jwk": jwk,
            },
            headers=headers,
        )
        start = await client.post(
            "/agents/agent_fictional_supplier_01/verification/start",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        challenge = start.json()["endpoint_challenge"]
        key_challenge = start.json()["key_challenge"]

        import registry_api.app as app_module

        async def _fake_fetch_text(url, **kwargs):
            return challenge

        monkeypatch.setattr(app_module, "fetch_text_safely", _fake_fetch_text)

        # Signed with a DIFFERENT private key than the one registered --
        # must not verify even though it is a well-formed signature.
        wrong_signature = attacker_private_key.sign(key_challenge.encode("utf-8"))
        confirm = await client.post(
            "/agents/agent_fictional_supplier_01/verification/confirm",
            params={"organization_id": SUPPLIER_ORG},
            json={"key_proof_signature": base64.b64encode(wrong_signature).decode("ascii")},
            headers=headers,
        )
        assert confirm.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_without_signing_key_is_unaffected(monkeypatch) -> None:
    """M0-M7 backward compatibility: an agent that never supplies a
    signing key keeps the original endpoint-control-only verification."""
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        register = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=headers,
        )
        assert register.json()["has_signing_key"] is False

        start = await client.post(
            "/agents/agent_fictional_supplier_01/verification/start",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert start.json()["key_challenge"] is None
        challenge = start.json()["endpoint_challenge"]

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
async def test_registering_a_different_signing_key_for_pending_agent_is_rejected(
    monkeypatch,
) -> None:
    """Covers the substitution guard while the agent is still `pending`
    (never confirmed) -- the genuinely-`active` case is a separate test
    (`test_registering_a_different_signing_key_for_active_agent_is_rejected`)
    below, since the 409 guard has no status condition and must fire
    identically in both states (ADR-015 section C)."""
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    _, public_key_1 = generate_signing_keypair()
    _, public_key_2 = generate_signing_keypair()
    jwk_1 = public_key_to_jwk(public_key_1, kid="org_supplier-k1")
    jwk_2 = public_key_to_jwk(public_key_2, kid="org_supplier-k2")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
                "signing_public_key_jwk": jwk_1,
            },
            headers=headers,
        )

        substitution_attempt = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
                "signing_public_key_jwk": jwk_2,
            },
            headers=headers,
        )
        assert substitution_attempt.status_code == 409

        # Re-registering with the SAME key is fine (idempotent update).
        same_key_again = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
                "signing_public_key_jwk": jwk_1,
            },
            headers=headers,
        )
        assert same_key_again.status_code == 200
        # ... and did NOT reset lifecycle state to "pending" again --
        # it already was pending, so this just confirms no error, not
        # yet the downgrade-prevention guarantee (see the active-agent
        # test below for that).
        assert same_key_again.json()["status"] == "pending"
    await engine.dispose()


async def _register_and_fully_verify(
    client, headers, *, agent_id: str, organization_id: str, card_url: str, jwk, private_key
) -> None:
    payload = {
        "agent_id": agent_id,
        "organization_id": organization_id,
        "a2a_card_url": card_url,
    }
    if jwk is not None:
        payload["signing_public_key_jwk"] = jwk
    await client.post("/agents", json=payload, headers=headers)
    start = await client.post(
        f"/agents/{agent_id}/verification/start",
        params={"organization_id": organization_id},
        headers=headers,
    )
    body = start.json()

    import registry_api.app as app_module

    async def _fake_fetch_text(url, **kwargs):
        return body["endpoint_challenge"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(app_module, "fetch_text_safely", _fake_fetch_text)
        confirm_body = {}
        if private_key is not None:
            signature = private_key.sign(body["key_challenge"].encode("utf-8"))
            confirm_body["key_proof_signature"] = base64.b64encode(signature).decode("ascii")
        confirm = await client.post(
            f"/agents/{agent_id}/verification/confirm",
            params={"organization_id": organization_id},
            json=confirm_body or None,
            headers=headers,
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["status"] == "active"


@pytest.mark.asyncio
async def test_registering_a_different_signing_key_for_active_agent_is_rejected(
    monkeypatch,
) -> None:
    """The genuinely-`active` case (ADR-015 section C): confirms the
    agent via `verification/confirm` first, THEN attempts the key
    substitution -- this is the scenario the review flagged as
    previously untested under that test's misleading name."""
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    private_key_1, public_key_1 = generate_signing_keypair()
    _, public_key_2 = generate_signing_keypair()
    jwk_1 = public_key_to_jwk(public_key_1, kid="org_supplier-k1")
    jwk_2 = public_key_to_jwk(public_key_2, kid="org_supplier-k2")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_and_fully_verify(
            client,
            headers,
            agent_id="agent_fictional_supplier_01",
            organization_id=SUPPLIER_ORG,
            card_url="http://127.0.0.1:9999",
            jwk=jwk_1,
            private_key=private_key_1,
        )

        substitution_attempt = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
                "signing_public_key_jwk": jwk_2,
            },
            headers=headers,
        )
        assert substitution_attempt.status_code == 409

        # And an identical same-key, same-url retry must NOT downgrade
        # the now-active agent back to "pending" (ADR-015 section B).
        same_key_again = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
                "signing_public_key_jwk": jwk_1,
            },
            headers=headers,
        )
        assert same_key_again.status_code == 200
        assert same_key_again.json()["status"] == "active"
    await engine.dispose()


@pytest.mark.asyncio
async def test_reregistering_active_agent_with_same_data_does_not_downgrade(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_and_fully_verify(
            client,
            headers,
            agent_id="agent_fictional_supplier_01",
            organization_id=SUPPLIER_ORG,
            card_url="http://127.0.0.1:9999",
            jwk=None,
            private_key=None,
        )

        same_data_again = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=headers,
        )
        assert same_data_again.status_code == 200
        assert same_data_again.json()["status"] == "active"
    await engine.dispose()


@pytest.mark.asyncio
async def test_reregistering_active_agent_with_new_card_url_requires_reverification(
    monkeypatch,
) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_and_fully_verify(
            client,
            headers,
            agent_id="agent_fictional_supplier_01",
            organization_id=SUPPLIER_ORG,
            card_url="http://127.0.0.1:9999",
            jwk=None,
            private_key=None,
        )

        moved = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:8888",
            },
            headers=headers,
        )
        assert moved.status_code == 200
        assert moved.json()["status"] == "pending"
    await engine.dispose()


@pytest.mark.asyncio
async def test_revoked_agent_cannot_be_resurrected_by_reregistration(monkeypatch) -> None:
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
        revoke = await client.post(
            "/agents/agent_fictional_supplier_01/revoke",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert revoke.json()["status"] == "revoked"

        resurrection_attempt = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=headers,
        )
        assert resurrection_attempt.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_suspended_agent_cannot_become_active_via_reregistration(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_and_fully_verify(
            client,
            headers,
            agent_id="agent_fictional_supplier_01",
            organization_id=SUPPLIER_ORG,
            card_url="http://127.0.0.1:9999",
            jwk=None,
            private_key=None,
        )
        suspend = await client.post(
            "/agents/agent_fictional_supplier_01/suspend",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert suspend.json()["status"] == "suspended"

        reregister = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=headers,
        )
        assert reregister.status_code == 200
        assert reregister.json()["status"] == "suspended"

        # /reactivate (not ordinary registration) is the only sanctioned
        # path back to active from suspended.
        reactivate = await client.post(
            "/agents/agent_fictional_supplier_01/reactivate",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert reactivate.json()["status"] == "active"
    await engine.dispose()


@pytest.mark.asyncio
async def test_reactivate_rejected_from_pending(monkeypatch) -> None:
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
        reactivate = await client.post(
            "/agents/agent_fictional_supplier_01/reactivate",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert reactivate.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_first_key_registration_only_one_wins(monkeypatch) -> None:
    """ADR-015 section C: two concurrent requests trying to establish
    the FIRST signing key for the same existing, keyless agent. Only one
    may succeed in setting the key; the other must get a deterministic
    409, never a silently-overwritten key."""
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    _, public_key_1 = generate_signing_keypair()
    _, public_key_2 = generate_signing_keypair()
    jwk_1 = public_key_to_jwk(public_key_1, kid="org_supplier-k1")
    jwk_2 = public_key_to_jwk(public_key_2, kid="org_supplier-k2")
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

        import asyncio

        results = await asyncio.gather(
            client.post(
                "/agents",
                json={
                    "agent_id": "agent_fictional_supplier_01",
                    "organization_id": SUPPLIER_ORG,
                    "a2a_card_url": "http://127.0.0.1:9999",
                    "signing_public_key_jwk": jwk_1,
                },
                headers=headers,
            ),
            client.post(
                "/agents",
                json={
                    "agent_id": "agent_fictional_supplier_01",
                    "organization_id": SUPPLIER_ORG,
                    "a2a_card_url": "http://127.0.0.1:9999",
                    "signing_public_key_jwk": jwk_2,
                },
                headers=headers,
            ),
        )
        statuses = sorted(r.status_code for r in results)
        # Both racing requests may observe a transient 409 (the loser of
        # the CAS, or -- depending on interleaving -- a substitution
        # conflict against the winner's already-committed key), but at
        # least one must succeed, and never both.
        assert statuses.count(200) == 1
        assert statuses.count(409) == 1

        final = await client.get(
            f"/organizations/{SUPPLIER_ORG}/agents",
            headers=headers,
        )
        [agent] = [a for a in final.json() if a["agent_id"] == "agent_fictional_supplier_01"]
        assert agent["has_signing_key"] is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_confirm_only_one_activates(monkeypatch) -> None:
    """ADR-015 section C: two concurrent `verification/confirm` calls
    for the same agent. Exactly one may activate it; the challenge must
    not be consumable twice."""
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
        challenge = start.json()["endpoint_challenge"]

        import registry_api.app as app_module

        async def _fake_fetch_text(url, **kwargs):
            return challenge

        monkeypatch.setattr(app_module, "fetch_text_safely", _fake_fetch_text)

        import asyncio

        results = await asyncio.gather(
            client.post(
                "/agents/agent_fictional_supplier_01/verification/confirm",
                params={"organization_id": SUPPLIER_ORG},
                headers=headers,
            ),
            client.post(
                "/agents/agent_fictional_supplier_01/verification/confirm",
                params={"organization_id": SUPPLIER_ORG},
                headers=headers,
            ),
        )
        statuses = sorted(r.status_code for r in results)
        assert statuses == [200, 409]
    await engine.dispose()


@pytest.mark.asyncio
async def test_endpoint_proof_cannot_be_used_as_key_proof_and_vice_versa(monkeypatch) -> None:
    """ADR-015 section D: the two challenges are distinct strings bound
    to different `purpose` values -- signing the endpoint challenge (or
    serving the key challenge at the well-known path) must not satisfy
    the other check."""
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    private_key, public_key = generate_signing_keypair()
    jwk = public_key_to_jwk(public_key, kid="org_supplier-k1")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
                "signing_public_key_jwk": jwk,
            },
            headers=headers,
        )
        start = await client.post(
            "/agents/agent_fictional_supplier_01/verification/start",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        body = start.json()
        endpoint_challenge, key_challenge = body["endpoint_challenge"], body["key_challenge"]
        assert endpoint_challenge != key_challenge

        import registry_api.app as app_module

        # Serve the KEY challenge at the well-known path instead of the
        # endpoint challenge, and sign the ENDPOINT challenge instead of
        # the key challenge -- both must be rejected.
        async def _fake_fetch_text(url, **kwargs):
            return key_challenge

        monkeypatch.setattr(app_module, "fetch_text_safely", _fake_fetch_text)
        wrong_signature = private_key.sign(endpoint_challenge.encode("utf-8"))

        confirm = await client.post(
            "/agents/agent_fictional_supplier_01/verification/confirm",
            params={"organization_id": SUPPLIER_ORG},
            json={"key_proof_signature": base64.b64encode(wrong_signature).decode("ascii")},
            headers=headers,
        )
        assert confirm.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_challenge_rejected(monkeypatch) -> None:
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
        challenge = start.json()["endpoint_challenge"]

        from datetime import datetime

        from registry_api.models import RegistryAgentRecord
        from sqlalchemy.ext.asyncio import async_sessionmaker

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            record = await session.get(RegistryAgentRecord, "agent_fictional_supplier_01")
            record.challenge_expires_at = datetime(2020, 1, 1)
            await session.commit()

        import registry_api.app as app_module

        async def _fake_fetch_text(url, **kwargs):
            return challenge

        monkeypatch.setattr(app_module, "fetch_text_safely", _fake_fetch_text)

        confirm = await client.post(
            "/agents/agent_fictional_supplier_01/verification/confirm",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert confirm.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_challenge_replayed_after_registration_change_is_invalidated(monkeypatch) -> None:
    """ADR-015 section D/B: a pending challenge is invalidated once the
    agent's card URL (what it was bound to) actually changes."""
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
        old_challenge = start.json()["endpoint_challenge"]

        await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:8888",
            },
            headers=headers,
        )

        import registry_api.app as app_module

        async def _fake_fetch_text(url, **kwargs):
            return old_challenge

        monkeypatch.setattr(app_module, "fetch_text_safely", _fake_fetch_text)

        confirm = await client.post(
            "/agents/agent_fictional_supplier_01/verification/confirm",
            params={"organization_id": SUPPLIER_ORG},
            headers=headers,
        )
        assert confirm.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_malformed_and_private_jwk_material_rejected(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    _, public_key = generate_signing_keypair()
    good_jwk = public_key_to_jwk(public_key, kid="org_supplier-k1")

    private_key, _ = generate_signing_keypair()
    private_jwk = dict(good_jwk)
    private_jwk["d"] = base64.urlsafe_b64encode(b"\x00" * 32).rstrip(b"=").decode("ascii")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for bad_jwk, label in [
            ({**good_jwk, "kty": "RSA"}, "wrong kty"),
            ({**good_jwk, "crv": "P-256"}, "wrong crv"),
            ({**good_jwk, "x": "not-valid-base64url!!"}, "malformed x"),
            (private_jwk, "private key material"),
        ]:
            response = await client.post(
                "/agents",
                json={
                    "agent_id": f"agent_bad_{label.replace(' ', '_')}",
                    "organization_id": SUPPLIER_ORG,
                    "a2a_card_url": "http://127.0.0.1:9999",
                    "signing_public_key_jwk": bad_jwk,
                },
                headers=headers,
            )
            assert response.status_code == 400, f"{label}: expected 400, got {response.text}"
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_organization_without_key_rejected_at_registration(monkeypatch) -> None:
    """ADR-015 section A: once an organization is admin-classified as
    requiring key proof, omitting the signing key is a 400 at
    registration time -- not merely "endpoint-control-only" fallback."""
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        classify = await client.post(
            f"/organizations/{SUPPLIER_ORG}/classification",
            json={"requires_key_proof": True, "note": "pilot partner"},
            headers={"Authorization": f"Bearer {settings.registry_admin_token}"},
        )
        assert classify.status_code == 200
        response = await client.post(
            "/agents",
            json={
                "agent_id": "agent_fictional_supplier_01",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
            },
            headers=headers,
        )
        assert response.status_code == 400
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_classification_cannot_be_set_by_the_registering_caller(
    monkeypatch,
) -> None:
    """ADR-015 section A: there is no field in the registration body
    that lets the caller declare itself "internal"/"legacy" to dodge
    key-control proof -- confirmed here by registering an org-management
    credential's own registration attempt against the admin-only
    classification endpoint, which must reject it outright."""
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/organizations/{SUPPLIER_ORG}/classification",
            json={"requires_key_proof": False},
            headers=headers,  # the org's OWN manage_agents credential, not the admin token
        )
        assert response.status_code in (401, 403)
    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_key_registration_across_agents_rejected(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    transport = ASGITransport(app=app)
    headers = _bearer(SUPPLIER_ORG, signing_secret=settings.service_credential_signing_secret)
    _, public_key = generate_signing_keypair()
    jwk = public_key_to_jwk(public_key, kid="org_supplier-k1")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/agents",
            json={
                "agent_id": "agent_one",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9999",
                "signing_public_key_jwk": jwk,
            },
            headers=headers,
        )
        assert first.status_code == 200

        second = await client.post(
            "/agents",
            json={
                "agent_id": "agent_two",
                "organization_id": SUPPLIER_ORG,
                "a2a_card_url": "http://127.0.0.1:9998",
                "signing_public_key_jwk": jwk,
            },
            headers=headers,
        )
        assert second.status_code == 409
    await engine.dispose()
