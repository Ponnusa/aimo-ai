"""Message-level authentication for every A2A business artifact (M5.1,
docs/DECISIONS.md ADR-012).

Signs this organization's own outbound artifacts with its Ed25519
private key, and cryptographically verifies every inbound artifact
(signature + replay + registered-agent + trading-relationship) before
any business handler ever sees it. Replaces M5's HMAC
``commit``/``negotiate`` credentials entirely, and is no longer limited
to the M5 authorization/commitment artifact types — it runs uniformly
for RFQ/quote/counteroffer/accept/reject/proposed_agreement too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from message_integrity import (
    HttpDelegationResolver,
    HttpJwksResolver,
    ProofError,
    VerifiedProof,
    build_acceptance_receipt,
    build_rejection_receipt,
    private_key_from_pem,
)
from message_integrity import sign_artifact as _sign_artifact
from message_integrity import verify_artifact_proof_with_delegation as _verify_artifact_proof
from message_integrity.proofs import parse_unverified_proof
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from supplier_agent.models import (
    SupplierSeenMessageProofRecord,
    SupplierVerificationEvidenceReceiptRecord,
)
from supplier_agent.registry_client import (
    RegistryVerificationError,
    verify_agent_is_registered_and_related,
)
from supplier_agent.settings import SupplierAgentSettings


class MessageAuthenticationError(Exception):
    """Raised for any reason an inbound artifact cannot be trusted.
    Callers must treat this as a rejection, never proceed past it."""


#: One resolver per network-api URL, reused across calls so its
#: internal JWKS cache (bounded TTL) actually has something to cache.
_resolver_cache: dict[str, HttpJwksResolver] = {}
#: One delegation resolver per network-api URL (M8 slice 2, ADR-017).
_delegation_resolver_cache: dict[str, HttpDelegationResolver] = {}


def _get_resolver(network_api_url: str) -> HttpJwksResolver:
    resolver = _resolver_cache.get(network_api_url)
    if resolver is None:
        resolver = HttpJwksResolver(network_api_url=network_api_url)
        _resolver_cache[network_api_url] = resolver
    return resolver


def _get_delegation_resolver(network_api_url: str) -> HttpDelegationResolver:
    resolver = _delegation_resolver_cache.get(network_api_url)
    if resolver is None:
        resolver = HttpDelegationResolver(
            network_api_url=network_api_url, jwks_resolver=_get_resolver(network_api_url)
        )
        _delegation_resolver_cache[network_api_url] = resolver
    return resolver


def sign_outbound_artifact(
    artifact: dict[str, Any], *, settings: SupplierAgentSettings, recipient_organization_id: str
) -> dict[str, Any]:
    private_key = private_key_from_pem(settings.supplier_signing_private_key_pem)
    return _sign_artifact(
        artifact,
        private_key=private_key,
        kid=settings.supplier_signing_kid,
        organization_id=settings.supplier_organization_id,
        agent_id=settings.supplier_agent_id,
        recipient_organization_id=recipient_organization_id,
    )


async def verify_inbound_artifact(
    artifact: dict[str, Any],
    *,
    settings: SupplierAgentSettings,
    session_factory: async_sessionmaker,
) -> VerifiedProof:
    """Steps 1-11 of the M5.1 verification flow. Raises
    :class:`MessageAuthenticationError` on any failure — signature,
    tampering, scope mismatch, expiry, replay, unregistered/inactive
    sender agent, or no active trading relationship."""
    resolver = _get_resolver(settings.network_api_url)
    delegation_resolver = _get_delegation_resolver(settings.network_api_url)
    try:
        verified = await _verify_artifact_proof(
            artifact,
            jwks_resolver=resolver,
            delegation_resolver=delegation_resolver,
            expected_recipient_organization_id=settings.supplier_organization_id,
            network_audience=settings.network_audience,
            protocol_version=settings.aimo_protocol_version,
        )
    except ProofError as exc:
        # M8 slice 3 (ADR-018 section 2): a rejection still produces an
        # evidence receipt, under claimed_*-named fields only -- nothing
        # here was cryptographically confirmed.
        await _record_evidence_receipt(
            session_factory, build_rejection_receipt(artifact, reason=str(exc))
        )
        raise MessageAuthenticationError(f"Message proof rejected: {exc}") from exc

    proof = artifact.get("proof") or {}
    header, _ = parse_unverified_proof(proof["jws"])
    kid_used = header.get("kid", "")

    await _record_seen_or_reject_replay(session_factory, verified)
    await _record_evidence_receipt(
        session_factory, build_acceptance_receipt(verified, kid_used=kid_used)
    )

    try:
        await verify_agent_is_registered_and_related(
            registry_url=settings.registry_api_url,
            requesting_organization_id=settings.supplier_organization_id,
            agent_id=verified.agent_id,
            organization_id=verified.organization_id,
        )
    except RegistryVerificationError as exc:
        raise MessageAuthenticationError(str(exc)) from exc

    return verified


async def _record_seen_or_reject_replay(
    session_factory: async_sessionmaker, verified: VerifiedProof
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session:
        existing = await session.get(SupplierSeenMessageProofRecord, verified.jti)
        if existing is not None:
            raise MessageAuthenticationError(f"Replayed message proof jti={verified.jti!r}.")
        session.add(
            SupplierSeenMessageProofRecord(
                jti=verified.jti,
                negotiation_id=verified.negotiation_id,
                artifact_type=verified.artifact_type,
                organization_id=verified.organization_id,
                agent_id=verified.agent_id,
                seen_at=now,
            )
        )
        try:
            await session.commit()
        except IntegrityError as exc:
            # Lost a race against a concurrent identical replay — the
            # other request's row is now the durable record.
            raise MessageAuthenticationError(
                f"Replayed message proof jti={verified.jti!r}."
            ) from exc


async def _record_evidence_receipt(session_factory: async_sessionmaker, receipt) -> None:
    """See the matching function in ``buyer_agent.message_auth`` (M8
    slice 3, ADR-018 section 2)."""
    async with session_factory() as session:
        session.add(
            SupplierVerificationEvidenceReceiptRecord(
                jti=receipt.jti,
                artifact_digest=receipt.artifact_digest,
                kid_used=receipt.kid_used,
                claimed_artifact_digest=receipt.claimed_artifact_digest,
                claimed_kid=receipt.claimed_kid,
                delegated_kid=receipt.delegated_kid,
                organization_id=receipt.organization_id,
                agent_id=receipt.agent_id,
                artifact_type=receipt.artifact_type,
                attribution=receipt.attribution,
                verification_outcome=receipt.verification_outcome,
                rejection_reason=receipt.rejection_reason,
                observed_at=receipt.observed_at,
            )
        )
        await session.commit()


__all__ = ["MessageAuthenticationError", "sign_outbound_artifact", "verify_inbound_artifact"]
