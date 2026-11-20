"""Food-wholesale authorization/commitment artifact builders (M5).

Domain-specific half of M5's independent-policy/human-approval/
commitment workflow: this module knows what a "line item" is and how to
turn a ``proposed_agreement`` artifact's payload into the generic policy
input terms ``packages/policy_engine`` evaluates. The industry-independent
half (OPA subprocess invocation, deny-by-default, idempotency hashing
primitives) lives in ``packages/policy_engine``/``packages/authorization``/
``trade_protocol.negotiation`` and is only ever called from here or from
the agent apps, never re-implemented.

Nothing here decides whether an agreement is authorized — that is OPA's
job via ``packages/authorization.evaluate_binding_action``. This module
only builds/validates the wire artifacts that carry a decision already
made, and computes the deterministic idempotency key both organizations
derive identically from the same agreed terms.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from food_wholesale.schemas import validate_against_schema

_SCHEMA_BASE = "https://schemas.aimobrain.example/food-wholesale"


def agreement_terms_from_proposed_agreement(artifact: dict[str, Any]) -> dict[str, Any]:
    """Extract the generic policy-input terms from a ``proposed_agreement``
    artifact's payload: lines, currency, delivery terms and a computed
    decimal ``total_amount``. This is the *only* place food-wholesale
    terms are translated into the industry-independent shape OPA sees —
    core policy evaluation never learns what a "product_ref" is.
    """
    payload = artifact["payload"]
    total = Decimal("0")
    for line in payload["lines"]:
        total += Decimal(line["quantity"]) * Decimal(line["unit_price"])
    terms: dict[str, Any] = {
        "lines": payload["lines"],
        "currency": payload["currency"],
        "total_amount": str(total.quantize(Decimal("0.01"))),
    }
    if payload.get("delivery_terms") is not None:
        terms["delivery_terms"] = payload["delivery_terms"]
    return terms


def compute_idempotency_key(
    *, content_hash_value: str, buyer_organization_id: str, supplier_organization_id: str
) -> str:
    """Deterministic idempotency key derived only from the agreed terms'
    content_hash and both organization ids — stable across retries,
    never randomly regenerated per attempt (docs/SECURITY.md, M5)."""
    raw = f"{content_hash_value}:{buyer_organization_id}:{supplier_organization_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _envelope(
    *,
    artifact_id: str,
    transaction_id: str,
    correlation_id: str,
    sender_agent_id: str,
    recipient_agent_id: str,
    organization_id: str,
    artifact_type: str,
    version: int,
    proposal_id: str,
    payload: dict[str, Any],
    validity: timedelta,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "schema": f"{_SCHEMA_BASE}/{artifact_type}/v0.1",
        "artifact_id": artifact_id,
        "transaction_id": transaction_id,
        "correlation_id": correlation_id,
        "sender_agent_id": sender_agent_id,
        "recipient_agent_id": recipient_agent_id,
        "organization_id": organization_id,
        "domain": "food-wholesale",
        "type": artifact_type,
        "version": version,
        "proposal_id": proposal_id,
        "created_at": now.isoformat(),
        "expires_at": (now + validity).isoformat(),
        "payload": payload,
    }


def build_authorization_evidence(
    *,
    artifact_id: str,
    transaction_id: str,
    correlation_id: str,
    sender_agent_id: str,
    recipient_agent_id: str,
    organization_id: str,
    proposal_id: str,
    version: int,
    agreement_id: str,
    content_hash_value: str,
    allow: bool,
    human_approval_required: bool,
    deny_reason: str | None,
    policy_version: str,
    policy_input_digest: str,
    approval_id: str | None = None,
    approver_id: str | None = None,
    validity: timedelta = timedelta(hours=2),
) -> dict[str, Any]:
    """Build and validate an ``authorization_evidence`` artifact carrying
    one organization's own, independent policy decision (and approval,
    if one was required) for ``content_hash_value``. The caller attaches
    the per-artifact JWS ``proof`` separately, via
    ``message_integrity.sign_artifact`` (M5.1, docs/DECISIONS.md
    ADR-012) — this builder never embeds a raw credential."""
    artifact = _envelope(
        artifact_id=artifact_id,
        transaction_id=transaction_id,
        correlation_id=correlation_id,
        sender_agent_id=sender_agent_id,
        recipient_agent_id=recipient_agent_id,
        organization_id=organization_id,
        artifact_type="authorization_evidence",
        version=version,
        proposal_id=proposal_id,
        payload={
            "agreement_id": agreement_id,
            "content_hash": content_hash_value,
            "allow": allow,
            "human_approval_required": human_approval_required,
            "deny_reason": deny_reason,
            "policy_version": policy_version,
            "policy_input_digest": policy_input_digest,
            "approval_id": approval_id,
            "approver_id": approver_id,
        },
        validity=validity,
    )
    validate_against_schema("authorization_evidence", artifact)
    return artifact


def build_order_commit_request(
    *,
    artifact_id: str,
    transaction_id: str,
    correlation_id: str,
    sender_agent_id: str,
    recipient_agent_id: str,
    organization_id: str,
    proposal_id: str,
    version: int,
    agreement_id: str,
    content_hash_value: str,
    idempotency_key: str,
    validity: timedelta = timedelta(hours=1),
) -> dict[str, Any]:
    """Build and validate an ``order_commit_request`` artifact: the
    buyer's commitment intent, never itself the binding event."""
    artifact = _envelope(
        artifact_id=artifact_id,
        transaction_id=transaction_id,
        correlation_id=correlation_id,
        sender_agent_id=sender_agent_id,
        recipient_agent_id=recipient_agent_id,
        organization_id=organization_id,
        artifact_type="order_commit_request",
        version=version,
        proposal_id=proposal_id,
        payload={
            "agreement_id": agreement_id,
            "content_hash": content_hash_value,
            "idempotency_key": idempotency_key,
        },
        validity=validity,
    )
    validate_against_schema("order_commit_request", artifact)
    return artifact


def build_order_confirmation(
    *,
    artifact_id: str,
    transaction_id: str,
    correlation_id: str,
    sender_agent_id: str,
    recipient_agent_id: str,
    organization_id: str,
    proposal_id: str,
    version: int,
    agreement_id: str,
    idempotency_key: str,
    status: str,
    external_order_id: str | None,
    validity: timedelta = timedelta(days=30),
) -> dict[str, Any]:
    """Build and validate an ``order_confirmation`` artifact. ``status ==
    "CONFIRMED"`` is the exact point the simulated transaction becomes
    binding (docs/DECISIONS.md M5) — never the earlier commit request."""
    artifact = _envelope(
        artifact_id=artifact_id,
        transaction_id=transaction_id,
        correlation_id=correlation_id,
        sender_agent_id=sender_agent_id,
        recipient_agent_id=recipient_agent_id,
        organization_id=organization_id,
        artifact_type="order_confirmation",
        version=version,
        proposal_id=proposal_id,
        payload={
            "agreement_id": agreement_id,
            "idempotency_key": idempotency_key,
            "status": status,
            "external_order_id": external_order_id,
        },
        validity=validity,
    )
    validate_against_schema("order_confirmation", artifact)
    return artifact


__all__ = [
    "agreement_terms_from_proposed_agreement",
    "build_authorization_evidence",
    "build_order_commit_request",
    "build_order_confirmation",
    "compute_idempotency_key",
]
