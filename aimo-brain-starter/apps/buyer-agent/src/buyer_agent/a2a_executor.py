"""Buyer-side AgentExecutor.

M1 scope: a minimal, honest "status" responder proving buyer-agent is a
real, independently reachable A2A server — not a fake endpoint that only
exists to be discovered. It carries no food-wholesale logic and no
negotiation logic (that is M4 scope).
"""

from __future__ import annotations

from datetime import UTC, datetime

from a2a.helpers import new_data_part, new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from sqlalchemy.ext.asyncio import async_sessionmaker

from buyer_agent.a2a_card import AGENT_STATUS_SKILL_ID
from buyer_agent.models import BuyerTaskRecord
from buyer_agent.settings import BuyerAgentSettings


def _extract_sender_agent_id(context: RequestContext) -> str | None:
    """``Message.metadata`` is a protobuf ``Struct``, which has no ``.get()``."""
    message = context.message
    if message is None or "sender_agent_id" not in message.metadata:
        return None
    return message.metadata["sender_agent_id"]


class BuyerAgentExecutor(AgentExecutor):
    def __init__(
        self,
        *,
        settings: BuyerAgentSettings,
        session_factory: async_sessionmaker,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            if context.message is None:
                raise ValueError("Request carried no message to create a task from.")
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.start_work()

        counterparty = _extract_sender_agent_id(context)

        status_payload = {
            "agent_id": self._settings.buyer_agent_id,
            "organization_id": self._settings.buyer_organization_id,
            "role": "buyer",
            "status": "ok",
        }
        await updater.add_artifact(parts=[new_data_part(status_payload)])
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
                BuyerTaskRecord(
                    task_id=task_id,
                    state=state,
                    skill_id=AGENT_STATUS_SKILL_ID,
                    role="buyer",
                    counterparty_agent_id=counterparty_agent_id,
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            await session.commit()
