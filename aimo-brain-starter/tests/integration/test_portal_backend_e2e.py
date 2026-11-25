"""M6 acceptance test: portal-to-backend integration over real HTTP.

Exercises exactly the sequence the Next.js portal performs (lib/
networkApi.ts, lib/managementApi.ts, lib/portalSigning.ts), but from
Python, against two real servers (network-api, buyer-agent) — no
ASGITransport, no mocked backend. The browser-level equivalent of this
same flow lives in ``apps/portal/tests-e2e/approval-flow.spec.ts``
(Playwright, driving the actual portal UI); this test instead proves
the HTTP contract those pages depend on, independent of the frontend.

Flow:
1. Register the buyer organization's and the portal's own Ed25519
   signing keys with network-api, exactly like an admin/operator would.
2. Grant one subject the ``approver`` role for the buyer organization.
3. Confirm network-api's member lookup (the portal's role-resolution
   call, and the agent's own revalidation call) reflects it.
4. Mint a ``manage_organization``-scoped credential (what the portal
   mints per request) and use it to list buyer-agent's pending
   approvals through its management API.
5. Mint a portal approval assertion (identity.portal_approval) and
   decide the approval through the same management API — verified by
   the agent's own ``decide_approval``, including the fresh member-role
   revalidation, not merely trusted from the assertion.
6. Revoke the approver role and confirm a second, otherwise-identical
   decision attempt is rejected — the actual "revocation takes effect
   on the very next action" guarantee docs/ARCHITECTURE.md's M6 section
   describes.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from identity.portal_approval import issue_portal_approval_token
from message_integrity import generate_signing_keypair, public_key_to_jwk
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import new_engine

from .a2a_server_harness import find_free_port, start_server

pytestmark = pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed")

BUYER_ORG = "org_fictional_bistro_01"
PORTAL_ORG = "org_aimo_portal"
BUYER_AGENT_ID = "agent_fictional_buyer_01"
APPROVER_SUBJECT = "portal-e2e-approver"

ADMIN_KEY = "portal-m6-admin-key"
INTERNAL_KEY = "portal-m6-internal-key"
SIGNING_SECRET = "portal-m6-signing-secret"


def _fresh_sqlite_url(label: str) -> str:
    path = os.path.join(tempfile.gettempdir(), f"aimo_portal_m6_{label}_{uuid.uuid4().hex}.sqlite3")
    return f"sqlite+aiosqlite:///{path}"


def _build_network_app(database_url: str):
    os.environ["DATABASE_URL"] = database_url
    os.environ["NETWORK_ADMIN_API_KEY"] = ADMIN_KEY
    os.environ["NETWORK_INTERNAL_SERVICE_KEY"] = INTERNAL_KEY
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET

    import network_api.app as network_app_module
    import network_api.settings as network_settings_module
    from network_api.models import (
        AgentSigningDelegationRecord,
        OrganizationKidNamespaceRecord,
        OrganizationMemberRecord,
        OrganizationRecord,
        OrganizationSigningKeyRecord,
    )

    network_settings_module.get_settings.cache_clear()
    app = network_app_module.create_app()
    return app, (
        OrganizationRecord,
        OrganizationSigningKeyRecord,
        OrganizationKidNamespaceRecord,
        AgentSigningDelegationRecord,
        OrganizationMemberRecord,
    )


def _build_buyer_app(database_url: str, buyer_a2a_url: str, network_api_url: str):
    os.environ["BUYER_A2A_URL"] = buyer_a2a_url
    os.environ["BUYER_DATABASE_URL"] = database_url
    os.environ["BUYER_ORGANIZATION_ID"] = BUYER_ORG
    os.environ["BUYER_AGENT_ID"] = BUYER_AGENT_ID
    os.environ["REGISTRY_API_URL"] = "http://127.0.0.1:1"
    os.environ["BUYER_MCP_URL"] = "http://127.0.0.1:1/mcp"
    os.environ["BUYER_MCP_SERVICE_CREDENTIAL"] = ""
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET
    os.environ["NETWORK_API_URL"] = network_api_url
    os.environ["NETWORK_INTERNAL_SERVICE_KEY"] = INTERNAL_KEY
    os.environ["PORTAL_ORGANIZATION_ID"] = PORTAL_ORG
    os.environ["BUYER_SIGNING_PRIVATE_KEY_PEM"] = ""
    os.environ["BUYER_SIGNING_KID"] = "buyer-m6-k1"
    os.environ["ENVIRONMENT"] = "local"

    import buyer_agent.app as buyer_app_module
    import buyer_agent.settings as buyer_settings_module

    buyer_settings_module.get_settings.cache_clear()
    return buyer_app_module.create_app()


async def _create_tables(engine, *models) -> None:
    from database import Base

    tables = [model.__table__ for model in models]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)


@pytest.fixture
async def servers():
    from network_api.models import OrganizationRecord

    network_database_url = _fresh_sqlite_url("network")
    network_app, network_models = _build_network_app(network_database_url)
    network_engine = new_engine(network_database_url)
    await _create_tables(network_engine, *network_models)
    session_factory = async_sessionmaker(network_engine, expire_on_commit=False)
    async with session_factory() as session:
        for org_id in (BUYER_ORG, PORTAL_ORG):
            await session.merge(
                OrganizationRecord(organization_id=org_id, display_name=org_id, verified=True)
            )
        await session.commit()

    network_port = find_free_port()
    network_server = start_server(network_app, port=network_port)

    buyer_database_url = _fresh_sqlite_url("buyer")
    buyer_port = find_free_port()
    buyer_app = _build_buyer_app(
        buyer_database_url, f"http://127.0.0.1:{buyer_port}", network_server.base_url
    )
    buyer_server = start_server(buyer_app, port=buyer_port)

    try:
        yield network_server, buyer_server, buyer_database_url
    finally:
        buyer_server.stop()
        network_server.stop()
        await network_engine.dispose()


async def _seed_pending_approval(database_url: str, *, negotiation_id: str = "txn_m6_01") -> str:
    from buyer_agent.models import BuyerApprovalRecord, BuyerNegotiationRecord, BuyerProposalRecord
    from food_wholesale import build_proposed_agreement

    from database import Base

    engine = new_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC).replace(tzinfo=None)
    suffix = negotiation_id
    agreement = build_proposed_agreement(
        artifact_id=f"art_{suffix}",
        transaction_id=negotiation_id,
        correlation_id=f"corr_{suffix}",
        sender_agent_id="agent_fictional_supplier_01",
        recipient_agent_id=BUYER_AGENT_ID,
        organization_id="org_fictional_wholesale_01",
        proposal_id=f"prop_{suffix}_pa",
        version=3,
        agreement_of_proposal_id=f"prop_{suffix}_quote",
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
    async with session_factory() as session:
        session.add(
            BuyerNegotiationRecord(
                negotiation_id=negotiation_id,
                counterparty_organization_id="org_fictional_wholesale_01",
                counterparty_agent_id="agent_fictional_supplier_01",
                state="PENDING_AUTHORIZATION",
                current_proposal_id=f"prop_{suffix}_pa",
                round_number=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            BuyerProposalRecord(
                proposal_id=f"prop_{suffix}_pa",
                negotiation_id=negotiation_id,
                version=3,
                artifact_type="proposed_agreement",
                content_hash=agreement["content_hash"],
                supersedes_proposal_id=f"prop_{suffix}_quote",
                payload_json=json.dumps(agreement),
                created_at=now,
            )
        )
        session.add(
            BuyerApprovalRecord(
                negotiation_id=negotiation_id,
                content_hash=agreement["content_hash"],
                organization_id=BUYER_ORG,
                approver_id=None,
                status="PENDING",
                policy_version="v1",
                policy_decision_json=json.dumps(
                    {"allow": True, "human_approval_required": True, "input_digest": "m6-digest"}
                ),
                audit_trail_json=json.dumps([{"event": "requested", "at": now.isoformat()}]),
                requested_at=now,
                decided_at=None,
                expires_at=now + timedelta(hours=24),
            )
        )
        await session.commit()
    await engine.dispose()
    return agreement["content_hash"]


@pytest.mark.asyncio
async def test_portal_backend_approval_flow_and_role_revocation(servers) -> None:
    network_server, buyer_server, buyer_database_url = servers
    content_hash = await _seed_pending_approval(buyer_database_url)

    portal_private_key, portal_public_key = generate_signing_keypair()
    portal_kid = "portal-m6-k1"

    async with httpx.AsyncClient() as client:
        # 1. Register the portal's own public signing key, exactly like
        # scripts/generate_signing_key.py's output would be registered.
        response = await client.post(
            f"{network_server.base_url}/organizations/{PORTAL_ORG}/signing-keys",
            json={
                "kid": portal_kid,
                "public_key_jwk": public_key_to_jwk(portal_public_key, kid=portal_kid),
            },
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert response.status_code == 200

        # 2. Grant the approver role.
        response = await client.post(
            f"{network_server.base_url}/organizations/{BUYER_ORG}/members",
            json={
                "oidc_subject": APPROVER_SUBJECT,
                "email": "approver@example.com",
                "display_name": "Portal E2E Approver",
                "role": "approver",
            },
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert response.status_code == 200
        member_id = response.json()["id"]

        # 3. The portal's own role-resolution call.
        response = await client.get(
            f"{network_server.base_url}/members/lookup",
            params={
                "organization_id": BUYER_ORG,
                "oidc_subject": APPROVER_SUBJECT,
                "role": "approver",
            },
            headers={"X-Internal-Service-Key": INTERNAL_KEY},
        )
        assert response.status_code == 200
        assert response.json()["active"] is True

        # 4. Mint the manage_organization credential and list approvals.
        response = await client.post(
            f"{network_server.base_url}/organizations/{BUYER_ORG}/service-credentials",
            json={"scope": "manage_organization", "ttl_seconds": 60},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert response.status_code == 200
        management_token = response.json()["token"]

        response = await client.get(
            f"{buyer_server.base_url}/management/approvals",
            params={"status": "PENDING"},
            headers={"Authorization": f"Bearer {management_token}"},
        )
        assert response.status_code == 200
        approvals = response.json()
        assert len(approvals) == 1
        approval_id = approvals[0]["id"]
        assert approvals[0]["content_hash"] == content_hash

        # 5. Decide it with a fresh portal approval assertion.
        approval_token = issue_portal_approval_token(
            organization_id=BUYER_ORG,
            approver_subject=APPROVER_SUBJECT,
            negotiation_id="txn_m6_01",
            content_hash=content_hash,
            private_key=portal_private_key,
            kid=portal_kid,
        )
        response = await client.post(
            f"{buyer_server.base_url}/management/approvals/{approval_id}/decide",
            json={"decision": "APPROVED", "approval_token": approval_token},
            headers={"Authorization": f"Bearer {management_token}"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "APPROVED"

        response = await client.get(
            f"{buyer_server.base_url}/management/evidence",
            headers={"Authorization": f"Bearer {management_token}"},
        )
        assert response.status_code == 200
        assert any(row["content_hash"] == content_hash and row["allow"] for row in response.json())

        # 6. Revoke the role; seed a second pending approval and confirm
        # a fresh decision attempt is rejected on the very next call —
        # not merely on the next portal login.
        response = await client.post(
            f"{network_server.base_url}/organizations/{BUYER_ORG}/members/{member_id}/revoke",
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert response.status_code == 200

    second_content_hash = await _seed_pending_approval(
        buyer_database_url, negotiation_id="txn_m6_02"
    )
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{buyer_server.base_url}/management/approvals",
            params={"status": "PENDING"},
            headers={"Authorization": f"Bearer {management_token}"},
        )
        pending = [row for row in response.json() if row["content_hash"] == second_content_hash]
        assert len(pending) == 1
        second_approval_id = pending[0]["id"]

        second_approval_token = issue_portal_approval_token(
            organization_id=BUYER_ORG,
            approver_subject=APPROVER_SUBJECT,
            negotiation_id="txn_m6_02",
            content_hash=second_content_hash,
            private_key=portal_private_key,
            kid=portal_kid,
        )
        response = await client.post(
            f"{buyer_server.base_url}/management/approvals/{second_approval_id}/decide",
            json={"decision": "APPROVED", "approval_token": second_approval_token},
            headers={"Authorization": f"Bearer {management_token}"},
        )
        assert response.status_code == 409
