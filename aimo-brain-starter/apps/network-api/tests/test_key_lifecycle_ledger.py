"""M8 slice 3 (ADR-018): the append-only key-event ledger, the
thumbprint-reservation-release mechanism (deliberately separate from
revocation), and its PostgreSQL concurrency/rollback behavior. See
``test_delegations.py``'s own module docstring for why the concurrency
tests here need real PostgreSQL to mean anything -- the same CI
mechanism applies.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from message_integrity import DelegationScope, build_delegation
from message_integrity.keys import generate_signing_keypair, public_key_to_jwk
from network_api.app import create_app
from network_api.models import (
    AgentSigningDelegationRecord,
    KeyLifecycleEventRecord,
    OrganizationRecord,
)
from network_api.settings import get_settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Base, new_engine

ORG = "org_fictional_bistro_01"
NETWORK_AUDIENCE = "aimo-network:test"


async def _fresh_app_with_org(organization_id: str = ORG):
    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.network_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await session.merge(
            OrganizationRecord(
                organization_id=organization_id, display_name=organization_id, verified=True
            )
        )
        await session.commit()

    app = create_app()
    return app, engine, settings, session_factory


async def _register_root_key(client, settings, *, kid: str, public_key, org: str = ORG):
    jwk = public_key_to_jwk(public_key, kid=kid)
    response = await client.post(
        f"/organizations/{org}/signing-keys",
        json={"kid": kid, "public_key_jwk": jwk},
        headers={"X-Admin-Key": settings.network_admin_api_key},
    )
    assert response.status_code == 200, response.text


def _build_token(
    *,
    root_private_key,
    root_kid: str,
    agent_id: str,
    delegated_kid: str,
    delegated_public_key_jwk: dict | None = None,
    artifact_types: tuple[str, ...] = ("request_for_quote", "quote"),
):
    if delegated_public_key_jwk is None:
        _, delegated_public = generate_signing_keypair()
        delegated_public_key_jwk = public_key_to_jwk(delegated_public, kid=delegated_kid)
    return build_delegation(
        root_private_key=root_private_key,
        issuing_kid=root_kid,
        organization_id=ORG,
        agent_id=agent_id,
        audience=NETWORK_AUDIENCE,
        delegated_kid=delegated_kid,
        delegated_public_key_jwk=delegated_public_key_jwk,
        scope=DelegationScope(artifact_types=artifact_types, protocol_version="v1"),
    )


@pytest.mark.asyncio
async def test_root_key_issue_and_revoke_record_ledger_events() -> None:
    app, engine, settings, session_factory = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    _, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)
        headers = {"X-Admin-Key": settings.network_admin_api_key}
        await client.post(f"/organizations/{ORG}/signing-keys/org_a-root1/revoke", headers=headers)

    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(KeyLifecycleEventRecord).where(
                        KeyLifecycleEventRecord.organization_id == ORG,
                        KeyLifecycleEventRecord.kid == "org_a-root1",
                    )
                )
            )
            .scalars()
            .all()
        )
    event_types = sorted(e.event_type for e in events)
    assert event_types == ["issued", "revoked"]
    assert all(e.key_material for e in events)
    assert all(not e.is_reconstructed_snapshot for e in events)
    await engine.dispose()


@pytest.mark.asyncio
async def test_delegation_publish_and_revoke_record_ledger_events() -> None:
    app, engine, settings, session_factory = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)
        headers = {"X-Admin-Key": settings.network_admin_api_key}
        token = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_01",
            delegated_kid="org_a-agent01-k1",
        )
        publish = await client.post(
            f"/organizations/{ORG}/delegations", json={"jws": token}, headers=headers
        )
        assert publish.status_code == 200, publish.text
        jti = publish.json()["jti"]
        await client.post(f"/organizations/{ORG}/delegations/{jti}/revoke", headers=headers)

    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(KeyLifecycleEventRecord).where(
                        KeyLifecycleEventRecord.organization_id == ORG,
                        KeyLifecycleEventRecord.kid == "org_a-agent01-k1",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert sorted(e.event_type for e in events) == ["issued", "revoked"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_key_events_endpoint_requires_admin_and_logs_access() -> None:
    app, engine, settings, session_factory = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    _, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)

        unauthorized = await client.get(f"/organizations/{ORG}/key-events.json")
        assert unauthorized.status_code == 401

        authorized = await client.get(
            f"/organizations/{ORG}/key-events.json",
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert authorized.status_code == 200
        events = authorized.json()["events"]
        assert any(e["event_type"] == "issued" for e in events)

    from network_api.models import KeyEventAccessLogRecord

    async with session_factory() as session:
        log_rows = (await session.execute(select(KeyEventAccessLogRecord))).scalars().all()
    assert len(log_rows) == 1
    assert log_rows[0].queried_organization_id == ORG
    await engine.dispose()


async def _expire_delegation_long_ago(session_factory, jti: str) -> None:
    async with session_factory() as session:
        record = (
            await session.execute(
                select(AgentSigningDelegationRecord).where(AgentSigningDelegationRecord.jti == jti)
            )
        ).scalar_one()
        record.not_after = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)
        await session.commit()


@pytest.mark.asyncio
async def test_release_reservation_rejected_before_grace_period_elapsed() -> None:
    app, engine, settings, session_factory = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)
        headers = {"X-Admin-Key": settings.network_admin_api_key}
        token = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_01",
            delegated_kid="org_a-agent01-fresh",
        )
        publish = await client.post(
            f"/organizations/{ORG}/delegations", json={"jws": token}, headers=headers
        )
        jti = publish.json()["jti"]

        # Not past not_after + grace yet -- must be rejected, not silently released.
        response = await client.post(
            f"/organizations/{ORG}/delegations/{jti}/release-reservation", headers=headers
        )
        assert response.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_release_after_grace_frees_thumbprint_never_touches_revoked_at() -> None:
    app, engine, settings, session_factory = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    _, shared_public = generate_signing_keypair()
    shared_jwk = public_key_to_jwk(shared_public, kid="shared-reuse-material")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)
        headers = {"X-Admin-Key": settings.network_admin_api_key}
        token_1 = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_01",
            delegated_kid="org_a-agent01-reuse",
            delegated_public_key_jwk=shared_jwk,
        )
        publish_1 = await client.post(
            f"/organizations/{ORG}/delegations", json={"jws": token_1}, headers=headers
        )
        assert publish_1.status_code == 200, publish_1.text
        jti_1 = publish_1.json()["jti"]

        # Before release: the same key material cannot be delegated to a
        # different agent -- the slot is still held.
        token_2_blocked = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_02",
            delegated_kid="org_a-agent02-reuse",
            delegated_public_key_jwk=shared_jwk,
        )
        blocked = await client.post(
            f"/organizations/{ORG}/delegations", json={"jws": token_2_blocked}, headers=headers
        )
        assert blocked.status_code == 409

        await _expire_delegation_long_ago(session_factory, jti_1)
        release = await client.post(
            f"/organizations/{ORG}/delegations/{jti_1}/release-reservation", headers=headers
        )
        assert release.status_code == 200, release.text

        # After release: the SAME key material can now be delegated to a
        # DIFFERENT agent.
        token_2 = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_02",
            delegated_kid="org_a-agent02-reuse-after",
            delegated_public_key_jwk=shared_jwk,
        )
        publish_2 = await client.post(
            f"/organizations/{ORG}/delegations", json={"jws": token_2}, headers=headers
        )
        assert publish_2.status_code == 200, publish_2.text

    async with session_factory() as session:
        original = (
            await session.execute(
                select(AgentSigningDelegationRecord).where(
                    AgentSigningDelegationRecord.jti == jti_1
                )
            )
        ).scalar_one()
        assert original.revoked_at is None  # never touched by reservation release
        assert original.thumbprint_reservation_released_at is not None

        events = (
            (
                await session.execute(
                    select(KeyLifecycleEventRecord).where(
                        KeyLifecycleEventRecord.organization_id == ORG,
                        KeyLifecycleEventRecord.kid == "org_a-agent01-reuse",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "reservation_released" in {e.event_type for e in events}
        assert "revoked" not in {e.event_type for e in events}

        # (organization_id, delegated_kid) remains permanently unique --
        # untouched by reservation release.
        still_unique = (
            (
                await session.execute(
                    select(AgentSigningDelegationRecord).where(
                        AgentSigningDelegationRecord.organization_id == ORG,
                        AgentSigningDelegationRecord.delegated_kid == "org_a-agent01-reuse",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(still_unique) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_forced_rollback_leaves_reservation_held_not_half_released() -> None:
    """Directly exercises atomicity at the session level: if the
    transaction containing the reservation-release UPDATE and its
    ledger INSERT is rolled back instead of committed, NEITHER takes
    effect -- the slot remains held, exactly as a real failure
    mid-transaction must behave."""
    app, engine, settings, session_factory = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)
        headers = {"X-Admin-Key": settings.network_admin_api_key}
        token = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_01",
            delegated_kid="org_a-agent01-rollback",
        )
        publish = await client.post(
            f"/organizations/{ORG}/delegations", json={"jws": token}, headers=headers
        )
        jti = publish.json()["jti"]
    await _expire_delegation_long_ago(session_factory, jti)

    async with session_factory() as session:
        record = (
            await session.execute(
                select(AgentSigningDelegationRecord).where(AgentSigningDelegationRecord.jti == jti)
            )
        ).scalar_one()
        record.thumbprint_reservation_released_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(
            KeyLifecycleEventRecord(
                organization_id=ORG,
                kid="org_a-agent01-rollback",
                kind="delegated",
                event_type="reservation_released",
                effective_time=datetime.now(UTC).replace(tzinfo=None),
                recorded_at=datetime.now(UTC).replace(tzinfo=None),
                key_material="irrelevant-for-this-test",
                is_reconstructed_snapshot=False,
            )
        )
        await session.rollback()  # simulates a failure before commit

    async with session_factory() as session:
        record = (
            await session.execute(
                select(AgentSigningDelegationRecord).where(AgentSigningDelegationRecord.jti == jti)
            )
        ).scalar_one()
        assert record.thumbprint_reservation_released_at is None  # still held
        events = (
            (
                await session.execute(
                    select(KeyLifecycleEventRecord).where(
                        KeyLifecycleEventRecord.kid == "org_a-agent01-rollback"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "reservation_released" not in {e.event_type for e in events}
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_release_and_new_publication_never_both_succeed_live() -> None:
    """PostgreSQL-backed: a reservation-release call for an expired
    delegation races a concurrent publication of a NEW delegation using
    the identical key material. Whichever commits first legitimately
    wins -- the invariant this test actually proves is that the two
    outcomes can never BOTH leave a live (unrevoked, unreleased) row
    with the same thumbprint, regardless of which one wins the race."""
    app, engine, settings, session_factory = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    _, shared_public = generate_signing_keypair()
    shared_jwk = public_key_to_jwk(shared_public, kid="shared-race-material")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)
        headers = {"X-Admin-Key": settings.network_admin_api_key}
        token_1 = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_01",
            delegated_kid="org_a-agent01-release-race",
            delegated_public_key_jwk=shared_jwk,
        )
        publish_1 = await client.post(
            f"/organizations/{ORG}/delegations", json={"jws": token_1}, headers=headers
        )
        jti_1 = publish_1.json()["jti"]
        await _expire_delegation_long_ago(session_factory, jti_1)

        token_2 = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_02",
            delegated_kid="org_a-agent02-release-race",
            delegated_public_key_jwk=shared_jwk,
        )
        await asyncio.gather(
            client.post(
                f"/organizations/{ORG}/delegations/{jti_1}/release-reservation", headers=headers
            ),
            client.post(
                f"/organizations/{ORG}/delegations", json={"jws": token_2}, headers=headers
            ),
            return_exceptions=True,
        )

    async with session_factory() as session:
        live_rows = (
            (
                await session.execute(
                    select(AgentSigningDelegationRecord).where(
                        AgentSigningDelegationRecord.organization_id == ORG,
                        AgentSigningDelegationRecord.pubkey_thumbprint
                        == (
                            await session.execute(
                                select(AgentSigningDelegationRecord.pubkey_thumbprint).where(
                                    AgentSigningDelegationRecord.jti == jti_1
                                )
                            )
                        ).scalar_one(),
                        AgentSigningDelegationRecord.revoked_at.is_(None),
                        AgentSigningDelegationRecord.thumbprint_reservation_released_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        # The core invariant: at most one row for this thumbprint is
        # simultaneously "live" (neither revoked nor reservation-
        # released) -- the race's two legitimate outcomes (release-wins
        # vs. publish-fails, or publish-attempted-before-release-so-
        # fails) both satisfy this; only a bug would leave two.
        assert len(live_rows) <= 1
    await engine.dispose()
