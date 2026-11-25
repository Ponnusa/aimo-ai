"""Focused tests added during PR #2's merge-readiness review (M8 slice
2, ADR-017): wrong-organization delegation publication under a valid
global admin credential, the publish-time-vs-verification-time
audience distinction, deterministic (not merely probabilistic)
coverage of both namespace-race orderings, and the documented
expired-but-unrevoked thumbprint behavior.
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
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Base, new_engine

ORG_A = "org_fictional_bistro_01"
ORG_B = "org_fictional_wholesale_01"
NETWORK_AUDIENCE = "aimo-network:test"


async def _fresh_app_with_two_orgs():
    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.network_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await session.merge(
            OrganizationRecord(organization_id=ORG_A, display_name=ORG_A, verified=True)
        )
        await session.merge(
            OrganizationRecord(organization_id=ORG_B, display_name=ORG_B, verified=True)
        )
        await session.commit()

    app = create_app()
    return app, engine, settings


async def _register_root_key(client, settings, *, organization_id: str, kid: str, public_key):
    jwk = public_key_to_jwk(public_key, kid=kid)
    response = await client.post(
        f"/organizations/{organization_id}/signing-keys",
        json={"kid": kid, "public_key_jwk": jwk},
        headers={"X-Admin-Key": settings.network_admin_api_key},
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_wrong_organization_delegation_rejected_even_with_valid_admin_key() -> None:
    """A valid GLOBAL admin credential (the only publication auth this
    reference deployment has) must not be enough to make a delegation
    actually signed by a DIFFERENT organization's root key count as
    belonging to the organization named in the publication URL. The
    publication endpoint's own `verified.organization_id != organization_id`
    check (resolved from the token's cryptographically-verified `iss`,
    never from the URL alone) must catch this regardless of admin
    authority."""
    app, engine, settings = await _fresh_app_with_two_orgs()
    transport = ASGITransport(app=app)
    root_private_b, root_public_b = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(
            client, settings, organization_id=ORG_B, kid="org_b-root1", public_key=root_public_b
        )
        _, delegated_public = generate_signing_keypair()
        delegated_jwk = public_key_to_jwk(delegated_public, kid="org_b-agent01-k1")
        # Genuinely, cryptographically issued BY ORG B (signed with B's
        # own root key, iss=B) — but submitted to ORG A's publication
        # URL with a valid global admin credential.
        token_issued_by_b = build_delegation(
            root_private_key=root_private_b,
            issuing_kid="org_b-root1",
            organization_id=ORG_B,
            agent_id="agent_from_b",
            audience=NETWORK_AUDIENCE,
            delegated_kid="org_b-agent01-k1",
            delegated_public_key_jwk=delegated_jwk,
            scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
        )
        response = await client.post(
            f"/organizations/{ORG_A}/delegations",
            json={"jws": token_issued_by_b},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert response.status_code == 400, response.text
        assert "does not match" in response.json()["detail"]

        # Confirm it IS accepted when published to its own, correct
        # organization's URL — proving the rejection above is about the
        # org mismatch specifically, not some unrelated defect.
        correct = await client.post(
            f"/organizations/{ORG_B}/delegations",
            json={"jws": token_issued_by_b},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert correct.status_code == 200, correct.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_publication_accepts_any_audience_value_verification_is_where_it_is_checked() -> None:
    """ADR-017's own documented design: network-api's publish-time
    verification (`expected_audience=None`) accepts whatever `aud` the
    token itself declares — it has no prior expectation of what a
    future receiver's configured network identifier will be. The
    stronger, receiver-side audience check is covered separately by
    `packages/message-integrity/tests/test_delegated_verification.py
    ::test_wrong_network_audience_rejected`. This test exists so that
    distinction is asserted explicitly here too, not merely implied."""
    app, engine, settings = await _fresh_app_with_two_orgs()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(
            client, settings, organization_id=ORG_A, kid="org_a-root1", public_key=root_public
        )
        _, delegated_public = generate_signing_keypair()
        delegated_jwk = public_key_to_jwk(delegated_public, kid="org_a-agent01-k1")
        token_for_a_different_network = build_delegation(
            root_private_key=root_private,
            issuing_kid="org_a-root1",
            organization_id=ORG_A,
            agent_id="agent_01",
            audience="aimo-network:some-other-environment",
            delegated_kid="org_a-agent01-k1",
            delegated_public_key_jwk=delegated_jwk,
            scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
        )
        response = await client.post(
            f"/organizations/{ORG_A}/delegations",
            json={"jws": token_for_a_different_network},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert response.status_code == 200, response.text
        assert response.json()["audience"] == "aimo-network:some-other-environment"
    await engine.dispose()


@pytest.mark.asyncio
async def test_namespace_race_deterministic_root_first_then_delegation_loses() -> None:
    """Deterministic (not merely probabilistic) coverage of ONE
    ordering of the symmetric root-vs-delegation kid-namespace guard:
    the root-key rotation commits first, so the later delegation
    publication for the same kid value is the one that loses."""
    app, engine, settings = await _fresh_app_with_two_orgs()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    headers = {"X-Admin-Key": settings.network_admin_api_key}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(
            client, settings, organization_id=ORG_A, kid="org_a-signer", public_key=root_public
        )
        contested_kid = "org_a-contested-deterministic-1"

        _, another_root_public = generate_signing_keypair()
        another_root_jwk = public_key_to_jwk(another_root_public, kid=contested_kid)
        root_first = await client.post(
            f"/organizations/{ORG_A}/signing-keys",
            json={"kid": contested_kid, "public_key_jwk": another_root_jwk},
            headers=headers,
        )
        assert root_first.status_code == 200, root_first.text

        _, delegated_public = generate_signing_keypair()
        delegated_jwk = public_key_to_jwk(delegated_public, kid=contested_kid)
        delegation_token = build_delegation(
            root_private_key=root_private,
            issuing_kid="org_a-signer",
            organization_id=ORG_A,
            agent_id="agent_01",
            audience=NETWORK_AUDIENCE,
            delegated_kid=contested_kid,
            delegated_public_key_jwk=delegated_jwk,
            scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
        )
        delegation_second = await client.post(
            f"/organizations/{ORG_A}/delegations",
            json={"jws": delegation_token},
            headers=headers,
        )
        assert delegation_second.status_code == 409, delegation_second.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_namespace_race_deterministic_delegation_first_then_root_loses() -> None:
    """The opposite ordering: the delegation publication commits
    first, so the later root-key rotation attempting to reuse the same
    kid value is the one that loses — proving the guard is genuinely
    symmetric, not accidentally favoring whichever write path happens
    to run first in the existing probabilistic `asyncio.gather` test."""
    app, engine, settings = await _fresh_app_with_two_orgs()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    headers = {"X-Admin-Key": settings.network_admin_api_key}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(
            client, settings, organization_id=ORG_A, kid="org_a-signer2", public_key=root_public
        )
        contested_kid = "org_a-contested-deterministic-2"

        _, delegated_public = generate_signing_keypair()
        delegated_jwk = public_key_to_jwk(delegated_public, kid=contested_kid)
        delegation_token = build_delegation(
            root_private_key=root_private,
            issuing_kid="org_a-signer2",
            organization_id=ORG_A,
            agent_id="agent_01",
            audience=NETWORK_AUDIENCE,
            delegated_kid=contested_kid,
            delegated_public_key_jwk=delegated_jwk,
            scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
        )
        delegation_first = await client.post(
            f"/organizations/{ORG_A}/delegations",
            json={"jws": delegation_token},
            headers=headers,
        )
        assert delegation_first.status_code == 200, delegation_first.text

        _, another_root_public = generate_signing_keypair()
        another_root_jwk = public_key_to_jwk(another_root_public, kid=contested_kid)
        root_second = await client.post(
            f"/organizations/{ORG_A}/signing-keys",
            json={"kid": contested_kid, "public_key_jwk": another_root_jwk},
            headers=headers,
        )
        assert root_second.status_code == 409, root_second.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_but_unrevoked_thumbprint_blocks_reuse_until_explicit_revocation() -> None:
    """Documents the known, explicitly-stated deviation from ADR-017's
    "(unexpired, unrevoked)" phrasing: `uq_org_live_pubkey_thumbprint`
    is a partial index predicated only on `revoked_at IS NULL` (a
    static index predicate cannot reference the current time). This
    test exercises the CURRENT, intentional behavior — re-delegating
    the same key material to a different agent is blocked by an
    expired-but-never-revoked prior delegation — and documents it as a
    named, accepted gap rather than silently assuming it away. An
    operator who wants to free the slot early must explicitly revoke
    the expired delegation first; this test also proves that path
    works."""
    app, engine, settings = await _fresh_app_with_two_orgs()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    _, shared_public = generate_signing_keypair()
    shared_jwk = public_key_to_jwk(shared_public, kid="expired-material")
    headers = {"X-Admin-Key": settings.network_admin_api_key}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(
            client, settings, organization_id=ORG_A, kid="org_a-root1", public_key=root_public
        )

        # A 1-second validity window: published while still (barely)
        # valid, left to actually expire by wall-clock time before the
        # next step — a real, short sleep is the simplest honest way to
        # observe this without faking network-api's own wall-clock
        # `datetime.now(UTC)` filtering, which isn't injectable here.
        short_lived_token = build_delegation(
            root_private_key=root_private,
            issuing_kid="org_a-root1",
            organization_id=ORG_A,
            agent_id="agent_01",
            audience=NETWORK_AUDIENCE,
            delegated_kid="org_a-agent01-expired",
            delegated_public_key_jwk=shared_jwk,
            scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
            validity_seconds=1,
        )
        published = await client.post(
            f"/organizations/{ORG_A}/delegations",
            json={"jws": short_lived_token},
            headers=headers,
        )
        assert published.status_code == 200, published.text
        await asyncio.sleep(1.5)  # now genuinely expired, never revoked

        # A second, DIFFERENT agent tries to reuse the exact same key
        # material. Current, documented behavior: still rejected,
        # because the row is unrevoked even though it is expired.
        second_token = build_delegation(
            root_private_key=root_private,
            issuing_kid="org_a-root1",
            organization_id=ORG_A,
            agent_id="agent_02",
            audience=NETWORK_AUDIENCE,
            delegated_kid="org_a-agent02-reuse",
            delegated_public_key_jwk=shared_jwk,
            scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
        )
        blocked = await client.post(
            f"/organizations/{ORG_A}/delegations",
            json={"jws": second_token},
            headers=headers,
        )
        assert blocked.status_code == 409, (
            "documents the known gap: an expired-but-unrevoked thumbprint "
            "still occupies the organization-wide unique slot"
        )

        # Explicit revocation frees the slot, as ADR-017 describes.
        jti = published.json()["jti"]
        revoke = await client.post(
            f"/organizations/{ORG_A}/delegations/{jti}/revoke",
            headers=headers,
        )
        assert revoke.status_code == 200

        now_allowed = await client.post(
            f"/organizations/{ORG_A}/delegations",
            json={"jws": second_token},
            headers=headers,
        )
        assert now_allowed.status_code == 200, now_allowed.text
    await engine.dispose()
