"""Supplier-side negotiation dispatch (M4).

Called from ``supplier_agent.a2a_executor`` once an inbound artifact has
already been schema-validated. Every quote line is sourced from this
organization's own ``supplier-mcp`` (via ``supplier_agent.mcp_client`` —
never invented here), every response is persisted via
``supplier_agent.negotiation_store`` before being returned, and every
``proposed_agreement`` is explicitly NONBINDING (``food_wholesale``
stamps this; nothing here can change it). Version numbers increase by
exactly 1 with every hop, regardless of which side sent the last message.

Counteroffer evaluation (``food_wholesale.negotiation.evaluate_counterproposal``)
is a deterministic rule over MCP-sourced reference prices. The optional
LLM rationale attached alongside a counter-quote (see
``_explain_counteroffer``) is a human-readable annotation only — it is
never consulted by the decision itself, per CLAUDE.md: "LLM outputs are
untrusted proposals. Never let an LLM bypass policy checks."
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from agent_runtime import LLMMessage, get_llm_adapter
from food_wholesale.negotiation import (
    build_proposed_agreement,
    build_quote,
    build_reject_proposal,
    evaluate_counterproposal,
)
from message_integrity import VerifiedProof
from sqlalchemy.ext.asyncio import async_sessionmaker
from trade_protocol import TradeState

from supplier_agent import negotiation_store
from supplier_agent.mcp_client import fetch_availability_matches
from supplier_agent.settings import SupplierAgentSettings


class NegotiationError(Exception):
    """Base class for negotiation-handling failures the executor turns
    into a failed A2A task."""


class ProposalMismatchError(NegotiationError):
    """Raised when an ``accept_proposal`` does not bind to this
    negotiation's exact current proposal_id and content_hash."""


class UnauthorizedCounterpartyError(NegotiationError):
    """Raised when an inbound artifact's organization_id/sender_agent_id
    does not match the organization this negotiation was actually
    started with. Negotiation IDs are not secrets (they travel in plain
    artifacts), so without this check any organization that learned
    another buyer's negotiation_id could inject counterproposals or
    accept/reject messages into a negotiation that is not theirs — a
    cross-tenant data/authority violation (docs/SECURITY.md)."""


def _verify_counterparty(negotiation: Any, verified: VerifiedProof) -> None:
    """``verified`` is the already-cryptographically-verified sender of
    the inbound artifact (M5.1) — checked here only for whether it
    matches *this* negotiation's actual recorded counterparty, never
    re-deriving identity from the unauthenticated payload fields
    (closing the M4 ADR-010 gap: this used to compare raw payload
    fields, not a verified signature)."""
    if (
        verified.organization_id != negotiation.counterparty_organization_id
        or verified.agent_id != negotiation.counterparty_agent_id
    ):
        raise UnauthorizedCounterpartyError(
            f"Verified sender organization_id={verified.organization_id!r}/"
            f"agent_id={verified.agent_id!r}, but negotiation "
            f"{verified.negotiation_id!r} belongs to "
            f"{negotiation.counterparty_organization_id!r}/"
            f"{negotiation.counterparty_agent_id!r}."
        )


async def _explain_counteroffer(*, llm_provider: str, countered_lines: list[dict[str, Any]]) -> str:
    """A purely explanatory, non-decisional rationale string. Never read
    by :func:`food_wholesale.negotiation.evaluate_counterproposal` or any
    state transition — decoration for audit/human readability only."""
    llm = get_llm_adapter(llm_provider)
    response = await llm.complete(
        [
            LLMMessage(
                role="user",
                content=f"Explain briefly why this counteroffer was evaluated: {countered_lines}",
            )
        ]
    )
    return response.content


