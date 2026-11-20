from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from food_wholesale import build_proposed_agreement
from identity.portal_approval import issue_portal_approval_token
from message_integrity import generate_signing_keypair, private_key_to_pem
from sqlalchemy.ext.asyncio import async_sessionmaker
from supplier_agent import authorization, negotiation_store
from supplier_agent.settings import SupplierAgentSettings
from trade_protocol import TradeState

from database import Base, new_engine

pytestmark = pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed")

NEGOTIATION_ID = "txn_portal_01"
BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"
BUYER_AGENT_ID = "agent_buyer_01"
SUPPLIER_AGENT_ID = "agent_supplier_01"

_SUPPLIER_PRIVATE_KEY, _ = generate_signing_keypair()
_PORTAL_PRIVATE_KEY, _PORTAL_PUBLIC_KEY = generate_signing_keypair()
PORTAL_KID = "portal-k1"


class _FakeJwksResolver:
    def __init__(self, *, network_api_url: str) -> None:
        del network_api_url

    async def get_key(self, *, organization_id: str, kid: str):
        assert kid == PORTAL_KID
        return _PORTAL_PUBLIC_KEY


@pytest.fixture
async def session_factory() -> async_sessionmaker:
    path = os.path.join(
        tempfile.gettempdir(), f"aimo_supplier_portal_test_{uuid.uuid4().hex}.sqlite3"
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


def _lines(unit_price: str) -> list[dict[str, str]]:
    return [
        {
            "product_ref": "sku_fictional_milk_lactose_free",
            "quantity": "100",
            "unit": "L",
            "unit_price": unit_price,
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


def _portal_token(*, content_hash: str, approver_subject: str) -> str:
    return issue_portal_approval_token(
        organization_id=SUPPLIER_ORG,
        approver_subject=approver_subject,
        negotiation_id=NEGOTIATION_ID,
        content_hash=content_hash,
        private_key=_PORTAL_PRIVATE_KEY,
        kid=PORTAL_KID,
    )


@pytest.mark.asyncio
async def test_portal_assertion_from_active_approver_approves(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(authorization, "HttpJwksResolver", _FakeJwksResolver)

    async def _active(**kwargs):
        return True

    monkeypatch.setattr(authorization, "is_active_member_with_role", _active)

    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    token = _portal_token(content_hash=outcome.content_hash, approver_subject="oidc|1")
    record = await authorization.decide_approval(
        session_factory,
        settings=settings,
        approval_id=outcome.approval_id,
        decision="APPROVED",
        approval_token=token,
    )
    assert record.status == "APPROVED"
    assert record.approver_issuer == "aimo-portal"


@pytest.mark.asyncio
async def test_portal_assertion_from_revoked_member_rejected(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(authorization, "HttpJwksResolver", _FakeJwksResolver)

    async def _inactive(**kwargs):
        return False

    monkeypatch.setattr(authorization, "is_active_member_with_role", _inactive)

    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings()
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    token = _portal_token(content_hash=outcome.content_hash, approver_subject="oidc|1")
    with pytest.raises(authorization.UnauthorizedApproverError):
        await authorization.decide_approval(
            session_factory,
            settings=settings,
            approval_id=outcome.approval_id,
            decision="APPROVED",
            approval_token=token,
        )


@pytest.mark.asyncio
async def test_dual_control_requires_two_distinct_approvers(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(authorization, "HttpJwksResolver", _FakeJwksResolver)

    async def _active(**kwargs):
        return True

    monkeypatch.setattr(authorization, "is_active_member_with_role", _active)

    await _seed_negotiation(session_factory, unit_price="2.50")
    settings = _settings(supplier_require_second_approver_above_amount="200")
    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )

    first = await authorization.decide_approval(
        session_factory,
        settings=settings,
        approval_id=outcome.approval_id,
        decision="APPROVED",
        approval_token=_portal_token(content_hash=outcome.content_hash, approver_subject="oidc|1"),
    )
    assert first.status == "PENDING"

    with pytest.raises(authorization.UnauthorizedApproverError):
        await authorization.decide_approval(
            session_factory,
            settings=settings,
            approval_id=outcome.approval_id,
            decision="APPROVED",
            approval_token=_portal_token(
                content_hash=outcome.content_hash, approver_subject="oidc|1"
            ),
        )

    second = await authorization.decide_approval(
        session_factory,
        settings=settings,
        approval_id=outcome.approval_id,
        decision="APPROVED",
        approval_token=_portal_token(content_hash=outcome.content_hash, approver_subject="oidc|2"),
    )
    assert second.status == "APPROVED"
