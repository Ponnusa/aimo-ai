"""Per-artifact JWS signing and cryptographic verification (M5.1).

This module owns steps 1-7 of the verification flow documented in the
M5.1 architecture review: digest/signature/scope checks that need no
deployment state. Replay (step 8), registry/relationship checks (9-10)
and negotiation-participant binding (11) need this deployment's own
database/HTTP clients and are the caller's job — see
``buyer_agent.message_auth``/``supplier_agent.message_auth``. Nothing
here is industry-specific; it knows "artifact" as a plain dict with a
handful of required top-level fields, never a line item or a SKU.
"""

from __future__ import annotations

import hashlib
import json
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

ALGORITHM = "EdDSA"
DEFAULT_VALIDITY_SECONDS = 600


class ProofError(Exception):
    """Base class for every reason a proof can be rejected."""


class MissingProofError(ProofError):
    pass


class TamperedArtifactError(ProofError):
    """The signed digest does not match the artifact's actual content —
    something changed after signing."""


class ScopeMismatchError(ProofError):
    """The signed claims (negotiation id / artifact type / content hash)
    do not match the artifact carrying them."""


class ExpiredProofError(ProofError):
    pass


class InvalidSignatureError(ProofError):
    pass


@dataclass(frozen=True)
class VerifiedProof:
    organization_id: str
    agent_id: str
    recipient_organization_id: str
    negotiation_id: str
    artifact_type: str
    version: int
    content_hash: str | None
    jti: str
    issued_at: int
    expires_at: int


def canonicalize(data: dict[str, Any]) -> str:
    """Deterministic JSON serialization: sorted keys, no incidental
    whitespace. Reused verbatim as ``trade_protocol.negotiation.
    canonicalize_terms`` so the whole codebase has exactly one
    canonicalization rule, never two slightly different ones."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _normalize_numbers(value: Any) -> Any:
    """The A2A SDK transports structured data as a protobuf ``Struct``,
    whose number type is always double-precision float — an integer
    field like ``version`` sent as ``1`` arrives on the other side as
    ``1.0``. Every monetary/quantity value in this codebase is already a
    decimal *string*, never a JSON number (docs/PROTOCOLS.md rule 2), so
    the only JSON numbers an artifact ever legitimately carries are
    integers; any float with a zero fractional part is therefore exactly
    that integer and is normalized back before (re)computing a digest,
    so a value that only changed representation in transit — never its
    actual content — does not look tampered."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _normalize_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_numbers(item) for item in value]
    return value


def _artifact_digest(artifact_without_proof: dict[str, Any]) -> str:
    normalized = _normalize_numbers(artifact_without_proof)
    return hashlib.sha256(canonicalize(normalized).encode("utf-8")).hexdigest()


def sign_artifact(
    artifact: dict[str, Any],
    *,
    private_key: Ed25519PrivateKey,
    kid: str,
    organization_id: str,
    agent_id: str,
    recipient_organization_id: str,
    validity_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> dict[str, Any]:
    """Return a copy of ``artifact`` with a ``proof`` field attached.
    ``artifact`` must not already carry one — signing always covers the
    full, final content."""
    if "proof" in artifact:
        raise ValueError("Artifact already carries a proof; sign before attaching one.")

    now = int(time.time())
    claims = {
        "iss": organization_id,
        "sub": agent_id,
        "aud": recipient_organization_id,
        "neg": artifact["transaction_id"],
        "typ": artifact["type"],
        "ver": artifact["version"],
        "hash": artifact.get("content_hash"),
        "artifact_digest": _artifact_digest(artifact),
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + validity_seconds,
    }
    token = jwt.encode(claims, private_key, algorithm=ALGORITHM, headers={"kid": kid})
    signed = dict(artifact)
    signed["proof"] = {"alg": ALGORITHM, "kid": kid, "jws": token}
    return signed


async def verify_artifact_proof(
    artifact: dict[str, Any],
    *,
    jwks_resolver: JwksResolver,
    expected_recipient_organization_id: str,
) -> VerifiedProof:
    """Cryptographic + scope verification only (steps 1-7). Raises a
    specific :class:`ProofError` subclass on any failure — callers must
    still perform replay/registry/relationship/participant checks
    before trusting the result for a business decision."""
    proof = artifact.get("proof")
    if not proof or "jws" not in proof:
        raise MissingProofError("Artifact carries no proof.")

    token = proof["jws"]
    try:
        header = jwt.get_unverified_header(token)
        unverified_claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.exceptions.PyJWTError as exc:
        raise InvalidSignatureError(f"Malformed proof: {exc}") from exc

    kid = header.get("kid")
    issuer_organization_id = unverified_claims.get("iss")
    if not kid or not issuer_organization_id:
        raise InvalidSignatureError("Proof is missing a kid or issuer.")

    try:
        public_key: Ed25519PublicKey = await jwks_resolver.get_key(
            organization_id=issuer_organization_id, kid=kid
        )
    except Exception as exc:  # KeyNotFoundError or a resolver-specific failure
        raise InvalidSignatureError(f"Could not resolve signing key: {exc}") from exc

    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=[ALGORITHM],
            audience=expected_recipient_organization_id,
            issuer=issuer_organization_id,
        )
    except jwt.exceptions.ExpiredSignatureError as exc:
        raise ExpiredProofError(str(exc)) from exc
    except jwt.exceptions.PyJWTError as exc:
        raise InvalidSignatureError(str(exc)) from exc

    artifact_without_proof = {k: v for k, v in artifact.items() if k != "proof"}
    expected_digest = _artifact_digest(artifact_without_proof)
    if claims.get("artifact_digest") != expected_digest:
        raise TamperedArtifactError("Signed digest does not match the artifact's actual content.")

    if (
        claims.get("neg") != artifact.get("transaction_id")
        or claims.get("typ") != artifact.get("type")
        or claims.get("hash") != artifact.get("content_hash")
        or claims.get("ver") != artifact.get("version")
    ):
        raise ScopeMismatchError(
            "Signed claims do not match this artifact's negotiation id, type, "
            "version or content_hash."
        )

    return VerifiedProof(
        organization_id=issuer_organization_id,
        agent_id=claims["sub"],
        recipient_organization_id=claims["aud"],
        negotiation_id=claims["neg"],
        artifact_type=claims["typ"],
        version=claims["ver"],
        content_hash=claims.get("hash"),
        jti=claims["jti"],
        issued_at=claims["iat"],
        expires_at=claims["exp"],
    )


__all__ = [
    "DEFAULT_VALIDITY_SECONDS",
    "ExpiredProofError",
    "InvalidSignatureError",
    "MissingProofError",
    "ProofError",
    "ScopeMismatchError",
    "TamperedArtifactError",
    "VerifiedProof",
    "canonicalize",
    "sign_artifact",
    "verify_artifact_proof",
]