async def handle_request_for_quote(
    payload: dict[str, Any],
    *,
    verified: VerifiedProof,
    settings: SupplierAgentSettings,
    session_factory: async_sessionmaker,
) -> dict[str, Any]:
    negotiation_id = payload["transaction_id"]
    # The negotiation's recorded counterparty is the cryptographically
    # verified sender, not the unauthenticated payload fields (M5.1) —
    # this is the identity every later message in this negotiation gets
    # checked against (``_verify_counterparty``).
    buyer_organization_id = verified.organization_id
    buyer_agent_id = verified.agent_id

    cached = await negotiation_store.check_replay(session_factory, payload["artifact_id"])
    if cached is not None:
        return cached

    await negotiation_store.start_negotiation(
        session_factory,
        negotiation_id=negotiation_id,
        counterparty_organization_id=buyer_organization_id,
        counterparty_agent_id=buyer_agent_id,
        rfq_artifact=payload,
    )

    matches_by_query = await fetch_availability_matches(
        settings=settings,
        lines=payload["payload"]["lines"],
        buyer_organization_id=buyer_organization_id,
    )
    quote_lines: list[dict[str, Any]] = []
    for line in payload["payload"]["lines"]:
        matches = matches_by_query.get(line["product_query"], [])
        if not matches:
            continue
        best = matches[0]
        quote_lines.append(
            {
                "product_ref": best["product_ref"],
                "quantity": line["quantity"],
                "unit": line["unit"],
                "unit_price": best["unit_price"],
            }
        )

    response_version = payload["version"] + 1
    if not quote_lines:
        response = build_reject_proposal(
            artifact_id=str(uuid.uuid4()),
            transaction_id=negotiation_id,
            correlation_id=payload["correlation_id"],
            sender_agent_id=settings.supplier_agent_id,
            recipient_agent_id=buyer_agent_id,
            organization_id=settings.supplier_organization_id,
            proposal_id=str(uuid.uuid4()),
            version=response_version,
            rejected_proposal_id=payload["proposal_id"],
            reason="None of the requested products are available in the catalogue.",
        )
        new_state = TradeState.REJECTED
    else:
        response = build_quote(
            artifact_id=str(uuid.uuid4()),
            transaction_id=negotiation_id,
            correlation_id=payload["correlation_id"],
            supplier_agent_id=settings.supplier_agent_id,
            buyer_agent_id=buyer_agent_id,
            supplier_organization_id=settings.supplier_organization_id,
            proposal_id=str(uuid.uuid4()),
            supersedes_proposal_id=payload["proposal_id"],
            version=response_version,
            lines=quote_lines,
            currency="EUR",
        )
        new_state = TradeState.PROPOSED

    await negotiation_store.advance_negotiation(
        session_factory,
        negotiation_id=negotiation_id,
        incoming_supersedes_proposal_id=payload["proposal_id"],
        artifact=response,
        new_state=new_state,
        round_number=1,
    )
    await negotiation_store.cache_response(
        session_factory,
        artifact_id=payload["artifact_id"],
        negotiation_id=negotiation_id,
        response=response,
    )
    return response


async def handle_counterproposal(
    payload: dict[str, Any],
    *,
    verified: VerifiedProof,
    settings: SupplierAgentSettings,
    session_factory: async_sessionmaker,
) -> dict[str, Any]:
    negotiation_id = payload["transaction_id"]
    buyer_agent_id = payload["sender_agent_id"]

    cached = await negotiation_store.check_replay(session_factory, payload["artifact_id"])
    if cached is not None:
        return cached

    negotiation = await negotiation_store.get_negotiation(session_factory, negotiation_id)
    if negotiation is None:
        raise NegotiationError(f"Unknown negotiation_id {negotiation_id!r}.")
    _verify_counterparty(negotiation, verified)

    previous_proposal_id = negotiation.current_proposal_id
    next_round = negotiation.round_number + 1

    # Step 1: record the buyer's counterproposal itself. This both
    # validates it supersedes exactly what this supplier last sent
    # (optimistic concurrency) and checks it has not expired.
    await negotiation_store.advance_negotiation(
        session_factory,
        negotiation_id=negotiation_id,
        incoming_supersedes_proposal_id=payload["supersedes_proposal_id"],
        artifact=payload,
        new_state=TradeState.NEGOTIATING,
        round_number=next_round,
    )

    previous_proposal = await negotiation_store.get_proposal(session_factory, previous_proposal_id)
    reference_prices: dict[str, Decimal] = {}
    if previous_proposal is not None:
        previous_artifact = json.loads(previous_proposal.payload_json)
        for line in previous_artifact.get("payload", {}).get("lines", []):
            reference_prices[line["product_ref"]] = Decimal(line["unit_price"])

    decision, counter_lines = evaluate_counterproposal(
        countered_lines=payload["payload"]["lines"],
        catalogue_unit_prices=reference_prices,
        round_number=next_round,
    )

    response_version = payload["version"] + 1
    if decision == "accept":
        response = build_proposed_agreement(
            artifact_id=str(uuid.uuid4()),
            transaction_id=negotiation_id,
            correlation_id=payload["correlation_id"],
            sender_agent_id=settings.supplier_agent_id,
            recipient_agent_id=buyer_agent_id,
            organization_id=settings.supplier_organization_id,
            proposal_id=str(uuid.uuid4()),
            version=response_version,
            agreement_of_proposal_id=payload["proposal_id"],
            lines=payload["payload"]["lines"],
            currency=payload["payload"]["currency"],
            delivery_terms=payload["payload"].get("delivery_terms"),
        )
        new_state = TradeState.PENDING_AUTHORIZATION
    elif decision == "counter" and counter_lines is not None:
        await _explain_counteroffer(
            llm_provider=settings.llm_provider, countered_lines=counter_lines
        )
        response = build_quote(
            artifact_id=str(uuid.uuid4()),
            transaction_id=negotiation_id,
            correlation_id=payload["correlation_id"],
            supplier_agent_id=settings.supplier_agent_id,
            buyer_agent_id=buyer_agent_id,
            supplier_organization_id=settings.supplier_organization_id,
            proposal_id=str(uuid.uuid4()),
            supersedes_proposal_id=payload["proposal_id"],
            version=response_version,
            lines=counter_lines,
            currency=payload["payload"]["currency"],
        )
        new_state = TradeState.PROPOSED
    else:
        response = build_reject_proposal(
            artifact_id=str(uuid.uuid4()),
            transaction_id=negotiation_id,
            correlation_id=payload["correlation_id"],
            sender_agent_id=settings.supplier_agent_id,
            recipient_agent_id=buyer_agent_id,
            organization_id=settings.supplier_organization_id,
            proposal_id=str(uuid.uuid4()),
            version=response_version,
            rejected_proposal_id=payload["proposal_id"],
            reason="Counteroffer is below the supplier's minimum acceptable margin "
            "after the maximum number of negotiation rounds.",
        )
        new_state = TradeState.REJECTED

    await negotiation_store.advance_negotiation(
        session_factory,
        negotiation_id=negotiation_id,
        incoming_supersedes_proposal_id=payload["proposal_id"],
        artifact=response,
        new_state=new_state,
        round_number=next_round,
    )
    await negotiation_store.cache_response(
        session_factory,
        artifact_id=payload["artifact_id"],
        negotiation_id=negotiation_id,
        response=response,
    )
    return response


