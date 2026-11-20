from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from identity.dev_oidc import (
    DevOidcError,
    DisabledInProductionError,
    issue_dev_approval_token,
    verify_dev_approval_token,
)

ORG = "org_fictional_bistro_01"


def test_valid_token_verifies_in_local_environment() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = issue_dev_approval_token(
        organization_id=ORG, approver_id="approver_01", private_key=private_key, kid="k1"
    )
    claims = verify_dev_approval_token(
        token, environment="local", organization_id=ORG, public_key=private_key.public_key()
    )
    assert claims.approver_id == "approver_01"
    assert claims.organization_id == ORG
    assert claims.issuer == f"dev-oidc:{ORG}"
    assert "approver" in claims.roles


def test_disabled_in_production_environment() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = issue_dev_approval_token(
        organization_id=ORG, approver_id="approver_01", private_key=private_key, kid="k1"
    )
    with pytest.raises(DisabledInProductionError):
        verify_dev_approval_token(
            token,
            environment="production",
            organization_id=ORG,
            public_key=private_key.public_key(),
        )


def test_token_from_wrong_key_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    attacker_key = Ed25519PrivateKey.generate()
    token = issue_dev_approval_token(
        organization_id=ORG, approver_id="approver_01", private_key=attacker_key, kid="k1"
    )
    with pytest.raises(DevOidcError):
        verify_dev_approval_token(
            token, environment="local", organization_id=ORG, public_key=private_key.public_key()
        )


def test_token_for_different_organization_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = issue_dev_approval_token(
        organization_id=ORG, approver_id="approver_01", private_key=private_key, kid="k1"
    )
    with pytest.raises(DevOidcError):
        verify_dev_approval_token(
            token,
            environment="local",
            organization_id="org_someone_else",
            public_key=private_key.public_key(),
        )


def test_expired_token_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = issue_dev_approval_token(
        organization_id=ORG,
        approver_id="approver_01",
        private_key=private_key,
        kid="k1",
        validity_seconds=-10,
    )
    with pytest.raises(DevOidcError):
        verify_dev_approval_token(
            token, environment="local", organization_id=ORG, public_key=private_key.public_key()
        )
