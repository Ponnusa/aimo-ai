from __future__ import annotations

import json
from pathlib import Path

import pytest
from message_integrity import (
    DelegationAudienceError,
    DelegationScope,
    InMemoryDelegationResolver,
    InMemoryJwksResolver,
    InvalidSignatureError,
    ScopeMismatchError,
    VerifiedDelegation,
    build_delegation,
    generate_signing_keypair,
    sign_artifact,
    verify_artifact_proof,
    verify_artifact_proof_with_delegation,
)
from message_integrity.keys import public_key_to_jwk

ORG_A = "org_fictional_bistro_01"
ORG_B = "org_fictional_wholesale_01"
NETWORK_AUDIENCE = "aimo-network:test"


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


def _make_delegation(
    *,
    root_private_key,
    root_kid: str,
    agent_id: str,
    delegated_kid: str,
    artifact_types: tuple[str, ...],
    audience: str = NETWORK_AUDIENCE,
    protocol_version: str = "v1",
):
    delegated_private, delegated_public = generate_signing_keypair()
    delegated_jwk = public_key_to_jwk(delegated_public, kid=delegated_kid)
    token = build_delegation(
        root_private_key=root_private_key,
        issuing_kid=root_kid,
        organization_id=ORG_A,
        agent_id=agent_id,
        audience=audience,
        delegated_kid=delegated_kid,
        delegated_public_key_jwk=delegated_jwk,
        scope=DelegationScope(artifact_types=artifact_types, protocol_version=protocol_version),
    )
    return token, delegated_private, delegated_public


@pytest.fixture
def root_keypair():
    return generate_signing_keypair()


@pytest.fixture
def jwks_resolver(root_keypair):
    private_key, public_key = root_keypair
    resolver = InMemoryJwksResolver()
    resolver.register(organization_id=ORG_A, kid="org_a-root1", public_key=public_key)
    return resolver


@pytest.mark.asyncio
async def test_delegated_key_signs_an_in_scope_artifact_successfully(root_keypair, jwks_resolver):
    root_private, _ = root_keypair
    token, delegated_private, _ = _make_delegation(
        root_private_key=root_private,
        root_kid="org_a-root1",
        agent_id="agent_buyer_01",
        delegated_kid="org_a-agent01-k1",
        artifact_types=("request_for_quote", "quote"),
    )
    delegation_resolver = InMemoryDelegationResolver()
    delegation_resolver.register(await _verify_for_registration(token, jwks_resolver))

    signed = sign_artifact(
        _artifact(),
        private_key=delegated_private,
        kid="org_a-agent01-k1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    verified = await verify_artifact_proof_with_delegation(
        signed,
        jwks_resolver=jwks_resolver,
        delegation_resolver=delegation_resolver,
        expected_recipient_organization_id=ORG_B,
        network_audience=NETWORK_AUDIENCE,
        protocol_version="v1",
    )
    assert verified.attribution == "delegated_agent"
    assert verified.agent_id == "agent_buyer_01"
    assert verified.typ_version == "v1"


