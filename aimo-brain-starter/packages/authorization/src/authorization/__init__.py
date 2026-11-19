"""Binding policy decision package boundary.

**M5 status: real.** ``evaluate_binding_action`` runs the organization's
own versioned OPA bundle (via ``packages/policy_engine``'s bounded
``opa eval`` subprocess wrapper) against the exact immutable proposal
terms, and returns a :class:`policy_engine.PolicyDecision`. Deny-by-default
is inherited from ``policy_engine.evaluate_policy`` itself — any OPA
failure (missing binary, timeout, malformed output) already comes back
as ``allow=False``, so this function never has to remember to fail
closed.

Per CLAUDE.md's non-negotiable architecture, no binding action may ever
be approved by an LLM or by default — this is the one place either
organization's own code is allowed to ask "is this specific, hashed set
of terms allowed by my own policy?", and the answer always comes from
OPA, never a model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from policy_engine import PolicyDecision, evaluate_policy


def evaluate_binding_action(
    *,
    action: str,
    organization_id: str,
    counterparty_organization_id: str,
    agreement_id: str,
    content_hash: str,
    terms: dict[str, Any],
    bundle_dir: Path | str,
    query: str,
    policy_version: str,
    opa_binary: str = "opa",
    timeout_seconds: float = 5.0,
) -> PolicyDecision:
    """Evaluate ``action`` (e.g. ``"authorize_agreement"``) for one
    organization's own OPA bundle, over the exact immutable
    ``content_hash``-bound ``terms`` of a proposed agreement.

    This function never trusts a cached or re-derived price/quantity —
    callers must pass the generic terms extracted from the agreement
    artifact itself.
    """
    input_data = {
        "action": action,
        "organization_id": organization_id,
        "counterparty_organization_id": counterparty_organization_id,
        "agreement_id": agreement_id,
        "content_hash": content_hash,
        "terms": terms,
    }
    return evaluate_policy(
        bundle_dir=bundle_dir,
        query=query,
        policy_version=policy_version,
        organization_id=organization_id,
        input_data=input_data,
        opa_binary=opa_binary,
        timeout_seconds=timeout_seconds,
    )


__all__ = ["PolicyDecision", "evaluate_binding_action"]
