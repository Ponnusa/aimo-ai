from __future__ import annotations

import os
import shutil
import tempfile
import uuid

import pytest
from buyer_agent import negotiation_store
from buyer_agent.app import create_app
from buyer_agent.settings import get_settings
from food_wholesale import build_proposed_agreement
from httpx import ASGITransport, AsyncClient
from identity.dev_oidc import issue_dev_approval_token
from message_integrity import generate_signing_keypair, private_key_to_pem
from trade_protocol import TradeState

from database import Base, new_engine
from identity import issue_service_credential

pytestmark = pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed")

BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"
BUYER_AGENT_ID = "agent_buyer_01"
SUPPLIER_AGENT_ID = "agent_supplier_01"
NEGOTIATION_ID = "txn_mgmt_01"

_BUYER_PRIVATE_KEY, _ = generate_signing_keypair()


async def _fresh_app():
    path = os.path.join(tempfile.gettempdir(), f"aimo_buyer_mgmt_test_{uuid.uuid4().hex}.sqlite3")
    os.environ["BUYER_DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
    os.environ["BUYER_ORGANIZATION_ID"] = BUYER_ORG
    os.environ["BUYER_AGENT_ID"] = BUYER_AGENT_ID
    os.environ["BUYER_SIGNING_PRIVATE_KEY_PEM"] = private_key_to_pem(_BUYER_PRIVATE_KEY)
    os.environ["BUYER_SIGNING_KID"] = "buyer-k1"
    os.environ["ENVIRONMENT"] = "local"
    get_settings.cache_clear()
    settings = get_settings()

    engine = new_engine(settings.buyer_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    app = create_app()
    return app, settings


def _management_credential(settings, *, agent_id: str | None = None) -> str:
    return issue_service_credential(
        organization_id=settings.buyer_organization_id,
        scope="manage_organization",
        signing_secret=settings.service_credential_signing_secret,
        agent_id=agent_id,
    )


async def _seed_negotiation_and_approval(session_factory) -> dict:
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
        lines=[
            {
                "product_ref": "sku_fictional_milk_lactose_free",
                "quantity": "100",
                "unit": "L",
                "unit_price": "2.50",
            }
        ],
        currency="EUR",
        delivery_terms=None,
    )
    await negotiation_store.start_negotiation(
        session_factory,
        negotiation_id=NEGOTIATION_ID,
        counterparty_organization_id=SUPPLIER_ORG,
        counterparty_agent_id=SUPPLIER_AGENT_ID,
        rfq_artifact={"proposal_id": "prop_rfq", "version": 1, "type": "request_for_quote"},
    )
    await negotiation_store.record_received_artifact(
        session_factory,
        negotiation_id=NEGOTIATION_ID,
        artifact={
            "proposal_id": "prop_02",
            "version": 2,
            "type": "quote",
            "content_hash": "sha256:quote-placeholder",
            "supersedes_proposal_id": "prop_rfq",
        },
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
async def test_management_endpoints_require_scoped_credential() -> None:
    app, settings = await _fresh_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        no_auth = await client.get("/management/negotiations")
        assert no_auth.status_code == 401

        wrong_scope_token = issue_service_credential(
            organization_id=settings.buyer_organization_id,
            scope="use_buyer_tools",
            signing_secret=settings.service_credential_signing_secret,
        )
        wrong_scope = await client.get(
            "/management/negotiations", headers={"Authorization": f"Bearer {wrong_scope_token}"}
        )
        assert wrong_scope.status_code == 401

        wrong_org_token = issue_service_credential(
            organization_id="org_someone_else",
            scope="manage_organization",
            signing_secret=settings.service_credential_signing_secret,
        )
        wrong_org = await client.get(
            "/management/negotiations", headers={"Authorization": f"Bearer {wrong_org_token}"}
        )
        assert wrong_org.status_code == 403


@pytest.mark.asyncio
async def test_list_negotiations_and_approvals_and_decide_via_http() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    app, settings = await _fresh_app()
    engine = new_engine(settings.buyer_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    agreement = await _seed_negotiation_and_approval(session_factory)

    from buyer_agent import authorization

    outcome = await authorization.evaluate_and_request_approval(
        session_factory, settings=settings, negotiation_id=NEGOTIATION_ID
    )
    assert outcome.human_approval_required is True

    token = _management_credential(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        negotiations = await client.get(
            "/management/negotiations", headers={"Authorization": f"Bearer {token}"}
        )
        assert negotiations.status_code == 200
        assert negotiations.json()[0]["negotiation_id"] == NEGOTIATION_ID

        approvals = await client.get(
            "/management/approvals", headers={"Authorization": f"Bearer {token}"}
        )
        assert approvals.status_code == 200
        assert approvals.json()[0]["status"] == "PENDING"

        approval_token = issue_dev_approval_token(
            organization_id=BUYER_ORG,
            approver_id="approver_01",
            private_key=_BUYER_PRIVATE_KEY,
            kid="buyer-k1",
        )
        decide = await client.post(
            f"/management/approvals/{outcome.approval_id}/decide",
            json={"decision": "APPROVED", "approval_token": approval_token},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert decide.status_code == 200
        assert decide.json()["status"] == "APPROVED"

        evidence = await client.get(
            "/management/evidence", headers={"Authorization": f"Bearer {token}"}
        )
        assert evidence.status_code == 200
        assert any(row["content_hash"] == agreement["content_hash"] for row in evidence.json())

    await engine.dispose()


@pytest.mark.asyncio
async def test_policy_catalog_dry_run_and_activate() -> None:
    app, settings = await _fresh_app()
    token = _management_credential(settings)
    admin_token = _management_credential(settings, agent_id="portal-admin-action")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        catalog = await client.get(
            "/management/policy/catalog", headers={"Authorization": f"Bearer {token}"}
        )
        assert catalog.status_code == 200
        assert catalog.json()["active_version"] == "v1"
        assert "v1" in catalog.json()["available_versions"]

        dry_run = await client.post(
            "/management/policy/dry-run",
            json={
                "policy_version": "v1",
                "counterparty_organization_id": SUPPLIER_ORG,
                "terms": {
                    "total_amount": "1.00",
                    "currency": "EUR",
                    "counterparty_organization_id": SUPPLIER_ORG,
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert dry_run.status_code == 200
        assert dry_run.json()["allow"] is True

        non_admin_activate = await client.post(
            "/management/policy/activate",
            json={"policy_version": "v1", "activated_by": "oidc|admin"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert non_admin_activate.status_code == 403

        activate = await client.post(
            "/management/policy/activate",
            json={"policy_version": "v1", "activated_by": "oidc|admin"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert activate.status_code == 200
        assert activate.json()["active_version"] == "v1"

        bad_version = await client.post(
            "/management/policy/activate",
            json={"policy_version": "v99", "activated_by": "oidc|admin"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert bad_version.status_code == 400
