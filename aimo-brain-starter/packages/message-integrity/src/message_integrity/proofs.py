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

#: Domain-separation header values (M8 slice 2, ADR-017). A business
#: artifact's JWS carries ``typ: ARTIFACT_TYP`` going forward; a
#: delegation token (``message_integrity.delegations``) carries
#: ``typ: DELEGATION_TYP`` — the two are structurally distinguishable by
#: header alone. ``TYP_LEGACY_VALUES`` is accepted ONLY for business
#: artifacts, so that no artifact signed before this slice (which never
#: set an explicit ``typ`` and so carried PyJWT's own default, ``"JWT"``)
#: is invalidated — it verifies exactly as before, recorded as
#: ``typ_version="legacy"`` rather than rejected.
ARTIFACT_TYP = "aimo-artifact+jws"
DELEGATION_TYP = "aimo-delegation+jws"
TYP_LEGACY_VALUES = frozenset({None, "JWT"})


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
    #: "organization" (signed directly by an org root key — proves
    #: organization-level authority only, not which agent/key produced
    #: it) or "delegated_agent" (signed by a key the organization
    #: explicitly delegated to this exact agent — M8 slice 2, ADR-017).
    #: Defaults to "organization" so every pre-slice-2 construction site
    #: (including existing tests) keeps its prior meaning unchanged.
    attribution: str = "organization"
    #: "legacy" (no explicit ``typ``, or PyJWT's old default ``"JWT"``)
    #: or "v1" (the new explicit ``ARTIFACT_TYP`` discriminator).
    typ_version: str = "legacy"
    #: The artifact's own ``artifact_digest`` claim (M8 slice 3, ADR-018)
    #: -- already computed and checked internally during verification;
    #: exposed here so a caller building a :class:`VerificationEvidenceReceiptRecord`
    #: never has to recompute it, and so a receipt can bind to this
    #: one-way digest without ever touching the artifact's own payload
    #: (which may carry a bearer credential, per ADR-011) in a form that
    #: could be recovered later -- a SHA-256 digest is one-way by
    #: construction, so storing it can never leak that credential.
    artifact_digest: str = ""


def canonicalize(data: dict[str, Any]) -> str:
    """Deterministic JSON serialization: sorted keys, no incidental
    whitespace, standard-JSON-only numbers. Reused verbatim as
    ``trade_protocol.negotiation.canonicalize_terms`` so the whole
    codebase has exactly one canonicalization rule, never two slightly
    different ones -- see ``docs/specs/jws-artifact-signatures.md`` for
    the full, language-neutral specification of this rule, including
    why it is a deliberate, documented deviation from RFC 8785 rather
    than an attempt to implement it.

    ``allow_nan=False`` (M8, ADR-015 section F): Python's ``json.dumps``
    otherwise happily emits the non-standard tokens ``NaN``/``Infinity``/
    ``-Infinity`` for those float values, which no conformant JSON
    parser in another language accepts -- silently producing bytes that
    could never have round-tripped across languages anyway. No
    previously-valid artifact has ever legitimately contained one of
    these (every monetary/quantity value is a decimal *string*, never a
    JSON number -- docs/PROTOCOLS.md rule 2), so turning this into an
    explicit, immediate :class:`ValueError` changes no existing valid
    artifact's digest; it only replaces a silent interoperability break
    with a loud one for inputs that were never valid to begin with."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"Duplicate JSON key {key!r} is not permitted in a signed artifact.")
        seen.add(key)
    return dict(pairs)


def strict_json_loads(text: str) -> dict[str, Any]:
    """Parse ``text`` as a JSON object, rejecting duplicate keys (M8,
    ADR-015 section F) -- a bare ``json.loads`` silently keeps only the
    last occurrence, which is a known class of canonicalization
    ambiguity (two parties' parsers could disagree on which value a
    duplicated key "really" held). :func:`canonicalize` itself takes an
    already-parsed Python ``dict`` -- which, being a Python dict, cannot
    contain a duplicate key by construction -- so this function matters
    only to a caller that deserializes untrusted *raw JSON text* into an
    artifact before canonicalizing/verifying it. Nothing in this
    codebase currently does that (every artifact arrives as an
    already-structured object via the A2A SDK's protobuf ``Struct``,
    which has no "duplicate key" concept at all); this helper exists so
    any *future* raw-JSON transport inherits strict parsing by
    construction rather than by someone remembering to write it."""
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys)


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
    token = jwt.encode(
        claims, private_key, algorithm=ALGORITHM, headers={"kid": kid, "typ": ARTIFACT_TYP}
    )
    signed = dict(artifact)
    signed["proof"] = {"alg": ALGORITHM, "kid": kid, "jws": token}
    return signed


def parse_unverified_proof(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Decode (without verifying) a proof token's header and claims —
    shared by the plain org-key path and M8 slice 2's delegation-aware
    orchestrator, so both inspect the exact same unverified view before
    deciding which keyspace to resolve against."""
    try:
        header = jwt.get_unverified_header(token)
        unverified_claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.exceptions.PyJWTError as exc:
        raise InvalidSignatureError(f"Malformed proof: {exc}") from exc
    return header, unverified_claims


