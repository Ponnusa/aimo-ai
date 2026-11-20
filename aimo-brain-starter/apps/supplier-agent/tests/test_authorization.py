from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from food_wholesale import build_proposed_agreement
from identity.dev_oidc import issue_dev_approval_token
from message_integrity import VerifiedProof, generate_signing_keypair, private_key_to_pem
from sqlalchemy.ext.asyncio import async_sessionmaker
from supplier_agent import authorization, negotiation_store
from supplier_agent.settings import SupplierAgentSettings

from database import Base, new_engine

pytestmark = pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed")

NEGOTIATION_ID = "txn_01"
BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"
BUYER_AGENT_ID = "agent_buyer_01"
SUPPLIER_AGENT_ID = "agent_supplier_01"

_SUPPLIER_PRIVATE_KEY, _ = generate_signing_keypair()


@pytest.fixture
async def session_factory() -> async_sessionmaker:
    path = os.path.join(
        tempfile.gettempdir(), f"aimo_supplier_authz_test_{uuid.uuid4().hex}.sqlite3"
    )
    engine = new_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _settings(**overrides) -> SupplierAgentSettings:
    params = {
        "supplier_organization_id": SUPPLIER_ORG,
        "supplier_agent_id": SUPPLIER_AGENT_ID,
        "supplier_signing_private_key_pem": private_key_to_pem(_SUPPLIER_PRIVATE_KEY),
        "supplier_signing_kid": "supplier-k1",
        "environment": "local",
    }
    params.update(overrides)
    return SupplierAgentSettings(**params)


def _lines(unit_price: str, *, quantity: str = "100") -> list[dict[str, str]]:
    return [
        {
            "product_ref": "sku_fictional_milk_lactose_free",
            "quantity": quantity,
            "unit": "L",
            "unit_price": unit_price,
        }
    ]


def _verified_buyer_proof(*, content_hash: str | None = None) -> VerifiedProof:
    return VerifiedProof(
        organization_id=BUYER_ORG,
        agent_id=BUYER_AGENT_ID,
        recipient_organization_id=SUPPLIER_ORG,
        negotiation_id=NEGOTIATION_ID,
        artifact_type="authorization_evidence",
        version=1,
        content_hash=content_hash,
        jti=str(uuid.uuid4()),
        issued_at=0,
        expires_at=0,
    )


