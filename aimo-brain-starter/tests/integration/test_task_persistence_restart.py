"""M1 acceptance test: task persistence and restart recovery.

Persists a Task via one ``DatabaseTaskStore``/engine bound to a SQLite
file, then opens a completely fresh engine and store against the same
file ("restart" the process) and confirms the task is still retrievable
with its state intact. This is the real mechanism each agent relies on
for restart recovery — the same ``DatabaseTaskStore`` wired into
``buyer_agent.app``/``supplier_agent.app``, not a stand-in.
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_survives_a_fresh_engine_against_the_same_database_file() -> None:
    db_path = os.path.join(tempfile.gettempdir(), f"aimo_m1_restart_{uuid.uuid4().hex}.sqlite3")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    task_id = f"task_{uuid.uuid4().hex}"
    context_id = f"ctx_{uuid.uuid4().hex}"

    # "Before restart": persist a task.
    engine_before = new_engine(database_url)
    store_before = DatabaseTaskStore(engine_before)
    await store_before.initialize()
    task = Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )
    await store_before.save(task, _call_context())
    await engine_before.dispose()

    # "After restart": a completely fresh engine/store, same file.
    engine_after = new_engine(database_url)
    store_after = DatabaseTaskStore(engine_after)
    recovered = await store_after.get(task_id, _call_context())
    await engine_after.dispose()

    assert recovered is not None
    assert recovered.id == task_id
    assert recovered.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_task_after_restart_is_none_not_an_error() -> None:
    db_path = os.path.join(tempfile.gettempdir(), f"aimo_m1_restart_{uuid.uuid4().hex}.sqlite3")
    database_url = f"sqlite+aiosqlite:///{db_path}"

    engine = new_engine(database_url)
    store = DatabaseTaskStore(engine)
    await store.initialize()
    result = await store.get("task_never_created", _call_context())
    await engine.dispose()

    assert result is None
