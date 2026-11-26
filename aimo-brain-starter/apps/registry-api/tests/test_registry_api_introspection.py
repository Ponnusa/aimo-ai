"""ADR-019 acceptance test 10 (timeout/unreachable half) — registry-api's
own call site for opaque ``svc_``-prefixed ``manage_agents`` credential
introspection must fail closed (503), never a best-effort accept, when
network-api cannot be reached, returns a non-200 status, or returns a
malformed body. `test_external_onboarding.py::test_introspection_fails_closed_on_malformed_token`
(network-api) exercises the malformed-*response-body* case from
network-api's own side; this file exercises the call site this failure
mode actually protects — registry-api's `require_org_bearer` — which was
referenced by a docstring comment in that other file but never written
until this review.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from registry_api.app import create_app
from registry_api.network_client import IntrospectionError
from registry_api.settings import get_settings

from database import Base, new_engine

SUPPLIER_ORG = "org_fictional_wholesale_01"


def _fake_card(
    name: str = "Fictional Supplier Agent",
    skill_ids: tuple[str, ...] = ("food.check_product_availability",),
):
    return SimpleNamespace(
        name=name,
        skills=[SimpleNamespace(id=skill_id, name=skill_id) for skill_id in skill_ids],
    )


async def _fresh_app(monkeypatch, *, fake_card=None):
    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.registry_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    if fake_card is not None:
        import registry_api.app as app_module

        async def _fake_fetch(url, **kwargs):
            return fake_card

        monkeypatch.setattr(app_module, "fetch_and_validate_agent_card", _fake_fetch)

    app = create_app()
    return app, engine, settings


async def _register_with_opaque_token(client: AsyncClient) -> httpx.Response:
    return await client.post(
        "/agents",
        json={
            "agent_id": "agent_fictional_supplier_01",
            "organization_id": SUPPLIER_ORG,
            "a2a_card_url": "http://127.0.0.1:9999",
        },
        headers={"Authorization": "Bearer svc_some-reference-id.some-secret"},
    )


@pytest.mark.asyncio
async def test_register_agent_fails_closed_when_network_api_unreachable(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    import registry_api.app as app_module

    async def _raise_unreachable(**kwargs):
        raise IntrospectionError(
            "Could not reach network-api for introspection: connection refused"
        )

    monkeypatch.setattr(app_module, "introspect_credential", _raise_unreachable)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await _register_with_opaque_token(client)

    assert response.status_code == 503
    assert "Could not confirm credential" in response.json()["detail"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_register_agent_fails_closed_on_introspection_timeout(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    import registry_api.app as app_module

    async def _raise_timeout(**kwargs):
        raise IntrospectionError("Could not reach network-api for introspection: timed out")

    monkeypatch.setattr(app_module, "introspect_credential", _raise_timeout)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await _register_with_opaque_token(client)

    assert response.status_code == 503
    await engine.dispose()


@pytest.mark.asyncio
async def test_register_agent_fails_closed_on_malformed_introspection_response(monkeypatch) -> None:
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    import registry_api.app as app_module

    async def _raise_malformed(**kwargs):
        raise IntrospectionError("network-api returned a malformed introspection body: ...")

    monkeypatch.setattr(app_module, "introspect_credential", _raise_malformed)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await _register_with_opaque_token(client)

    assert response.status_code == 503
    await engine.dispose()


@pytest.mark.asyncio
async def test_register_agent_rejects_inactive_introspection_result(monkeypatch) -> None:
    """network-api reachable, responds successfully, but reports the
    credential is not active (revoked/expired/never issued) — must be a
    plain 401, never conflated with the 503 fail-closed-on-ambiguity path
    above: this is an unambiguous, authoritative "no", not uncertainty."""
    app, engine, settings = await _fresh_app(monkeypatch, fake_card=_fake_card())
    import registry_api.app as app_module

    async def _inactive(**kwargs):
        return {"active": False}

    monkeypatch.setattr(app_module, "introspect_credential", _inactive)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await _register_with_opaque_token(client)

    assert response.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_introspect_credential_raises_on_real_httpx_timeout(monkeypatch) -> None:
    """Unit-level proof that the HTTP client function itself, not just
    the mocked call site above, converts a real httpx timeout into
    IntrospectionError rather than letting it propagate as a bare
    httpx exception a caller might mishandle."""
    from registry_api.network_client import introspect_credential

    class _RaisingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("simulated connection timeout")

    import registry_api.network_client as network_client_module

    original_async_client = httpx.AsyncClient

    def _patched_client(*args, **kwargs):
        kwargs["transport"] = _RaisingTransport()
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(network_client_module.httpx, "AsyncClient", _patched_client)

    with pytest.raises(IntrospectionError):
        await introspect_credential(
            network_api_url="http://network-api.invalid",
            internal_service_key="irrelevant",
            token="svc_x.y",
            expected_organization_id=SUPPLIER_ORG,
            expected_scope="manage_agents",
        )
