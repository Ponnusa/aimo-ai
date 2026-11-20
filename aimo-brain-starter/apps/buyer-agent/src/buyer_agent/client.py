"""Buyer-side A2A client: discovery + send.

This module is the only place in buyer-agent that talks to the supplier.
It uses the official A2A SDK client over real HTTP; it never imports
``supplier_agent`` and never calls into it directly — see
``tests/contracts/test_database_isolation.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.client.errors import A2AClientError, AgentCardResolutionError
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import AgentCard, Role, SendMessageRequest, Task, TaskState
from food_wholesale.availability import build_request, validate_response
from food_wholesale.negotiation import build_accept_proposal, build_counterproposal, build_rfq
from food_wholesale.schemas import validate_against_schema
from message_integrity import VerifiedProof
from sqlalchemy.ext.asyncio import async_sessionmaker
from trade_protocol import TradeState

from buyer_agent import message_auth, negotiation_store
from buyer_agent.mcp_client import fetch_purchasing_context
from buyer_agent.models import BuyerTaskRecord
from buyer_agent.negotiation import evaluate_quote_against_budget
from buyer_agent.registry_client import discover_supplier_via_registry
from buyer_agent.settings import BuyerAgentSettings

#: The remote skill this client invokes. Not this agent's own skill id —
#: it is the supplier's, kept here only so callers/tests have one source
#: of truth for which skill is being exercised.
TARGET_SKILL_ID = "food.check_product_availability"

#: The supplier skill this client negotiates against (M4).
NEGOTIATE_SKILL_ID = "food.request_quote"

#: Safety bound on negotiation rounds on the buyer's own side, in
#: addition to the supplier's own round cap
#: (``food_wholesale.negotiation.MAX_NEGOTIATION_ROUNDS``) — this agent
#: never loops indefinitely even if that bound were ever misconfigured.
_MAX_BUYER_ROUNDS = 8


class NegotiationFailedError(Exception):
    """Raised when a negotiation cannot proceed for a reason other than
    an explicit supplier rejection (e.g. too many rounds without
    convergence)."""


class AgentUnavailableError(Exception):
    """Raised when the supplier's A2A endpoint cannot be reached at all."""


class InvalidAgentResponseError(Exception):
    """Raised when the supplier's response does not validate against the
    expected ``product_availability_response`` schema."""


class TaskNotCompletedError(Exception):
    """Raised when the supplier's task did not reach a completed state."""


