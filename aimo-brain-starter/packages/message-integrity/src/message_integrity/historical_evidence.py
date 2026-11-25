"""Historical evidence reporting (M8 slice 3, ADR-018).

This module answers a DIFFERENT question than the rest of this package.
Everywhere else in ``message_integrity`` (``proofs``, ``delegated_verification``,
``jwks``, ``delegation_resolver``) answers Q1 -- "may this key authorize a
new action right now" -- against a LIVE, currently-valid view. This
module answers Q2 -- "what can we establish about an artifact signed in
the past" -- and is read-only, evidentiary, and structurally never
consulted by Q1. Nothing in ``proofs.py`` or ``delegated_verification.py``
imports this module, and this module never substitutes its own
judgment for a live check -- see the structural-separation test in
``test_historical_evidence_structural_separation.py``.

Three structural rules this module enforces, named here because they
were the point of the design review that preceded this code:

1. An invalid signature is reported invalid regardless of any timestamp
   evidence -- checked first, and nothing downstream can override it.
2. A signer-asserted ``iat`` is never treated as independently
   corroborated. A report whose only time anchor is the signer's own
   claim is always labeled ``"signer_claim_only"``, never upgraded to
   look like an independent observation.
3. Historical-context evidence (registry/relationship/policy/approval
   status "at the time") is reported "unavailable" whenever no real
   historical record exists for the requested time -- including, most
   importantly, for any time before a ``migration_snapshot`` ledger
   event. A migration snapshot describes CURRENT state observed at
   migration time; it is never usable to assert anything about an
   earlier point, no matter how tempting it is to assume "it was
   probably already like this."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from message_integrity.evidence import EvidenceReceipt

Status = Literal["active", "expired", "revoked", "unknown"]
ScopeCheck = Literal["in_scope", "out_of_scope", "not_applicable"]
AudienceCheck = Literal["match", "mismatch", "not_applicable"]
ContextEvidence = Literal["established", "contradicted", "unavailable"]
Anchor = Literal["receiver_observation", "signer_claim_only", "none"]


@dataclass(frozen=True)
class KeyStatusAtTime:
    status: Status
    scope_check: ScopeCheck
    audience_check: AudienceCheck


@dataclass(frozen=True)
class KeyLifecycleFact:
    """One ledger-derived fact about a key/delegation's lifecycle,
    supplied by the caller (which owns the actual ledger query -- this
    module is deliberately database-free, consistent with the rest of
    this package). ``migration_snapshot`` facts carry no information
    about any time before ``recorded_at`` and must never be treated as
    extending backward."""

    event_type: Literal[
        "issued", "rotating", "expired", "revoked", "reservation_released", "migration_snapshot"
    ]
    effective_time: datetime
    recorded_at: datetime
    not_before: datetime | None = None
    not_after: datetime | None = None


@dataclass(frozen=True)
class HistoricalEvidenceReport:
    signature_verification: Literal["valid", "invalid"]
    key_status_at_signer_claimed_time: KeyStatusAtTime | None  # None iff signature invalid
    key_status_at_receiver_observed_time: (
        KeyStatusAtTime | None
    )  # None iff no receipt / signature invalid
    signer_asserted_iat: int | None
    receiver_observed_at: datetime | None
    anchor_used: Anchor
    registry_status_at_time: ContextEvidence
    trading_relationship_at_time: ContextEvidence
    policy_version_at_time: ContextEvidence
    approval_evidence_at_time: ContextEvidence
    current_authorization_status: Literal["yes", "no", "not_requested"]


_NOT_APPLICABLE_KEY_STATUS = KeyStatusAtTime(
    status="unknown", scope_check="not_applicable", audience_check="not_applicable"
)


def _earliest_real_record_time(facts: list[KeyLifecycleFact]) -> datetime | None:
    """The earliest time any of ``facts`` can actually speak to --
    excludes migration-snapshot facts, whose ``recorded_at`` describes
    only present-at-migration state, never anything earlier."""
    real_times = [f.effective_time for f in facts if f.event_type != "migration_snapshot"]
    return min(real_times) if real_times else None


def _status_at(facts: list[KeyLifecycleFact], at_time: datetime | None) -> Status:
    if at_time is None:
        return "unknown"
    earliest = _earliest_real_record_time(facts)
    if earliest is not None and at_time < earliest:
        # No real (non-migration-snapshot) ledger record reaches this
        # far back -- never answered by assuming current/known state
        # extends backward.
        return "unknown"
    applicable = [
        f for f in facts if f.event_type != "migration_snapshot" and f.effective_time <= at_time
    ]
    if not applicable:
        return "unknown"
    latest = max(applicable, key=lambda f: f.effective_time)
    if latest.event_type == "revoked":
        return "revoked"
    if latest.event_type == "expired":
        return "expired"
    if latest.not_after is not None and at_time > latest.not_after:
        return "expired"
    return "active"


def evaluate_historical_evidence(
    *,
    signature_valid: bool,
    scope_in_scope_at_claimed: bool | None,
    scope_in_scope_at_observed: bool | None,
    audience_match_at_claimed: bool | None,
    audience_match_at_observed: bool | None,
    signer_asserted_iat: int | None,
    lifecycle_facts: list[KeyLifecycleFact],
    receipt: EvidenceReceipt | None,
    registry_status_at_time: ContextEvidence = "unavailable",
    trading_relationship_at_time: ContextEvidence = "unavailable",
    policy_version_at_time: ContextEvidence = "unavailable",
    approval_evidence_at_time: ContextEvidence = "unavailable",
    current_authorization_status: Literal["yes", "no", "not_requested"] = "not_requested",
) -> HistoricalEvidenceReport:
    """Pure function, no I/O: the caller (network-api or an agent, which
    actually owns the ledger/receipt/registry data) supplies everything
    this needs to look up, and this function only applies the
    evidentiary rules -- so those rules are tested once, here, rather
    than re-implemented at every call site."""
    if not signature_valid:
        return HistoricalEvidenceReport(
            signature_verification="invalid",
            key_status_at_signer_claimed_time=None,
            key_status_at_receiver_observed_time=None,
            signer_asserted_iat=signer_asserted_iat,
            receiver_observed_at=receipt.observed_at if receipt else None,
            anchor_used="none",
            registry_status_at_time="unavailable",
            trading_relationship_at_time="unavailable",
            policy_version_at_time="unavailable",
            approval_evidence_at_time="unavailable",
            current_authorization_status="not_requested",
        )

    claimed_time = (
        datetime.fromtimestamp(signer_asserted_iat) if signer_asserted_iat is not None else None
    )
    claimed_status = _status_at(lifecycle_facts, claimed_time)
    at_claimed = KeyStatusAtTime(
        status=claimed_status,
        scope_check=(
            "not_applicable"
            if scope_in_scope_at_claimed is None
            else ("in_scope" if scope_in_scope_at_claimed else "out_of_scope")
        ),
        audience_check=(
            "not_applicable"
            if audience_match_at_claimed is None
            else ("match" if audience_match_at_claimed else "mismatch")
        ),
    )

    observed_time = receipt.observed_at if receipt is not None else None
    if observed_time is not None:
        observed_status = _status_at(lifecycle_facts, observed_time)
        at_observed: KeyStatusAtTime | None = KeyStatusAtTime(
            status=observed_status,
            scope_check=(
                "not_applicable"
                if scope_in_scope_at_observed is None
                else ("in_scope" if scope_in_scope_at_observed else "out_of_scope")
            ),
            audience_check=(
                "not_applicable"
                if audience_match_at_observed is None
                else ("match" if audience_match_at_observed else "mismatch")
            ),
        )
        anchor: Anchor = "receiver_observation"
    else:
        at_observed = None
        anchor = "signer_claim_only" if claimed_time is not None else "none"

    return HistoricalEvidenceReport(
        signature_verification="valid",
        key_status_at_signer_claimed_time=at_claimed,
        key_status_at_receiver_observed_time=at_observed,
        signer_asserted_iat=signer_asserted_iat,
        receiver_observed_at=observed_time,
        anchor_used=anchor,
        registry_status_at_time=registry_status_at_time,
        trading_relationship_at_time=trading_relationship_at_time,
        policy_version_at_time=policy_version_at_time,
        approval_evidence_at_time=approval_evidence_at_time,
        current_authorization_status=current_authorization_status,
    )


__all__ = [
    "HistoricalEvidenceReport",
    "KeyLifecycleFact",
    "KeyStatusAtTime",
    "evaluate_historical_evidence",
]
