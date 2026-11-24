"""M7 adversarial/security tests for the supplier business-system
adapter boundary.

Covers: an adapter authentication failure fails closed (never retried,
never silently treated as "unknown"); a malformed business-system
response is rejected rather than partially trusted; and scope
separation holds at the sandbox ERP boundary exactly like every other
AIMO-internal service credential (a read-only credential cannot reach
the execution-shaped surface, and vice versa is enforced by the
narrower ``use_sandbox_erp`` scope itself not existing on any other
service).
"""

from __future__ import annotations

import contextlib
import os
import tempfile

import pytest
from business_system_integration import (
    AdapterAuthenticationError,
    AdapterMalformedResponseError,
)
from food_wholesale.adapters.sandbox import SandboxErpAdapter
from tests.integration.a2a_server_harness import find_free_port, running_server

from database import Base, new_engine
from identity import issue_service_credential

ORG = "org_fictional_wholesale_01"
OTHER_ORG = "org_fictional_other_01"

_db_file = os.path.join(tempfile.gettempdir(), "aimo_adapter_security_test.sqlite3")
with contextlib.suppress(FileNotFoundError):
    os.remove(_db_file)
os.environ["SUPPLIER_SANDBOX_ERP_DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_file}"
os.environ.setdefault("SERVICE_CREDENTIAL_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("SUPPLIER_SANDBOX_ERP_ORGANIZATION_ID", ORG)
os.environ.setdefault("ENVIRONMENT", "test")


@pytest.mark.security
@pytest.mark.asyncio
async def test_adapter_fails_closed_on_wrong_organization_credential() -> None:
    """A credential valid for a different organization must be
    rejected as an authentication failure, never silently treated as
    an "unavailable" (retryable) condition."""
    from supplier_sandbox_erp.app import create_app
    from supplier_sandbox_erp.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.supplier_sandbox_erp_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    app = create_app()
    port = find_free_port()
    wrong_org_token = issue_service_credential(
        organization_id=OTHER_ORG,
        scope="use_sandbox_erp",
        signing_secret=settings.service_credential_signing_secret,
    )
    with running_server(app, port=port):
        adapter = SandboxErpAdapter(base_url=f"http://127.0.0.1:{port}", token=wrong_org_token)
        with pytest.raises(AdapterAuthenticationError):
            await adapter.search_catalogue(query="milk")


@pytest.mark.security
@pytest.mark.asyncio
async def test_adapter_fails_closed_on_wrong_scope_credential() -> None:
    from supplier_sandbox_erp.app import create_app
    from supplier_sandbox_erp.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.supplier_sandbox_erp_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    app = create_app()
    port = find_free_port()
    # Valid for this organization, but scoped for a different internal
    # purpose entirely -- a scope that genuinely exists elsewhere in
    # this system (supplier-mcp's own read-only tools), never a scope
    # the sandbox grants.
    wrong_scope_token = issue_service_credential(
        organization_id=ORG,
        scope="use_supplier_tools",
        signing_secret=settings.service_credential_signing_secret,
    )
    with running_server(app, port=port):
        adapter = SandboxErpAdapter(base_url=f"http://127.0.0.1:{port}", token=wrong_scope_token)
        with pytest.raises(AdapterAuthenticationError):
            await adapter.search_catalogue(query="milk")


@pytest.mark.security
@pytest.mark.asyncio
async def test_adapter_rejects_malformed_response_rather_than_guessing() -> None:
    """A response that doesn't fit the adapter's expected shape (e.g. a
    compromised/buggy business system) must be rejected explicitly,
    never partially trusted into a half-populated result."""
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    rogue_app = FastAPI()

    @rogue_app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @rogue_app.get("/catalogue/search")
    async def search() -> PlainTextResponse:
        return PlainTextResponse("not json at all", status_code=200)

    port = find_free_port()
    with running_server(rogue_app, port=port):
        adapter = SandboxErpAdapter(base_url=f"http://127.0.0.1:{port}", token="irrelevant-token")
        with pytest.raises(AdapterMalformedResponseError):
            await adapter.search_catalogue(query="milk")


@pytest.mark.security
@pytest.mark.asyncio
async def test_sandbox_erp_scope_is_distinct_from_every_other_internal_scope() -> None:
    """The ``use_sandbox_erp`` scope is its own narrow grant -- a
    credential minted for any other AIMO-internal purpose
    (``use_supplier_tools``, ``execute_order``, ``manage_agents``, ...)
    must never work here, matching the narrow-scope pattern already
    enforced for supplier-mcp's own execute_order tool (M5)."""
    from supplier_sandbox_erp.app import create_app
    from supplier_sandbox_erp.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    engine = new_engine(settings.supplier_sandbox_erp_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    app = create_app()
    port = find_free_port()
    with running_server(app, port=port):
        for other_scope in ("use_supplier_tools", "execute_order", "manage_agents"):
            token = issue_service_credential(
                organization_id=ORG,
                scope=other_scope,
                signing_secret=settings.service_credential_signing_secret,
            )
            adapter = SandboxErpAdapter(base_url=f"http://127.0.0.1:{port}", token=token)
            with pytest.raises(AdapterAuthenticationError):
                await adapter.search_catalogue(query="milk")
