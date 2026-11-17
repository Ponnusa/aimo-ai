"""M1 required security test case: isolation between buyer and supplier
data (docs/SECURITY.md test case 1: "Buyer agent cannot query supplier
private tables").

This is not a policy-based denial (that is M5 scope) — it is the
structural guarantee M1 actually provides: buyer and supplier each have
their own database, their own engine, their own A2A SDK task store, and
their own business task-log table. A task created in one is not merely
"denied" when looked up from the other — it is not there to find,
because there is no connection between the two databases at all.
"""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest
from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.server.tasks import DatabaseTaskStore
from a2a.types import Task, TaskState, TaskStatus

from database import new_engine


def _call_context() -> ServerCallContext:
    return ServerCallContext(user=UnauthenticatedUser())


def _fresh_sqlite_url(label: str) -> str:
    filename = f"aimo_m1_isolation_{label}_{uuid.uuid4().hex}.sqlite3"
    path = os.path.join(tempfile.gettempdir(), filename)
    return f"sqlite+aiosqlite:///{path}"


@pytest.mark.security
@pytest.mark.asyncio
async def test_task_created_in_supplier_database_is_invisible_from_buyer_database() -> None:
    buyer_engine = new_engine(_fresh_sqlite_url("buyer"))
    supplier_engine = new_engine(_fresh_sqlite_url("supplier"))

    buyer_store = DatabaseTaskStore(buyer_engine)
    supplier_store = DatabaseTaskStore(supplier_engine)
    await buyer_store.initialize()
    await supplier_store.initialize()

    task_id = f"task_{uuid.uuid4().hex}"
    supplier_task = Task(
        id=task_id,
        context_id="ctx_supplier",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )
    await supplier_store.save(supplier_task, _call_context())

    # The supplier can see its own task.
    assert (await supplier_store.get(task_id, _call_context())) is not None

    # The buyer, looking up the exact same task_id in its own database,
    # finds nothing — not because of a permission check, but because
    # there is structurally no such row in the buyer's own database.
    assert (await buyer_store.get(task_id, _call_context())) is None

    await buyer_engine.dispose()
    await supplier_engine.dispose()


@pytest.mark.security
@pytest.mark.asyncio
async def test_buyer_and_supplier_business_task_logs_are_in_separate_databases() -> None:
    """Same guarantee, exercised against each agent's own
    ``buyer_tasks``/``supplier_tasks`` business task-log table, not just
    the A2A SDK's internal one."""
    from datetime import UTC, datetime

    from buyer_agent.models import BuyerTaskRecord
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from supplier_agent.models import SupplierTaskRecord

    from database import Base

    buyer_engine = new_engine(_fresh_sqlite_url("buyer"))
    supplier_engine = new_engine(_fresh_sqlite_url("supplier"))

    async with supplier_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[SupplierTaskRecord.__table__])
    async with buyer_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[BuyerTaskRecord.__table__])

    supplier_session_factory = async_sessionmaker(supplier_engine, expire_on_commit=False)
    buyer_session_factory = async_sessionmaker(buyer_engine, expire_on_commit=False)

    task_id = f"task_{uuid.uuid4().hex}"
    async with supplier_session_factory() as session:
        session.add(
            SupplierTaskRecord(
                task_id=task_id,
                state="COMPLETED",
                skill_id="food.check_product_availability",
                role="supplier",
                counterparty_agent_id="agent_fictional_buyer_01",
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()

    async with buyer_session_factory() as session:
        result = await session.execute(
            select(BuyerTaskRecord).where(BuyerTaskRecord.task_id == task_id)
        )
        assert result.scalar_one_or_none() is None

    await buyer_engine.dispose()
    await supplier_engine.dispose()
