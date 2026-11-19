from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from policy_engine import evaluate_policy

OPA_AVAILABLE = shutil.which("opa") is not None

pytestmark = pytest.mark.skipif(OPA_AVAILABLE is False, reason="opa binary not installed")

_TEST_POLICY = """
package aimo.test.v1

default decision := {"allow": false, "human_approval_required": false, "deny_reason": "no_match"}

decision := {"allow": true, "human_approval_required": false, "deny_reason": null} if {
    input.amount <= 100
}

decision := {"allow": false, "human_approval_required": false, "deny_reason": "too_large"} if {
    input.amount > 100
}
"""


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    (tmp_path / "policy.rego").write_text(_TEST_POLICY, encoding="utf-8")
    return tmp_path


def test_allowed_decision(bundle_dir: Path) -> None:
    decision = evaluate_policy(
        bundle_dir=bundle_dir,
        query="data.aimo.test.v1.decision",
        policy_version="v1",
        organization_id="org_test",
        input_data={"amount": 10},
    )
    assert decision.allow is True
    assert decision.human_approval_required is False
    assert decision.deny_reason is None
    assert decision.policy_version == "v1"
    assert decision.organization_id == "org_test"
    assert len(decision.input_digest) == 64


def test_denied_decision(bundle_dir: Path) -> None:
    decision = evaluate_policy(
        bundle_dir=bundle_dir,
        query="data.aimo.test.v1.decision",
        policy_version="v1",
        organization_id="org_test",
        input_data={"amount": 1000},
    )
    assert decision.allow is False
    assert decision.deny_reason == "too_large"


def test_same_input_produces_same_digest_regardless_of_key_order(bundle_dir: Path) -> None:
    a = evaluate_policy(
        bundle_dir=bundle_dir,
        query="data.aimo.test.v1.decision",
        policy_version="v1",
        organization_id="org_test",
        input_data={"amount": 10, "extra": "x"},
    )
    b = evaluate_policy(
        bundle_dir=bundle_dir,
        query="data.aimo.test.v1.decision",
        policy_version="v1",
        organization_id="org_test",
        input_data={"extra": "x", "amount": 10},
    )
    assert a.input_digest == b.input_digest


def test_missing_bundle_directory_denies_by_default(tmp_path: Path) -> None:
    decision = evaluate_policy(
        bundle_dir=tmp_path / "does-not-exist",
        query="data.aimo.test.v1.decision",
        policy_version="v1",
        organization_id="org_test",
        input_data={"amount": 1},
    )
    assert decision.allow is False
    assert decision.deny_reason is not None
    assert "policy_evaluation_failed" in decision.deny_reason


def test_missing_opa_binary_denies_by_default(bundle_dir: Path) -> None:
    decision = evaluate_policy(
        bundle_dir=bundle_dir,
        query="data.aimo.test.v1.decision",
        policy_version="v1",
        organization_id="org_test",
        input_data={"amount": 1},
        opa_binary="opa-binary-that-does-not-exist",
    )
    assert decision.allow is False
    assert "not found" in (decision.deny_reason or "")


def test_timeout_denies_by_default(bundle_dir: Path) -> None:
    decision = evaluate_policy(
        bundle_dir=bundle_dir,
        query="data.aimo.test.v1.decision",
        policy_version="v1",
        organization_id="org_test",
        input_data={"amount": 1},
        timeout_seconds=0.0000001,
    )
    assert decision.allow is False
    assert decision.deny_reason is not None


def test_malformed_policy_output_denies_by_default(tmp_path: Path) -> None:
    (tmp_path / "policy.rego").write_text(
        "package aimo.test.v1\ndecision := 42\n", encoding="utf-8"
    )
    decision = evaluate_policy(
        bundle_dir=tmp_path,
        query="data.aimo.test.v1.decision",
        policy_version="v1",
        organization_id="org_test",
        input_data={"amount": 1},
    )
    assert decision.allow is False
    assert "policy_evaluation_failed" in (decision.deny_reason or "")


def test_unknown_query_path_denies_by_default(bundle_dir: Path) -> None:
    decision = evaluate_policy(
        bundle_dir=bundle_dir,
        query="data.aimo.nonexistent.v9.decision",
        policy_version="v9",
        organization_id="org_test",
        input_data={"amount": 1},
    )
    assert decision.allow is False
    assert "policy_evaluation_failed" in (decision.deny_reason or "")