@pytest.mark.asyncio
async def test_out_of_scope_artifact_type_rejected_before_business_logic(
    root_keypair, jwks_resolver
):
    """ADR-017 test 15: a quote-only delegation must not authorize a
    commitment-type artifact, even though the signature itself is
    perfectly valid."""
    root_private, _ = root_keypair
    token, delegated_private, _ = _make_delegation(
        root_private_key=root_private,
        root_kid="org_a-root1",
        agent_id="agent_buyer_01",
        delegated_kid="org_a-agent01-k1",
        artifact_types=("request_for_quote", "quote"),
    )
    delegation_resolver = InMemoryDelegationResolver()
    delegation_resolver.register(await _verify_for_registration(token, jwks_resolver))

    signed = sign_artifact(
        _artifact(type="order_commit_request"),
        private_key=delegated_private,
        kid="org_a-agent01-k1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    with pytest.raises(ScopeMismatchError):
        await verify_artifact_proof_with_delegation(
            signed,
            jwks_resolver=jwks_resolver,
            delegation_resolver=delegation_resolver,
            expected_recipient_organization_id=ORG_B,
            network_audience=NETWORK_AUDIENCE,
            protocol_version="v1",
        )


@pytest.mark.asyncio
async def test_wrong_network_audience_rejected(root_keypair, jwks_resolver):
    """ADR-017 test 16."""
    root_private, _ = root_keypair
    token, delegated_private, _ = _make_delegation(
        root_private_key=root_private,
        root_kid="org_a-root1",
        agent_id="agent_buyer_01",
        delegated_kid="org_a-agent01-k1",
        artifact_types=("request_for_quote",),
        audience="aimo-network:staging",
    )
    with pytest.raises(DelegationAudienceError):
        await _verify_for_registration(
            token, jwks_resolver, expected_audience="aimo-network:production"
        )


@pytest.mark.asyncio
async def test_delegation_lookup_failure_has_no_fallback_to_legacy_path(jwks_resolver):
    """ADR-017 test 19: a kid that resolves to neither a root key nor a
    live delegation is rejected outright — there is no retry against
    the other keyspace."""
    # Sign with a key that was never delegated and is not a root key.
    stray_private, _ = generate_signing_keypair()
    signed = sign_artifact(
        _artifact(),
        private_key=stray_private,
        kid="org_a-never-delegated",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    empty_delegation_resolver = InMemoryDelegationResolver()
    with pytest.raises(InvalidSignatureError):
        await verify_artifact_proof_with_delegation(
            signed,
            jwks_resolver=jwks_resolver,
            delegation_resolver=empty_delegation_resolver,
            expected_recipient_organization_id=ORG_B,
            network_audience=NETWORK_AUDIENCE,
            protocol_version="v1",
        )


@pytest.mark.asyncio
async def test_delegation_claiming_wrong_agent_is_rejected(root_keypair, jwks_resolver):
    """A delegation issued to agent A's key must not authorize an
    artifact that claims to be from agent B, even if both are signed
    with the exact delegated key."""
    root_private, _ = root_keypair
    token, delegated_private, _ = _make_delegation(
        root_private_key=root_private,
        root_kid="org_a-root1",
        agent_id="agent_buyer_01",
        delegated_kid="org_a-agent01-k1",
        artifact_types=("request_for_quote",),
    )
    delegation_resolver = InMemoryDelegationResolver()
    delegation_resolver.register(await _verify_for_registration(token, jwks_resolver))

    signed = sign_artifact(
        _artifact(),
        private_key=delegated_private,
        kid="org_a-agent01-k1",
        organization_id=ORG_A,
        agent_id="agent_buyer_99",  # not the delegated agent
        recipient_organization_id=ORG_B,
    )
    with pytest.raises(ScopeMismatchError):
        await verify_artifact_proof_with_delegation(
            signed,
            jwks_resolver=jwks_resolver,
            delegation_resolver=delegation_resolver,
            expected_recipient_organization_id=ORG_B,
            network_audience=NETWORK_AUDIENCE,
            protocol_version="v1",
        )


@pytest.mark.asyncio
async def test_legacy_root_signed_artifact_still_verifies_with_organization_attribution(
    root_keypair, jwks_resolver
):
    """ADR-017: a root-signed artifact (the only kind that existed
    before this slice) must continue to work unchanged, now explicitly
    labeled attribution='organization', typ_version='legacy'."""
    root_private, _ = root_keypair
    signed = sign_artifact(
        _artifact(),
        private_key=root_private,
        kid="org_a-root1",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    delegation_resolver = InMemoryDelegationResolver()
    verified = await verify_artifact_proof_with_delegation(
        signed,
        jwks_resolver=jwks_resolver,
        delegation_resolver=delegation_resolver,
        expected_recipient_organization_id=ORG_B,
        network_audience=NETWORK_AUDIENCE,
        protocol_version="v1",
    )
    assert verified.attribution == "organization"
    assert verified.typ_version == "v1"  # signed by this slice's sign_artifact


@pytest.mark.asyncio
async def test_pre_slice2_signed_artifact_verifies_as_legacy_typ(root_keypair, jwks_resolver):
    """A proof signed exactly the way pre-slice-2 code signed it (no
    explicit 'typ' header at all) must still verify, recorded as
    typ_version='legacy' — no previously-valid artifact is invalidated."""
    import jwt as _jwt

    root_private, _ = root_keypair
    artifact = _artifact()
    claims = {
        "iss": ORG_A,
        "sub": "agent_buyer_01",
        "aud": ORG_B,
        "neg": artifact["transaction_id"],
        "typ": artifact["type"],
        "ver": artifact["version"],
        "hash": artifact.get("content_hash"),
        "jti": "jti-legacy-01",
        "iat": 1700000000,
        "exp": 2000000000,
    }
    from message_integrity.proofs import _artifact_digest  # noqa: SLF001

    claims["artifact_digest"] = _artifact_digest(artifact)
    token = _jwt.encode(claims, root_private, algorithm="EdDSA", headers={"kid": "org_a-root1"})
    signed = dict(artifact)
    signed["proof"] = {"alg": "EdDSA", "kid": "org_a-root1", "jws": token}

    verified = await verify_artifact_proof(
        signed, jwks_resolver=jwks_resolver, expected_recipient_organization_id=ORG_B
    )
    assert verified.attribution == "organization"
    assert verified.typ_version == "legacy"


_VECTOR_07_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "specs"
    / "test-vectors"
    / "vector_07_delegation.json"
)


def _load_delegation_vector_07() -> dict:
    return json.loads(_VECTOR_07_PATH.read_text(encoding="utf-8"))


async def _verify_for_registration(
    token: str, jwks_resolver, *, expected_audience: str = NETWORK_AUDIENCE
) -> VerifiedDelegation:
    from message_integrity.delegations import verify_delegation_token

    return await verify_delegation_token(
        token, jwks_resolver=jwks_resolver, expected_audience=expected_audience
    )


@pytest.mark.asyncio
async def test_delegation_vector_07_verifies_end_to_end() -> None:
    """The committed cross-language vector (vector_07_delegation.json)
    verifies through the real Python implementation — both the
    delegation itself and the artifact it covers, with the expected
    attribution/typ_version/scope. The Node.js harness independently
    verifies the exact same file (see
    docs/specs/test-vectors/cross-lang-harness/verify-delegation.mjs)."""
    from message_integrity import InMemoryJwksResolver
    from message_integrity.delegations import verify_delegation_token
    from message_integrity.keys import public_key_from_jwk

    vector = _load_delegation_vector_07()

    resolver = InMemoryJwksResolver()
    resolver.register(
        organization_id="org_vector_buyer_01",
        kid="org_vector_buyer_01-root-k1",
        public_key=public_key_from_jwk(vector["root_public_key_jwk"]),
    )
    delegation = await verify_delegation_token(
        vector["delegation_jws"],
        jwks_resolver=resolver,
        expected_audience=vector["network_audience"],
    )
    assert delegation.agent_id == vector["expected_delegation_claims"]["sub"]
    assert delegation.scope.artifact_types == tuple(
        vector["expected_delegation_claims"]["scope"]["artifact_types"]
    )

    delegation_resolver = InMemoryDelegationResolver()
    delegation_resolver.register(delegation)

    verified = await verify_artifact_proof_with_delegation(
        vector["signed_artifact"],
        jwks_resolver=resolver,
        delegation_resolver=delegation_resolver,
        expected_recipient_organization_id=vector["expected_recipient_organization_id"],
        network_audience=vector["network_audience"],
        protocol_version="v1",
    )
    expected = vector["expected_verified_claims"]
    assert verified.organization_id == expected["organization_id"]
    assert verified.agent_id == expected["agent_id"]
    assert verified.attribution == expected["attribution"]
    assert verified.typ_version == expected["typ_version"]
