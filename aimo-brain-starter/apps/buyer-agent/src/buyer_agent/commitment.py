"""Buyer-side authorization-evidence exchange and commitment (M5).

The buyer always drives this over the existing direct A2A connection
(same pattern ``buyer_agent.client`` already uses for negotiation): send
this organization's own evidence, receive the supplier's reply (its own
independent evidence) in the same synchronous task, and -- only once
both sides' evidence allow the exact same content_hash -- send the
commit request and receive the order_confirmation. No new transport, no
new service.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from food_wholesale import (
    build_authorization_evidence,
    build_order_commit_request,
    compute_idempotency_key,
    validate_against_schema,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from buyer_agent import authorization, message_auth, negotiation_store
from buyer_agent.client import (
    AgentUnavailableError,
    TaskNotCompletedError,
    _send_negotiation_artifact,
    discover_supplier_agent_card,
)
from buyer_agent.models import BuyerOrderReconciliationEventRecord, BuyerOrderRecord
from buyer_agent.settings import BuyerAgentSettings


class CommitmentError(Exception):
    """Raised for any commitment-path failure — never silently swallowed."""


class ApprovalPendingError(CommitmentError):
    """Raised when this organization's own human approval has not yet
    been granted — the caller must retry after an approver decides, not
    treat this as a failure."""


class CounterpartyApprovalPendingError(CommitmentError):
    """Raised when the supplier's own evidence reports its human
    approval is still outstanding — retry later, not a denial."""


class DeniedError(CommitmentError):
    """Raised when either side's policy denies the agreement outright."""


async def run_authorization_and_commitment(
    negotiation_id: str, *, settings: BuyerAgentSettings, session_factory: async_sessionmaker
) -> dict[str, Any]:
    """Drive this negotiation from ``PENDING_AUTHORIZATION`` through to a
    final ``order_confirmation`` (or raise a typed, retryable/terminal
    error). Idempotent and safe to call repeatedly — every step checks
    existing durable state before acting again."""
    own_outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=negotiation_id
    )
    if not own_outcome.allow:
        raise DeniedError(f"Own policy denied: {own_outcome.deny_reason}")
    if own_outcome.human_approval_required and own_outcome.approval_status != "APPROVED":
        raise ApprovalPendingError(
            f"Own human approval still pending (approval_id={own_outcome.approval_id})."
        )

    content_hash = own_outcome.content_hash
    own_evidence = await authorization.get_own_evidence(
        session_factory, negotiation_id=negotiation_id, content_hash=content_hash
    )
    if own_evidence is None:
        raise ApprovalPendingError("Own evidence not yet finalized.")

    negotiation = await negotiation_store.get_negotiation(session_factory, negotiation_id)
    if negotiation is None:
        raise CommitmentError(f"Unknown negotiation {negotiation_id!r}.")

    own_evidence_artifact = build_authorization_evidence(
        artifact_id=str(uuid.uuid4()),
        transaction_id=negotiation_id,
        correlation_id=str(uuid.uuid4()),
        sender_agent_id=settings.buyer_agent_id,
        recipient_agent_id=negotiation.counterparty_agent_id,
        organization_id=settings.buyer_organization_id,
        proposal_id=str(uuid.uuid4()),
        version=1,
        agreement_id=negotiation_id,
        content_hash_value=content_hash,
        allow=own_evidence.allow,
        human_approval_required=own_evidence.human_approval_required,
        deny_reason=own_evidence.deny_reason,
        policy_version=own_evidence.policy_version,
        policy_input_digest=own_evidence.policy_input_digest,
    )

    agent_card = await discover_supplier_agent_card(settings.supplier_a2a_url)
    try:
        counterparty_response, verified = await _send_negotiation_artifact(
            agent_card,
            own_evidence_artifact,
            settings=settings,
            session_factory=session_factory,
            recipient_organization_id=negotiation.counterparty_organization_id,
        )
    except message_auth.MessageAuthenticationError as exc:
        raise CommitmentError(f"Could not authenticate supplier's evidence: {exc}") from exc
    validate_against_schema("authorization_evidence", counterparty_response)

    await authorization.record_counterparty_evidence(
        session_factory,
        negotiation_id=negotiation_id,
        content_hash=content_hash,
        artifact=counterparty_response,
        verified=verified,
    )

    if counterparty_response["payload"]["deny_reason"] == authorization.APPROVAL_PENDING_REASON:
        raise CounterpartyApprovalPendingError("Supplier's own human approval is still pending.")
    if not counterparty_response["payload"]["allow"]:
        raise DeniedError(
            f"Supplier policy denied: {counterparty_response['payload']['deny_reason']}"
        )

    if not await authorization.is_mutually_authorized(
        session_factory, negotiation_id=negotiation_id, content_hash=content_hash
    ):
        raise CommitmentError("Evidence exchange completed but mutual authorization not met.")

    return await _commit(
        negotiation_id,
        settings=settings,
        session_factory=session_factory,
        negotiation=negotiation,
        content_hash=content_hash,
        agent_card=agent_card,
    )


