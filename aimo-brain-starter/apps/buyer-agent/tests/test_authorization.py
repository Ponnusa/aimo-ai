from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from buyer_agent import authorization, negotiation_store
from buyer_agent.settings import BuyerAgentSettings
from food_wholesale import build_proposed_agreement
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Base, new_engine
from identity import issue_service_credential

pytestmark = pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed")

SECRET = "test-signing-secret"
NEGOTIATION_ID = "txn_01"
BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"
BUYER_AGENT_ID = "agent_buyer_01"
SUPPLIER_AGENT_ID = "agent_supplier_01"


@pytest.fixture
async def session_factory() -> async_sessionmaker:
    path = os.path.join(tempfile.gettempdir(), f"aimo_buyer_authz_test_{uuid.uuid4().hex}.sqlite3")
    engine = new_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _settings(**overrides) -> BuyerAgentSettings:
    return BuyerAgentSettings(
        buyer_organization_id=BUYER_ORG,
        buyer_agent_id=BUYER_AGENT_ID,
        service_credential_signing_secret=SECRET,
        **overrides,
    )


def _lines(total_amount: str) -> list[dict[str, str]]:
    return [
        {
            "product_ref": "sku_fictional_milk_lactose_free",
            "quantity": "100",
            "unit": "L",
            "unit_price": total_amount,
        }
    ]


async def _seed_negotiation(session_factory, *, unit_price: str) -> dict:
    agreement = build_proposed_agreement(
        artifact_id="art_pa_01",
        transaction_id=NEGOTIATION_ID,
        correlation_id="corr_01",
        sender_agent_id=SUPPLIER_AGENT_ID,
        recipient_agent_id=BUYER_AGENT_ID,
        organization_id=SUPPLIER_ORG,
        proposal_id="prop_pa_01",
        version=3,
        agreement_of_proposal_id="prop_02",
        lines=_lines(unit_price),
        currency="EUR",
        delivery_terms=None,
    )
    rfq_stub = {
        "proposal_id": "prop_rfq",
        "version": 1,
        "type": "request_for_quote",
    }
    await negotiation_store.start_negotiation(
        session_factory,
        negotiation_id=NEGOTIATION_ID,
        counterparty_organization_id=SUPPLIER_ORG,
        counterparty_agent_id=SUPPLIER_AGENT_ID,
        rfq_artifact=rfq_stub,
    )
    from trade_protocol import TradeState

    quote_stub = {
        "proposal_id": "prop_02",
        "version": 2,
        "type": "quote",
        "content_hash": "sha256:quote-placeholder",
        "supersedes_proposal_id": "prop_rfq",
    }
    await negotiation_store.record_received_artifact(
        session_factory,
        negotiation_id=NEGOTIATION_ID,
        artifact=quote_stub,
        new_state=TradeState.PROPOSED,
        round_number=1,
    )
    await negotiation_store.record_received_artifact(
        session_factory,
        negotiation_id=NEGOTIATION_ID,
        artifact=agreement,
        new_state=TradeState.PENDING_AUTHORIZATION,
        round_number=1,
    )
    return agreement


@pytest.mark.asyncio
async def test_within_budget_allows_without_approval(session_factory) -> None:
    agreement = await _seed_negotiation(session_factory, unit_price="1.00")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    assert outcome.allow is True
    assert outcome.human_approval_required is False
    assert outcome.content_hash == agreement["content_hash"]

    evidence = await authorization.get_own_evidence(
        session_factory, negotiation_id=NEGOTIATION_ID, content_hash=agreement["content_hash"]
    )
    assert evidence is not None
    assert evidence.allow is True


@pytest.mark.asyncio
async def test_over_budget_denies(session_factory) -> None:
    await _seed_negotiation(session_factory, unit_price="10.00")  # 100 * 10.00 = 1000 > 500
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    assert outcome.allow is False
    assert "exceeds budget limit" in (outcome.deny_reason or "")


@pytest.mark.asyncio
async def test_human_approval_required_creates_pending_approval(session_factory) -> None:
    # 100 * 2.50 = 250.00: within budget_limit (500) but above
    # human_approval_threshold (200).
    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    assert outcome.allow is True
    assert outcome.human_approval_required is True
    assert outcome.approval_status == "PENDING"
    assert outcome.approval_id is not None

    # No evidence is finalized yet -- authorization is still pending.
    evidence = await authorization.get_own_evidence(
        session_factory, negotiation_id=NEGOTIATION_ID, content_hash=outcome.content_hash
    )
    assert evidence is None


