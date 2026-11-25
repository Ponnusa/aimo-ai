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


@pytest.mark.asyncio
async def test_altered_content_with_otherwise_identical_signing_produces_a_different_digest():
    """Merge-review addition: proves the digest half of the receipt
    genuinely distinguishes different CONTENT signed by the same key --
    this is the half of disambiguation ``artifact_digest`` is actually
    responsible for."""
    private_key, public_key = generate_signing_keypair()
    resolver = InMemoryJwksResolver()
    resolver.register(organization_id=ORG_A, kid="org_a-root1", public_key=public_key)

    signed_original = sign_artifact(
        _artifact(),
        private_key=private_key,
        kid="org_a-root1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    signed_altered = sign_artifact(
        _artifact(payload={"lines": [{"sku": "different"}], "credential": "x"}),
        private_key=private_key,
        kid="org_a-root1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )

    verified_original = await verify_artifact_proof(
        signed_original, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
    )
    verified_altered = await verify_artifact_proof(
        signed_altered, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
    )
    receipt_original = build_acceptance_receipt(verified_original, kid_used="org_a-root1")
    receipt_altered = build_acceptance_receipt(verified_altered, kid_used="org_a-root1")

    assert receipt_original.kid_used == receipt_altered.kid_used == "org_a-root1"
    assert receipt_original.artifact_digest != receipt_altered.artifact_digest


@pytest.mark.asyncio
async def test_substituted_proof_over_identical_content_shares_a_digest_but_differs_in_kid_used():
    """Merge-review addition, proving the exact gap the review raised:
    the SAME artifact content, signed twice by two different, equally
    valid keys, produces the SAME ``artifact_digest`` in both receipts
    -- the digest alone cannot tell these two proofs apart. ``kid_used``
    is what actually disambiguates them. A consumer that only compares
    ``artifact_digest`` would wrongly treat a substituted proof as
    identical to the original; the combination of both fields is
    required, exactly as now documented in this module's docstring."""
    private_key_1, public_key_1 = generate_signing_keypair()
    private_key_2, public_key_2 = generate_signing_keypair()
    resolver = InMemoryJwksResolver()
    resolver.register(organization_id=ORG_A, kid="org_a-root1", public_key=public_key_1)
    resolver.register(organization_id=ORG_A, kid="org_a-root2", public_key=public_key_2)

    content = _artifact()
    signed_by_key_1 = sign_artifact(
        dict(content),
        private_key=private_key_1,
        kid="org_a-root1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    signed_by_key_2 = sign_artifact(
        dict(content),
        private_key=private_key_2,
        kid="org_a-root2",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )

    verified_1 = await verify_artifact_proof(
        signed_by_key_1, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
    )
    verified_2 = await verify_artifact_proof(
        signed_by_key_2, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
    )
    receipt_1 = build_acceptance_receipt(verified_1, kid_used="org_a-root1")
    receipt_2 = build_acceptance_receipt(verified_2, kid_used="org_a-root2")

    # The digest alone is identical -- same content, different key.
    assert receipt_1.artifact_digest == receipt_2.artifact_digest
    # Only kid_used distinguishes which proof was actually checked.
    assert receipt_1.kid_used != receipt_2.kid_used
