"""Buyer-side A2A client: discovery + send.

This module is the only place in buyer-agent that talks to the supplier.
It uses the official A2A SDK client over real HTTP; it never imports
``supplier_agent`` and never calls into it directly — see
``tests/contracts/test_database_isolation.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.client.errors import A2AClientError, AgentCardResolutionError
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import AgentCard, Role, SendMessageRequest, Task, TaskState
from food_wholesale.availability import build_request, validate_response
from sqlalchemy.ext.asyncio import async_sessionmaker

from buyer_agent.mcp_client import fetch_purchasing_context
from buyer_agent.models import BuyerTaskRecord
from buyer_agent.registry_client import discover_supplier_via_registry
from buyer_agent.settings import BuyerAgentSettings

#: The remote skill this client invokes. Not this agent's own skill id —
#: it is the supplier's, kept here only so callers/tests have one source
#: of truth for which skill is being exercised.
TARGET_SKILL_ID = "food.check_product_availability"


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
