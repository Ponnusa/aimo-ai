from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from identity.portal_approval import (
    PortalApprovalError,
    issue_portal_approval_token,
    peek_issuer,
    peek_kid,
    verify_portal_approval_token,
)

ORG = "org_fictional_bistro_01"
NEGOTIATION_ID = "neg_01"
CONTENT_HASH = "sha256:abc"


def _issue(private_key: Ed25519PrivateKey, **overrides) -> str:
    kwargs = {
        "organization_id": ORG,
        "approver_subject": "oidc|approver-1",
        "negotiation_id": NEGOTIATION_ID,
        "content_hash": CONTENT_HASH,
        "private_key": private_key,
        "kid": "portal-k1",
    }
    kwargs.update(overrides)
    return issue_portal_approval_token(**kwargs)


def test_valid_assertion_verifies() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = _issue(private_key)
    claims = verify_portal_approval_token(
        token,
        public_key=private_key.public_key(),
        organization_id=ORG,
        negotiation_id=NEGOTIATION_ID,
        content_hash=CONTENT_HASH,
    )
    assert claims.approver_subject == "oidc|approver-1"
    assert claims.issuer == "aimo-portal"


def test_peek_issuer_and_kid_without_verifying() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = _issue(private_key)
    assert peek_issuer(token) == "aimo-portal"
    assert peek_kid(token) == "portal-k1"


def test_wrong_organization_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = _issue(private_key)
    with pytest.raises(PortalApprovalError):
        verify_portal_approval_token(
            token,
            public_key=private_key.public_key(),
            organization_id="org_someone_else",
            negotiation_id=NEGOTIATION_ID,
            content_hash=CONTENT_HASH,
        )


def test_wrong_content_hash_rejected() -> None:
    """A token legitimately issued for one proposal revision must not
    authorize a decision on a different (even later, same-negotiation)
    content_hash."""
    private_key = Ed25519PrivateKey.generate()
    token = _issue(private_key)
    with pytest.raises(PortalApprovalError):
        verify_portal_approval_token(
            token,
            public_key=private_key.public_key(),
            organization_id=ORG,
            negotiation_id=NEGOTIATION_ID,
            content_hash="sha256:different",
        )


def test_wrong_key_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    attacker_key = Ed25519PrivateKey.generate()
    token = _issue(attacker_key)
    with pytest.raises(PortalApprovalError):
        verify_portal_approval_token(
            token,
            public_key=private_key.public_key(),
            organization_id=ORG,
            negotiation_id=NEGOTIATION_ID,
            content_hash=CONTENT_HASH,
        )


def test_expired_assertion_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = _issue(private_key, validity_seconds=-10)
    with pytest.raises(PortalApprovalError):
        verify_portal_approval_token(
            token,
            public_key=private_key.public_key(),
            organization_id=ORG,
            negotiation_id=NEGOTIATION_ID,
            content_hash=CONTENT_HASH,
        )
