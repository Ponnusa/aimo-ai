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
from buyer_agent.app import create_app
from buyer_agent.settings import get_settings
from httpx import ASGITransport, AsyncClient
from message_integrity import generate_signing_keypair, private_key_to_pem

from database import Base, new_engine
from identity import issue_service_credential

BUYER_ORG = "org_fictional_bistro_01"
OTHER_ORG = "org_fictional_wholesale_01"

_PRIVATE_KEY, _ = generate_signing_keypair()


async def _fresh_app():
    path = os.path.join(
        tempfile.gettempdir(), f"aimo_buyer_challenge_test_{uuid.uuid4().hex}.sqlite3"
    )
    os.environ["BUYER_DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
    os.environ["BUYER_ORGANIZATION_ID"] = BUYER_ORG
    os.environ["BUYER_SIGNING_PRIVATE_KEY_PEM"] = private_key_to_pem(_PRIVATE_KEY)
    os.environ["BUYER_SIGNING_KID"] = "buyer-k1"
    os.environ["ENVIRONMENT"] = "local"
    get_settings.cache_clear()
    settings = get_settings()

    engine = new_engine(settings.buyer_database_url)
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
        # And the well-known path never served the rejected value.
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
        organization_id=BUYER_ORG,
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
async def test_denied_overwrite_leaves_previously_set_challenge_unchanged() -> None:
    """A real value is set with a valid credential; a subsequent denied PUT
    (wrong org, wrong scope, and no credential at all) must not alter it --
    the GET side must keep serving the original value throughout."""
    app, settings = await _fresh_app()
    token = issue_service_credential(
        organization_id=BUYER_ORG,
        scope="manage_organization",
        signing_secret=settings.service_credential_signing_secret,
    )
    other_org_token = issue_service_credential(
        organization_id=OTHER_ORG,
        scope="manage_organization",
        signing_secret=settings.service_credential_signing_secret,
    )
    wrong_scope_token = issue_service_credential(
        organization_id=BUYER_ORG,
        scope="manage_agents",
        signing_secret=settings.service_credential_signing_secret,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        set_response = await client.put(
            "/internal/ownership-challenge",
            json={"challenge": "the-original-real-value"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert set_response.status_code == 200

        denied_attempts = [
            {},
            {"Authorization": f"Bearer {other_org_token}"},
            {"Authorization": f"Bearer {wrong_scope_token}"},
        ]
        for headers in denied_attempts:
            denied_response = await client.put(
                "/internal/ownership-challenge",
                json={"challenge": "attacker-supplied-value"},
                headers=headers,
            )
            assert denied_response.status_code in (401, 403)

            served = await client.get("/.well-known/aimo-brain-challenge")
            assert served.status_code == 200
            assert served.text == "the-original-real-value"


@pytest.mark.asyncio
async def test_accepts_setting_the_challenge_with_the_right_credential() -> None:
    app, settings = await _fresh_app()
    token = issue_service_credential(
        organization_id=BUYER_ORG,
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

        # The GET side is, and must remain, unauthenticated -- registry-api
        # fetches it with no credential at all.
        served = await client.get("/.well-known/aimo-brain-challenge")
        assert served.status_code == 200
        assert served.text == "the-real-challenge-value"
