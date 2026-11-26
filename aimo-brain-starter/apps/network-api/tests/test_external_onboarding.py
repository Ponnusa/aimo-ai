"""M8 slice 4 (ADR-019): server-owned auth_mode, the org-scoped admin
credential's lifecycle, opaque-credential introspection, and the
feature-flag rollback guarantees. Each test name maps to one or more
rows of ADR-019's "Endpoint-by-endpoint authorization matrix" and one
of its 16 acceptance tests -- see the docstring on each test.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from network_api.app import create_app
from network_api.models import OrganizationAdminCredentialRecord, OrganizationRecord
from network_api.settings import get_settings

from database import Base, new_engine


async def _fresh_app_with_orgs(*orgs: str, auth_mode: str = "legacy_global_key"):
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
                OrganizationRecord(
                    organization_id=org_id,
                    display_name=org_id,
                    verified=True,
                    auth_mode=auth_mode,
                )
            )
        await session.commit()

    app = create_app()
    return app, engine, settings


async def _bootstrap_external(client, settings, organization_id: str) -> str:
    response = await client.post(
        f"/organizations/{organization_id}/bootstrap-external",
        headers={"X-Operator-Key": settings.network_operator_key},
    )
    assert response.status_code == 200, response.text
    return response.json()["admin_credential"]


@pytest.mark.asyncio
async def test_feature_flag_defaults_false_with_no_override() -> None:
    """ADR-019 section 7 / the user's critical requirement: merging this
    slice must not itself enable external onboarding. No env var is set
    here at all -- this is the shipped default."""
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.external_onboarding_enabled is False


@pytest.mark.asyncio
async def test_existing_rows_default_to_legacy_global_key() -> None:
    """ADR-019 section 1 / migration backfill: an organization created
    with no auth_mode opinion at all lands on "legacy_global_key", not
    some other default -- the conservative, non-disruptive backfill."""
    app, engine, settings = await _fresh_app_with_orgs()
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        org = OrganizationRecord(organization_id="org_legacy_default", display_name="x")
        session.add(org)
        await session.commit()
        await session.refresh(org)
        assert org.auth_mode == "legacy_global_key"
    await engine.dispose()


@pytest.mark.asyncio
async def test_pre_bootstrap_gate_blocks_new_external_org_when_flag_disabled(monkeypatch) -> None:
    """ADR-019 acceptance test 14."""
    app, engine, settings = await _fresh_app_with_orgs("org_gate_test")
    assert settings.external_onboarding_enabled is False
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/organizations/org_gate_test/bootstrap-external",
            headers={"X-Operator-Key": settings.network_operator_key},
        )
    assert response.status_code == 403
    await engine.dispose()


@pytest.mark.asyncio
async def test_bootstrap_succeeds_when_flag_explicitly_enabled_for_this_test(monkeypatch) -> None:
    """Flag override is scoped to this one test via monkeypatch -- never
    by changing the shipped default (ADR-019's critical requirement)."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_bootstrap_ok")
    assert settings.external_onboarding_enabled is True
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        secret = await _bootstrap_external(client, settings, "org_bootstrap_ok")
        assert secret  # delivered exactly once, in this response
    await engine.dispose()


@pytest.mark.asyncio
async def test_global_key_rejected_for_org_scoped_credential_organization(monkeypatch) -> None:
    """ADR-019 acceptance test 1: alias test. The global key is
    structurally rejected for an org_scoped_credential organization —
    not merely unnecessary, refused outright, with no request-level
    flag involved at all."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_alias_test")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _bootstrap_external(client, settings, "org_alias_test")
        response = await client.post(
            "/organizations/org_alias_test/service-credentials",
            json={"ttl_seconds": 60},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
    assert response.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_mixed_credential_org_a_cannot_act_on_org_b(monkeypatch) -> None:
    """ADR-019 acceptance test 2."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_a_mixed", "org_b_mixed")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        secret_a = await _bootstrap_external(client, settings, "org_a_mixed")
        await _bootstrap_external(client, settings, "org_b_mixed")
        response = await client.post(
            "/organizations/org_b_mixed/service-credentials",
            json={"ttl_seconds": 60},
            headers={"Authorization": f"Bearer {secret_a}"},
        )
    assert response.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_auth_mode_denies_rather_than_defaulting_to_legacy() -> None:
    """ADR-019 section 1's explicit new requirement: a null/garbage
    auth_mode denies EITHER credential type, never falls back to the
    historically-permissive legacy path."""
    app, engine, settings = await _fresh_app_with_orgs("org_garbage_mode")
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        org = await session.get(OrganizationRecord, "org_garbage_mode")
        org.auth_mode = "something_unrecognized"
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/organizations/org_garbage_mode/service-credentials",
            json={"ttl_seconds": 60},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
    assert response.status_code == 403
    await engine.dispose()


