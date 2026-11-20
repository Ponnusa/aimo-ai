"""Per-artifact JWS signing/verification for cross-organization A2A
business messages (M5.1) — see docs/DECISIONS.md ADR-012.
"""

from __future__ import annotations

from message_integrity.jwks import (
    HttpJwksResolver,
    InMemoryJwksResolver,
    JwksResolver,
    KeyNotFoundError,
)
from message_integrity.keys import (
    generate_signing_keypair,
    private_key_from_pem,
    private_key_to_pem,
    public_key_from_jwk,
    public_key_to_jwk,
)
from message_integrity.proofs import (
    ExpiredProofError,
    InvalidSignatureError,
    MissingProofError,
    ProofError,
    ScopeMismatchError,
    TamperedArtifactError,
    VerifiedProof,
    sign_artifact,
    verify_artifact_proof,
)

__all__ = [
    "ExpiredProofError",
    "HttpJwksResolver",
    "InMemoryJwksResolver",
    "InvalidSignatureError",
    "JwksResolver",
    "KeyNotFoundError",
    "MissingProofError",
    "ProofError",
    "ScopeMismatchError",
    "TamperedArtifactError",
    "VerifiedProof",
    "generate_signing_keypair",
    "private_key_from_pem",
    "private_key_to_pem",
    "public_key_from_jwk",
    "public_key_to_jwk",
    "sign_artifact",
    "verify_artifact_proof",
]
