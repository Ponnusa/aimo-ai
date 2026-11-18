from __future__ import annotations

import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from supplier_agent import negotiation_store
from supplier_agent.negotiation_store import (
    NegotiationExpiredError,
    StaleProposalReferenceError,
)
from trade_protocol import NegotiationTransitionError, TradeState

from database import Base, new_engine


@pytest.fixture
async def session_factory():
    path = os.path.join(
        tempfile.gettempdir(), f"aimo_supplier_store_test_{uuid.uuid4().hex}.sqlite3"
    )
    engine = new_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _rfq(*, proposal_id: str = "prop_rfq", expires_in: timedelta = timedelta(days=1)) -> dict:
    now = datetime.now(UTC)
    return {
        "schema": "https://schemas.aimobrain.example/food-wholesale/request_for_quote/v0.1",
        "artifact_id": "art_rfq",
        "transaction_id": "neg_01",
        "correlation_id": "corr_01",
        "sender_agent_id": "agent_buyer_01",
        "recipient_agent_id": "agent_supplier_01",
        "organization_id": "org_fictional_bistro_01",
        "domain": "food-wholesale",
        "type": "request_for_quote",
        "version": 1,
        "proposal_id": proposal_id,
        "created_at": now.isoformat(),
        "expires_at": (now + expires_in).isoformat(),
        "payload": {"lines": [], "delivery_location_ref": "site_01"},
    }


def _quote_artifact(*, proposal_id: str, supersedes: str, version: int = 2) -> dict:
    now = datetime.now(UTC)
    return {
        "schema": "https://schemas.aimobrain.example/food-wholesale/quote/v0.1",
        "artifact_id": f"art_{proposal_id}",
        "transaction_id": "neg_01",
        "correlation_id": "corr_01",
        "sender_agent_id": "agent_supplier_01",
        "recipient_agent_id": "agent_buyer_01",
        "organization_id": "org_fictional_wholesale_01",
        "domain": "food-wholesale",
        "type": "quote",
        "version": version,
        "proposal_id": proposal_id,
        "supersedes_proposal_id": supersedes,
        "content_hash": "deadbeef",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "payload": {"lines": [], "currency": "EUR"},
    }


@pytest.mark.asyncio
async def test_start_negotiation_is_idempotent(session_factory) -> None:
    rfq = _rfq()
    await negotiation_store.start_negotiation(
        session_factory,
        negotiation_id="neg_01",
        counterparty_organization_id="org_fictional_bistro_01",
        counterparty_agent_id="agent_buyer_01",
        rfq_artifact=rfq,
    )
    await negotiation_store.start_negotiation(
        session_factory,
        negotiation_id="neg_01",
        counterparty_organization_id="org_fictional_bistro_01",
        counterparty_agent_id="agent_buyer_01",
        rfq_artifact=rfq,
    )
    proposals = await negotiation_store.list_proposals(session_factory, "neg_01")
    assert len(proposals) == 1


@pytest.mark.asyncio
async def test_advance_negotiation_rejects_stale_supersedes_reference(session_factory) -> None:
    await negotiation_store.start_negotiation(
        session_factory,
        negotiation_id="neg_01",
        counterparty_organization_id="org_fictional_bistro_01",
        counterparty_agent_id="agent_buyer_01",
        rfq_artifact=_rfq(),
    )
    with pytest.raises(StaleProposalReferenceError):
        await negotiation_store.advance_negotiation(
            session_factory,
            negotiation_id="neg_01",
            incoming_supersedes_proposal_id="prop_does_not_exist",
            artifact=_quote_artifact(proposal_id="prop_quote", supersedes="prop_does_not_exist"),
            new_state=TradeState.PROPOSED,
            round_number=1,
        )


@pytest.mark.asyncio
async def test_advance_negotiation_rejects_illegal_transition(session_factory) -> None:
    await negotiation_store.start_negotiation(
        session_factory,
        negotiation_id="neg_01",
        counterparty_organization_id="org_fictional_bistro_01",
        counterparty_agent_id="agent_buyer_01",
        rfq_artifact=_rfq(),
    )
    with pytest.raises(NegotiationTransitionError):
        await negotiation_store.advance_negotiation(
            session_factory,
            negotiation_id="neg_01",
            incoming_supersedes_proposal_id="prop_rfq",
            artifact=_quote_artifact(proposal_id="prop_x", supersedes="prop_rfq"),
            # REQUESTED -> PENDING_AUTHORIZATION is illegal.
            new_state=TradeState.PENDING_AUTHORIZATION,
            round_number=1,
        )


@pytest.mark.asyncio
async def test_advance_negotiation_rejects_expired_proposal(session_factory) -> None:
    await negotiation_store.start_negotiation(
        session_factory,
        negotiation_id="neg_01",
        counterparty_organization_id="org_fictional_bistro_01",
        counterparty_agent_id="agent_buyer_01",
        rfq_artifact=_rfq(expires_in=timedelta(seconds=-1)),
    )
    with pytest.raises(NegotiationExpiredError):
        await negotiation_store.advance_negotiation(
            session_factory,
            negotiation_id="neg_01",
            incoming_supersedes_proposal_id="prop_rfq",
            artifact=_quote_artifact(proposal_id="prop_quote", supersedes="prop_rfq"),
            new_state=TradeState.PROPOSED,
            round_number=1,
        )
    record = await negotiation_store.get_negotiation(session_factory, "neg_01")
    assert record is not None
    assert record.state == TradeState.EXPIRED.value


@pytest.mark.asyncio
async def test_check_replay_returns_cached_response_without_reprocessing(session_factory) -> None:
    assert await negotiation_store.check_replay(session_factory, "art_rfq") is None
    cached_response = {"type": "quote", "proposal_id": "prop_quote"}
    await negotiation_store.cache_response(
        session_factory, artifact_id="art_rfq", negotiation_id="neg_01", response=cached_response
    )
    replayed = await negotiation_store.check_replay(session_factory, "art_rfq")
    assert replayed == cached_response

    # Caching again for the same artifact_id must not overwrite or error.
    await negotiation_store.cache_response(
        session_factory,
        artifact_id="art_rfq",
        negotiation_id="neg_01",
        response={"type": "something_else"},
    )
    assert await negotiation_store.check_replay(session_factory, "art_rfq") == cached_response
