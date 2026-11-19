"""Industry-independent OPA policy evaluation (M5).

Each organization evaluates its own policy independently, against its own
versioned Rego bundle, with no shared process or shared state between
buyer and supplier. This package knows nothing about budgets, margins or
SKUs — only the generic input/output contract and how to invoke
``opa eval`` safely. ``industries/food-wholesale`` owns the actual
bundle contents (``packages/policy-engine`` just runs whatever bundle it
is pointed at).

Deny-by-default (CLAUDE.md, docs/SECURITY.md): any OPA invocation that
times out, exits non-zero, produces unparseable output, or produces
output that does not match the required ``{"allow", "human_approval_required",
"deny_reason"}`` shape is turned into an explicit ``allow=False`` decision
with a ``deny_reason`` explaining why evaluation itself failed — never
silently skipped and never treated as "maybe allowed."
"""

from __future__ import annotations

from policy_engine.evaluator import (
    PolicyDecision,
    PolicyEvaluationError,
    evaluate_policy,
)

__all__ = [
    "PolicyDecision",
    "PolicyEvaluationError",
    "evaluate_policy",
]