async def handle_accept_proposal(
    payload: dict[str, Any],
    *,
    verified: VerifiedProof,
    settings: SupplierAgentSettings,
    session_factory: async_sessionmaker,
) -> dict[str, Any]:
    negotiation_id = payload["transaction_id"]
    buyer_agent_id = payload["sender_agent_id"]

    cached = await negotiation_store.check_replay(session_factory, payload["artifact_id"])
    if cached is not None:
        return cached

    negotiation = await negotiation_store.get_negotiation(session_factory, negotiation_id)
    if negotiation is None:
        raise NegotiationError(f"Unknown negotiation_id {negotiation_id!r}.")
    _verify_counterparty(negotiation, verified)

    accepted_proposal_id = payload["payload"]["accepted_proposal_id"]
    accepted_content_hash = payload["payload"]["accepted_content_hash"]
    current_proposal = await negotiation_store.get_proposal(
        session_factory, negotiation.current_proposal_id
    )
    if (
        current_proposal is None
        or current_proposal.proposal_id != accepted_proposal_id
        or current_proposal.content_hash != accepted_content_hash
    ):
        raise ProposalMismatchError(
            "accept_proposal does not match this negotiation's current proposal "
            "and content_hash — rejected as stale or mismatched, not reinterpreted."
        )

    current_artifact = json.loads(current_proposal.payload_json)
    response = build_proposed_agreement(
        artifact_id=str(uuid.uuid4()),
        transaction_id=negotiation_id,
        correlation_id=payload["correlation_id"],
        sender_agent_id=settings.supplier_agent_id,
        recipient_agent_id=buyer_agent_id,
        organization_id=settings.supplier_organization_id,
        proposal_id=str(uuid.uuid4()),
        version=payload["version"] + 1,
        agreement_of_proposal_id=accepted_proposal_id,
        lines=current_artifact["payload"]["lines"],
        currency=current_artifact["payload"]["currency"],
        delivery_terms=current_artifact["payload"].get("delivery_terms"),
    )
    await negotiation_store.advance_negotiation(
        session_factory,
        negotiation_id=negotiation_id,
        incoming_supersedes_proposal_id=accepted_proposal_id,
        artifact=response,
        new_state=TradeState.PENDING_AUTHORIZATION,
        round_number=negotiation.round_number,
    )
    await negotiation_store.cache_response(
        session_factory,
        artifact_id=payload["artifact_id"],
        negotiation_id=negotiation_id,
        response=response,
    )
    return response


async def handle_reject_proposal(
    payload: dict[str, Any],
    *,
    verified: VerifiedProof,
    settings: SupplierAgentSettings,
    session_factory: async_sessionmaker,
) -> dict[str, Any]:
    negotiation_id = payload["transaction_id"]
    buyer_agent_id = payload["sender_agent_id"]

    cached = await negotiation_store.check_replay(session_factory, payload["artifact_id"])
    if cached is not None:
        return cached

    negotiation = await negotiation_store.get_negotiation(session_factory, negotiation_id)
    if negotiation is None:
        raise NegotiationError(f"Unknown negotiation_id {negotiation_id!r}.")
    _verify_counterparty(negotiation, verified)

    response = build_reject_proposal(
        artifact_id=str(uuid.uuid4()),
        transaction_id=negotiation_id,
        correlation_id=payload["correlation_id"],
        sender_agent_id=settings.supplier_agent_id,
        recipient_agent_id=buyer_agent_id,
        organization_id=settings.supplier_organization_id,
        proposal_id=str(uuid.uuid4()),
        version=payload["version"] + 1,
        rejected_proposal_id=payload["payload"]["rejected_proposal_id"],
        reason="Cancellation acknowledged.",
    )
    await negotiation_store.advance_negotiation(
        session_factory,
        negotiation_id=negotiation_id,
        incoming_supersedes_proposal_id=payload["payload"]["rejected_proposal_id"],
        artifact=response,
        new_state=TradeState.REJECTED,
        round_number=negotiation.round_number,
    )
    await negotiation_store.cache_response(
        session_factory,
        artifact_id=payload["artifact_id"],
        negotiation_id=negotiation_id,
        response=response,
    )
    return response
