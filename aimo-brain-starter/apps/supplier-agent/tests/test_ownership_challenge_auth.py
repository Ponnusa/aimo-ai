"""Found during a staging end-to-end exercise (2026-11-27): the PUT side
of ``/internal/ownership-challenge`` had no authentication at all, so any
unauthenticated caller on the internet could overwrite or clear the
challenge value this agent serves to registry-api mid-verification. The
GET side is deliberately still public (registry-api must be able to
fetch it) -- only the write path needed a credential."""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from message_integrity import generate_signing_keypair, private_key_to_pem
from supplier_agent.app import create_app
from supplier_agent.settings import get_settings

from database import Base, new_engine
from identity import issue_service_credential

SUPPLIER_ORG = "org_fictional_wholesale_01"
OTHER_ORG = "org_fictional_bistro_01"

_PRIVATE_KEY, _ = generate_signing_keypair()


async def _fresh_app():
    path = os.path.join(
        tempfile.gettempdir(), f"aimo_supplier_challenge_test_{uuid.uuid4().hex}.sqlite3"
    )
    os.environ["SUPPLIER_DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
    os.environ["SUPPLIER_ORGANIZATION_ID"] = SUPPLIER_ORG
    os.environ["SUPPLIER_SIGNING_PRIVATE_KEY_PEM"] = private_key_to_pem(_PRIVATE_KEY)
    os.environ["SUPPLIER_SIGNING_KID"] = "supplier-k1"
    os.environ["ENVIRONMENT"] = "local"
    get_settings.cache_clear()
    settings = get_settings()

    engine = new_engine(settings.supplier_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    return create_app(), settings


@pytest.mark.asyncio
async def test_rejects_setting_the_challenge_with_no_credential() -> None:
    app, _ = await _fresh_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/internal/ownership-challenge", json={"challenge": "attacker-supplied-value"}
        )
        assert response.status_code == 401
        served = await client.get("/.well-known/aimo-brain-challenge")
        assert served.status_code == 404


@pytest.mark.asyncio
async def test_rejects_setting_the_challenge_with_wrong_organizations_credential() -> None:
    app, settings = await _fresh_app()
    other_org_token = issue_service_credential(
        organization_id=OTHER_ORG,
        scope="manage_organization",
        signing_secret=settings.service_credential_signing_secret,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/internal/ownership-challenge",
            json={"challenge": "attacker-supplied-value"},
            headers={"Authorization": f"Bearer {other_org_token}"},
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_rejects_setting_the_challenge_with_wrong_scope() -> None:
    app, settings = await _fresh_app()
    wrong_scope_token = issue_service_credential(
        organization_id=SUPPLIER_ORG,
        scope="manage_agents",
        signing_secret=settings.service_credential_signing_secret,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/internal/ownership-challenge",
            json={"challenge": "attacker-supplied-value"},
            headers={"Authorization": f"Bearer {wrong_scope_token}"},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_accepts_setting_the_challenge_with_the_right_credential() -> None:
    app, settings = await _fresh_app()
    token = issue_service_credential(
        organization_id=SUPPLIER_ORG,
        scope="manage_organization",
        signing_secret=settings.service_credential_signing_secret,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/internal/ownership-challenge",
            json={"challenge": "the-real-challenge-value"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        served = await client.get("/.well-known/aimo-brain-challenge")
        assert served.status_code == 200
        assert served.text == "the-real-challenge-value"
