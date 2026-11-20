from __future__ import annotations

import pytest
from message_integrity import (
    ExpiredProofError,
    InMemoryJwksResolver,
    InvalidSignatureError,
    ScopeMismatchError,
    TamperedArtifactError,
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
        "payload": {"lines": []},
        "content_hash": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def resolver_and_key():
    private_key, public_key = generate_signing_keypair()
    resolver = InMemoryJwksResolver()
    resolver.register(organization_id=ORG_A, kid="org_a-k1", public_key=public_key)
    return resolver, private_key


@pytest.mark.asyncio
async def test_valid_proof_verifies(resolver_and_key) -> None:
    resolver, private_key = resolver_and_key
    signed = sign_artifact(
        _artifact(),
        private_key=private_key,
        kid="org_a-k1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    verified = await verify_artifact_proof(
        signed, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
    )
    assert verified.organization_id == ORG_A
    assert verified.agent_id == "agent_buyer_01"
    assert verified.negotiation_id == "txn_01"


@pytest.mark.asyncio
async def test_missing_proof_rejected(resolver_and_key) -> None:
    resolver, _ = resolver_and_key
    from message_integrity import MissingProofError

    with pytest.raises(MissingProofError):
        await verify_artifact_proof(
            _artifact(), jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
        )


@pytest.mark.asyncio
async def test_tampered_artifact_rejected(resolver_and_key) -> None:
    resolver, private_key = resolver_and_key
    signed = sign_artifact(
        _artifact(),
        private_key=private_key,
        kid="org_a-k1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    signed["payload"]["lines"] = [{"product_ref": "injected"}]
    with pytest.raises(TamperedArtifactError):
        await verify_artifact_proof(
            signed, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
        )


@pytest.mark.asyncio
async def test_scope_mismatch_rejected_when_negotiation_id_swapped(resolver_and_key) -> None:
    resolver, private_key = resolver_and_key
    signed = sign_artifact(
        _artifact(),
        private_key=private_key,
        kid="org_a-k1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    # Swap in a different negotiation's artifact fields without
    # re-signing -- the digest check would also catch this, but the
    # explicit scope-claim check gives a clearer failure.
    signed["transaction_id"] = "txn_02"
    with pytest.raises((TamperedArtifactError, ScopeMismatchError)):
        await verify_artifact_proof(
            signed, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
        )


@pytest.mark.asyncio
async def test_wrong_audience_rejected(resolver_and_key) -> None:
    resolver, private_key = resolver_and_key
    signed = sign_artifact(
        _artifact(),
        private_key=private_key,
        kid="org_a-k1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    with pytest.raises(InvalidSignatureError):
        await verify_artifact_proof(
            signed, jwks_resolver=resolver, expected_recipient_organization_id="org_someone_else"
        )


@pytest.mark.asyncio
async def test_unknown_key_rejected(resolver_and_key) -> None:
    resolver, private_key = resolver_and_key
    signed = sign_artifact(
        _artifact(),
        private_key=private_key,
        kid="org_a-k9-never-registered",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    with pytest.raises(InvalidSignatureError):
        await verify_artifact_proof(
            signed, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
        )


@pytest.mark.asyncio
async def test_signature_from_impersonating_key_rejected(resolver_and_key) -> None:
    """A different keypair (not registered for ORG_A) signs, claiming to
    be ORG_A -- impersonation without the real private key must fail."""
    resolver, _ = resolver_and_key
    attacker_private_key, _ = generate_signing_keypair()
    signed = sign_artifact(
        _artifact(),
        private_key=attacker_private_key,
        kid="org_a-k1",  # claims the victim's own kid
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    with pytest.raises(InvalidSignatureError):
        await verify_artifact_proof(
            signed, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
        )


@pytest.mark.asyncio
async def test_revoked_key_rejected(resolver_and_key) -> None:
    resolver, private_key = resolver_and_key
    signed = sign_artifact(
        _artifact(),
        private_key=private_key,
        kid="org_a-k1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    resolver.revoke(organization_id=ORG_A, kid="org_a-k1")
    with pytest.raises(InvalidSignatureError):
        await verify_artifact_proof(
            signed, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
        )


@pytest.mark.asyncio
async def test_expired_proof_rejected(resolver_and_key) -> None:
    resolver, private_key = resolver_and_key
    signed = sign_artifact(
        _artifact(),
        private_key=private_key,
        kid="org_a-k1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
        validity_seconds=-10,
    )
    with pytest.raises(ExpiredProofError):
        await verify_artifact_proof(
            signed, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
        )


@pytest.mark.asyncio
async def test_key_rotation_old_key_still_valid_until_revoked(resolver_and_key) -> None:
    resolver, old_private_key = resolver_and_key
    new_private_key, new_public_key = generate_signing_keypair()
    resolver.register(organization_id=ORG_A, kid="org_a-k2", public_key=new_public_key)

    old_signed = sign_artifact(
        _artifact(artifact_id="art_old"),
        private_key=old_private_key,
        kid="org_a-k1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    new_signed = sign_artifact(
        _artifact(artifact_id="art_new"),
        private_key=new_private_key,
        kid="org_a-k2",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    for artifact in (old_signed, new_signed):
        await verify_artifact_proof(
            artifact, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
        )

    resolver.revoke(organization_id=ORG_A, kid="org_a-k1")
    with pytest.raises(InvalidSignatureError):
        await verify_artifact_proof(
            old_signed, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
        )
    # The rotated-to key remains valid.
    await verify_artifact_proof(
        new_signed, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
    )


def test_sign_artifact_rejects_already_signed_artifact(resolver_and_key) -> None:
    resolver, private_key = resolver_and_key
    signed = sign_artifact(
        _artifact(),
        private_key=private_key,
        kid="org_a-k1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    with pytest.raises(ValueError):
        sign_artifact(
            signed,
            private_key=private_key,
            kid="org_a-k1",
            organization_id=ORG_A,
            agent_id="agent_buyer_01",
            recipient_organization_id=ORG_B,
        )