async def discover_supplier_agent_card(supplier_a2a_url: str) -> AgentCard:
    """Fetch and return the supplier's real Agent Card over HTTP.

    Raises :class:`AgentUnavailableError` if the supplier cannot be reached
    at all (connection refused, DNS failure, timeout).
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as httpx_client:
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=supplier_a2a_url)
            return await resolver.get_agent_card()
    except (httpx.HTTPError, AgentCardResolutionError) as exc:
        raise AgentUnavailableError(
            f"Could not reach supplier agent at {supplier_a2a_url}: {exc}"
        ) from exc


async def request_product_availability(
    *,
    settings: BuyerAgentSettings,
    session_factory: async_sessionmaker,
    lines: list[dict[str, str]],
) -> dict[str, Any]:
    """Discover the supplier at its known, hardcoded A2A URL, send a
    product_availability_request, validate and return the response
    payload. Records this agent's own task history regardless of outcome.

    This path supports agents outside AIMO BRAIN's own registry entirely
    (a directly-configured counterparty URL, BYOA-style) — for the
    registry-mediated path, see :func:`request_product_availability_via_registry`.

    Raises :class:`AgentUnavailableError` if the supplier is unreachable,
    or :class:`InvalidAgentResponseError`/:class:`TaskNotCompletedError` if
    it responds but with something unusable.
    """
    agent_card = await discover_supplier_agent_card(settings.supplier_a2a_url)
    return await _send_and_validate(
        agent_card, settings=settings, session_factory=session_factory, lines=lines
    )


async def request_product_availability_via_registry(
    *,
    settings: BuyerAgentSettings,
    session_factory: async_sessionmaker,
    lines: list[dict[str, str]],
) -> dict[str, Any]:
    """Obtain this organization's own purchasing context over MCP (M3 —
    see ``buyer_agent.mcp_client``), discover the supplier through
    registry-api (capability-indexed, relationship-gated discovery — see
    ``buyer_agent.registry_client``), then send/validate exactly as
    :func:`request_product_availability` does. Neither registry-api nor
    buyer-mcp is involved past those steps: the A2A exchange itself is
    direct, over the official SDK.

    This does not block the A2A request on the fetched budget/
    requirements — gating a request on policy is M4/M5 scope. A
    buyer-mcp failure is not swallowed either: it propagates as
    :class:`mcp_integration.McpToolError`.

    Raises :class:`RegistryDiscoveryError` (from
    ``buyer_agent.registry_client``) if no discoverable, authorized
    supplier is found; otherwise the same errors as
    :func:`request_product_availability`.
    """
    await fetch_purchasing_context(settings=settings)

    discovered = await discover_supplier_via_registry(
        registry_url=settings.registry_api_url,
        requesting_organization_id=settings.buyer_organization_id,
        skill_id=TARGET_SKILL_ID,
    )
    agent_card = await discover_supplier_agent_card(discovered.a2a_endpoint_url)
    return await _send_and_validate(
        agent_card, settings=settings, session_factory=session_factory, lines=lines
    )


async def _send_and_validate(
    agent_card: AgentCard,
    *,
    settings: BuyerAgentSettings,
    session_factory: async_sessionmaker,
    lines: list[dict[str, str]],
) -> dict[str, Any]:
    request_payload = build_request(lines, buyer_organization_id=settings.buyer_organization_id)

    message = new_data_message(data=request_payload, role=Role.ROLE_USER)
    message.metadata["sender_agent_id"] = settings.buyer_agent_id

    async with httpx.AsyncClient(timeout=10.0) as httpx_client:
        client_config = ClientConfig(streaming=False, httpx_client=httpx_client)
        client = await create_client(agent=agent_card, client_config=client_config)

        final_task: Task | None = None
        try:
            async for chunk in client.send_message(SendMessageRequest(message=message)):
                if chunk.HasField("task"):
                    final_task = chunk.task
        except (httpx.HTTPError, A2AClientError) as exc:
            raise AgentUnavailableError(
                f"Could not reach supplier agent at {settings.supplier_a2a_url}: {exc}"
            ) from exc

    if final_task is None:
        raise TaskNotCompletedError("Supplier did not return a Task result.")

    await _record(
        session_factory,
        task_id=final_task.id,
        state=TaskState.Name(final_task.status.state),
        counterparty_agent_id=agent_card.name,
    )

    if final_task.status.state != TaskState.TASK_STATE_COMPLETED:
        raise TaskNotCompletedError(
            f"Supplier task ended in state {TaskState.Name(final_task.status.state)}, "
            "not completed."
        )

    parts = [part for artifact in final_task.artifacts for part in artifact.parts]
    data_parts = get_data_parts(parts)
    if not data_parts:
        raise InvalidAgentResponseError("Supplier response contained no structured data part.")

    response_payload = data_parts[0]
    try:
        validate_response(response_payload)
    except Exception as exc:  # jsonschema.ValidationError
        raise InvalidAgentResponseError(
            f"Supplier response failed schema validation: {exc}"
        ) from exc

    return response_payload


async def _send_negotiation_artifact(
    agent_card: AgentCard,
    artifact: dict[str, Any],
    *,
    settings: BuyerAgentSettings,
    session_factory: async_sessionmaker,
    recipient_organization_id: str,
) -> tuple[dict[str, Any], VerifiedProof]:
    """Sign ``artifact`` with this organization's own key, send it over
    a real A2A task, and cryptographically verify the supplier's
    structured response before returning it (M5.1) — signature, replay,
    registered-agent, trading-relationship, *and* (once the negotiation
    already has a recorded counterparty) negotiation-participant
    binding. Callers still check ``response["type"]`` and validate
    against the matching schema themselves, since the response type
    varies by negotiation state.

    Raises :class:`buyer_agent.message_auth.MessageAuthenticationError`
    if the response cannot be authenticated — never returns an
    unauthenticated response to a caller."""
    signed_artifact = message_auth.sign_outbound_artifact(
        artifact, settings=settings, recipient_organization_id=recipient_organization_id
    )
    message = new_data_message(data=signed_artifact, role=Role.ROLE_USER)
    message.metadata["sender_agent_id"] = settings.buyer_agent_id

    async with httpx.AsyncClient(timeout=10.0) as httpx_client:
        client_config = ClientConfig(streaming=False, httpx_client=httpx_client)
        client = await create_client(agent=agent_card, client_config=client_config)

        final_task: Task | None = None
        try:
            async for chunk in client.send_message(SendMessageRequest(message=message)):
                if chunk.HasField("task"):
                    final_task = chunk.task
        except (httpx.HTTPError, A2AClientError) as exc:
            raise AgentUnavailableError(f"Could not reach supplier agent: {exc}") from exc

    if final_task is None:
        raise TaskNotCompletedError("Supplier did not return a Task result.")
    if final_task.status.state != TaskState.TASK_STATE_COMPLETED:
        raise TaskNotCompletedError(
            f"Supplier task ended in state {TaskState.Name(final_task.status.state)}, "
            "not completed."
        )

    parts = [part for sent_artifact in final_task.artifacts for part in sent_artifact.parts]
    data_parts = get_data_parts(parts)
    if not data_parts:
        raise InvalidAgentResponseError("Supplier response contained no structured data part.")
    response = data_parts[0]

    verified = await message_auth.verify_inbound_artifact(
        response, settings=settings, session_factory=session_factory
    )
    negotiation = await negotiation_store.get_negotiation(
        session_factory, response.get("transaction_id", artifact["transaction_id"])
    )
    if negotiation is not None and (
        verified.organization_id != negotiation.counterparty_organization_id
        or verified.agent_id != negotiation.counterparty_agent_id
    ):
        raise message_auth.MessageAuthenticationError(
            "Verified response sender does not match this negotiation's counterparty."
        )
    return response, verified


async def negotiate_purchase_via_registry(
    *,
    settings: BuyerAgentSettings,
    session_factory: async_sessionmaker,
    lines: list[dict[str, str]],
    delivery_location_ref: str,
) -> dict[str, Any]:
    """Run the full M4 nonbinding negotiation: fetch this organization's
    own MCP-sourced remaining budget, discover the supplier through
    registry-api, send an RFQ, and converge on a NONBINDING
    ``proposed_agreement`` (or an explicit ``reject_proposal``) by
    countering whenever the supplier's offer exceeds budget.

    **Never creates an order, reservation or payment** — M4 is
    explicitly nonbinding end-to-end (``docs/ROADMAP.md``). Both this
    agent and the supplier persist their own negotiation history
    independently (``buyer_agent.negotiation_store`` /
    ``supplier_agent.negotiation_store``).
    """
    purchasing_context = await fetch_purchasing_context(settings=settings)
    remaining_budget = Decimal(purchasing_context.budget.get("remaining_amount", "0"))

    discovered = await discover_supplier_via_registry(
        registry_url=settings.registry_api_url,
        requesting_organization_id=settings.buyer_organization_id,
        skill_id=NEGOTIATE_SKILL_ID,
    )
    agent_card = await discover_supplier_agent_card(discovered.a2a_endpoint_url)

    negotiation_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    rfq = build_rfq(
        artifact_id=str(uuid.uuid4()),
        transaction_id=negotiation_id,
        correlation_id=correlation_id,
        buyer_agent_id=settings.buyer_agent_id,
        supplier_agent_id=discovered.agent_id,
        buyer_organization_id=settings.buyer_organization_id,
        proposal_id=str(uuid.uuid4()),
        lines=lines,
        delivery_location_ref=delivery_location_ref,
    )
    await negotiation_store.start_negotiation(
        session_factory,
        negotiation_id=negotiation_id,
        counterparty_organization_id=discovered.organization_id,
        counterparty_agent_id=discovered.agent_id,
        rfq_artifact=rfq,
    )

    response, _verified = await _send_negotiation_artifact(
        agent_card,
        rfq,
        settings=settings,
        session_factory=session_factory,
        recipient_organization_id=discovered.organization_id,
    )
    return await _negotiate_round(
        agent_card,
        response,
        settings=settings,
        session_factory=session_factory,
        negotiation_id=negotiation_id,
        remaining_budget=remaining_budget,
        round_number=1,
        supplier_organization_id=discovered.organization_id,
    )


async def _negotiate_round(
    agent_card: AgentCard,
    response: dict[str, Any],
    *,
    settings: BuyerAgentSettings,
    session_factory: async_sessionmaker,
    negotiation_id: str,
    remaining_budget: Decimal,
    round_number: int,
    supplier_organization_id: str,
) -> dict[str, Any]:
    """One hop of the negotiation loop: record what the supplier just
    sent, and either converge, counter, or give up — bounded by
    ``_MAX_BUYER_ROUNDS`` regardless of the supplier's own round cap."""
    response_type = response.get("type")

    if response_type == "reject_proposal":
        await negotiation_store.record_received_artifact(
            session_factory,
            negotiation_id=negotiation_id,
            artifact=response,
            new_state=TradeState.REJECTED,
            round_number=round_number,
        )
        return response

    if response_type == "proposed_agreement":
        validate_against_schema("proposed_agreement", response)
        await negotiation_store.record_received_artifact(
            session_factory,
            negotiation_id=negotiation_id,
            artifact=response,
            new_state=TradeState.PENDING_AUTHORIZATION,
            round_number=round_number,
        )
        return response

    if response_type != "quote":
        raise InvalidAgentResponseError(f"Unexpected negotiation response type: {response_type!r}")
    validate_against_schema("quote", response)
    await negotiation_store.record_received_artifact(
        session_factory,
        negotiation_id=negotiation_id,
        artifact=response,
        new_state=TradeState.PROPOSED,
        round_number=round_number,
    )

    evaluation = evaluate_quote_against_budget(
        lines=response["payload"]["lines"], remaining_budget=remaining_budget
    )
    if evaluation.within_budget:
        accept = build_accept_proposal(
            artifact_id=str(uuid.uuid4()),
            transaction_id=negotiation_id,
            correlation_id=response["correlation_id"],
            sender_agent_id=settings.buyer_agent_id,
            recipient_agent_id=response["sender_agent_id"],
            organization_id=settings.buyer_organization_id,
            proposal_id=str(uuid.uuid4()),
            version=response["version"] + 1,
            accepted_proposal_id=response["proposal_id"],
            accepted_content_hash=response["content_hash"],
        )
        agreement_response, _verified = await _send_negotiation_artifact(
            agent_card,
            accept,
            settings=settings,
            session_factory=session_factory,
            recipient_organization_id=supplier_organization_id,
        )
        validate_against_schema("proposed_agreement", agreement_response)
        await negotiation_store.record_received_artifact(
            session_factory,
            negotiation_id=negotiation_id,
            artifact=agreement_response,
            new_state=TradeState.PENDING_AUTHORIZATION,
            round_number=round_number,
        )
        return agreement_response

    if round_number >= _MAX_BUYER_ROUNDS:
        raise NegotiationFailedError(
            f"Negotiation {negotiation_id} did not converge within {_MAX_BUYER_ROUNDS} rounds."
        )

    counter = build_counterproposal(
        artifact_id=str(uuid.uuid4()),
        transaction_id=negotiation_id,
        correlation_id=response["correlation_id"],
        buyer_agent_id=settings.buyer_agent_id,
        supplier_agent_id=response["sender_agent_id"],
        buyer_organization_id=settings.buyer_organization_id,
        proposal_id=str(uuid.uuid4()),
        supersedes_proposal_id=response["proposal_id"],
        version=response["version"] + 1,
        lines=evaluation.counter_lines or response["payload"]["lines"],
        currency=response["payload"]["currency"],
    )
    await negotiation_store.record_sent_artifact(
        session_factory,
        negotiation_id=negotiation_id,
        artifact=counter,
        new_state=TradeState.NEGOTIATING,
        round_number=round_number + 1,
    )
    counter_response, _verified = await _send_negotiation_artifact(
        agent_card,
        counter,
        settings=settings,
        session_factory=session_factory,
        recipient_organization_id=supplier_organization_id,
    )
    return await _negotiate_round(
        agent_card,
        counter_response,
        settings=settings,
        session_factory=session_factory,
        negotiation_id=negotiation_id,
        remaining_budget=remaining_budget,
        round_number=round_number + 1,
        supplier_organization_id=supplier_organization_id,
    )


async def _record(
    session_factory: async_sessionmaker,
    *,
    task_id: str,
    state: str,
    counterparty_agent_id: str | None,
) -> None:
    async with session_factory() as session:
        await session.merge(
            BuyerTaskRecord(
                task_id=task_id,
                state=state,
                skill_id=TARGET_SKILL_ID,
                role="buyer",
                counterparty_agent_id=counterparty_agent_id,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()