def _check_artifact_typ(header: dict[str, Any]) -> str:
    """Reject a delegation token presented as a business artifact, and
    any other unrecognized ``typ`` — accept only the new explicit
    discriminator or a legacy (pre-slice-2) value. Returns the resolved
    ``typ_version`` ("legacy" or "v1")."""
    typ = header.get("typ")
    if typ == DELEGATION_TYP:
        raise InvalidSignatureError(
            "A delegation token cannot be verified as a business-artifact proof."
        )
    if typ in TYP_LEGACY_VALUES:
        return "legacy"
    if typ == ARTIFACT_TYP:
        return "v1"
    raise InvalidSignatureError(f"Unrecognized proof 'typ' header: {typ!r}.")


def verify_artifact_proof_with_known_key(
    artifact: dict[str, Any],
    *,
    token: str,
    header: dict[str, Any],
    public_key: Ed25519PublicKey,
    issuer_organization_id: str,
    expected_recipient_organization_id: str,
    attribution: str,
) -> VerifiedProof:
    """Steps 3-7 of verification, once a candidate public key has
    already been resolved (from either the organization-root keyspace
    or a per-agent delegation's key — the caller decides which and
    passes the correct ``attribution``). Factored out of
    :func:`verify_artifact_proof` so M8 slice 2's delegation-aware
    orchestrator can reuse the exact same digest/scope/claim checks
    without duplicating them for the delegated path."""
    typ_version = _check_artifact_typ(header)
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
        attribution=attribution,
        typ_version=typ_version,
        artifact_digest=expected_digest,
    )


async def verify_artifact_proof(
    artifact: dict[str, Any],
    *,
    jwks_resolver: JwksResolver,
    expected_recipient_organization_id: str,
) -> VerifiedProof:
    """Cryptographic + scope verification only (steps 1-7), against an
    organization's *root* key only — unchanged, pre-slice-2 behavior,
    kept as its own entry point so every existing call site keeps
    working exactly as before. Raises a specific :class:`ProofError`
    subclass on any failure — callers must still perform
    replay/registry/relationship/participant checks before trusting the
    result for a business decision. M8 slice 2 callers that also need to
    resolve a per-agent delegated key should use
    :func:`message_integrity.delegated_verification.verify_artifact_proof_with_delegation`
    instead."""
    proof = artifact.get("proof")
    if not proof or "jws" not in proof:
        raise MissingProofError("Artifact carries no proof.")

    token = proof["jws"]
    header, unverified_claims = parse_unverified_proof(token)

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

    return verify_artifact_proof_with_known_key(
        artifact,
        token=token,
        header=header,
        public_key=public_key,
        issuer_organization_id=issuer_organization_id,
        expected_recipient_organization_id=expected_recipient_organization_id,
        attribution="organization",
    )


__all__ = [
    "ARTIFACT_TYP",
    "DEFAULT_VALIDITY_SECONDS",
    "DELEGATION_TYP",
    "TYP_LEGACY_VALUES",
    "ExpiredProofError",
    "InvalidSignatureError",
    "MissingProofError",
    "ProofError",
    "ScopeMismatchError",
    "TamperedArtifactError",
    "VerifiedProof",
    "canonicalize",
    "parse_unverified_proof",
    "sign_artifact",
    "strict_json_loads",
    "verify_artifact_proof",
    "verify_artifact_proof_with_known_key",
]
