"""The two-hop, delegation-aware artifact verification orchestrator
(M8 slice 2, ADR-017).

Which keyspace a ``kid`` resolves through is structural and
deterministic, never a retry: this module looks the ``kid`` up in the
organization's *root* JWKS first; only a clean "not found there" result
(the kid simply isn't a root key at all) causes it to consult the
delegation keyspace instead. Once either lookup actually succeeds, the
resulting verification path is final — a cryptographic or scope
failure on one path is never silently retried against the other. This
is what "no fallback between the delegated-key path and the legacy
root-key path" (ADR-017) means in code, not merely in prose.

Commitment-adjacent artifact types (``authorization_evidence``,
``order_commit_request``, ``order_confirmation``) force a fresh,
uncached fetch of both the root/delegation lookup and (by the caller,
afterward — this module only does the cryptographic half) the
registry-api agent-status check.
"""

from __future__ import annotations

from typing import Any

from message_integrity.delegation_resolver import DelegationNotFoundError, DelegationResolver
from message_integrity.delegations import DelegationError
from message_integrity.jwks import JwksResolver, KeyNotFoundError
from message_integrity.proofs import (
    InvalidSignatureError,
    MissingProofError,
    ScopeMismatchError,
    VerifiedProof,
    parse_unverified_proof,
    verify_artifact_proof_with_known_key,
)

#: Artifact types whose verification must never rely on a cached
#: delegation/root-key lookup — ADR-017's revocation-freshness section.
COMMITMENT_ADJACENT_ARTIFACT_TYPES = frozenset(
    {"authorization_evidence", "order_commit_request", "order_confirmation"}
)


async def verify_artifact_proof_with_delegation(
    artifact: dict[str, Any],
    *,
    jwks_resolver: JwksResolver,
    delegation_resolver: DelegationResolver,
    expected_recipient_organization_id: str,
    network_audience: str,
    protocol_version: str,
) -> VerifiedProof:
    """Verify ``artifact``'s proof against either the issuing
    organization's root key or one of its currently-live per-agent
    delegations, returning a :class:`VerifiedProof` whose ``attribution``
    records which. Raises a :class:`message_integrity.proofs.ProofError`
    (or :class:`message_integrity.delegations.DelegationError`, a
    ``ProofError`` subclass) on any failure.

    ``force_fresh`` is not a parameter here — it is derived from the
    artifact's own (unverified) business type, per ADR-017: a
    commitment-adjacent type always bypasses the cache, regardless of
    what the caller might otherwise have wanted."""
    proof = artifact.get("proof")
    if not proof or "jws" not in proof:
        raise MissingProofError("Artifact carries no proof.")
    token = proof["jws"]

    header, unverified_claims = parse_unverified_proof(token)
    kid = header.get("kid")
    issuer_organization_id = unverified_claims.get("iss")
    if not kid or not issuer_organization_id:
        raise InvalidSignatureError("Proof is missing a kid or issuer.")

    force_fresh = unverified_claims.get("typ") in COMMITMENT_ADJACENT_ARTIFACT_TYPES

    try:
        public_key = await jwks_resolver.get_key(
            organization_id=issuer_organization_id, kid=kid, force_fresh=force_fresh
        )
    except KeyNotFoundError:
        public_key = None
    except Exception as exc:
        # A fetch failure is a rejection, full stop — never a signal to
        # also try the delegation path for the same kid.
        raise InvalidSignatureError(f"Could not resolve signing key: {exc}") from exc

    if public_key is not None:
        return verify_artifact_proof_with_known_key(
            artifact,
            token=token,
            header=header,
            public_key=public_key,
            issuer_organization_id=issuer_organization_id,
            expected_recipient_organization_id=expected_recipient_organization_id,
            attribution="organization",
        )

    # Structurally not a root key — try the delegation keyspace. A
    # failure from here on is final; it never falls back to re-attempt
    # the root path.
    try:
        delegation = await delegation_resolver.get_delegation(
            organization_id=issuer_organization_id,
            kid=kid,
            expected_audience=network_audience,
            force_fresh=force_fresh,
        )
    except DelegationNotFoundError as exc:
        raise InvalidSignatureError(f"Could not resolve signing key: {exc}") from exc
    except DelegationError:
        raise

    verified = verify_artifact_proof_with_known_key(
        artifact,
        token=token,
        header=header,
        public_key=delegation.delegated_public_key,
        issuer_organization_id=issuer_organization_id,
        expected_recipient_organization_id=expected_recipient_organization_id,
        attribution="delegated_agent",
    )

    if verified.agent_id != delegation.agent_id:
        raise ScopeMismatchError(
            f"Delegated key {kid!r} is delegated to agent {delegation.agent_id!r}, "
            f"but the artifact claims agent {verified.agent_id!r}."
        )
    if not delegation.scope.allows(verified.artifact_type, protocol_version=protocol_version):
        raise ScopeMismatchError(
            f"Delegation for agent {delegation.agent_id!r} does not authorize "
            f"artifact type {verified.artifact_type!r} at protocol version "
            f"{protocol_version!r} (scope: {delegation.scope!r})."
        )

    return verified


__all__ = ["COMMITMENT_ADJACENT_ARTIFACT_TYPES", "verify_artifact_proof_with_delegation"]
