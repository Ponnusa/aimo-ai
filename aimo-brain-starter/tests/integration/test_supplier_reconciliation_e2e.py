"""M7 acceptance test: real supplier-side reconciliation.

Closes the M5.1/ADR-012-documented gap that ``WORKER_ROLE=supplier`` was
a no-op. A ``supplier_orders`` row stuck in ``RECONCILIATION_REQUIRED``
(simulating an earlier ambiguous ``execute_order`` outcome -- a timeout
or an unresolved delayed-ack) is recovered by
``worker.run_dispatch_cycle(role="supplier")`` re-asking the real
supplier-mcp server's ``get_order_status`` tool, which is itself backed
by its own durable ``supplier_mcp_orders`` idempotency authority. Never
re-submits the order -- only re-reads status.

Boots a real supplier-mcp server (real uvicorn, real HTTP) per this
repo's standing test convention -- never ``ASGITransport``.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.a2a_server_harness import find_free_port, running_server

from database import Base, new_engine
from identity import issue_service_credential

ORG = "org_fictional_wholesale_01"

_mcp_db_file = os.path.join(tempfile.gettempdir(), "aimo_supplier_mcp_reconcile_test.sqlite3")
with contextlib.suppress(FileNotFoundError):
    os.remove(_mcp_db_file)
os.environ["SUPPLIER_MCP_DATABASE_URL"] = f"sqlite+aiosqlite:///{_mcp_db_file}"
os.environ.setdefault("SERVICE_CREDENTIAL_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("SUPPLIER_MCP_ORGANIZATION_ID", ORG)
os.environ.setdefault("SUPPLIER_ORGANIZATION_ID", ORG)

_agent_db_file = os.path.join(tempfile.gettempdir(), "aimo_supplier_agent_reconcile_test.sqlite3")
with contextlib.suppress(FileNotFoundError):
    os.remove(_agent_db_file)
os.environ["SUPPLIER_DATABASE_URL"] = f"sqlite+aiosqlite:///{_agent_db_file}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_resolves_stuck_supplier_order_via_reconciliation() -> None:
    from supplier_mcp.app import create_app as create_mcp_app
    from supplier_mcp.models import SupplierMcpOrderRecord
    from supplier_mcp.settings import get_settings as get_mcp_settings

    get_mcp_settings.cache_clear()
    mcp_settings = get_mcp_settings()
    mcp_engine = new_engine(mcp_settings.supplier_mcp_database_url)
    async with mcp_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    idempotency_key = "idem_reconcile_test_01"
    now = datetime.now(UTC).replace(tzinfo=None)
    mcp_session_factory = async_sessionmaker(mcp_engine, expire_on_commit=False)
    async with mcp_session_factory() as session:
        # The business system's own authority already confirmed this
        # order (e.g. the earlier execute_order call's response was
        # lost to a network timeout, but the order itself went through)
        # -- exactly the scenario reconciliation exists for.
        session.add(
            SupplierMcpOrderRecord(
                idempotency_key=idempotency_key,
                agreement_id="txn_reconcile_01",
                buyer_organization_id="org_fictional_bistro_01",
                content_hash="sha256:reconcile",
                external_order_id="mock_order_already_confirmed",
                status="confirmed",
                created_at=now,
            )
        )
        await session.commit()

    mcp_app = create_mcp_app()
    mcp_port = find_free_port()

    with running_server(mcp_app, port=mcp_port):
        os.environ["SUPPLIER_MCP_URL"] = f"http://127.0.0.1:{mcp_port}/mcp"
        os.environ["SUPPLIER_MCP_EXECUTION_CREDENTIAL"] = issue_service_credential(
            organization_id=ORG,
            scope="execute_order",
            signing_secret=mcp_settings.service_credential_signing_secret,
        )

        from supplier_agent.models import SupplierOrderRecord
        from supplier_agent.settings import get_settings as get_agent_settings

        get_agent_settings.cache_clear()
        agent_settings = get_agent_settings()
        agent_engine = new_engine(agent_settings.supplier_database_url)
        async with agent_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        agent_session_factory = async_sessionmaker(agent_engine, expire_on_commit=False)

        stale_time = now - timedelta(minutes=5)
        async with agent_session_factory() as session:
            session.add(
                SupplierOrderRecord(
                    negotiation_id="neg_reconcile_01",
                    content_hash="sha256:reconcile",
                    idempotency_key=idempotency_key,
                    state="RECONCILIATION_REQUIRED",
                    external_order_id=None,
                    attempts=1,
                    created_at=stale_time,
                    updated_at=stale_time,
                )
            )
            await session.commit()

        from worker import run_dispatch_cycle

        result = await run_dispatch_cycle(role="supplier", retry_backoff_seconds=1)
        assert result.attempted == 1
        assert result.recovered == 1

        async with agent_session_factory() as session:
            row = (
                await session.execute(
                    select(SupplierOrderRecord).where(
                        SupplierOrderRecord.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one()
            assert row.state == "CONFIRMED"
            assert row.external_order_id == "mock_order_already_confirmed"
            assert row.attempts == 2

        # A second cycle against an already-resolved order is a no-op.
        result_again = await run_dispatch_cycle(role="supplier", retry_backoff_seconds=1)
        assert result_again.attempted == 0

        await agent_engine.dispose()

    await mcp_engine.dispose()
