"""Supplier-side AgentExecutor.

This class contains **no** food-wholesale knowledge of its own. It
unwraps the A2A message into a plain dict, validates it against the
``food_wholesale`` schema, sources catalogue/inventory/price data by
calling ``supplier-mcp`` over MCP (``supplier_agent.mcp_client`` —
never a direct import of ``food_wholesale.adapters.mock``, per M3's
"agents must use MCP clients, not tool implementations" requirement),
and asks ``food_wholesale.availability`` to assemble/validate the
response envelope. If a different industry module were swapped in,
nothing here would need to change — only the ``skill_id``/domain
wiring in ``app.py`` would.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import jsonschema
from a2a.helpers import get_data_parts, new_data_part, new_task_from_user_message, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from food_wholesale.availability import assemble_response_from_matches, validate_against_schema
from mcp_integration import McpToolError
from sqlalchemy.ext.asyncio import async_sessionmaker

from supplier_agent.a2a_card import CHECK_AVAILABILITY_SKILL_ID
from supplier_agent.mcp_client import fetch_availability_matches
from supplier_agent.models import SupplierTaskRecord
from supplier_agent.settings import SupplierAgentSettings

logger = logging.getLogger(__name__)


def _extract_sender_agent_id(context: RequestContext) -> str | None:
    """``Message.metadata`` is a protobuf ``Struct``, which has no ``.get()``."""
    message = context.message
    if message is None or "sender_agent_id" not in message.metadata:
        return None
    return message.metadata["sender_agent_id"]


class DomainHandlerError(Exception):
    """Raised when the domain handler cannot process the request.

    Caught by this executor and turned into a failed A2A task — never
    silently swallowed, and never retried automatically (that is M5/M8
    reconciliation scope).
    """


class SupplierAgentExecutor(AgentExecutor):
    def __init__(
        self,
        *,
        settings: SupplierAgentSettings,
        session_factory: async_sessionmaker,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            if context.message is None:
                raise DomainHandlerError("Request carried no message to create a task from.")
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.start_work()

        counterparty = _extract_sender_agent_id(context)

        try:
            data_parts = get_data_parts(context.message.parts) if context.message else []
            if not data_parts:
                raise DomainHandlerError("No structured data payload provided.")

            request_payload = data_parts[0]
            validate_against_schema("product_availability_request", request_payload)

            matches_by_query = await fetch_availability_matches(
                settings=self._settings,
                lines=request_payload["lines"],
                buyer_organization_id=request_payload["buyer_organization_id"],
            )
            response = assemble_response_from_matches(
                request_payload,
                supplier_organization_id=self._settings.supplier_organization_id,
                matches_by_query=matches_by_query,
            )
        except jsonschema.ValidationError as exc:
            await self._record(task.id, state="FAILED", counterparty_agent_id=counterparty)
            await updater.failed(
                message=new_text_message(f"Invalid product_availability_request: {exc.message}")
            )
            return
        except McpToolError as exc:
            await self._record(task.id, state="FAILED", counterparty_agent_id=counterparty)
            await updater.failed(
                message=new_text_message(f"Could not complete catalogue/pricing lookup: {exc}")
            )
            return
        except DomainHandlerError as exc:
            await self._record(task.id, state="FAILED", counterparty_agent_id=counterparty)
            await updater.failed(message=new_text_message(str(exc)))
            return

        await updater.add_artifact(parts=[new_data_part(response)])
        await self._record(task.id, state="COMPLETED", counterparty_agent_id=counterparty)
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            return
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.update_status(state=TaskState.TASK_STATE_CANCELED)

    async def _record(self, task_id: str, *, state: str, counterparty_agent_id: str | None) -> None:
        async with self._session_factory() as session:
            await session.merge(
                SupplierTaskRecord(
                    task_id=task_id,
                    state=state,
                    skill_id=CHECK_AVAILABILITY_SKILL_ID,
                    role="supplier",
                    counterparty_agent_id=counterparty_agent_id,
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            await session.commit()
