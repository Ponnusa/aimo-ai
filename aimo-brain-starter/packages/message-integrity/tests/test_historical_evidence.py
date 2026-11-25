from __future__ import annotations

from datetime import datetime, timedelta

from message_integrity.evidence import EvidenceReceipt
from message_integrity.historical_evidence import KeyLifecycleFact, evaluate_historical_evidence

ISSUED_AT = datetime(2026, 1, 1)
REVOKED_AT = datetime(2026, 6, 1)


def _receipt(observed_at: datetime) -> EvidenceReceipt:
    return EvidenceReceipt(
        jti="jti-1",
        artifact_digest="digest-1",
        kid_used="kid-1",
        claimed_artifact_digest=None,
        claimed_kid=None,
        delegated_kid=None,
        organization_id="org_a",
        agent_id="agent_1",
        artifact_type="quote",
        attribution="organization",
        verification_outcome="accepted",
        rejection_reason=None,
        observed_at=observed_at,
    )


def test_invalid_signature_short_circuits_every_other_field():
    report = evaluate_historical_evidence(
        signature_valid=False,
        scope_in_scope_at_claimed=True,
        scope_in_scope_at_observed=True,
        audience_match_at_claimed=True,
        audience_match_at_observed=True,
        signer_asserted_iat=int(ISSUED_AT.timestamp()),
        lifecycle_facts=[],
        receipt=None,
    )
    assert report.signature_verification == "invalid"
    assert report.key_status_at_signer_claimed_time is None
    assert report.key_status_at_receiver_observed_time is None
    assert report.anchor_used == "none"
    assert report.registry_status_at_time == "unavailable"


def test_signer_claim_only_is_never_upgraded_to_receiver_observation():
    report = evaluate_historical_evidence(
        signature_valid=True,
        scope_in_scope_at_claimed=True,
        scope_in_scope_at_observed=None,
        audience_match_at_claimed=True,
        audience_match_at_observed=None,
        signer_asserted_iat=int(ISSUED_AT.timestamp()),
        lifecycle_facts=[
            KeyLifecycleFact(event_type="issued", effective_time=ISSUED_AT, recorded_at=ISSUED_AT)
        ],
        receipt=None,
    )
    assert report.anchor_used == "signer_claim_only"
    assert report.key_status_at_receiver_observed_time is None
    # A backdated iat with no independent receipt stays signer_claim_only
    # -- never promoted to receiver_observation just because a status
    # could technically be computed for that claimed time.
    assert report.key_status_at_signer_claimed_time is not None


def test_receiver_observation_anchor_used_when_a_receipt_exists():
    report = evaluate_historical_evidence(
        signature_valid=True,
        scope_in_scope_at_claimed=True,
        scope_in_scope_at_observed=True,
        audience_match_at_claimed=True,
        audience_match_at_observed=True,
        signer_asserted_iat=int(ISSUED_AT.timestamp()),
        lifecycle_facts=[
            KeyLifecycleFact(event_type="issued", effective_time=ISSUED_AT, recorded_at=ISSUED_AT)
        ],
        receipt=_receipt(observed_at=ISSUED_AT + timedelta(days=10)),
    )
    assert report.anchor_used == "receiver_observation"
    assert report.key_status_at_receiver_observed_time is not None
    assert report.key_status_at_receiver_observed_time.status == "active"


def test_key_revoked_before_observed_time_is_reported_revoked():
    report = evaluate_historical_evidence(
        signature_valid=True,
        scope_in_scope_at_claimed=True,
        scope_in_scope_at_observed=True,
        audience_match_at_claimed=True,
        audience_match_at_observed=True,
        signer_asserted_iat=int(ISSUED_AT.timestamp()),
        lifecycle_facts=[
            KeyLifecycleFact(event_type="issued", effective_time=ISSUED_AT, recorded_at=ISSUED_AT),
            KeyLifecycleFact(
                event_type="revoked", effective_time=REVOKED_AT, recorded_at=REVOKED_AT
            ),
        ],
        receipt=_receipt(observed_at=REVOKED_AT + timedelta(days=1)),
    )
    assert report.key_status_at_receiver_observed_time.status == "revoked"


def test_migration_snapshot_cannot_establish_status_before_its_own_recorded_time():
    migration_time = datetime(2026, 11, 26)
    report = evaluate_historical_evidence(
        signature_valid=True,
        scope_in_scope_at_claimed=True,
        scope_in_scope_at_observed=None,
        audience_match_at_claimed=True,
        audience_match_at_observed=None,
        signer_asserted_iat=int((migration_time - timedelta(days=365)).timestamp()),
        lifecycle_facts=[
            KeyLifecycleFact(
                event_type="migration_snapshot",
                effective_time=migration_time,
                recorded_at=migration_time,
            )
        ],
        receipt=None,
    )
    # A claimed signing time a year before the ONLY record (a migration
    # snapshot, which speaks only to current state at migration) must
    # never be answered "active" by assuming current state extends
    # backward -- it must be "unknown".
    assert report.key_status_at_signer_claimed_time.status == "unknown"


def test_backdated_iat_with_no_receipt_is_not_corroborated():
    """A signer claims iat before a revocation, but no evidence receipt
    exists to independently corroborate it -- the report must never
    claim this was observed, only that the signer claims it."""
    report = evaluate_historical_evidence(
        signature_valid=True,
        scope_in_scope_at_claimed=True,
        scope_in_scope_at_observed=None,
        audience_match_at_claimed=True,
        audience_match_at_observed=None,
        signer_asserted_iat=int((REVOKED_AT - timedelta(days=100)).timestamp()),
        lifecycle_facts=[
            KeyLifecycleFact(event_type="issued", effective_time=ISSUED_AT, recorded_at=ISSUED_AT),
            KeyLifecycleFact(
                event_type="revoked", effective_time=REVOKED_AT, recorded_at=REVOKED_AT
            ),
        ],
        receipt=None,
    )
    assert report.anchor_used == "signer_claim_only"
    assert report.key_status_at_receiver_observed_time is None
    assert report.key_status_at_signer_claimed_time.status == "active"
    # The claimed-time status is reported, but anchor_used tells any
    # caller it is not independently corroborated -- this is the
    # structural guard, not a suppressed field.
