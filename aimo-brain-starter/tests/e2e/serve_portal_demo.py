"""Standalone backend fixture for the portal's browser-level M6 tests
(``apps/portal/tests-e2e``), started by Playwright's own ``webServer``
config — never by pytest.

Boots network-api and buyer-agent as real HTTP servers on fixed ports,
seeds a buyer organization, a portal pseudo-organization holding the
fixed throwaway e2e signing key below (never used outside this script),
one organization member with the ``approver`` role, and one pending
human-approval request inserted directly into buyer-agent's own
database. Deliberately skips supplier-agent/registry-api/OPA — the
Playwright spec's job is to prove the *portal's own* login/approval UI
against real backend APIs, not to re-prove the negotiation/policy
protocol underneath, which hundreds of existing Python tests already
cover end to end.

This file is read by apps/portal/playwright.config.ts, which also sets
the matching PORTAL_* environment variables for the Next.js server it
starts alongside this one.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import uuid
from datetime import UTC, datetime, timedelta

import httpx

NETWORK_PORT = 18000
BUYER_PORT = 18001
ADMIN_KEY = "portal-e2e-admin-key"
INTERNAL_KEY = "portal-e2e-internal-key"
SIGNING_SECRET = "portal-e2e-signing-secret"

BUYER_ORG = "org_fictional_bistro_01"
PORTAL_ORG = "org_aimo_portal"
BUYER_AGENT_ID = "agent_fictional_buyer_01"
BUYER_KID = "portal-e2e-buyer-k1"

APPROVER_SUBJECT = "dev-approver-1"
APPROVER_EMAIL = "dev-approver-1@example.com"

# Fixed, checked-in-for-tests-only throwaway keypair (never a production
# secret) — see the matching PEM/JWK in apps/portal/tests-e2e for the
# values Playwright's config injects as PORTAL_SIGNING_PRIVATE_KEY_PEM.
PORTAL_PRIVATE_KEY_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MC4CAQAwBQYDK2VwBCIEIJcuGCd6PaRYUXmuxcK/Y4E6RebCdIQhtEEAa0s0kToo\n"
    "-----END PRIVATE KEY-----\n"
)
PORTAL_KID = "portal-e2e-k1"
PORTAL_PUBLIC_JWK = {
    "kty": "OKP",
    "crv": "Ed25519",
    "kid": PORTAL_KID,
    "x": "W3IDMKz9Nt1P2Ku7QwI24xmmv_ufhlfpkr3YbYZIXKU",
    "use": "sig",
    "alg": "EdDSA",
}


def _fresh_sqlite_url(label: str) -> str:
    path = os.path.join(
        tempfile.gettempdir(), f"aimo_portal_e2e_{label}_{uuid.uuid4().hex}.sqlite3"
    )
    return f"sqlite+aiosqlite:///{path}"


def _build_network_app(database_url: str):
    os.environ["DATABASE_URL"] = database_url
    os.environ["NETWORK_ADMIN_API_KEY"] = ADMIN_KEY
    os.environ["NETWORK_INTERNAL_SERVICE_KEY"] = INTERNAL_KEY
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET

    import network_api.app as network_app_module
    import network_api.settings as network_settings_module

    network_settings_module.get_settings.cache_clear()
    return network_app_module.create_app()


def _build_buyer_app(database_url: str, buyer_a2a_url: str):
    os.environ["BUYER_A2A_URL"] = buyer_a2a_url
    os.environ["BUYER_DATABASE_URL"] = database_url
    os.environ["BUYER_ORGANIZATION_ID"] = BUYER_ORG
    os.environ["BUYER_AGENT_ID"] = BUYER_AGENT_ID
    os.environ["REGISTRY_API_URL"] = "http://127.0.0.1:1"  # unused in this demo
    os.environ["BUYER_MCP_URL"] = "http://127.0.0.1:1/mcp"  # unused in this demo
    os.environ["BUYER_MCP_SERVICE_CREDENTIAL"] = ""
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = SIGNING_SECRET
    os.environ["NETWORK_API_URL"] = f"http://127.0.0.1:{NETWORK_PORT}"
    os.environ["NETWORK_INTERNAL_SERVICE_KEY"] = INTERNAL_KEY
    os.environ["PORTAL_ORGANIZATION_ID"] = PORTAL_ORG
    os.environ["BUYER_SIGNING_PRIVATE_KEY_PEM"] = PORTAL_PRIVATE_KEY_PEM  # placeholder, unused here
    os.environ["BUYER_SIGNING_KID"] = BUYER_KID
    os.environ["ENVIRONMENT"] = "local"

    import buyer_agent.app as buyer_app_module
    import buyer_agent.settings as buyer_settings_module

    buyer_settings_module.get_settings.cache_clear()
    return buyer_app_module.create_app()


async def _create_network_tables_and_orgs(database_url: str) -> None:
    from network_api.models import OrganizationRecord
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from database import Base, new_engine

    engine = new_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        for org_id in (BUYER_ORG, PORTAL_ORG):
            await session.merge(
                OrganizationRecord(organization_id=org_id, display_name=org_id, verified=True)
            )
        await session.commit()
    await engine.dispose()


async def _seed_network(base_url: str) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/organizations/{PORTAL_ORG}/signing-keys",
            json={"kid": PORTAL_KID, "public_key_jwk": PORTAL_PUBLIC_JWK},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        response.raise_for_status()

        response = await client.post(
            f"{base_url}/organizations/{BUYER_ORG}/members",
            json={
                "oidc_subject": APPROVER_SUBJECT,
                "email": APPROVER_EMAIL,
                "display_name": "Dev Approver",
                "role": "approver",
            },
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        response.raise_for_status()


async def _seed_buyer_approval(database_url: str) -> None:
    from buyer_agent.models import BuyerApprovalRecord, BuyerNegotiationRecord, BuyerProposalRecord
    from food_wholesale import build_proposed_agreement
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from database import Base, new_engine

    engine = new_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC).replace(tzinfo=None)
    agreement = build_proposed_agreement(
        artifact_id="art_e2e_01",
        transaction_id="txn_e2e_01",
        correlation_id="corr_e2e_01",
        sender_agent_id="agent_fictional_supplier_01",
        recipient_agent_id=BUYER_AGENT_ID,
        organization_id="org_fictional_wholesale_01",
        proposal_id="prop_e2e_pa",
        version=3,
        agreement_of_proposal_id="prop_e2e_quote",
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
                negotiation_id="txn_e2e_01",
                counterparty_organization_id="org_fictional_wholesale_01",
                counterparty_agent_id="agent_fictional_supplier_01",
                state="PENDING_AUTHORIZATION",
                current_proposal_id="prop_e2e_pa",
                round_number=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            BuyerProposalRecord(
                proposal_id="prop_e2e_pa",
                negotiation_id="txn_e2e_01",
                version=3,
                artifact_type="proposed_agreement",
                content_hash=agreement["content_hash"],
                supersedes_proposal_id="prop_e2e_quote",
                payload_json=json.dumps(agreement),
                created_at=now,
            )
        )
        session.add(
            BuyerApprovalRecord(
                negotiation_id="txn_e2e_01",
                content_hash=agreement["content_hash"],
                organization_id=BUYER_ORG,
                approver_id=None,
                status="PENDING",
                policy_version="v1",
                policy_decision_json=json.dumps(
                    {"allow": True, "human_approval_required": True, "input_digest": "e2e-digest"}
                ),
                audit_trail_json=json.dumps([{"event": "requested", "at": now.isoformat()}]),
                requested_at=now,
                decided_at=None,
                expires_at=now + timedelta(hours=24),
            )
        )
        await session.commit()
    await engine.dispose()


def main() -> None:
    tests_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, tests_dir)
    from integration.a2a_server_harness import start_server

    network_database_url = _fresh_sqlite_url("network")
    network_app = _build_network_app(network_database_url)
    asyncio.run(_create_network_tables_and_orgs(network_database_url))
    network_server = start_server(network_app, port=NETWORK_PORT)
    print(f"network-api ready at {network_server.base_url}", flush=True)

    asyncio.run(_seed_network(network_server.base_url))

    buyer_database_url = _fresh_sqlite_url("buyer")
    asyncio.run(_seed_buyer_approval(buyer_database_url))

    buyer_app = _build_buyer_app(buyer_database_url, f"http://127.0.0.1:{BUYER_PORT}")
    buyer_server = start_server(buyer_app, port=BUYER_PORT)
    print(f"buyer-agent ready at {buyer_server.base_url}", flush=True)

    print("PORTAL_E2E_BACKEND_READY", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