@pytest.mark.asyncio
async def test_approving_pending_approval_finalizes_evidence(session_factory) -> None:
    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    approver_token = issue_service_credential(
        organization_id=BUYER_ORG, scope="approve", signing_secret=SECRET, approver_id="approver_01"
    )
    record = await authorization.decide_approval(
        session_factory,
        settings=settings,
        approval_id=outcome.approval_id,
        decision="APPROVED",
        approver_credential=approver_token,
    )
    assert record.status == "APPROVED"
    assert record.approver_id == "approver_01"

    evidence = await authorization.get_own_evidence(
        session_factory, negotiation_id=NEGOTIATION_ID, content_hash=outcome.content_hash
    )
    assert evidence is not None
    assert evidence.allow is True


@pytest.mark.asyncio
async def test_approval_rejects_cross_organization_approver(session_factory) -> None:
    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    wrong_org_token = issue_service_credential(
        organization_id="org_someone_else",
        scope="approve",
        signing_secret=SECRET,
        approver_id="approver_x",
    )
    with pytest.raises(authorization.UnauthorizedApproverError):
        await authorization.decide_approval(
            session_factory,
            settings=settings,
            approval_id=outcome.approval_id,
            decision="APPROVED",
            approver_credential=wrong_org_token,
        )


@pytest.mark.asyncio
async def test_approval_rejects_non_approve_scoped_credential(session_factory) -> None:
    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    wrong_scope_token = issue_service_credential(
        organization_id=BUYER_ORG, scope="use_buyer_tools", signing_secret=SECRET
    )
    with pytest.raises(authorization.UnauthorizedApproverError):
        await authorization.decide_approval(
            session_factory,
            settings=settings,
            approval_id=outcome.approval_id,
            decision="APPROVED",
            approver_credential=wrong_scope_token,
        )


@pytest.mark.asyncio
async def test_approving_twice_is_rejected(session_factory) -> None:
    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    approver_token = issue_service_credential(
        organization_id=BUYER_ORG, scope="approve", signing_secret=SECRET, approver_id="approver_01"
    )
    await authorization.decide_approval(
        session_factory,
        settings=settings,
        approval_id=outcome.approval_id,
        decision="APPROVED",
        approver_credential=approver_token,
    )
    with pytest.raises(authorization.ApprovalNotPendingError):
        await authorization.decide_approval(
            session_factory,
            settings=settings,
            approval_id=outcome.approval_id,
            decision="APPROVED",
            approver_credential=approver_token,
        )


@pytest.mark.asyncio
async def test_expired_approval_cannot_be_decided(session_factory) -> None:
    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    # Force expiry directly, simulating time passing.
    from buyer_agent.models import BuyerApprovalRecord

    async with session_factory() as session:
        record = await session.get(BuyerApprovalRecord, outcome.approval_id)
        record.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        await session.merge(record)
        await session.commit()

    approver_token = issue_service_credential(
        organization_id=BUYER_ORG, scope="approve", signing_secret=SECRET, approver_id="approver_01"
    )
    with pytest.raises(authorization.ApprovalNotPendingError):
        await authorization.decide_approval(
            session_factory,
            settings=settings,
            approval_id=outcome.approval_id,
            decision="APPROVED",
            approver_credential=approver_token,
        )


@pytest.mark.asyncio
async def test_record_counterparty_evidence_verifies_credential(session_factory) -> None:
    agreement = await _seed_negotiation(session_factory, unit_price="1.00")
    settings = _settings()
    good_token = issue_service_credential(
        organization_id=SUPPLIER_ORG,
        scope="commit",
        signing_secret=SECRET,
        agent_id=SUPPLIER_AGENT_ID,
    )
    artifact = {
        "payload": {
            "agreement_id": NEGOTIATION_ID,
            "content_hash": agreement["content_hash"],
            "allow": True,
            "human_approval_required": False,
            "deny_reason": None,
            "policy_version": "v1",
            "policy_input_digest": "sha256:digest",
            "credential": good_token,
        }
    }
    record = await authorization.record_counterparty_evidence(
        session_factory,
        settings=settings,
        negotiation_id=NEGOTIATION_ID,
        content_hash=agreement["content_hash"],
        artifact=artifact,
    )
    assert record.organization_id == SUPPLIER_ORG
    assert record.allow is True


