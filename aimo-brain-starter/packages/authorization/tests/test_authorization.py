from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from authorization import evaluate_binding_action

pytestmark = pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed")

_POLICY = """
package aimo.test.v1

default decision := {"allow": false, "human_approval_required": false, "deny_reason": "no_match"}

decision := {"allow": true, "human_approval_required": false, "deny_reason": null} if {
    input.counterparty_organization_id == "org_good"
}

decision := {"allow": false, "human_approval_required": false, "deny_reason": "not_approved"} if {
    input.counterparty_organization_id != "org_good"
}
"""


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    (tmp_path / "policy.rego").write_text(_POLICY, encoding="utf-8")
    return tmp_path


def test_allows_approved_counterparty(bundle_dir: Path) -> None:
    decision = evaluate_binding_action(
        action="authorize_agreement",
        organization_id="org_me",
        counterparty_organization_id="org_good",
        agreement_id="agr_1",
        content_hash="sha256:abc",
        terms={"total_amount": "10.00"},
        bundle_dir=bundle_dir,
        query="data.aimo.test.v1.decision",
        policy_version="v1",
    )
    assert decision.allow is True
    assert decision.organization_id == "org_me"


def test_denies_unapproved_counterparty(bundle_dir: Path) -> None:
    decision = evaluate_binding_action(
        action="authorize_agreement",
        organization_id="org_me",
        counterparty_organization_id="org_bad",
        agreement_id="agr_1",
        content_hash="sha256:abc",
        terms={"total_amount": "10.00"},
        bundle_dir=bundle_dir,
        query="data.aimo.test.v1.decision",
        policy_version="v1",
    )
    assert decision.allow is False
    assert decision.deny_reason == "not_approved"


def test_policy_outage_fails_closed(bundle_dir: Path) -> None:
    decision = evaluate_binding_action(
        action="authorize_agreement",
        organization_id="org_me",
        counterparty_organization_id="org_good",
        agreement_id="agr_1",
        content_hash="sha256:abc",
        terms={"total_amount": "10.00"},
        bundle_dir=bundle_dir,
        query="data.aimo.test.v1.decision",
        policy_version="v1",
        opa_binary="opa-binary-that-does-not-exist",
    )
    assert decision.allow is False
