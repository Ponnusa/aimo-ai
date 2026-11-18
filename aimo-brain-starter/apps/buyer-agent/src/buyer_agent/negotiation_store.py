"""Buyer-side negotiation persistence (M4).

Owns ``buyer_negotiations``/``buyer_proposals`` — this organization's own,
independently-recorded negotiation history. Never reads or writes any
other service's database. See the matching module in ``supplier_agent``
for the (structurally identical, never shared) supplier-side store.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from trade_protocol import TradeState, assert_valid_transition

from buyer_agent.models import BuyerNegotiationRecord, BuyerProposalRecord


class NegotiationNotFoundError(Exception):
    """Raised when a negotiation_id this agent should already know about
    cannot be found in its own database."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def start_negotiation(
    session_factory: async_sessionmaker,
    *,
    negotiation_id: str,
    counterparty_organization_id: str,
    counterparty_agent_id: str,
    rfq_artifact: dict[str, Any],
) -> None:
    """Record a brand-new negotiation at REQUESTED, with its RFQ as
    proposal version 1. If a record for this negotiation_id already
    exists (a restart replaying the same intent), this is a no-op rather
    than a duplicate insert — recovery, not re-creation."""
    async with session_factory() as session:
        existing = await session.get(BuyerNegotiationRecord, negotiation_id)
        if existing is not None:
            return
        now = _now()
        session.add(
            BuyerNegotiationRecord(
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
            BuyerProposalRecord(
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


async def get_negotiation(
    session_factory: async_sessionmaker, negotiation_id: str
) -> BuyerNegotiationRecord | None:
    async with session_factory() as session:
        return await session.get(BuyerNegotiationRecord, negotiation_id)


async def record_received_artifact(
    session_factory: async_sessionmaker,
    *,
    negotiation_id: str,
    artifact: dict[str, Any],
    new_state: TradeState,
    round_number: int,
) -> None:
    """Record an artifact received from the supplier (quote, counter, or
    proposed agreement) and advance this negotiation's own state.
    Validates the transition before writing — never silently applied."""
    async with session_factory() as session:
        record = await session.get(BuyerNegotiationRecord, negotiation_id)
        if record is None:
            raise NegotiationNotFoundError(negotiation_id)
        assert_valid_transition(TradeState(record.state), new_state)

        now = _now()
        session.add(
            BuyerProposalRecord(
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


async def record_sent_artifact(
    session_factory: async_sessionmaker,
    *,
    negotiation_id: str,
    artifact: dict[str, Any],
    new_state: TradeState,
    round_number: int,
) -> None:
    """Record an artifact this agent itself sent (counterproposal, accept,
    reject) — same bookkeeping as :func:`record_received_artifact`, kept
    as a separate name only for readability at call sites."""
    await record_received_artifact(
        session_factory,
        negotiation_id=negotiation_id,
        artifact=artifact,
        new_state=new_state,
        round_number=round_number,
    )


async def list_proposals(
    session_factory: async_sessionmaker, negotiation_id: str
) -> list[BuyerProposalRecord]:
    async with session_factory() as session:
        result = await session.execute(
            select(BuyerProposalRecord)
            .where(BuyerProposalRecord.negotiation_id == negotiation_id)
            .order_by(BuyerProposalRecord.version)
        )
        return list(result.scalars().all())
