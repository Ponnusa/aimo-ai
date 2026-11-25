"""M8 slice 2 (ADR-017): per-agent signing-authority delegation
publication, listing, revocation, and the atomic kid-namespace /
cross-agent thumbprint invariants. The concurrency tests in this file
are the ones that genuinely need real PostgreSQL (not SQLite) to mean
anything — see ADR-017's "PostgreSQL verification status" section;
CI's ``python`` job already points ``DATABASE_URL`` at a real Postgres
service for this test module, the same mechanism slice 1's
registry-api concurrency tests already relied on.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from message_integrity import DelegationScope, build_delegation
from message_integrity.keys import generate_signing_keypair, public_key_to_jwk
from network_api.app import create_app
from network_api.models import OrganizationRecord
from network_api.settings import get_settings

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

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await session.merge(
            OrganizationRecord(
                organization_id=organization_id, display_name=organization_id, verified=True
            )
        )
        await session.commit()

    app = create_app()
    return app, engine, settings


async def _register_root_key(client, settings, *, kid: str, public_key):
    jwk = public_key_to_jwk(public_key, kid=kid)
    response = await client.post(
        f"/organizations/{ORG}/signing-keys",
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
    artifact_types: tuple[str, ...] = ("request_for_quote", "quote"),
    audience: str = NETWORK_AUDIENCE,
):
    _, delegated_public = generate_signing_keypair()
    delegated_jwk = public_key_to_jwk(delegated_public, kid=delegated_kid)
    return build_delegation(
        root_private_key=root_private_key,
        issuing_kid=root_kid,
        organization_id=ORG,
        agent_id=agent_id,
        audience=audience,
        delegated_kid=delegated_kid,
        delegated_public_key_jwk=delegated_jwk,
        scope=DelegationScope(artifact_types=artifact_types, protocol_version="v1"),
    )


@pytest.mark.asyncio
async def test_publish_list_and_revoke_delegation_lifecycle() -> None:
    app, engine, settings = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)
        token = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_01",
            delegated_kid="org_a-agent01-k1",
        )
        publish = await client.post(
            f"/organizations/{ORG}/delegations",
            json={"jws": token},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert publish.status_code == 200, publish.text
        body = publish.json()
        assert body["agent_id"] == "agent_buyer_01"
        assert body["delegated_kid"] == "org_a-agent01-k1"
        jti = body["jti"]

        listed = await client.get(f"/organizations/{ORG}/delegations.json")
        assert listed.status_code == 200
        assert listed.json()["delegations"] == [token]

        revoke = await client.post(
            f"/organizations/{ORG}/delegations/{jti}/revoke",
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert revoke.status_code == 200
        assert revoke.json()["status"] == "revoked"

        after = await client.get(f"/organizations/{ORG}/delegations.json")
        assert after.json()["delegations"] == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_publish_requires_admin_key() -> None:
    app, engine, settings = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)
        token = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_01",
            delegated_kid="org_a-agent01-k1",
        )
        response = await client.post(f"/organizations/{ORG}/delegations", json={"jws": token})
    assert response.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_revoke_then_republish_identical_token_cannot_resurrect_it() -> None:
    """ADR-017 test 21 (half): (organization_id, delegated_kid) is
    permanently unique — resubmitting the exact same, already-revoked
    token is rejected, not silently re-activated."""
    app, engine, settings = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)
        token = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_01",
            delegated_kid="org_a-agent01-k1",
        )
        first = await client.post(
            f"/organizations/{ORG}/delegations",
            json={"jws": token},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        jti = first.json()["jti"]
        await client.post(
            f"/organizations/{ORG}/delegations/{jti}/revoke",
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )

        republish = await client.post(
            f"/organizations/{ORG}/delegations",
            json={"jws": token},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert republish.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_delegated_kid_colliding_with_root_kid_rejected() -> None:
    app, engine, settings = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="shared-kid-01", public_key=root_public)
        token = _build_token(
            root_private_key=root_private,
            root_kid="shared-kid-01",
            agent_id="agent_buyer_01",
            delegated_kid="shared-kid-01",  # collides with the root kid
        )
        response = await client.post(
            f"/organizations/{ORG}/delegations",
            json={"jws": token},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert response.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_cross_agent_thumbprint_reuse_rejected_sequentially() -> None:
    """ADR-017's organization-wide thumbprint exclusion: the same key
    material cannot be delegated to a second, different agent while
    the first delegation using it is still live."""
    app, engine, settings = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    _, shared_delegated_public = generate_signing_keypair()
    shared_jwk = public_key_to_jwk(shared_delegated_public, kid="shared-material")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)

        token_a = build_delegation(
            root_private_key=root_private,
            issuing_kid="org_a-root1",
            organization_id=ORG,
            agent_id="agent_buyer_01",
            audience=NETWORK_AUDIENCE,
            delegated_kid="org_a-agent01-k1",
            delegated_public_key_jwk=shared_jwk,
            scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
        )
        token_b = build_delegation(
            root_private_key=root_private,
            issuing_kid="org_a-root1",
            organization_id=ORG,
            agent_id="agent_buyer_02",
            audience=NETWORK_AUDIENCE,
            delegated_kid="org_a-agent02-k1",
            delegated_public_key_jwk=shared_jwk,
            scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
        )

        first = await client.post(
            f"/organizations/{ORG}/delegations",
            json={"jws": token_a},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert first.status_code == 200, first.text

        second = await client.post(
            f"/organizations/{ORG}/delegations",
            json={"jws": token_b},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert second.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_delegated_kid_publication_only_one_wins() -> None:
    """PostgreSQL-backed concurrency test: two concurrent delegation
    publications for the same (organization_id, delegated_kid)."""
    app, engine, settings = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)

        token_1 = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_01",
            delegated_kid="org_a-race-kid",
        )
        token_2 = _build_token(
            root_private_key=root_private,
            root_kid="org_a-root1",
            agent_id="agent_buyer_02",
            delegated_kid="org_a-race-kid",
        )
        headers = {"X-Admin-Key": settings.network_admin_api_key}
        results = await asyncio.gather(
            client.post(
                f"/organizations/{ORG}/delegations", json={"jws": token_1}, headers=headers
            ),
            client.post(
                f"/organizations/{ORG}/delegations", json={"jws": token_2}, headers=headers
            ),
        )
        statuses = sorted(r.status_code for r in results)
        assert statuses == [200, 409]
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_cross_agent_thumbprint_reuse_only_one_wins() -> None:
    """PostgreSQL-backed concurrency test: two concurrent, DIFFERENT-
    agent delegations sharing the same key material race for the
    organization-wide live-thumbprint slot."""
    app, engine, settings = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    _, shared_delegated_public = generate_signing_keypair()
    shared_jwk = public_key_to_jwk(shared_delegated_public, kid="shared-material-race")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-root1", public_key=root_public)
        token_a = build_delegation(
            root_private_key=root_private,
            issuing_kid="org_a-root1",
            organization_id=ORG,
            agent_id="agent_buyer_01",
            audience=NETWORK_AUDIENCE,
            delegated_kid="org_a-agent01-race",
            delegated_public_key_jwk=shared_jwk,
            scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
        )
        token_b = build_delegation(
            root_private_key=root_private,
            issuing_kid="org_a-root1",
            organization_id=ORG,
            agent_id="agent_buyer_02",
            audience=NETWORK_AUDIENCE,
            delegated_kid="org_a-agent02-race",
            delegated_public_key_jwk=shared_jwk,
            scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
        )
        headers = {"X-Admin-Key": settings.network_admin_api_key}
        results = await asyncio.gather(
            client.post(
                f"/organizations/{ORG}/delegations", json={"jws": token_a}, headers=headers
            ),
            client.post(
                f"/organizations/{ORG}/delegations", json={"jws": token_b}, headers=headers
            ),
        )
        statuses = sorted(r.status_code for r in results)
        assert statuses == [200, 409]
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_root_kid_rotation_vs_delegation_publication_root_wins() -> None:
    """PostgreSQL-backed, SYMMETRIC namespace-guard test, direction 1:
    a root-key rotation and a delegation publication race on the exact
    same kid value; whichever commits first wins the shared namespace
    row, the other gets a deterministic 409 — regardless of which side
    it is."""
    app, engine, settings = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # A separate, already-established root key, used to SIGN the
        # delegation attempt — distinct from the contested kid value.
        await _register_root_key(client, settings, kid="org_a-root-signer", public_key=root_public)

        contested_kid = "org_a-contested-kid"
        _, contested_delegated_public = generate_signing_keypair()
        contested_jwk = public_key_to_jwk(contested_delegated_public, kid=contested_kid)
        delegation_token = build_delegation(
            root_private_key=root_private,
            issuing_kid="org_a-root-signer",
            organization_id=ORG,
            agent_id="agent_buyer_01",
            audience=NETWORK_AUDIENCE,
            delegated_kid=contested_kid,
            delegated_public_key_jwk=contested_jwk,
            scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
        )
        _, another_root_public = generate_signing_keypair()
        another_root_jwk = public_key_to_jwk(another_root_public, kid=contested_kid)
        headers = {"X-Admin-Key": settings.network_admin_api_key}

        results = await asyncio.gather(
            client.post(
                f"/organizations/{ORG}/signing-keys",
                json={"kid": contested_kid, "public_key_jwk": another_root_jwk},
                headers=headers,
            ),
            client.post(
                f"/organizations/{ORG}/delegations",
                json={"jws": delegation_token},
                headers=headers,
            ),
        )
        statuses = sorted(r.status_code for r in results)
        assert statuses == [200, 409]
    await engine.dispose()