async def _commit(
    negotiation_id: str,
    *,
    settings: BuyerAgentSettings,
    session_factory: async_sessionmaker,
    negotiation: Any,
    content_hash: str,
    agent_card: Any,
) -> dict[str, Any]:
    idempotency_key = compute_idempotency_key(
        content_hash_value=content_hash,
        buyer_organization_id=settings.buyer_organization_id,
        supplier_organization_id=negotiation.counterparty_organization_id,
    )

    existing_order = await _get_order(session_factory, idempotency_key=idempotency_key)
    if existing_order is not None and existing_order.state in ("CONFIRMED", "FAILED"):
        return {
            "type": "order_confirmation",
            "payload": {
                "status": existing_order.state,
                "external_order_id": existing_order.external_order_id,
                "idempotency_key": idempotency_key,
            },
        }

    now = datetime.now(UTC).replace(tzinfo=None)
    if existing_order is None:
        # First attempt: persist intent (state=COMMITTING) *before* any
        # network call, in the same local transaction — a crash right
        # after this write and before a response is always recoverable
        # by retrying with this exact row and idempotency_key, never by
        # assuming the commit did or did not happen.
        async with session_factory() as session:
            order = BuyerOrderRecord(
                negotiation_id=negotiation_id,
                content_hash=content_hash,
                idempotency_key=idempotency_key,
                state="COMMITTING",
                attempts=1,
                created_at=now,
                updated_at=now,
            )
            session.add(order)
            await session.flush()
            session.add(
                BuyerOrderReconciliationEventRecord(
                    order_id=order.id,
                    negotiation_id=negotiation_id,
                    previous_state="NONE",
                    new_state="COMMITTING",
                    detail="commit intent persisted before order_commit_request sent",
                    created_at=now,
                )
            )
            await session.commit()
    else:
        # A retry (worker-driven recovery after a crash, timeout, or an
        # ambiguous RECONCILIATION_REQUIRED outcome) — same idempotency
        # key, a freshly re-signed order_commit_request (proofs expire
        # and jti is never reused), bounded attempt count for the
        # worker's own backoff/give-up policy.
        async with session_factory() as session:
            record = await session.get(BuyerOrderRecord, existing_order.id)
            previous_state = record.state
            record.attempts += 1
            record.state = "COMMITTING"
            record.updated_at = now
            await session.merge(record)
            session.add(
                BuyerOrderReconciliationEventRecord(
                    order_id=record.id,
                    negotiation_id=negotiation_id,
                    previous_state=previous_state,
                    new_state="COMMITTING",
                    detail=f"retry attempt {record.attempts}",
                    created_at=now,
                )
            )
            await session.commit()

    commit_request = build_order_commit_request(
        artifact_id=str(uuid.uuid4()),
        transaction_id=negotiation_id,
        correlation_id=str(uuid.uuid4()),
        sender_agent_id=settings.buyer_agent_id,
        recipient_agent_id=negotiation.counterparty_agent_id,
        organization_id=settings.buyer_organization_id,
        proposal_id=str(uuid.uuid4()),
        version=1,
        agreement_id=negotiation_id,
        content_hash_value=content_hash,
        idempotency_key=idempotency_key,
    )
    try:
        confirmation, _verified = await _send_negotiation_artifact(
            agent_card,
            commit_request,
            settings=settings,
            session_factory=session_factory,
            recipient_organization_id=negotiation.counterparty_organization_id,
        )
    except message_auth.MessageAuthenticationError as exc:
        raise CommitmentError(f"Could not authenticate order_confirmation: {exc}") from exc
    except (AgentUnavailableError, TaskNotCompletedError) as exc:
        # Never assume a timeout/unreachable-agent means the order was
        # not created — leave the row at COMMITTING (already written
        # above) for the worker to retry with the same idempotency_key;
        # the supplier's own UNIQUE constraint on it is what actually
        # prevents a duplicate order, regardless of how many times this
        # is retried.
        raise CommitmentError(f"Commit request did not complete: {exc}") from exc
    validate_against_schema("order_confirmation", confirmation)

    await _finalize_order(
        session_factory,
        idempotency_key=idempotency_key,
        state=confirmation["payload"]["status"],
        external_order_id=confirmation["payload"]["external_order_id"],
    )
    return confirmation


async def _get_order(
    session_factory: async_sessionmaker, *, idempotency_key: str
) -> BuyerOrderRecord | None:
    async with session_factory() as session:
        result = await session.execute(
            select(BuyerOrderRecord).where(BuyerOrderRecord.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()


async def _finalize_order(
    session_factory: async_sessionmaker,
    *,
    idempotency_key: str,
    state: str,
    external_order_id: str | None,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session:
        result = await session.execute(
            select(BuyerOrderRecord).where(BuyerOrderRecord.idempotency_key == idempotency_key)
        )
        record = result.scalar_one()
        previous_state = record.state
        record.state = state
        record.external_order_id = external_order_id
        record.updated_at = now
        await session.merge(record)
        if previous_state != state:
            session.add(
                BuyerOrderReconciliationEventRecord(
                    order_id=record.id,
                    negotiation_id=record.negotiation_id,
                    previous_state=previous_state,
                    new_state=state,
                    detail=f"order_confirmation received: status={state!r}",
                    created_at=now,
                )
            )
        await session.commit()
