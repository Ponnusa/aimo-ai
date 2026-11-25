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
    private_key_from_pem,
)
from message_integrity import sign_artifact as _sign_artifact
from message_integrity import verify_artifact_proof_with_delegation as _verify_artifact_proof
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from buyer_agent.models import BuyerSeenMessageProofRecord
from buyer_agent.registry_client import (
    RegistryDiscoveryError,
    verify_agent_is_registered_and_related,
)
from buyer_agent.settings import BuyerAgentSettings


class MessageAuthenticationError(Exception):
    """Raised for any reason an inbound artifact cannot be trusted.
    Callers must treat this as a rejection, never proceed past it."""


#: One resolver per network-api URL, reused across calls so its
#: internal JWKS cache (bounded TTL) actually has something to cache.
_resolver_cache: dict[str, HttpJwksResolver] = {}
#: One delegation resolver per network-api URL (M8 slice 2, ADR-017) —
#: wraps the matching JWKS resolver above, since verifying a delegation
#: itself requires resolving its issuing root key.
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
    artifact: dict[str, Any], *, settings: BuyerAgentSettings, recipient_organization_id: str
) -> dict[str, Any]:
    private_key = private_key_from_pem(settings.buyer_signing_private_key_pem)
    return _sign_artifact(
        artifact,
        private_key=private_key,
        kid=settings.buyer_signing_kid,
        organization_id=settings.buyer_organization_id,
        agent_id=settings.buyer_agent_id,
        recipient_organization_id=recipient_organization_id,
    )


async def verify_inbound_artifact(
    artifact: dict[str, Any],
    *,
    settings: BuyerAgentSettings,
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
            expected_recipient_organization_id=settings.buyer_organization_id,
            network_audience=settings.network_audience,
            protocol_version=settings.aimo_protocol_version,
        )
    except ProofError as exc:
        raise MessageAuthenticationError(f"Message proof rejected: {exc}") from exc

    await _record_seen_or_reject_replay(session_factory, verified)

    try:
        await verify_agent_is_registered_and_related(
            registry_url=settings.registry_api_url,
            requesting_organization_id=settings.buyer_organization_id,
            agent_id=verified.agent_id,
            organization_id=verified.organization_id,
        )
    except RegistryDiscoveryError as exc:
        raise MessageAuthenticationError(str(exc)) from exc

    return verified


async def _record_seen_or_reject_replay(
    session_factory: async_sessionmaker, verified: VerifiedProof
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session:
        existing = await session.get(BuyerSeenMessageProofRecord, verified.jti)
        if existing is not None:
            raise MessageAuthenticationError(f"Replayed message proof jti={verified.jti!r}.")
        session.add(
            BuyerSeenMessageProofRecord(
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


__all__ = ["MessageAuthenticationError", "sign_outbound_artifact", "verify_inbound_artifact"]
