"""Loads docs/specs/test-vectors/*.json and asserts the Python reference
implementation produces/accepts exactly what each vector states.

This is both a regression test (the implementation must keep matching
these fixed, committed signatures) and proof the vectors themselves are
accurate -- an independent, non-Python implementer is expected to produce
byte-identical signing input and identical accept/reject outcomes against
these same files, using only docs/specs/jws-artifact-signatures.md and
RFC 7515/7517/8032/8037, with no access to this module's source.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from message_integrity import InMemoryJwksResolver, verify_artifact_proof
from message_integrity.keys import public_key_from_jwk
from message_integrity.proofs import (
    InvalidSignatureError,
    TamperedArtifactError,
    _artifact_digest,
    canonicalize,
    strict_json_loads,
)

VECTORS_DIR = Path(__file__).resolve().parents[3] / "docs" / "specs" / "test-vectors"


def _load(name: str) -> dict:
    return json.loads((VECTORS_DIR / name).read_text(encoding="utf-8"))


def _resolver_for(vector: dict) -> InMemoryJwksResolver:
    resolver = InMemoryJwksResolver()
    resolver.register(
        organization_id=vector["signer_organization_id"],
        kid=vector["kid"],
        public_key=public_key_from_jwk(vector["public_key_jwk"]),
    )
    return resolver


@pytest.mark.asyncio
async def test_vector_01_valid_signature_verifies() -> None:
    vector = _load("vector_01_valid_signature.json")
    artifact = vector["signed_artifact"]
    artifact_without_proof = {k: v for k, v in artifact.items() if k != "proof"}

    assert (
        canonicalize(artifact_without_proof)
        == vector["expected_canonicalized_artifact_without_proof"]
    )
    assert _artifact_digest(artifact_without_proof) == vector["expected_artifact_digest"]

    verified = await verify_artifact_proof(
        artifact,
        jwks_resolver=_resolver_for(vector),
        expected_recipient_organization_id=vector["expected_recipient_organization_id"],
    )
    expected = vector["expected_verified_claims"]
    assert verified.organization_id == expected["organization_id"]
    assert verified.agent_id == expected["agent_id"]
    assert verified.recipient_organization_id == expected["recipient_organization_id"]
    assert verified.negotiation_id == expected["negotiation_id"]
    assert verified.artifact_type == expected["artifact_type"]
    assert verified.version == expected["version"]
    assert verified.content_hash == expected["content_hash"]
    assert verified.jti == expected["jti"]
    assert verified.issued_at == expected["issued_at"]
    assert verified.expires_at == expected["expires_at"]


@pytest.mark.asyncio
async def test_vector_02_tampered_artifact_rejected() -> None:
    vector = _load("vector_02_tampered_artifact.json")
    assert vector["expected_failure_reason"] == "artifact_digest_mismatch"
    with pytest.raises(TamperedArtifactError):
        await verify_artifact_proof(
            vector["signed_artifact"],
            jwks_resolver=_resolver_for(vector),
            expected_recipient_organization_id=vector["expected_recipient_organization_id"],
        )


@pytest.mark.asyncio
async def test_vector_03_wrong_key_rejected() -> None:
    vector = _load("vector_03_wrong_key.json")
    assert vector["expected_failure_reason"] == "invalid_signature"
    with pytest.raises(InvalidSignatureError):
        await verify_artifact_proof(
            vector["signed_artifact"],
            jwks_resolver=_resolver_for(vector),
            expected_recipient_organization_id=vector["expected_recipient_organization_id"],
        )


def test_vector_04_integer_float_coercion_digest_matches() -> None:
    vector = _load("vector_04_integer_float_coercion.json")
    int_artifact = vector["artifact_with_integer_version"]
    float_artifact = vector["artifact_with_float_version"]

    assert float_artifact["version"] == 1.0
    assert int_artifact["version"] == 1

    int_digest = _artifact_digest(int_artifact)
    float_digest = _artifact_digest(float_artifact)
    assert int_digest == float_digest == vector["expected_digest_both"]
    assert canonicalize(int_artifact) == vector["expected_canonicalized_form_both"]


@pytest.mark.asyncio
async def test_vector_05_unicode_content_verifies_and_escapes_as_ascii() -> None:
    vector = _load("vector_05_unicode_content.json")
    artifact = vector["signed_artifact"]
    artifact_without_proof = {k: v for k, v in artifact.items() if k != "proof"}

    canon = canonicalize(artifact_without_proof)
    assert canon == vector["expected_canonicalized_artifact_without_proof"]
    # Spec section 2d: every non-ASCII codepoint is \u-escaped, never raw
    # UTF-8 bytes, in the canonical form.
    assert "café" not in canon
    assert "\\u00e9" in canon  # é
    assert "\\u65e5" in canon  # 日
    assert _artifact_digest(artifact_without_proof) == vector["expected_artifact_digest"]

    verified = await verify_artifact_proof(
        artifact,
        jwks_resolver=_resolver_for(vector),
        expected_recipient_organization_id=vector["expected_recipient_organization_id"],
    )
    assert verified.organization_id == vector["signer_organization_id"]


def test_vector_06_duplicate_key_rejected_by_strict_parsing() -> None:
    vector = _load("vector_06_duplicate_key_rejected.json")
    assert vector["expected_failure_reason"] == "duplicate_key_rejected"
    with pytest.raises(ValueError):
        strict_json_loads(vector["raw_json_text"])

    # A bare json.loads, by contrast, silently keeps the LAST occurrence --
    # this is exactly the ambiguity strict_json_loads exists to close, not
    # a property canonicalize()/verify_artifact_proof() themselves need to
    # guard (they only ever receive an already-parsed dict, which cannot
    # contain a duplicate key by construction -- see spec section 2f).
    assert json.loads(vector["raw_json_text"])["version"] == 2


def test_nan_and_infinity_are_rejected_by_canonicalize() -> None:
    with pytest.raises(ValueError):
        canonicalize({"amount": float("nan")})
    with pytest.raises(ValueError):
        canonicalize({"amount": float("inf")})


@pytest.mark.asyncio
async def test_node_generated_artifact_verifies_in_python() -> None:
    """Node.js -> Python direction of cross-language interoperability
    (ADR-015 section G): ``cross-lang-harness/generate-artifact.mjs``
    signs this fixture entirely in Node.js, with no AIMO Python package
    involved; the Python reference implementation must verify it
    exactly as it would any Python-signed artifact. The companion
    direction (Python-signed vectors verified by the Node.js harness) is
    ``cross-lang-harness/verify-vectors.mjs``."""
    path = VECTORS_DIR / "cross-lang-harness" / "node-generated-artifact.json"
    if not path.exists():
        pytest.skip(
            "node-generated-artifact.json not present; regenerate with "
            "`node generate-artifact.mjs` in cross-lang-harness/."
        )
    vector = json.loads(path.read_text(encoding="utf-8"))
    verified = await verify_artifact_proof(
        vector["signed_artifact"],
        jwks_resolver=_resolver_for(vector),
        expected_recipient_organization_id=vector["expected_recipient_organization_id"],
    )
    assert verified.organization_id == vector["signer_organization_id"]
    assert verified.agent_id == vector["signed_artifact"]["sender_agent_id"]


def test_negative_zero_normalizes_identically_to_zero() -> None:
    # Normalization (spec section 2e) happens in _artifact_digest's call
    # to _normalize_numbers before canonicalize() ever sees the value --
    # canonicalize() itself is the raw serializer and does not normalize.
    assert _artifact_digest({"n": -0.0}) == _artifact_digest({"n": 0})
