from __future__ import annotations

import pytest

from identity import (
    ServiceCredentialExpiredError,
    ServiceCredentialMalformedError,
    ServiceCredentialScopeError,
    ServiceCredentialSignatureError,
    issue_service_credential,
    verify_service_credential,
)

SECRET = "test-signing-secret"


def test_valid_credential_round_trips() -> None:
    token = issue_service_credential(
        organization_id="org_fictional_bistro_01",
        scope="manage_agents",
        signing_secret=SECRET,
        ttl_seconds=3600,
    )
    claims = verify_service_credential(token, signing_secret=SECRET, required_scope="manage_agents")
    assert claims.organization_id == "org_fictional_bistro_01"
    assert claims.scope == "manage_agents"
    assert claims.expires_at > claims.issued_at


def test_tampered_payload_is_rejected() -> None:
    token = issue_service_credential(
        organization_id="org_fictional_bistro_01",
        scope="manage_agents",
        signing_secret=SECRET,
    )
    payload_b64, signature_b64 = token.split(".")
    tampered = f"{payload_b64}x.{signature_b64}"
    with pytest.raises(ServiceCredentialSignatureError):
        verify_service_credential(tampered, signing_secret=SECRET)


def test_wrong_signing_secret_is_rejected() -> None:
    token = issue_service_credential(
        organization_id="org_fictional_bistro_01",
        scope="manage_agents",
        signing_secret=SECRET,
    )
    with pytest.raises(ServiceCredentialSignatureError):
        verify_service_credential(token, signing_secret="a-different-secret")


def test_expired_credential_is_rejected() -> None:
    token = issue_service_credential(
        organization_id="org_fictional_bistro_01",
        scope="manage_agents",
        signing_secret=SECRET,
        ttl_seconds=60,
        now=1_000_000,
    )
    with pytest.raises(ServiceCredentialExpiredError):
        verify_service_credential(token, signing_secret=SECRET, now=1_000_100)


def test_wrong_scope_is_rejected() -> None:
    token = issue_service_credential(
        organization_id="org_fictional_bistro_01",
        scope="manage_agents",
        signing_secret=SECRET,
    )
    with pytest.raises(ServiceCredentialScopeError):
        verify_service_credential(token, signing_secret=SECRET, required_scope="something_else")


def test_malformed_token_is_rejected() -> None:
    with pytest.raises(ServiceCredentialMalformedError):
        verify_service_credential("not-a-real-token", signing_secret=SECRET)


def test_agent_bound_credential_round_trips() -> None:
    token = issue_service_credential(
        organization_id="org_fictional_bistro_01",
        scope="negotiate",
        signing_secret=SECRET,
        agent_id="agent_buyer_01",
    )
    claims = verify_service_credential(
        token, signing_secret=SECRET, required_scope="negotiate", required_agent_id="agent_buyer_01"
    )
    assert claims.agent_id == "agent_buyer_01"


def test_agent_bound_credential_rejects_wrong_agent_id() -> None:
    token = issue_service_credential(
        organization_id="org_fictional_bistro_01",
        scope="negotiate",
        signing_secret=SECRET,
        agent_id="agent_buyer_01",
    )
    with pytest.raises(ServiceCredentialScopeError):
        verify_service_credential(
            token, signing_secret=SECRET, required_agent_id="agent_someone_else"
        )


def test_approver_bound_credential_round_trips() -> None:
    token = issue_service_credential(
        organization_id="org_fictional_bistro_01",
        scope="approve",
        signing_secret=SECRET,
        approver_id="approver_01",
    )
    claims = verify_service_credential(token, signing_secret=SECRET, required_scope="approve")
    assert claims.approver_id == "approver_01"
