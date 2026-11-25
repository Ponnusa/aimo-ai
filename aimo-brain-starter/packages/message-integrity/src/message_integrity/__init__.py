"""Per-artifact JWS signing/verification for cross-organization A2A
business messages (M5.1) — see docs/DECISIONS.md ADR-012.
"""

from __future__ import annotations

from message_integrity.delegated_verification import (
    COMMITMENT_ADJACENT_ARTIFACT_TYPES,
    verify_artifact_proof_with_delegation,
)
from message_integrity.delegation_resolver import (
    DelegationNotFoundError,
    DelegationResolver,
    HttpDelegationResolver,
    InMemoryDelegationResolver,
)
from message_integrity.delegations import (
    DelegationAudienceError,
    DelegationError,
    DelegationScope,
    DelegationScopeError,
    VerifiedDelegation,
    build_delegation,
)
from message_integrity.evidence import (
    EvidenceReceipt,
    build_acceptance_receipt,
    build_rejection_receipt,
)
from message_integrity.historical_evidence import (
    HistoricalEvidenceReport,
    KeyLifecycleFact,
    KeyStatusAtTime,
    evaluate_historical_evidence,
)
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
    ARTIFACT_TYP,
    DELEGATION_TYP,
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
    "ARTIFACT_TYP",
    "COMMITMENT_ADJACENT_ARTIFACT_TYPES",
    "DELEGATION_TYP",
    "DelegationAudienceError",
    "DelegationError",
    "DelegationNotFoundError",
    "DelegationResolver",
    "DelegationScope",
    "DelegationScopeError",
    "EvidenceReceipt",
    "ExpiredProofError",
    "HistoricalEvidenceReport",
    "HttpDelegationResolver",
    "HttpJwksResolver",
    "InMemoryDelegationResolver",
    "InMemoryJwksResolver",
    "InvalidSignatureError",
    "JwksResolver",
    "KeyLifecycleFact",
    "KeyNotFoundError",
    "KeyStatusAtTime",
    "MissingProofError",
    "ProofError",
    "ScopeMismatchError",
    "TamperedArtifactError",
    "VerifiedDelegation",
    "VerifiedProof",
    "build_acceptance_receipt",
    "build_delegation",
    "build_rejection_receipt",
    "evaluate_historical_evidence",
    "generate_signing_keypair",
    "private_key_from_pem",
    "private_key_to_pem",
    "public_key_from_jwk",
    "public_key_to_jwk",
    "sign_artifact",
    "verify_artifact_proof",
    "verify_artifact_proof_with_delegation",
]