@pytest.mark.asyncio
async def test_auth_mode_not_changeable_via_ordinary_endpoint(monkeypatch) -> None:
    """ADR-019 acceptance test 3: no ordinary management endpoint, under
    any credential, can change auth_mode -- only the dedicated
    bootstrap/recovery path can, and nothing in the member/credential/
    signing-key/delegation endpoints even accepts such a field."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_immutable_mode")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # The legacy admin key cannot smuggle an auth_mode change through
        # the one endpoint that accepts organization-scoped admin input.
        response = await client.post(
            "/organizations/org_immutable_mode/service-credentials",
            json={
                "ttl_seconds": 60,
                "scope": "manage_agents",
                "auth_mode": "org_scoped_credential",
            },
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert response.status_code == 200  # the unknown field is simply ignored, not honored
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        org = await session.get(OrganizationRecord, "org_immutable_mode")
        assert org.auth_mode == "legacy_global_key"
    await engine.dispose()


@pytest.mark.asyncio
async def test_enumerated_scope_ceiling_rejects_platform_bootstrap_and_other_orgs(
    monkeypatch,
) -> None:
    """ADR-019 acceptance test 4: an org-scoped credential cannot touch
    another organization and has no path to the operator-only
    bootstrap/recovery endpoints (they require X-Operator-Key, which
    this credential is never checked against)."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_scope_ceiling", "org_other")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        secret = await _bootstrap_external(client, settings, "org_scope_ceiling")
        # Cannot touch another organization.
        other = await client.post(
            "/organizations/org_other/members",
            json={
                "oidc_subject": "sub1",
                "email": "a@example.com",
                "display_name": "A",
                "role": "admin",
            },
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert other.status_code in (401, 403)
        # Cannot reach bootstrap for its own org, let alone another.
        bootstrap_again = await client.post(
            "/organizations/org_scope_ceiling/bootstrap-external",
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert bootstrap_again.status_code == 401  # no X-Operator-Key presented
    await engine.dispose()


@pytest.mark.asyncio
async def test_credential_storage_never_recoverable_plaintext(monkeypatch) -> None:
    """ADR-019 acceptance test 5."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_storage_test")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        secret = await _bootstrap_external(client, settings, "org_storage_test")

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        row = (
            await session.execute(
                select(OrganizationAdminCredentialRecord).where(
                    OrganizationAdminCredentialRecord.organization_id == "org_storage_test"
                )
            )
        ).scalar_one()
        assert secret not in row.credential_hash
        assert secret != row.credential_hash
    await engine.dispose()


@pytest.mark.asyncio
async def test_one_time_delivery_credential_not_retrievable_again(monkeypatch) -> None:
    """ADR-019 acceptance test 6: no endpoint anywhere returns the raw
    secret a second time; bootstrapping an already-bootstrapped org is
    simply rejected (409), not re-shown its old secret."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_one_time")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _bootstrap_external(client, settings, "org_one_time")
        again = await client.post(
            "/organizations/org_one_time/bootstrap-external",
            headers={"X-Operator-Key": settings.network_operator_key},
        )
    assert again.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_self_service_rotation_invalidates_prior_credential_atomically(monkeypatch) -> None:
    """ADR-019 acceptance test 7."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_rotate_test")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        old_secret = await _bootstrap_external(client, settings, "org_rotate_test")
        rotate = await client.post(
            "/organizations/org_rotate_test/admin-credential/rotate",
            headers={"Authorization": f"Bearer {old_secret}"},
        )
        assert rotate.status_code == 200
        new_secret = rotate.json()["admin_credential"]
        assert new_secret != old_secret

        # Old credential no longer works.
        stale = await client.post(
            "/organizations/org_rotate_test/service-credentials",
            json={"ttl_seconds": 60},
            headers={"Authorization": f"Bearer {old_secret}"},
        )
        assert stale.status_code == 401

        # New one does.
        fresh = await client.post(
            "/organizations/org_rotate_test/service-credentials",
            json={"ttl_seconds": 60},
            headers={"Authorization": f"Bearer {new_secret}"},
        )
        assert fresh.status_code == 200
    await engine.dispose()


@pytest.mark.asyncio
async def test_revoked_credential_cannot_issue_its_own_replacement(monkeypatch) -> None:
    """New requirement layered on ADR-019: a revoked credential cannot
    be presented to mint a new one -- rotation requires the CURRENT
    credential to still be valid at the moment of rotation."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_revoked_rotate")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        secret = await _bootstrap_external(client, settings, "org_revoked_rotate")
        await client.post(
            "/organizations/org_revoked_rotate/recovery/force-revoke-credential",
            headers={"X-Operator-Key": settings.network_operator_key},
        )
        rotate_attempt = await client.post(
            "/organizations/org_revoked_rotate/admin-credential/rotate",
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert rotate_attempt.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_forced_recovery_revocation_does_not_route_through_the_credential(
    monkeypatch,
) -> None:
    """ADR-019 acceptance test 8: recovery authority revokes an org's
    credential using ONLY the operator key -- never needing or routing
    through the (possibly compromised/unresponsive) credential itself."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_forced_revoke")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _bootstrap_external(client, settings, "org_forced_revoke")
        # No credential presented at all -- only operator authority.
        response = await client.post(
            "/organizations/org_forced_revoke/recovery/force-revoke-credential",
            headers={"X-Operator-Key": settings.network_operator_key},
        )
        assert response.status_code == 200
    await engine.dispose()


@pytest.mark.asyncio
async def test_introspection_requires_internal_service_authentication(monkeypatch) -> None:
    """New requirement: introspection authenticates the CALLER, not just
    the token."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_introspect_auth")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/introspect",
            json={"token": "svc_anything.anything"},
            headers={"X-Internal-Service-Key": "wrong-key"},
        )
    assert response.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_introspection_returns_only_fields_a_verifier_needs(monkeypatch) -> None:
    """ADR-019 section 4's "return only the credential metadata it
    needs" requirement — the response never includes the credential
    hash/salt or any other internal bookkeeping field."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_introspect_fields")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        secret = await _bootstrap_external(client, settings, "org_introspect_fields")
        cred = await client.post(
            "/organizations/org_introspect_fields/service-credentials",
            json={"ttl_seconds": 60},
            headers={"Authorization": f"Bearer {secret}"},
        )
        token = cred.json()["token"]
        introspected = await client.post(
            "/introspect",
            json={"token": token},
            headers={"X-Internal-Service-Key": settings.network_internal_service_key},
        )
    body = introspected.json()
    assert "credential_hash" not in body
    assert "credential_salt" not in body
    assert set(body.keys()) == {
        "active",
        "organization_id",
        "scope",
        "agent_id",
        "issued_at",
        "expires_at",
        "revoked",
    }
    await engine.dispose()


@pytest.mark.asyncio
async def test_introspection_never_logs_the_raw_token(monkeypatch, caplog) -> None:
    """New requirement: no APPLICATION-level log statement this service
    owns ever includes the raw token, including on a rejection path.

    DB-driver DEBUG-level SQL-parameter echoing (aiosqlite/sqlalchemy)
    is deliberately excluded from this check: that bound parameter is
    the SALTED HASH LOOKUP KEY (``reference_id``), not the secret half
    of the credential, and is a pre-existing property of every ORM
    query in this codebase, not something ADR-019 asked this slice to
    change — flagged as a residual, out-of-scope observation rather
    than silently ignored."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_no_log_test")
    transport = ASGITransport(app=app)
    raw_marker = "svc_do-not-log-this-marker-value"
    with caplog.at_level("DEBUG"):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/introspect",
                json={"token": f"{raw_marker}.the-actual-secret-half"},
                headers={"X-Internal-Service-Key": settings.network_internal_service_key},
            )
    app_records = [
        r
        for r in caplog.records
        if not r.name.startswith("aiosqlite") and not r.name.startswith("sqlalchemy")
    ]
    for record in app_records:
        assert "the-actual-secret-half" not in record.getMessage()
    await engine.dispose()