@pytest.mark.asyncio
async def test_record_counterparty_evidence_rejects_spoofed_organization(session_factory) -> None:
    agreement = await _seed_negotiation(session_factory, unit_price="1.00")
    settings = _settings()
    # Credential belongs to a THIRD organization, not the real
    # counterparty -- payload claims to be the supplier, but the
    # credential proves otherwise.
    spoofed_token = issue_service_credential(
        organization_id="org_attacker",
        scope="commit",
        signing_secret=SECRET,
        agent_id="agent_attacker",
    )
    artifact = {
        "organization_id": SUPPLIER_ORG,
        "sender_agent_id": SUPPLIER_AGENT_ID,
        "payload": {
            "agreement_id": NEGOTIATION_ID,
            "content_hash": agreement["content_hash"],
            "allow": True,
            "human_approval_required": False,
            "deny_reason": None,
            "policy_version": "v1",
            "policy_input_digest": "sha256:digest",
            "credential": spoofed_token,
        },
    }
    with pytest.raises(authorization.UnverifiedCounterpartyError):
        await authorization.record_counterparty_evidence(
            session_factory,
            settings=settings,
            negotiation_id=NEGOTIATION_ID,
            content_hash=agreement["content_hash"],
            artifact=artifact,
        )


@pytest.mark.asyncio
async def test_record_counterparty_evidence_rejects_missing_credential(session_factory) -> None:
    agreement = await _seed_negotiation(session_factory, unit_price="1.00")
    settings = _settings()
    artifact = {
        "payload": {
            "agreement_id": NEGOTIATION_ID,
            "content_hash": agreement["content_hash"],
            "allow": True,
            "human_approval_required": False,
            "deny_reason": None,
            "policy_version": "v1",
            "policy_input_digest": "sha256:digest",
        }
    }
    with pytest.raises(authorization.UnverifiedCounterpartyError):
        await authorization.record_counterparty_evidence(
            session_factory,
            settings=settings,
            negotiation_id=NEGOTIATION_ID,
            content_hash=agreement["content_hash"],
            artifact=artifact,
        )


@pytest.mark.asyncio
async def test_record_counterparty_evidence_rejects_wrong_content_hash(session_factory) -> None:
    agreement = await _seed_negotiation(session_factory, unit_price="1.00")
    settings = _settings()
    good_token = issue_service_credential(
        organization_id=SUPPLIER_ORG,
        scope="commit",
        signing_secret=SECRET,
        agent_id=SUPPLIER_AGENT_ID,
    )
    artifact = {
        "payload": {
            "agreement_id": NEGOTIATION_ID,
            "content_hash": "sha256:a-different-hash",
            "allow": True,
            "human_approval_required": False,
            "deny_reason": None,
            "policy_version": "v1",
            "policy_input_digest": "sha256:digest",
            "credential": good_token,
        }
    }
    with pytest.raises(authorization.StaleEvidenceError):
        await authorization.record_counterparty_evidence(
            session_factory,
            settings=settings,
            negotiation_id=NEGOTIATION_ID,
            content_hash=agreement["content_hash"],
            artifact=artifact,
        )


@pytest.mark.asyncio
async def test_is_mutually_authorized_requires_both_sides(session_factory) -> None:
    agreement = await _seed_negotiation(session_factory, unit_price="1.00")
    settings = _settings()
    content_hash = agreement["content_hash"]

    assert (
        await authorization.is_mutually_authorized(
            session_factory, negotiation_id=NEGOTIATION_ID, content_hash=content_hash
        )
        is False
    )

    await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    assert (
        await authorization.is_mutually_authorized(
            session_factory, negotiation_id=NEGOTIATION_ID, content_hash=content_hash
        )
        is False
    )

    good_token = issue_service_credential(
        organization_id=SUPPLIER_ORG,
        scope="commit",
        signing_secret=SECRET,
        agent_id=SUPPLIER_AGENT_ID,
    )
    await authorization.record_counterparty_evidence(
        session_factory,
        settings=settings,
        negotiation_id=NEGOTIATION_ID,
        content_hash=content_hash,
        artifact={
            "payload": {
                "agreement_id": NEGOTIATION_ID,
                "content_hash": content_hash,
                "allow": True,
                "human_approval_required": False,
                "deny_reason": None,
                "policy_version": "v1",
                "policy_input_digest": "sha256:digest",
                "credential": good_token,
            }
        },
    )
    assert (
        await authorization.is_mutually_authorized(
            session_factory, negotiation_id=NEGOTIATION_ID, content_hash=content_hash
        )
        is True
    )
