from __future__ import annotations

import pytest
from message_integrity import (
    InMemoryJwksResolver,
    build_acceptance_receipt,
    build_rejection_receipt,
    generate_signing_keypair,
    sign_artifact,
    verify_artifact_proof,
)

ORG_A = "org_fictional_bistro_01"
ORG_B = "org_fictional_wholesale_01"


def _artifact(**overrides) -> dict:
    base = {
        "artifact_id": "art_01",
        "transaction_id": "txn_01",
        "correlation_id": "corr_01",
        "sender_agent_id": "agent_buyer_01",
        "recipient_agent_id": "agent_supplier_01",
        "organization_id": ORG_A,
        "type": "request_for_quote",
        "version": 1,
        "payload": {"lines": [], "credential": "super-secret-bearer-token"},
        "content_hash": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_acceptance_receipt_binds_digest_kid_and_outcome_without_retaining_credential():
    private_key, public_key = generate_signing_keypair()
    resolver = InMemoryJwksResolver()
    resolver.register(organization_id=ORG_A, kid="org_a-root1", public_key=public_key)
    signed = sign_artifact(
        _artifact(),
        private_key=private_key,
        kid="org_a-root1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    verified = await verify_artifact_proof(
        signed, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
    )
    receipt = build_acceptance_receipt(verified, kid_used="org_a-root1")

    assert receipt.verification_outcome == "accepted"
    assert receipt.kid_used == "org_a-root1"
    assert receipt.artifact_digest == verified.artifact_digest
    assert receipt.claimed_kid is None
    assert receipt.claimed_artifact_digest is None
    # The receipt is a dataclass of plain fields -- confirm none of them
    # is or contains the plaintext bearer credential the artifact's own
    # payload carried (ADR-011). A SHA-256 digest cannot be reversed to
    # recover it, which is the actual guarantee -- this assertion proves
    # the literal secret string is absent from every stored field.
    for value in vars(receipt).values():
        assert "super-secret-bearer-token" not in str(value)


def test_rejection_receipt_uses_claimed_fields_never_trusted_fields():
    private_key, _ = generate_signing_keypair()
    signed = sign_artifact(
        _artifact(),
        private_key=private_key,
        kid="org_a-root1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    receipt = build_rejection_receipt(signed, reason="signing key not found")

    assert receipt.verification_outcome == "rejected"
    assert receipt.rejection_reason == "signing key not found"
    assert receipt.claimed_kid == "org_a-root1"
    # Never populated on a rejection -- these are reserved for a
    # cryptographically confirmed acceptance only.
    assert receipt.kid_used is None
    assert receipt.artifact_digest is None
    for value in vars(receipt).values():
        assert "super-secret-bearer-token" not in str(value)


def test_rejection_receipt_on_completely_unparseable_artifact_never_raises():
    receipt = build_rejection_receipt({"not": "a valid artifact at all"}, reason="no proof field")
    assert receipt.verification_outcome == "rejected"
    assert receipt.claimed_kid is None
    assert receipt.claimed_artifact_digest is None
