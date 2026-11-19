from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trade_protocol.negotiation import canonicalize_terms, content_hash

#: Required shape of a policy bundle's decision rule result. Anything
#: missing or additional is still accepted (forward-compatible), but
#: these three keys must exist and have the right types, or the decision
#: is treated as a failed evaluation (deny-by-default).
_REQUIRED_KEYS = ("allow", "human_approval_required", "deny_reason")


class PolicyEvaluationError(Exception):
    """Raised internally to describe *why* evaluation failed; callers
    should prefer reading :attr:`PolicyDecision.deny_reason` — this
    exception type exists so :func:`evaluate_policy` has one place that
    turns every failure mode into the same deny-by-default shape."""


@dataclass(frozen=True)
class PolicyDecision:
    allow: bool
    human_approval_required: bool
    deny_reason: str | None
    policy_version: str
    input_digest: str
    organization_id: str
    raw_result: dict[str, Any]
    evaluated_at: str


def _deny(
    *, reason: str, policy_version: str, input_digest: str, organization_id: str
) -> PolicyDecision:
    return PolicyDecision(
        allow=False,
        human_approval_required=False,
        deny_reason=reason,
        policy_version=policy_version,
        input_digest=input_digest,
        organization_id=organization_id,
        raw_result={},
        evaluated_at=datetime.now(UTC).isoformat(),
    )


def evaluate_policy(
    *,
    bundle_dir: Path | str,
    query: str,
    policy_version: str,
    organization_id: str,
    input_data: dict[str, Any],
    opa_binary: str = "opa",
    timeout_seconds: float = 5.0,
) -> PolicyDecision:
    """Evaluate ``query`` (e.g. ``data.aimo.buyer.v1.decision``) against
    the Rego bundle at ``bundle_dir`` using the real ``opa eval`` CLI as a
    bounded subprocess — never a server call, never an in-process Rego
    reimplementation. ``input_data`` is canonicalized and hashed
    (reusing ``trade_protocol.negotiation``) before being sent, so the
    exact input a decision was made against can always be reproduced and
    compared later.

    Every failure mode (binary missing, timeout, non-zero exit,
    unparseable or malformed output) returns a ``PolicyDecision`` with
    ``allow=False`` rather than raising — the caller never has to
    remember to fail closed, because this function already does.
    """
    digest = content_hash(input_data)
    bundle_path = Path(bundle_dir)

    if not bundle_path.is_dir():
        return _deny(
            reason=f"policy_evaluation_failed: bundle directory not found: {bundle_path}",
            policy_version=policy_version,
            input_digest=digest,
            organization_id=organization_id,
        )

    command = [
        opa_binary,
        "eval",
        "--data",
        str(bundle_path),
        "--stdin-input",
        "--format",
        "json",
        query,
    ]

    try:
        completed = subprocess.run(  # noqa: ASYNC221 - bounded, synchronous by design
            command,
            input=canonicalize_terms(input_data),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return _deny(
            reason=f"policy_evaluation_failed: opa binary {opa_binary!r} not found",
            policy_version=policy_version,
            input_digest=digest,
            organization_id=organization_id,
        )
    except subprocess.TimeoutExpired:
        return _deny(
            reason=f"policy_evaluation_failed: opa eval timed out after {timeout_seconds}s",
            policy_version=policy_version,
            input_digest=digest,
            organization_id=organization_id,
        )

    if completed.returncode != 0:
        return _deny(
            reason=(
                "policy_evaluation_failed: opa eval exited "
                f"{completed.returncode}: {completed.stderr.strip()[:500]}"
            ),
            policy_version=policy_version,
            input_digest=digest,
            organization_id=organization_id,
        )

    try:
        result = _extract_result(completed.stdout, query=query)
    except PolicyEvaluationError as exc:
        return _deny(
            reason=f"policy_evaluation_failed: {exc}",
            policy_version=policy_version,
            input_digest=digest,
            organization_id=organization_id,
        )

    return PolicyDecision(
        allow=bool(result["allow"]),
        human_approval_required=bool(result["human_approval_required"]),
        deny_reason=result["deny_reason"],
        policy_version=policy_version,
        input_digest=digest,
        organization_id=organization_id,
        raw_result=result,
        evaluated_at=datetime.now(UTC).isoformat(),
    )


def _extract_result(stdout: str, *, query: str) -> dict[str, Any]:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise PolicyEvaluationError(f"opa eval produced unparseable JSON: {exc}") from exc

    try:
        expressions = parsed["result"][0]["expressions"]
        value = expressions[0]["value"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PolicyEvaluationError(
            f"opa eval result did not contain a value for query {query!r}"
        ) from exc

    if not isinstance(value, dict):
        raise PolicyEvaluationError(f"decision result is not an object: {value!r}")

    missing = [key for key in _REQUIRED_KEYS if key not in value]
    if missing:
        raise PolicyEvaluationError(f"decision result missing required keys: {missing}")

    if not isinstance(value["allow"], bool) or not isinstance(
        value["human_approval_required"], bool
    ):
        raise PolicyEvaluationError(
            "decision result 'allow'/'human_approval_required' must be bool"
        )

    return value
