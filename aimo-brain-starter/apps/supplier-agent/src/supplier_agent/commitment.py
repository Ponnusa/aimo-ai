"""Supplier-side authorization-evidence exchange and commitment (M5).

Dispatched from ``supplier_agent.a2a_executor`` for the ``authorization_
evidence`` and ``order_commit_request`` artifact types, over the same
direct A2A connection M4 already uses for negotiation — no new
transport. Reuses ``supplier_agent.authorization`` for the independent
OPA decision/approval bookkeeping and ``supplier_agent.mcp_client`` for
the actual (idempotent, mock) order execution — this module only wires
the two together and builds/validates the wire artifacts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from food_wholesale import (
    build_authorization_evidence,
    build_order_confirmation,
    compute_idempotency_key,
)
from mcp_integration import McpToolError
from message_integrity import VerifiedProof
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from supplier_agent import authorization, mcp_client, negotiation_store
from supplier_agent.models import SupplierOrderRecord
from supplier_agent.negotiation_store import NegotiationNotFoundError
from supplier_agent.settings import SupplierAgentSettings


class CommitmentError(Exception):
    """Raised for any commitment-path failure the executor should turn
    into a failed A2A task — never silently swallowed."""


class NotAuthorizedError(CommitmentError):
    """Raised when a commit is attempted before both organizations hold
    matching, allowed authorization evidence for the exact content_hash."""


async def handle_authorization_evidence(
    payload: dict[str, Any],
    *,
    verified: VerifiedProof,
    settings: SupplierAgentSettings,
    session_factory: async_sessionmaker,
) -> dict[str, Any]:
    """``verified`` is the already-cryptographically-verified sender of
    ``payload`` (signature, replay, registry/relationship checks all
    already done by ``supplier_agent.a2a_executor`` before this handler
    ever runs). Records the buyer's evidence, independently evaluates
    this organization's own policy/approval for the same exact
    content_hash, and replies with this organization's own evidence
    artifact (allow=False/deny_reason="human_approval_pending" if a
    human approval is still outstanding -- never a premature allow)."""
    negotiation_id = payload["payload"]["agreement_id"]
    content_hash_value = payload["payload"]["content_hash"]

    try:
        await authorization.record_counterparty_evidence(
            session_factory,
            negotiation_id=negotiation_id,
            content_hash=content_hash_value,
            artifact=payload,
            verified=verified,
        )
    except NegotiationNotFoundError as exc:
        raise CommitmentError(str(exc)) from exc

    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=negotiation_id
    )

    negotiation = await negotiation_store.get_negotiation(session_factory, negotiation_id)
    if negotiation is None:
        raise CommitmentError(f"Unknown negotiation {negotiation_id!r}.")

    if outcome.human_approval_required and outcome.approval_status != "APPROVED":
        return build_authorization_evidence(
            artifact_id=str(uuid.uuid4()),
            transaction_id=negotiation_id,
            correlation_id=payload["correlation_id"],
            sender_agent_id=settings.supplier_agent_id,
            recipient_agent_id=payload["sender_agent_id"],
            organization_id=settings.supplier_organization_id,
            proposal_id=str(uuid.uuid4()),
            version=payload["version"] + 1,
            agreement_id=negotiation_id,
            content_hash_value=content_hash_value,
            allow=False,
            human_approval_required=True,
            deny_reason=authorization.APPROVAL_PENDING_REASON,
            policy_version=outcome.policy_version,
            policy_input_digest=outcome.policy_input_digest,
        )

    return build_authorization_evidence(
        artifact_id=str(uuid.uuid4()),
        transaction_id=negotiation_id,
        correlation_id=payload["correlation_id"],
        sender_agent_id=settings.supplier_agent_id,
        recipient_agent_id=payload["sender_agent_id"],
        organization_id=settings.supplier_organization_id,
        proposal_id=str(uuid.uuid4()),
        version=payload["version"] + 1,
        agreement_id=negotiation_id,
        content_hash_value=content_hash_value,
        allow=outcome.allow,
        human_approval_required=outcome.human_approval_required,
        deny_reason=outcome.deny_reason,
        policy_version=outcome.policy_version,
        policy_input_digest=outcome.policy_input_digest,
    )


async def handle_order_commit_request(
    payload: dict[str, Any],
    *,
    verified: VerifiedProof,
    settings: SupplierAgentSettings,
    session_factory: async_sessionmaker,
) -> dict[str, Any]:
    """``verified`` is the already-cryptographically-verified sender of
    ``payload`` (``supplier_agent.a2a_executor`` runs signature/replay/
    registry checks before this handler is ever called). Only reachable
    once this organization independently holds ``allow=True`` evidence
    from *both* sides for the exact same content_hash
    (:func:`supplier_agent.authorization.is_mutually_authorized`) --
    otherwise raises :class:`NotAuthorizedError` rather than touching
    the mock order adapter at all."""
    negotiation_id = payload["payload"]["agreement_id"]
    content_hash_value = payload["payload"]["content_hash"]
    idempotency_key = payload["payload"]["idempotency_key"]

    negotiation = await negotiation_store.get_negotiation(session_factory, negotiation_id)
    if negotiation is None:
        raise CommitmentError(f"Unknown negotiation {negotiation_id!r}.")

    if (
        verified.organization_id != negotiation.counterparty_organization_id
        or verified.agent_id != negotiation.counterparty_agent_id
    ):
        raise NotAuthorizedError("Verified sender does not match this negotiation's counterparty.")

    expected_key = compute_idempotency_key(
        content_hash_value=content_hash_value,
        buyer_organization_id=negotiation.counterparty_organization_id,
        supplier_organization_id=settings.supplier_organization_id,
    )
    if idempotency_key != expected_key:
        raise NotAuthorizedError("Idempotency key does not match the agreed terms.")

    if not await authorization.is_mutually_authorized(
        session_factory, negotiation_id=negotiation_id, content_hash=content_hash_value
    ):
        raise NotAuthorizedError(
            f"Negotiation {negotiation_id!r} is not mutually authorized for "
            f"content_hash {content_hash_value!r}."
        )

    try:
        execution = await mcp_client.execute_order(
            settings=settings,
            idempotency_key=idempotency_key,
            agreement_id=negotiation_id,
            content_hash=content_hash_value,
            buyer_organization_id=negotiation.counterparty_organization_id,
        )
        status = "CONFIRMED" if execution.status == "confirmed" else "FAILED"
        external_order_id: str | None = execution.external_order_id
    except McpToolError:
        status = "RECONCILIATION_REQUIRED"
        external_order_id = None

    await _record_order(
        session_factory,
        negotiation_id=negotiation_id,
        content_hash=content_hash_value,
        idempotency_key=idempotency_key,
        status=status,
        external_order_id=external_order_id,
    )

    return build_order_confirmation(
        artifact_id=str(uuid.uuid4()),
        transaction_id=negotiation_id,
        correlation_id=payload["correlation_id"],
        sender_agent_id=settings.supplier_agent_id,
        recipient_agent_id=payload["sender_agent_id"],
        organization_id=settings.supplier_organization_id,
        proposal_id=str(uuid.uuid4()),
        version=payload["version"] + 1,
        agreement_id=negotiation_id,
        idempotency_key=idempotency_key,
        status=status,
        external_order_id=external_order_id,
    )


async def _record_order(
    session_factory: async_sessionmaker,
    *,
    negotiation_id: str,
    content_hash: str,
    idempotency_key: str,
    status: str,
    external_order_id: str | None,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session:
        existing = (
            await session.execute(
                select(SupplierOrderRecord).where(
                    SupplierOrderRecord.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.status = status
            existing.external_order_id = external_order_id
            existing.updated_at = now
            await session.merge(existing)
        else:
            session.add(
                SupplierOrderRecord(
                    negotiation_id=negotiation_id,
                    content_hash=content_hash,
                    idempotency_key=idempotency_key,
                    state=status,
                    external_order_id=external_order_id,
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.commit()