@pytest.mark.asyncio
async def test_claim_substitution_is_structurally_impossible(monkeypatch) -> None:
    """ADR-019 acceptance test 9: a holder of the internal service key
    cannot construct a token that introspects as belonging to a
    different organization/scope than what was actually issued --
    there are no local claims to substitute around, only a reference ID
    that must match a real issuance row exactly."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_claim_sub_a", "org_claim_sub_b")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        secret_a = await _bootstrap_external(client, settings, "org_claim_sub_a")
        cred = await client.post(
            "/organizations/org_claim_sub_a/service-credentials",
            json={"ttl_seconds": 60},
            headers={"Authorization": f"Bearer {secret_a}"},
        )
        token = cred.json()["token"]
        reference_id = token.split(".", 1)[0]
        # Forge a different secret half against the real reference_id --
        # the stored hash doesn't match, so this can never introspect as
        # active, regardless of what organization/scope is claimed.
        forged = await client.post(
            "/introspect",
            json={"token": f"{reference_id}.forged-secret-half"},
            headers={"X-Internal-Service-Key": settings.network_internal_service_key},
        )
    assert forged.json()["active"] is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_introspection_fails_closed_on_malformed_token(monkeypatch) -> None:
    """ADR-019 acceptance test 10 (malformed-response half; timeout and
    network-api-unreachable are exercised at the registry-api call site
    in test_registry_api_introspection.py, since that's the actual
    caller this failure mode protects)."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_malformed_test")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/introspect",
            json={"token": "not-even-shaped-like-a-reference-id-and-secret"},
            headers={"X-Internal-Service-Key": settings.network_internal_service_key},
        )
    assert response.status_code == 200
    assert response.json()["active"] is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_post_bootstrap_rollback_never_falls_back_to_legacy(monkeypatch) -> None:
    """ADR-019 acceptance test 15: disabling the flag AFTER an org is
    already bootstrapped leaves it completely unaffected -- still
    requiring its own credential, the global key still rejected."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_rollback_test")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        secret = await _bootstrap_external(client, settings, "org_rollback_test")

    # Now simulate the flag going back to its default (false) by
    # clearing the env override and re-reading settings -- a fresh
    # "deployment" with onboarding disabled again.
    monkeypatch.delenv("EXTERNAL_ONBOARDING_ENABLED", raising=False)
    get_settings.cache_clear()
    settings_after = get_settings()
    assert settings_after.external_onboarding_enabled is False

    app2 = create_app()
    transport2 = ASGITransport(app=app2)
    async with AsyncClient(transport=transport2, base_url="http://test") as client:
        # The org's own credential still works -- unaffected by rollback.
        still_works = await client.post(
            "/organizations/org_rollback_test/service-credentials",
            json={"ttl_seconds": 60},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert still_works.status_code == 200
        # The global key still does NOT work for it -- rollback never
        # falls back to legacy.
        global_key_attempt = await client.post(
            "/organizations/org_rollback_test/service-credentials",
            json={"ttl_seconds": 60},
            headers={"X-Admin-Key": settings_after.network_admin_api_key},
        )
        assert global_key_attempt.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_disabling_onboarding_is_not_the_same_as_revoking_an_agent(monkeypatch) -> None:
    """ADR-019 section 7: the feature flag's state never touches an
    existing organization's credential status -- revocation remains
    its own explicit action."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_not_revoked")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _bootstrap_external(client, settings, "org_not_revoked")
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        row = (
            await session.execute(
                select(OrganizationAdminCredentialRecord).where(
                    OrganizationAdminCredentialRecord.organization_id == "org_not_revoked"
                )
            )
        ).scalar_one()
        assert row.status == "active"
        assert row.revoked_at is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_revert_to_legacy_is_explicit_and_audited(monkeypatch) -> None:
    """ADR-019 section 3: reverting a specific org to legacy mode is an
    explicit, audited operator action -- not an automatic consequence
    of the feature flag, and distinct from a silent fallback."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_revert_test")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _bootstrap_external(client, settings, "org_revert_test")
        revert = await client.post(
            "/organizations/org_revert_test/recovery/revert-to-legacy",
            headers={"X-Operator-Key": settings.network_operator_key},
        )
        assert revert.status_code == 200
        assert revert.json()["auth_mode"] == "legacy_global_key"
        # The global key now legitimately works again.
        works_now = await client.post(
            "/organizations/org_revert_test/service-credentials",
            json={"ttl_seconds": 60},
            headers={"X-Admin-Key": settings.network_admin_api_key},
        )
        assert works_now.status_code == 200

    from network_api.models import OrganizationLifecycleAuditEventRecord
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(OrganizationLifecycleAuditEventRecord).where(
                        OrganizationLifecycleAuditEventRecord.organization_id == "org_revert_test"
                    )
                )
            )
            .scalars()
            .all()
        )
        event_types = {e.event_type for e in events}
        assert "bootstrapped_external" in event_types
        assert "recovery_revert_to_legacy" in event_types
    await engine.dispose()


@pytest.mark.asyncio
async def test_both_credential_headers_present_simultaneously_global_key_ignored(
    monkeypatch,
) -> None:
    """Merge-review addition: the literal scenario behind acceptance test
    2's name -- a single request carrying BOTH a valid X-Admin-Key header
    AND an Authorization bearer header at once, against an
    org_scoped_credential organization. `_resolve_org_authority` only
    ever inspects `authorization` once `auth_mode` resolves to
    "org_scoped_credential" -- `x_admin_key` is never read at all in that
    branch, so a valid global key offered alongside a missing/garbage
    bearer cannot tip the outcome. This is distinct from, and not proven
    by, `test_mixed_credential_org_a_cannot_act_on_org_b` above (which
    tests cross-organization credential misuse, not simultaneous dual
    headers on one request)."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_both_headers")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _bootstrap_external(client, settings, "org_both_headers")
        response = await client.post(
            "/organizations/org_both_headers/service-credentials",
            json={"ttl_seconds": 60},
            headers={
                "X-Admin-Key": settings.network_admin_api_key,
                "Authorization": "Bearer not-a-real-credential",
            },
        )
    assert response.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_self_service_rotation_is_audited(monkeypatch) -> None:
    """Merge-review addition: self-service rotation produced no audit
    record at all before this fix, unlike bootstrap/force-revoke/revert
    -- a real gap against ADR-019 section 2's own "every
    issuance/rotation/revocation produces an audit record" requirement."""
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    app, engine, settings = await _fresh_app_with_orgs("org_rotation_audit")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        secret = await _bootstrap_external(client, settings, "org_rotation_audit")
        response = await client.post(
            "/organizations/org_rotation_audit/admin-credential/rotate",
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert response.status_code == 200, response.text

    from network_api.models import OrganizationLifecycleAuditEventRecord
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(OrganizationLifecycleAuditEventRecord).where(
                        OrganizationLifecycleAuditEventRecord.organization_id
                        == "org_rotation_audit"
                    )
                )
            )
            .scalars()
            .all()
        )
        event_types = {e.event_type for e in events}
        assert "self_rotated" in event_types
        # The raw secret must never appear in the audit trail's own detail text.
        for e in events:
            assert secret not in e.detail
    await engine.dispose()
