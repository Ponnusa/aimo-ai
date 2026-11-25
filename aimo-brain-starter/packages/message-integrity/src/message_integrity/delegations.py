"""Per-agent signing-authority delegation tokens (M8 slice 2, ADR-017).

A delegation is constructed and signed entirely inside the delegating
organization's own trust boundary, using one of that organization's
*current* root signing keys — exactly the same key material and the
same place (``buyer_signing_private_key_pem``/
``supplier_signing_private_key_pem``) that already signs business
artifacts today. network-api never signs a delegation and never
receives a private key; it only verifies a submitted token against the
organization's own published root JWKS before storing and serving it
(see ``apps/network-api``'s ``/organizations/{id}/delegations``
endpoints).

A delegation authorizes a *scope* — a bounded set of business-artifact
types plus a protocol version — never blanket signing authority. A
verifier must check an artifact's own type against the presented
delegation's scope before treating the artifact's signature as
authorized at all; OPA and the existing commitment-authorization flow
(ADR-011) are completely independent of this and remain the only source
of permission to actually execute a binding action.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from message_integrity.jwks import JwksResolver
from message_integrity.keys import jwk_thumbprint, public_key_from_jwk, validate_ed25519_public_jwk
from message_integrity.proofs import ALGORITHM, DELEGATION_TYP, InvalidSignatureError, ProofError

#: Delegations default to a much longer validity than a single
#: business artifact's proof (minutes) — they are a standing grant of
#: signing authority for a key, not a one-shot message. 30 days is a
#: starting point; nothing in this slice prevents an operator from
#: issuing a shorter-lived one.
DEFAULT_DELEGATION_VALIDITY_SECONDS = 30 * 24 * 3600


class DelegationError(ProofError):
    """Base class for every reason a delegation token can be rejected."""


class DelegationScopeError(DelegationError):
    """The presented delegation does not cover the artifact type (or
    protocol version) actually being verified."""


class DelegationAudienceError(DelegationError):
    """The delegation's ``aud`` (network/environment identifier) does
    not match the verifier's own configured audience — kept entirely
    separate from a business artifact's ``aud`` (recipient
    organization_id), which is a different claim semantically even
    though both use the JWT claim name ``aud``."""


@dataclass(frozen=True)
class DelegationScope:
    artifact_types: tuple[str, ...]
    protocol_version: str

    def allows(self, artifact_type: str, *, protocol_version: str) -> bool:
        return artifact_type in self.artifact_types and protocol_version == self.protocol_version

    def to_claim(self) -> dict[str, Any]:
        return {
            "artifact_types": list(self.artifact_types),
            "protocol_version": self.protocol_version,
        }

    @classmethod
    def from_claim(cls, claim: dict[str, Any]) -> DelegationScope:
        return cls(
            artifact_types=tuple(claim["artifact_types"]),
            protocol_version=claim["protocol_version"],
        )


@dataclass(frozen=True)
class VerifiedDelegation:
    organization_id: str
    agent_id: str
    issuing_kid: str
    delegated_kid: str
    delegated_public_key: Ed25519PublicKey
    #: The delegated key's JWK exactly as the signed claims carried it —
    #: kept alongside the parsed key object so a caller that needs to
    #: re-serialize it (e.g. network-api, storing a delegation) never
    #: has to reverse-engineer a JWK from an ``Ed25519PublicKey``.
    delegated_pubkey_jwk: dict[str, str]
    audience: str
    scope: DelegationScope
    jti: str
    issued_at: int
    expires_at: int


def build_delegation(
    *,
    root_private_key: Ed25519PrivateKey,
    issuing_kid: str,
    organization_id: str,
    agent_id: str,
    audience: str,
    delegated_kid: str,
    delegated_public_key_jwk: dict[str, str],
    scope: DelegationScope,
    validity_seconds: int = DEFAULT_DELEGATION_VALIDITY_SECONDS,
) -> str:
    """Construct and sign a delegation JWS entirely with organization-
    held key material. The caller (buyer-agent/supplier-agent, or an
    operator script acting on an organization's behalf) must already
    hold ``root_private_key`` — this function never talks to
    network-api and never persists anything; the resulting compact
    token is the caller's own responsibility to submit to
    ``POST /organizations/{id}/delegations``."""
    validate_ed25519_public_jwk(delegated_public_key_jwk)
    now = int(time.time())
    claims = {
        "iss": organization_id,
        "sub": agent_id,
        "aud": audience,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + validity_seconds,
        "delegated_kid": delegated_kid,
        "delegated_pubkey_jwk": delegated_public_key_jwk,
        "scope": scope.to_claim(),
    }
    return jwt.encode(
        claims,
        root_private_key,
        algorithm=ALGORITHM,
        headers={"kid": issuing_kid, "typ": DELEGATION_TYP},
    )


async def verify_delegation_token(
    token: str,
    *,
    jwks_resolver: JwksResolver,
    expected_audience: str | None = None,
    force_fresh: bool = False,
) -> VerifiedDelegation:
    """Verify a delegation token's signature against the issuing
    organization's *current* root JWKS (never a private key network-api
    holds — it never holds one). Raises :class:`DelegationError` (or a
    subclass) on any failure. Does not check scope against a specific
    artifact — callers do that with :meth:`DelegationScope.allows`.

    ``expected_audience=None`` (network-api's own publish-time use)
    verifies the signature and every other claim but accepts whatever
    ``aud`` the token itself declares, without asserting a *specific*
    expected value — that stronger check belongs to the receiver
    (buyer-agent/supplier-agent), which always passes its own configured
    network/environment identifier here."""
    try:
        header = jwt.get_unverified_header(token)
        unverified_claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.exceptions.PyJWTError as exc:
        raise InvalidSignatureError(f"Malformed delegation token: {exc}") from exc

    if header.get("typ") != DELEGATION_TYP:
        raise InvalidSignatureError(
            "A business-artifact proof cannot be verified as a delegation token."
        )

    issuing_kid = header.get("kid")
    organization_id = unverified_claims.get("iss")
    if not issuing_kid or not organization_id:
        raise InvalidSignatureError("Delegation token is missing a kid or issuer.")

    try:
        root_public_key = await jwks_resolver.get_key(
            organization_id=organization_id, kid=issuing_kid, force_fresh=force_fresh
        )
    except Exception as exc:  # KeyNotFoundError or a resolver-specific failure
        raise InvalidSignatureError(
            f"Could not resolve the delegation's issuing root key: {exc}"
        ) from exc

    audience_to_check = (
        expected_audience if expected_audience is not None else unverified_claims.get("aud")
    )
    try:
        claims = jwt.decode(
            token,
            root_public_key,
            algorithms=[ALGORITHM],
            audience=audience_to_check,
            issuer=organization_id,
        )
    except jwt.exceptions.InvalidAudienceError as exc:
        raise DelegationAudienceError(str(exc)) from exc
    except jwt.exceptions.PyJWTError as exc:
        raise InvalidSignatureError(str(exc)) from exc

    delegated_pubkey_jwk = claims["delegated_pubkey_jwk"]
    validate_ed25519_public_jwk(delegated_pubkey_jwk)

    return VerifiedDelegation(
        organization_id=organization_id,
        agent_id=claims["sub"],
        issuing_kid=issuing_kid,
        delegated_kid=claims["delegated_kid"],
        delegated_public_key=public_key_from_jwk(delegated_pubkey_jwk),
        delegated_pubkey_jwk=delegated_pubkey_jwk,
        audience=claims["aud"],
        scope=DelegationScope.from_claim(claims["scope"]),
        jti=claims["jti"],
        issued_at=claims["iat"],
        expires_at=claims["exp"],
    )


def delegated_pubkey_thumbprint(delegated_public_key_jwk: dict[str, str]) -> str:
    """RFC 7638 thumbprint of a delegated key — the value network-api
    enforces organization-wide uniqueness over (see ADR-017)."""
    return jwk_thumbprint(delegated_public_key_jwk)


__all__ = [
    "DEFAULT_DELEGATION_VALIDITY_SECONDS",
    "DelegationAudienceError",
    "DelegationError",
    "DelegationScope",
    "DelegationScopeError",
    "VerifiedDelegation",
    "build_delegation",
    "delegated_pubkey_thumbprint",
    "verify_delegation_token",
]
