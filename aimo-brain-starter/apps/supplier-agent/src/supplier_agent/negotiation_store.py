"""Supplier-side negotiation persistence (M4).

Owns ``supplier_negotiations``/``supplier_proposals``/
``supplier_processed_artifacts`` — this organization's own,
independently-recorded negotiation history and inbound-message dedup
cache. Never reads or writes any other service's database. See the
matching module in ``buyer_agent`` for the (structurally identical,
never shared) buyer-side store.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from trade_protocol import TradeState, assert_valid_transition

from supplier_agent.models import (
    SupplierNegotiationRecord,
    SupplierProcessedArtifactRecord,
    SupplierProposalRecord,
)


class NegotiationNotFoundError(Exception):
    """Raised when an inbound message references a negotiation_id this
    supplier has no record of."""


class StaleProposalReferenceError(Exception):
    """Raised when an inbound message's supersedes/accepted proposal_id
    does not match this negotiation's current recorded head — an
    optimistic-concurrency rejection, not a silent reinterpretation."""


class NegotiationExpiredError(Exception):
    """Raised when the negotiation's current proposal has passed its
    expires_at. The negotiation is marked EXPIRED rather than mutated
    further."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def check_replay(
    session_factory: async_sessionmaker, artifact_id: str
) -> dict[str, Any] | None:
    """If ``artifact_id`` has already been processed, return the exact
    response this supplier gave last time, so a retried/duplicated
    inbound message is never reprocessed."""
    async with session_factory() as session:
        record = await session.get(SupplierProcessedArtifactRecord, artifact_id)
        if record is None:
            return None
        return json.loads(record.response_payload_json)


async def cache_response(
    session_factory: async_sessionmaker,
    *,
    artifact_id: str,
    negotiation_id: str,
    response: dict[str, Any],
) -> None:
    async with session_factory() as session:
        existing = await session.get(SupplierProcessedArtifactRecord, artifact_id)
        if existing is not None:
            return
        session.add(
            SupplierProcessedArtifactRecord(
                artifact_id=artifact_id,
                negotiation_id=negotiation_id,
                response_payload_json=json.dumps(response),
                created_at=_now(),
            )
        )
        await session.commit()


async def get_negotiation(
    session_factory: async_sessionmaker, negotiation_id: str
) -> SupplierNegotiationRecord | None:
    async with session_factory() as session:
        return await session.get(SupplierNegotiationRecord, negotiation_id)


async def start_negotiation(
    session_factory: async_sessionmaker,
    *,
    negotiation_id: str,
    counterparty_organization_id: str,
    counterparty_agent_id: str,
    rfq_artifact: dict[str, Any],
) -> None:
    async with session_factory() as session:
        existing = await session.get(SupplierNegotiationRecord, negotiation_id)
        if existing is not None:
            return
        now = _now()
        session.add(
            SupplierNegotiationRecord(
                negotiation_id=negotiation_id,
                counterparty_organization_id=counterparty_organization_id,
                counterparty_agent_id=counterparty_agent_id,
                state=TradeState.REQUESTED.value,
                current_proposal_id=rfq_artifact["proposal_id"],
                round_number=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            SupplierProposalRecord(
                proposal_id=rfq_artifact["proposal_id"],
                negotiation_id=negotiation_id,
                version=rfq_artifact["version"],
                artifact_type=rfq_artifact["type"],
                content_hash=None,
                supersedes_proposal_id=None,
                payload_json=json.dumps(rfq_artifact),
                created_at=now,
            )
        )
        await session.commit()


async def advance_negotiation(
    session_factory: async_sessionmaker,
    *,
    negotiation_id: str,
    incoming_supersedes_proposal_id: str | None,
    artifact: dict[str, Any],
    new_state: TradeState,
    round_number: int,
) -> SupplierNegotiationRecord:
    """Validate (negotiation exists, not expired, supersedes matches the
    current head, transition is legal) then persist ``artifact`` as the
    new head. Raises a typed error on any failure — never silently
    applies a stale or out-of-order update."""
    async with session_factory() as session:
        record = await session.get(SupplierNegotiationRecord, negotiation_id)
        if record is None:
            raise NegotiationNotFoundError(negotiation_id)

        current_proposal = await session.get(SupplierProposalRecord, record.current_proposal_id)
        if current_proposal is not None:
            current_artifact = json.loads(current_proposal.payload_json)
            expires_at = datetime.fromisoformat(current_artifact["expires_at"])
            if expires_at.tzinfo is not None:
                expires_at = expires_at.astimezone(UTC).replace(tzinfo=None)
            if datetime.now(UTC).replace(tzinfo=None) > expires_at:
                record.state = TradeState.EXPIRED.value
                record.updated_at = _now()
                await session.merge(record)
                await session.commit()
                raise NegotiationExpiredError(negotiation_id)

        if (
            incoming_supersedes_proposal_id is not None
            and incoming_supersedes_proposal_id != record.current_proposal_id
        ):
            raise StaleProposalReferenceError(
                f"Expected supersedes_proposal_id={record.current_proposal_id!r}, "
                f"got {incoming_supersedes_proposal_id!r}."
            )

        assert_valid_transition(TradeState(record.state), new_state)

        now = _now()
        session.add(
            SupplierProposalRecord(
                proposal_id=artifact["proposal_id"],
                negotiation_id=negotiation_id,
                version=artifact["version"],
                artifact_type=artifact["type"],
                content_hash=artifact.get("content_hash"),
                supersedes_proposal_id=artifact.get("supersedes_proposal_id")
                or artifact.get("agreement_of_proposal_id"),
                payload_json=json.dumps(artifact),
                created_at=now,
            )
        )
        record.state = new_state.value
        record.current_proposal_id = artifact["proposal_id"]
        record.round_number = round_number
        record.updated_at = now
        await session.merge(record)
        await session.commit()
        return record


async def mark_terminal(
    session_factory: async_sessionmaker, *, negotiation_id: str, state: TradeState
) -> None:
    async with session_factory() as session:
        record = await session.get(SupplierNegotiationRecord, negotiation_id)
        if record is None:
            raise NegotiationNotFoundError(negotiation_id)
        assert_valid_transition(TradeState(record.state), state)
        record.state = state.value
        record.updated_at = _now()
        await session.merge(record)
        await session.commit()


async def get_proposal(
    session_factory: async_sessionmaker, proposal_id: str
) -> SupplierProposalRecord | None:
    async with session_factory() as session:
        return await session.get(SupplierProposalRecord, proposal_id)


async def list_proposals(
    session_factory: async_sessionmaker, negotiation_id: str
) -> list[SupplierProposalRecord]:
    async with session_factory() as session:
        result = await session.execute(
            select(SupplierProposalRecord)
            .where(SupplierProposalRecord.negotiation_id == negotiation_id)
            .order_by(SupplierProposalRecord.version)
        )
        return list(result.scalars().all())
