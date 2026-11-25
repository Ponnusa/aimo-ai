"""Verification evidence receipts (M8 slice 3, ADR-018).

A receipt binds an exact verification OUTCOME (accepted or rejected) to
the exact artifact it was about, via a one-way cryptographic digest --
never the artifact's plaintext content. Because SHA-256 is one-way, a
receipt can safely reference an artifact's digest even when that
artifact's own payload happens to carry a bearer credential (ADR-011's
credential-in-payload design, still unchanged) without ever retaining
anything that could be used to recover that credential -- "receipts
must never retain bearer credentials" is satisfied by construction
(nothing recoverable from a one-way digest), not by field-level
redaction of a stored plaintext copy.

An unverified ``kid`` (or digest) taken from a REJECTED message is
recorded under a deliberately different field name -- ``claimed_kid``,
``claimed_artifact_digest`` -- than the trusted fields a successful
verification populates (``kid_used``, ``artifact_digest``), so a
receipt can never let something merely CLAIMED by an unverified,
possibly-forged message be confused with something that was actually
cryptographically resolved and verified. This is the exact distinction
ADR-018 section 1's historical-evidence report also depends on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import jwt

from message_integrity.proofs import VerifiedProof


@dataclass(frozen=True)
class EvidenceReceipt:
    jti: str | None
    #: Populated, and trusted, only when verification_outcome == "accepted".
    artifact_digest: str | None
    kid_used: str | None
    #: Populated only when verification_outcome == "rejected" -- an
    #: unverified claim read from the raw token, never cryptographically
    #: confirmed. Must never be treated as equivalent to the fields above.
    claimed_artifact_digest: str | None
    claimed_kid: str | None
    delegated_kid: str | None
    organization_id: str | None
    agent_id: str | None
    artifact_type: str | None
    attribution: str | None
    verification_outcome: str  # "accepted" | "rejected"
    rejection_reason: str | None
    observed_at: datetime


def build_acceptance_receipt(verified: VerifiedProof, *, kid_used: str) -> EvidenceReceipt:
    """A receipt for a successfully verified artifact. ``kid_used`` and
    ``artifact_digest`` are trusted here precisely because verification
    already succeeded -- this function never re-derives or re-checks
    anything, it only records what the caller's own successful
    verification already established. ``kid_used`` must be the exact
    ``kid`` the caller actually verified against (the proof's own
    protected-header ``kid`` -- :class:`VerifiedProof` does not carry
    this itself, so the caller, which already has the header, supplies
    it explicitly rather than this function guessing at it)."""
    delegated_kid = kid_used if verified.attribution == "delegated_agent" else None
    return EvidenceReceipt(
        jti=verified.jti,
        artifact_digest=verified.artifact_digest,
        kid_used=kid_used,
        claimed_artifact_digest=None,
        claimed_kid=None,
        delegated_kid=delegated_kid,
        organization_id=verified.organization_id,
        agent_id=verified.agent_id,
        artifact_type=verified.artifact_type,
        attribution=verified.attribution,
        verification_outcome="accepted",
        rejection_reason=None,
        observed_at=datetime.now(UTC).replace(tzinfo=None),
    )


def build_rejection_receipt(artifact: dict[str, Any], *, reason: str) -> EvidenceReceipt:
    """Best-effort extraction from an artifact whose verification
    failed. Nothing extracted here was cryptographically confirmed --
    every field uses the ``claimed_*`` naming precisely so it can never
    be mistaken for a verified one. Never raises: a malformed or
    unparseable artifact simply yields a receipt with every claimed
    field left ``None``, because a rejection must always produce a
    receipt, even when there is nothing trustworthy to extract."""
    claimed_kid: str | None = None
    claimed_digest: str | None = None
    jti: str | None = None
    organization_id: str | None = None
    agent_id: str | None = None
    artifact_type: str | None = None

    proof = artifact.get("proof") if isinstance(artifact, dict) else None
    token = proof.get("jws") if isinstance(proof, dict) else None
    if isinstance(token, str):
        try:
            header = jwt.get_unverified_header(token)
            claimed_kid = header.get("kid")
        except Exception:  # noqa: BLE001 - best-effort only, never raises
            pass
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
            claimed_digest = unverified.get("artifact_digest")
            jti = unverified.get("jti")
            organization_id = unverified.get("iss")
            agent_id = unverified.get("sub")
            artifact_type = unverified.get("typ")
        except Exception:  # noqa: BLE001
            pass

    return EvidenceReceipt(
        jti=jti,
        artifact_digest=None,
        kid_used=None,
        claimed_artifact_digest=claimed_digest,
        claimed_kid=claimed_kid,
        delegated_kid=None,
        organization_id=organization_id,
        agent_id=agent_id,
        artifact_type=artifact_type,
        attribution=None,
        verification_outcome="rejected",
        rejection_reason=reason,
        observed_at=datetime.now(UTC).replace(tzinfo=None),
    )


__all__ = ["EvidenceReceipt", "build_acceptance_receipt", "build_rejection_receipt"]
