"""M8 slice 3 merge review: GENUINELY interleaved PostgreSQL concurrency
tests for the symmetric root-vs-delegation kid-namespace guard and the
thumbprint-reservation-release-vs-publication race.

``asyncio.gather`` (used by ``test_delegations.py``'s probabilistic
tests) and plain sequential awaits (used by
``test_delegations_merge_review.py``'s "deterministic" tests, which are
in fact just ordered, not concurrent) do NOT prove the two transactions
were ever actually open on PostgreSQL at the same time -- either could
pass purely because one request's commit happened to finish before the
other's was even dispatched, which exercises the unique constraint
exactly as a non-concurrent test would. Slice 3 explicitly committed to
closing this; slice 2's own merge review accepting a probabilistic test
was not a waiver carried forward.

Each test here holds one transaction open with an uncommitted write
(via ``session.flush()``, which sends the INSERT/UPDATE to PostgreSQL
and takes its row/index lock, without committing), launches the
conflicting operation as a background task through the real HTTP app,
and proves that task is GENUINELY blocked -- not just slower -- by
asserting it does not complete within a short timeout while the first
transaction is still open. Only then does the test release the first
transaction (commit or rollback) and observe the second's outcome
deterministically. This exercises PostgreSQL's own lock manager, not
test-process scheduling.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from message_integrity import DelegationScope, build_delegation
from message_integrity.delegations import delegated_pubkey_thumbprint
from message_integrity.keys import generate_signing_keypair, public_key_to_jwk
from network_api.app import create_app
from network_api.models import (
    AgentSigningDelegationRecord,
    KeyLifecycleEventRecord,
    OrganizationKidNamespaceRecord,
    OrganizationRecord,
    OrganizationSigningKeyRecord,
)
from network_api.settings import get_settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Base, new_engine

ORG = "org_fictional_bistro_01"
NETWORK_AUDIENCE = "aimo-network:test"

#: How long to wait before concluding a task is genuinely blocked, not
#: merely slow. Long enough that ordinary request handling (even a slow
#: CI runner) would have finished well within it if it were not
#: actually waiting on a PostgreSQL lock.
_BLOCK_PROOF_TIMEOUT_SECONDS = 2.0


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


async def _assert_genuinely_blocked(task: asyncio.Task) -> None:
    """Proves ``task`` has NOT completed after a generous timeout,
    while some other transaction is still holding an open, uncommitted
    write that contends on the same row/index -- i.e. it is waiting on
    PostgreSQL's own lock manager, not merely running slowly.
    ``asyncio.shield`` so the timeout never cancels the underlying
    task; we still need its real result once the lock is released."""
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=_BLOCK_PROOF_TIMEOUT_SECONDS)
    assert not task.done(), "task completed despite the proof-of-blocking timeout firing"


@pytest.mark.asyncio
async def test_interleaved_root_lock_blocks_delegation_then_loses() -> None:
    """Direction 1, genuinely interleaved: a root-key registration's
    namespace-row insert is held open (uncommitted) while a conflicting
    delegation publication for the SAME kid is launched concurrently
    through the real app. The delegation request must be observed
    still pending -- not merely eventually rejected -- before the root
    transaction commits, proving real lock contention, not scheduling
    luck. Once the root commits, the blocked delegation deterministically
    loses (409), and the ledger shows exactly one ``issued`` event for
    the contested kid (the root's), never two, never zero."""
    app, engine, settings, session_factory = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    contested_kid = "org_a-interleaved-root-first"
    headers = {"X-Admin-Key": settings.network_admin_api_key}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-signer-i1", public_key=root_public)

        # Hold transaction A open: replicate register_signing_key's
        # FULL write set (the signing-key row, its namespace
        # reservation, and its ledger event -- the same three inserts
        # the real endpoint makes in one transaction) and flush it to
        # PostgreSQL (taking the lock) without committing -- this is
        # deliberately a raw write, not a call through the HTTP app, so
        # the test controls exactly when it commits, while still
        # producing the same rows a real call would, so the post-hoc
        # ledger assertion below is meaningful rather than vacuous.
        held_session = session_factory()
        now = datetime.now(UTC).replace(tzinfo=None)
        _, contested_root_public = generate_signing_keypair()
        contested_root_jwk = public_key_to_jwk(contested_root_public, kid=contested_kid)

        held_session.add(
            OrganizationSigningKeyRecord(
                organization_id=ORG,
                kid=contested_kid,
                public_key_jwk_json=json.dumps(contested_root_jwk),
                status="active",
                not_before=now,
                not_after=now + timedelta(days=365),
                created_at=now,
                revoked_at=None,
            )
        )
        held_session.add(
            OrganizationKidNamespaceRecord(
                organization_id=ORG, kid=contested_kid, kind="root", created_at=now
            )
        )
        held_session.add(
            KeyLifecycleEventRecord(
                organization_id=ORG,
                kid=contested_kid,
                kind="root",
                event_type="issued",
                effective_time=now,
                recorded_at=now,
                key_material=json.dumps(contested_root_jwk),
                is_reconstructed_snapshot=False,
            )
        )
        await held_session.flush()

        try:
            _, delegated_public = generate_signing_keypair()
            delegated_jwk = public_key_to_jwk(delegated_public, kid=contested_kid)
            delegation_token = build_delegation(
                root_private_key=root_private,
                issuing_kid="org_a-signer-i1",
                organization_id=ORG,
                agent_id="agent_interleaved_01",
                audience=NETWORK_AUDIENCE,
                delegated_kid=contested_kid,
                delegated_public_key_jwk=delegated_jwk,
                scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
            )
            delegation_task = asyncio.create_task(
                client.post(
                    f"/organizations/{ORG}/delegations",
                    json={"jws": delegation_token},
                    headers=headers,
                )
            )

            # Proof of genuine contention: the delegation publish must
            # still be pending while transaction A is open.
            await _assert_genuinely_blocked(delegation_task)

            # Release A -- it wins, since it committed first.
            await held_session.commit()

            delegation_response = await delegation_task
            assert delegation_response.status_code == 409, delegation_response.text
        finally:
            await held_session.close()

        async with session_factory() as verify_session:
            events = (
                (
                    await verify_session.execute(
                        select(KeyLifecycleEventRecord).where(
                            KeyLifecycleEventRecord.organization_id == ORG,
                            KeyLifecycleEventRecord.kid == contested_kid,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(events) == 1, (
                f"expected exactly one ledger event for the contested kid "
                f"(the winning root), found {len(events)}"
            )
            assert events[0].kind == "root"
            assert events[0].event_type == "issued"

            delegation_rows = (
                (
                    await verify_session.execute(
                        select(AgentSigningDelegationRecord).where(
                            AgentSigningDelegationRecord.organization_id == ORG,
                            AgentSigningDelegationRecord.delegated_kid == contested_kid,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(delegation_rows) == 0, "the losing delegation must not have left any row"
    await engine.dispose()


@pytest.mark.asyncio
async def test_interleaved_delegation_lock_blocks_root_then_loses() -> None:
    """Direction 2 (the opposite ordering), genuinely interleaved: a
    delegation publication's namespace-row insert is held open while a
    conflicting root-key registration for the SAME kid is launched
    concurrently. Proves the guard is symmetric under real contention,
    not just under whichever write path happens to be dispatched
    first."""
    app, engine, settings, session_factory = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    contested_kid = "org_a-interleaved-delegation-first"
    headers = {"X-Admin-Key": settings.network_admin_api_key}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-signer-i2", public_key=root_public)

        # Hold transaction A open: replicate publish_delegation's FULL
        # write set (the delegation row, its namespace reservation, and
        # its ledger event) so the post-hoc ledger assertion below is
        # meaningful, mirroring direction 1's fix above.
        held_session = session_factory()
        now = datetime.now(UTC).replace(tzinfo=None)
        _, contested_delegated_public = generate_signing_keypair()
        contested_delegated_jwk = public_key_to_jwk(contested_delegated_public, kid=contested_kid)
        held_session.add(
            AgentSigningDelegationRecord(
                organization_id=ORG,
                agent_id="agent_interleaved_02",
                delegated_kid=contested_kid,
                delegated_pubkey_jwk_json=json.dumps(contested_delegated_jwk),
                pubkey_thumbprint=delegated_pubkey_thumbprint(contested_delegated_jwk),
                issuing_kid="org_a-signer-i2",
                audience=NETWORK_AUDIENCE,
                scope_json=json.dumps({"artifact_types": ["quote"], "protocol_version": "v1"}),
                jti="held-delegation-jti-direction-2",
                delegation_jws="unused-for-this-held-raw-insert",
                not_before=now,
                not_after=now + timedelta(days=30),
                created_at=now,
                revoked_at=None,
                thumbprint_reservation_released_at=None,
            )
        )
        held_session.add(
            OrganizationKidNamespaceRecord(
                organization_id=ORG, kid=contested_kid, kind="delegated", created_at=now
            )
        )
        held_session.add(
            KeyLifecycleEventRecord(
                organization_id=ORG,
                kid=contested_kid,
                kind="delegated",
                event_type="issued",
                effective_time=now,
                recorded_at=now,
                key_material="unused-for-this-held-raw-insert",
                is_reconstructed_snapshot=False,
            )
        )
        await held_session.flush()

        try:
            _, another_root_public = generate_signing_keypair()
            another_root_jwk = public_key_to_jwk(another_root_public, kid=contested_kid)
            root_task = asyncio.create_task(
                client.post(
                    f"/organizations/{ORG}/signing-keys",
                    json={"kid": contested_kid, "public_key_jwk": another_root_jwk},
                    headers=headers,
                )
            )

            await _assert_genuinely_blocked(root_task)

            await held_session.commit()

            root_response = await root_task
            assert root_response.status_code == 409, root_response.text
        finally:
            await held_session.close()

        async with session_factory() as verify_session:
            events = (
                (
                    await verify_session.execute(
                        select(KeyLifecycleEventRecord).where(
                            KeyLifecycleEventRecord.organization_id == ORG,
                            KeyLifecycleEventRecord.kid == contested_kid,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(events) == 1, (
                f"expected exactly one ledger event for the contested kid "
                f"(the winning delegation), found {len(events)}"
            )
            assert events[0].kind == "delegated"
            assert events[0].event_type == "issued"

            root_key_rows = (
                (
                    await verify_session.execute(
                        select(OrganizationSigningKeyRecord).where(
                            OrganizationSigningKeyRecord.organization_id == ORG,
                            OrganizationSigningKeyRecord.kid == contested_kid,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(root_key_rows) == 0, (
                "the losing root-key rotation must not have left any row"
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_interleaved_namespace_rollback_unblocks_the_waiting_transaction() -> None:
    """Rollback behavior, genuinely interleaved: if the transaction
    holding the contested kid's namespace row ROLLS BACK instead of
    committing, the previously-blocked transaction must be released and
    allowed to proceed (and succeed) -- never left permanently stuck
    waiting on a lock that will now never be released by a commit."""
    app, engine, settings, session_factory = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    contested_kid = "org_a-interleaved-rollback"
    headers = {"X-Admin-Key": settings.network_admin_api_key}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-signer-i3", public_key=root_public)

        held_session = session_factory()
        now = datetime.now(UTC).replace(tzinfo=None)
        held_session.add(
            OrganizationKidNamespaceRecord(
                organization_id=ORG, kid=contested_kid, kind="root", created_at=now
            )
        )
        await held_session.flush()

        try:
            _, delegated_public = generate_signing_keypair()
            delegated_jwk = public_key_to_jwk(delegated_public, kid=contested_kid)
            delegation_token = build_delegation(
                root_private_key=root_private,
                issuing_kid="org_a-signer-i3",
                organization_id=ORG,
                agent_id="agent_interleaved_rb",
                audience=NETWORK_AUDIENCE,
                delegated_kid=contested_kid,
                delegated_public_key_jwk=delegated_jwk,
                scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
            )
            delegation_task = asyncio.create_task(
                client.post(
                    f"/organizations/{ORG}/delegations",
                    json={"jws": delegation_token},
                    headers=headers,
                )
            )

            await _assert_genuinely_blocked(delegation_task)

            # The holder rolls back instead of committing -- its
            # reservation never actually lands.
            await held_session.rollback()

            # The previously-blocked delegation must now be released and
            # succeed, not hang forever waiting on a lock the rollback
            # just released without ever satisfying.
            delegation_response = await asyncio.wait_for(delegation_task, timeout=5.0)
            assert delegation_response.status_code == 200, delegation_response.text
        finally:
            await held_session.close()

        async with session_factory() as verify_session:
            events = (
                (
                    await verify_session.execute(
                        select(KeyLifecycleEventRecord).where(
                            KeyLifecycleEventRecord.organization_id == ORG,
                            KeyLifecycleEventRecord.kid == contested_kid,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(events) == 1
            assert events[0].kind == "delegated"
            assert events[0].event_type == "issued"
    await engine.dispose()


@pytest.mark.asyncio
async def test_interleaved_thumbprint_release_vs_new_publication() -> None:
    """Genuinely interleaved coverage of invariant B's reservation-
    release-vs-publication race: an existing, already-past-grace
    delegation's reservation-release UPDATE is held open (uncommitted)
    while a brand-new delegation publication for a DIFFERENT agent,
    reusing the exact same key material (thumbprint), is launched
    concurrently. The new publication must be observed genuinely
    pending while the release is still uncommitted -- proving it is
    waiting on PostgreSQL's own partial-unique-index enforcement, not
    merely slow -- and only succeeds once the release actually commits,
    at which point exactly one *live* row for that thumbprint exists."""
    app, engine, settings, session_factory = await _fresh_app_with_org()
    transport = ASGITransport(app=app)
    root_private, root_public = generate_signing_keypair()
    headers = {"X-Admin-Key": settings.network_admin_api_key}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _register_root_key(client, settings, kid="org_a-signer-i4", public_key=root_public)

        _, shared_delegated_public = generate_signing_keypair()
        shared_jwk = public_key_to_jwk(shared_delegated_public, kid="org_a-i4-original-kid")

        # Directly insert an existing delegation row that is already
        # well past its own not_after plus grace period, so the release
        # endpoint's own fail-closed check would allow release -- using
        # a raw insert (not the real publish flow) specifically so the
        # test controls not_after/not_before precisely.
        long_past = datetime(2020, 1, 1, tzinfo=UTC).replace(tzinfo=None)
        async with session_factory() as setup_session:
            existing = AgentSigningDelegationRecord(
                organization_id=ORG,
                agent_id="agent_original_holder",
                delegated_kid="org_a-i4-original-kid",
                delegated_pubkey_jwk_json=json.dumps(shared_jwk),
                pubkey_thumbprint=delegated_pubkey_thumbprint(shared_jwk),
                issuing_kid="org_a-signer-i4",
                audience=NETWORK_AUDIENCE,
                scope_json=json.dumps({"artifact_types": ["quote"], "protocol_version": "v1"}),
                jti="original-jti-i4",
                delegation_jws="unused-for-this-raw-insert",
                not_before=long_past,
                not_after=long_past,
                created_at=long_past,
                revoked_at=None,
                thumbprint_reservation_released_at=None,
            )
            setup_session.add(existing)
            await setup_session.commit()
            existing_id = existing.id

        # Hold the release transaction open: load the row, set the
        # release timestamp, flush (takes the index-relevant lock),
        # but do not commit yet.
        held_session = session_factory()
        existing_row = await held_session.get(AgentSigningDelegationRecord, existing_id)
        assert existing_row is not None
        release_time = datetime.now(UTC).replace(tzinfo=None)
        existing_row.thumbprint_reservation_released_at = release_time
        await held_session.flush()

        try:
            new_delegation_token = build_delegation(
                root_private_key=root_private,
                issuing_kid="org_a-signer-i4",
                organization_id=ORG,
                agent_id="agent_new_claimant",
                audience=NETWORK_AUDIENCE,
                delegated_kid="org_a-i4-new-kid",
                delegated_public_key_jwk=shared_jwk,
                scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
            )
            publish_task = asyncio.create_task(
                client.post(
                    f"/organizations/{ORG}/delegations",
                    json={"jws": new_delegation_token},
                    headers=headers,
                )
            )

            await _assert_genuinely_blocked(publish_task)

            await held_session.commit()

            publish_response = await asyncio.wait_for(publish_task, timeout=5.0)
            assert publish_response.status_code == 200, publish_response.text
        finally:
            await held_session.close()

        async with session_factory() as verify_session:
            thumbprint = delegated_pubkey_thumbprint(shared_jwk)
            rows = (
                (
                    await verify_session.execute(
                        select(AgentSigningDelegationRecord).where(
                            AgentSigningDelegationRecord.organization_id == ORG,
                            AgentSigningDelegationRecord.pubkey_thumbprint == thumbprint,
                        )
                    )
                )
                .scalars()
                .all()
            )
            live_rows = [
                r
                for r in rows
                if r.revoked_at is None and r.thumbprint_reservation_released_at is None
            ]
            assert len(live_rows) == 1, (
                f"exactly one live row should hold this thumbprint after the "
                f"release committed and the new publication succeeded; "
                f"found {len(live_rows)} of {len(rows)} total rows"
            )
            assert live_rows[0].agent_id == "agent_new_claimant"
    await engine.dispose()
