from __future__ import annotations

import os
import shutil
import tempfile
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from message_integrity import generate_signing_keypair, private_key_to_pem
from supplier_agent.app import create_app
from supplier_agent.settings import get_settings

from database import Base, new_engine
from identity import issue_service_credential

pytestmark = pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed")

SUPPLIER_ORG = "org_fictional_wholesale_01"
SUPPLIER_AGENT_ID = "agent_supplier_01"

_SUPPLIER_PRIVATE_KEY, _ = generate_signing_keypair()


async def _fresh_app():
    path = os.path.join(
        tempfile.gettempdir(), f"aimo_supplier_mgmt_test_{uuid.uuid4().hex}.sqlite3"
    )
    os.environ["SUPPLIER_DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
    os.environ["SUPPLIER_ORGANIZATION_ID"] = SUPPLIER_ORG
    os.environ["SUPPLIER_AGENT_ID"] = SUPPLIER_AGENT_ID
    os.environ["SUPPLIER_SIGNING_PRIVATE_KEY_PEM"] = private_key_to_pem(_SUPPLIER_PRIVATE_KEY)
    os.environ["SUPPLIER_SIGNING_KID"] = "supplier-k1"
    os.environ["ENVIRONMENT"] = "local"
    get_settings.cache_clear()
    settings = get_settings()

    engine = new_engine(settings.supplier_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    app = create_app()
    return app, settings


@pytest.mark.asyncio
async def test_management_endpoints_require_scoped_credential() -> None:
    app, settings = await _fresh_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        no_auth = await client.get("/management/negotiations")
        assert no_auth.status_code == 401

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
async def test_policy_catalog_and_activate_requires_admin() -> None:
    app, settings = await _fresh_app()
    token = issue_service_credential(
        organization_id=settings.supplier_organization_id,
        scope="manage_organization",
        signing_secret=settings.service_credential_signing_secret,
    )
    admin_token = issue_service_credential(
        organization_id=settings.supplier_organization_id,
        scope="manage_organization",
        signing_secret=settings.service_credential_signing_secret,
        agent_id="portal-admin-action",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        catalog = await client.get(
            "/management/policy/catalog", headers={"Authorization": f"Bearer {token}"}
        )
        assert catalog.status_code == 200
        assert catalog.json()["active_version"] == "v1"

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


@pytest.mark.asyncio
async def test_integration_health_and_reconciliation_history() -> None:
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker
    from supplier_agent.models import (
        SupplierOrderReconciliationEventRecord,
        SupplierOrderRecord,
    )

    app, settings = await _fresh_app()
    engine = new_engine(settings.supplier_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session:
        order = SupplierOrderRecord(
            negotiation_id="neg_mgmt_test",
            content_hash="sha256:mgmt",
            idempotency_key="idem_mgmt_test",
            state="RECONCILIATION_REQUIRED",
            external_order_id=None,
            attempts=1,
            created_at=now,
            updated_at=now,
        )
        session.add(order)
        await session.flush()
        session.add(
            SupplierOrderReconciliationEventRecord(
                order_id=order.id,
                negotiation_id="neg_mgmt_test",
                previous_state="NONE",
                new_state="RECONCILIATION_REQUIRED",
                detail="seeded for test",
                created_at=now,
            )
        )
        await session.commit()
    await engine.dispose()

    token = issue_service_credential(
        organization_id=settings.supplier_organization_id,
        scope="manage_organization",
        signing_secret=settings.service_credential_signing_secret,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get(
            "/management/integration/health", headers={"Authorization": f"Bearer {token}"}
        )
        assert health.status_code == 200
        assert health.json() == {
            "reconciliation_required_orders": 1,
            "failed_orders": 0,
            "confirmed_orders": 0,
            "total_orders": 1,
        }

        history = await client.get(
            "/management/integration/reconciliation",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert history.status_code == 200
        events = history.json()
        assert len(events) == 1
        assert events[0]["negotiation_id"] == "neg_mgmt_test"
        assert events[0]["new_state"] == "RECONCILIATION_REQUIRED"

        orders = await client.get(
            "/management/orders",
            params={"state": "RECONCILIATION_REQUIRED"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert orders.status_code == 200
        assert orders.json()[0]["idempotency_key"] == "idem_mgmt_test"