async def _seed_negotiation(session_factory, *, unit_price: str, quantity: str = "100") -> dict:
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
        lines=_lines(unit_price, quantity=quantity),
        currency="EUR",
        delivery_terms=None,
    )
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    rfq_stub = {
        "proposal_id": "prop_rfq",
        "version": 1,
        "type": "request_for_quote",
        "expires_at": future,
    }
    await negotiation_store.start_negotiation(
        session_factory,
        negotiation_id=NEGOTIATION_ID,
        counterparty_organization_id=BUYER_ORG,
        counterparty_agent_id=BUYER_AGENT_ID,
        rfq_artifact=rfq_stub,
    )

    from trade_protocol import TradeState

    quote_stub = {
        "proposal_id": "prop_02",
        "version": 2,
        "type": "quote",
        "content_hash": "sha256:quote-placeholder",
        "supersedes_proposal_id": "prop_rfq",
        "expires_at": future,
    }
    await negotiation_store.advance_negotiation(
        session_factory,
        negotiation_id=NEGOTIATION_ID,
        incoming_supersedes_proposal_id="prop_rfq",
        artifact=quote_stub,
        new_state=TradeState.PROPOSED,
        round_number=1,
    )
    await negotiation_store.advance_negotiation(
        session_factory,
        negotiation_id=NEGOTIATION_ID,
        incoming_supersedes_proposal_id="prop_02",
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
async def test_quantity_exceeding_max_order_quantity_denies(session_factory) -> None:
    # max_order_quantity is 500; 9000 units exceeds it regardless of price.
    await _seed_negotiation(session_factory, unit_price="0.50", quantity="9000")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    assert outcome.allow is False
    assert "max_order_quantity" in (outcome.deny_reason or "")


@pytest.mark.asyncio
async def test_unapproved_customer_denies(session_factory) -> None:
    await _seed_negotiation(session_factory, unit_price="1.00")
    settings = _settings()
    from supplier_agent.models import SupplierNegotiationRecord

    async with session_factory() as session:
        record = await session.get(SupplierNegotiationRecord, NEGOTIATION_ID)
        record.counterparty_organization_id = "org_unapproved_customer"
        await session.merge(record)
        await session.commit()

    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    assert outcome.allow is False
    assert "not an approved customer" in (outcome.deny_reason or "")


@pytest.mark.asyncio
async def test_human_approval_required_creates_pending_approval(session_factory) -> None:
    # 100 * 2.50 = 250.00: within max_order_quantity but above
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
    approval_token = issue_dev_approval_token(
        organization_id=SUPPLIER_ORG,
        approver_id="approver_01",
        private_key=_SUPPLIER_PRIVATE_KEY,
        kid="supplier-k1",
    )
    record = await authorization.decide_approval(
        session_factory,
        settings=settings,
        approval_id=outcome.approval_id,
        decision="APPROVED",
        approval_token=approval_token,
    )
    assert record.status == "APPROVED"
    assert record.approver_id == "approver_01"
    assert record.approver_issuer == f"dev-oidc:{SUPPLIER_ORG}"

    evidence = await authorization.get_own_evidence(
        session_factory, negotiation_id=NEGOTIATION_ID, content_hash=outcome.content_hash
    )
    assert evidence is not None
    assert evidence.allow is True


@pytest.mark.asyncio
async def test_approval_disabled_in_production_environment(session_factory) -> None:
    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings(environment="production")
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    approval_token = issue_dev_approval_token(
        organization_id=SUPPLIER_ORG,
        approver_id="approver_01",
        private_key=_SUPPLIER_PRIVATE_KEY,
        kid="supplier-k1",
    )
    with pytest.raises(authorization.UnauthorizedApproverError):
        await authorization.decide_approval(
            session_factory,
            settings=settings,
            approval_id=outcome.approval_id,
            decision="APPROVED",
            approval_token=approval_token,
        )


@pytest.mark.asyncio
async def test_approval_rejects_token_for_different_organization(session_factory) -> None:
    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    wrong_org_token = issue_dev_approval_token(
        organization_id="org_someone_else",
        approver_id="approver_x",
        private_key=_SUPPLIER_PRIVATE_KEY,
        kid="supplier-k1",
    )
    with pytest.raises(authorization.UnauthorizedApproverError):
        await authorization.decide_approval(
            session_factory,
            settings=settings,
            approval_id=outcome.approval_id,
            decision="APPROVED",
            approval_token=wrong_org_token,
        )


@pytest.mark.asyncio
async def test_approval_rejects_token_signed_by_different_key(session_factory) -> None:
    """Impersonation: a token claiming to be this organization but
    signed with a *different* private key must be rejected."""
    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    attacker_private_key, _ = generate_signing_keypair()
    forged_token = issue_dev_approval_token(
        organization_id=SUPPLIER_ORG,
        approver_id="approver_01",
        private_key=attacker_private_key,
        kid="supplier-k1",
    )
    with pytest.raises(authorization.UnauthorizedApproverError):
        await authorization.decide_approval(
            session_factory,
            settings=settings,
            approval_id=outcome.approval_id,
            decision="APPROVED",
            approval_token=forged_token,
        )


@pytest.mark.asyncio
async def test_approving_twice_is_rejected(session_factory) -> None:
    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    approval_token = issue_dev_approval_token(
        organization_id=SUPPLIER_ORG,
        approver_id="approver_01",
        private_key=_SUPPLIER_PRIVATE_KEY,
        kid="supplier-k1",
    )
    await authorization.decide_approval(
        session_factory,
        settings=settings,
        approval_id=outcome.approval_id,
        decision="APPROVED",
        approval_token=approval_token,
    )
    with pytest.raises(authorization.ApprovalNotPendingError):
        await authorization.decide_approval(
            session_factory,
            settings=settings,
            approval_id=outcome.approval_id,
            decision="APPROVED",
            approval_token=approval_token,
        )


@pytest.mark.asyncio
async def test_expired_approval_cannot_be_decided(session_factory) -> None:
    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    # Force expiry directly, simulating time passing.
    from supplier_agent.models import SupplierApprovalRecord

    async with session_factory() as session:
        record = await session.get(SupplierApprovalRecord, outcome.approval_id)
        record.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        await session.merge(record)
        await session.commit()

    approval_token = issue_dev_approval_token(
        organization_id=SUPPLIER_ORG,
        approver_id="approver_01",
        private_key=_SUPPLIER_PRIVATE_KEY,
        kid="supplier-k1",
    )
    with pytest.raises(authorization.ApprovalNotPendingError):
        await authorization.decide_approval(
            session_factory,
            settings=settings,
            approval_id=outcome.approval_id,
            decision="APPROVED",
            approval_token=approval_token,
        )


@pytest.mark.asyncio
async def test_record_counterparty_evidence_accepts_verified_proof(session_factory) -> None:
    agreement = await _seed_negotiation(session_factory, unit_price="1.00")
    verified = _verified_buyer_proof(content_hash=agreement["content_hash"])
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
    record = await authorization.record_counterparty_evidence(
        session_factory,
        negotiation_id=NEGOTIATION_ID,
        content_hash=agreement["content_hash"],
        artifact=artifact,
        verified=verified,
    )
    assert record.organization_id == BUYER_ORG
    assert record.allow is True


@pytest.mark.asyncio
async def test_record_counterparty_evidence_rejects_verified_sender_not_matching_negotiation(
    session_factory,
) -> None:
    """A cryptographically valid proof, but from an organization/agent
    that is not this negotiation's actual counterparty -- the
    signature is real, but it does not authorize *this* negotiation."""
    agreement = await _seed_negotiation(session_factory, unit_price="1.00")
    verified = VerifiedProof(
        organization_id="org_attacker",
        agent_id="agent_attacker",
        recipient_organization_id=SUPPLIER_ORG,
        negotiation_id=NEGOTIATION_ID,
        artifact_type="authorization_evidence",
        version=1,
        content_hash=agreement["content_hash"],
        jti=str(uuid.uuid4()),
        issued_at=0,
        expires_at=0,
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
        }
    }
    with pytest.raises(authorization.UnverifiedCounterpartyError):
        await authorization.record_counterparty_evidence(
            session_factory,
            negotiation_id=NEGOTIATION_ID,
            content_hash=agreement["content_hash"],
            artifact=artifact,
            verified=verified,
        )


@pytest.mark.asyncio
async def test_record_counterparty_evidence_rejects_wrong_content_hash(session_factory) -> None:
    agreement = await _seed_negotiation(session_factory, unit_price="1.00")
    verified = _verified_buyer_proof()
    artifact = {
        "payload": {
            "agreement_id": NEGOTIATION_ID,
            "content_hash": "sha256:a-different-hash",
            "allow": True,
            "human_approval_required": False,
            "deny_reason": None,
            "policy_version": "v1",
            "policy_input_digest": "sha256:digest",
        }
    }
    with pytest.raises(authorization.StaleEvidenceError):
        await authorization.record_counterparty_evidence(
            session_factory,
            negotiation_id=NEGOTIATION_ID,
            content_hash=agreement["content_hash"],
            artifact=artifact,
            verified=verified,
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

    verified = _verified_buyer_proof(content_hash=content_hash)
    await authorization.record_counterparty_evidence(
        session_factory,
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
            }
        },
        verified=verified,
    )
    assert (
        await authorization.is_mutually_authorized(
            session_factory, negotiation_id=NEGOTIATION_ID, content_hash=content_hash
        )
        is True
    )
