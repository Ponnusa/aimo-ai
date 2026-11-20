"""M5.1 acceptance test: durable recovery for a crashed/pending
simulated commitment, using the real worker (``apps/worker``).

Reuses the exact same seven-server harness as
``test_authorization_commitment_e2e.py`` (network-api, registry-api,
buyer-mcp, supplier-mcp, buyer-agent, supplier-agent). The negotiation
and evidence exchange run normally up to mutual authorization; a crash
*after* writing commit intent but *before* a response is simulated
directly (a stale ``COMMITTING`` ``buyer_orders`` row, exactly what a
real crash at that point would leave behind — see
``buyer_agent.commitment._commit``'s own comment on why intent is
persisted before any network call). ``worker.run_dispatch_cycle``
must then recover it to ``CONFIRMED`` without creating a duplicate
order, verified directly against supplier-mcp's own
``supplier_mcp_orders`` table (the actual idempotency authority).
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from buyer_agent import negotiation_store
from buyer_agent.authorization import (
    evaluate_and_request_approval,
    get_own_evidence,
    is_mutually_authorized,
    record_counterparty_evidence,
)
from buyer_agent.client import (
    _send_negotiation_artifact,
    discover_supplier_agent_card,
    negotiate_purchase_via_registry,
)
from buyer_agent.models import BuyerOrderRecord
from buyer_agent.settings import get_settings
from food_wholesale import build_authorization_evidence, compute_idempotency_key
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from supplier_mcp.models import SupplierMcpOrderRecord

from database import new_engine
from worker import run_dispatch_cycle

from .test_authorization_commitment_e2e import (  # noqa: F401 -- fixtures
    BUYER_ORG,
    SUPPLIER_ORG,
    _create_relationship,
    _mint_credential,
    _register_and_verify_buyer,
    _register_and_verify_supplier,
    buyer_server,
    network_server,
    registry_server,
    supplier_server,
)

pytestmark = pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_recovers_crashed_commit_without_duplicate_order(
    network_server,  # noqa: F811 -- pytest fixture, imported from the sibling e2e module
    registry_server,  # noqa: F811
    supplier_server,  # noqa: F811
    buyer_server,  # noqa: F811
) -> None:
    token = await _mint_credential(network_server, SUPPLIER_ORG)
    await _create_relationship(network_server)
    await _register_and_verify_supplier(
        registry_server=registry_server, supplier_server=supplier_server, token=token
    )
    buyer_token = await _mint_credential(network_server, BUYER_ORG)
    await _register_and_verify_buyer(
        registry_server=registry_server, buyer_server=buyer_server, token=buyer_token
    )

    settings = get_settings()
    buyer_engine = new_engine(os.environ["BUYER_DATABASE_URL"])
    buyer_session_factory = async_sessionmaker(buyer_engine, expire_on_commit=False)

    agreement = await negotiate_purchase_via_registry(
        settings=settings,
        session_factory=buyer_session_factory,
        lines=[{"product_query": "lactose-free milk", "quantity": "50", "unit": "L"}],
        delivery_location_ref="site_01",
    )
    negotiation_id = agreement["transaction_id"]
    content_hash = agreement["content_hash"]

    # Drive evidence exchange to mutual authorization, but stop short of
    # actually sending the commit request -- simulating a process that
    # crashed right after deciding to commit.
    outcome = await evaluate_and_request_approval(
        buyer_session_factory, settings=settings, negotiation_id=negotiation_id
    )
    assert outcome.allow is True and outcome.human_approval_required is False

    negotiation = await negotiation_store.get_negotiation(buyer_session_factory, negotiation_id)
    own_evidence = await get_own_evidence(
        buyer_session_factory, negotiation_id=negotiation_id, content_hash=content_hash
    )
    own_evidence_artifact = build_authorization_evidence(
        artifact_id=str(uuid.uuid4()),
        transaction_id=negotiation_id,
        correlation_id=str(uuid.uuid4()),
        sender_agent_id=settings.buyer_agent_id,
        recipient_agent_id=negotiation.counterparty_agent_id,
        organization_id=settings.buyer_organization_id,
        proposal_id=str(uuid.uuid4()),
        version=1,
        agreement_id=negotiation_id,
        content_hash_value=content_hash,
        allow=own_evidence.allow,
        human_approval_required=own_evidence.human_approval_required,
        deny_reason=own_evidence.deny_reason,
        policy_version=own_evidence.policy_version,
        policy_input_digest=own_evidence.policy_input_digest,
    )
    agent_card = await discover_supplier_agent_card(settings.supplier_a2a_url)
    counterparty_response, verified = await _send_negotiation_artifact(
        agent_card,
        own_evidence_artifact,
        settings=settings,
        session_factory=buyer_session_factory,
        recipient_organization_id=negotiation.counterparty_organization_id,
    )
    await record_counterparty_evidence(
        buyer_session_factory,
        negotiation_id=negotiation_id,
        content_hash=content_hash,
        artifact=counterparty_response,
        verified=verified,
    )
    assert await is_mutually_authorized(
        buyer_session_factory, negotiation_id=negotiation_id, content_hash=content_hash
    )

    # Simulate the crash: commit intent was durably recorded, but the
    # process died before any order_commit_request was ever sent.
    idempotency_key = compute_idempotency_key(
        content_hash_value=content_hash,
        buyer_organization_id=settings.buyer_organization_id,
        supplier_organization_id=negotiation.counterparty_organization_id,
    )
    stale_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    async with buyer_session_factory() as session:
        session.add(
            BuyerOrderRecord(
                negotiation_id=negotiation_id,
                content_hash=content_hash,
                idempotency_key=idempotency_key,
                state="COMMITTING",
                attempts=1,
                created_at=stale_time,
                updated_at=stale_time,
            )
        )
        await session.commit()

    result = await run_dispatch_cycle(role="buyer", retry_backoff_seconds=1)
    assert result.attempted == 1
    assert result.recovered == 1

    async with buyer_session_factory() as session:
        row = (
            await session.execute(
                select(BuyerOrderRecord).where(BuyerOrderRecord.idempotency_key == idempotency_key)
            )
        ).scalar_one()
        assert row.state == "CONFIRMED"
        assert row.external_order_id is not None
    recovered_external_order_id = row.external_order_id
    await buyer_engine.dispose()

    # A second recovery cycle (e.g. the worker's next scheduled tick)
    # must be a no-op: the order is already CONFIRMED, never retried,
    # never duplicated.
    result_again = await run_dispatch_cycle(role="buyer", retry_backoff_seconds=1)
    assert result_again.attempted == 0

    # Independently verify against supplier-mcp's own idempotency
    # authority: exactly one order for this key, ever.
    supplier_mcp_database_url = os.environ["SUPPLIER_MCP_DATABASE_URL"]
    mcp_engine = new_engine(supplier_mcp_database_url)
    mcp_session_factory = async_sessionmaker(mcp_engine, expire_on_commit=False)
    async with mcp_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(SupplierMcpOrderRecord).where(
                        SupplierMcpOrderRecord.idempotency_key == idempotency_key
                    )
                )
            )
            .scalars()
            .all()
        )
    await mcp_engine.dispose()
    assert len(rows) == 1
    assert rows[0].external_order_id == recovered_external_order_id
