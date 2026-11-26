"""M8 slice 5 implementation step 2 (ADR-020): the required gate before
any negotiation logic is written. Starts the real, unmodified
supplier-mcp + supplier-agent stack over genuine HTTP (same harness
pattern as ``test_a2a_exchange.py``), then runs the TypeScript reference
agent's minimal probe (``apps/buyer-agent-ts/src/probe.ts``) as a real
subprocess against it, using the official ``@a2a-js/sdk`` client --
never a Python-side simulation of what the TS client would do. A
failure here is a real wire-level incompatibility, not a test artifact.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from database import new_engine
from identity import issue_service_credential

from .a2a_server_harness import find_free_port, running_server

_SIGNING_SECRET = "test-signing-secret"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TS_PACKAGE_DIR = _REPO_ROOT / "apps" / "buyer-agent-ts"


def _fresh_sqlite_url(label: str) -> str:
    path = os.path.join(tempfile.gettempdir(), f"aimo_slice5_probe_{label}_{uuid.uuid4().hex}.sqlite3")
    return f"sqlite+aiosqlite:///{path}"


async def _create_tables(engine, model) -> None:
    from database import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[model.__table__])


def _build_supplier_mcp_app(*, organization_id: str, database_url: str):
    import supplier_mcp.app as supplier_mcp_app_module
    import supplier_mcp.settings as supplier_mcp_settings_module

    os.environ["SUPPLIER_MCP_DATABASE_URL"] = database_url
    os.environ["SUPPLIER_MCP_ORGANIZATION_ID"] = organization_id
    os.environ["SERVICE_CREDENTIAL_SIGNING_SECRET"] = _SIGNING_SECRET
    supplier_mcp_settings_module.get_settings.cache_clear()
    return supplier_mcp_app_module.create_app()


def _build_supplier_app(*, a2a_url: str, database_url: str, supplier_mcp_url: str):
    import supplier_agent.app as supplier_app_module
    import supplier_agent.settings as supplier_settings_module
    from supplier_agent.models import SupplierTaskRecord

    os.environ["SUPPLIER_A2A_URL"] = a2a_url
    os.environ["SUPPLIER_DATABASE_URL"] = database_url
    os.environ["SUPPLIER_MCP_URL"] = supplier_mcp_url
    os.environ["SUPPLIER_MCP_SERVICE_CREDENTIAL"] = issue_service_credential(
        organization_id="org_fictional_wholesale_01",
        scope="use_supplier_tools",
        signing_secret=_SIGNING_SECRET,
    )
    supplier_settings_module.get_settings.cache_clear()
    return supplier_app_module.create_app(), SupplierTaskRecord


@pytest.fixture
async def supplier_server():
    mcp_port = find_free_port()
    mcp_url = f"http://127.0.0.1:{mcp_port}/mcp"
    mcp_database_url = _fresh_sqlite_url("mcp")
    mcp_app = _build_supplier_mcp_app(
        organization_id="org_fictional_wholesale_01", database_url=mcp_database_url
    )

    from supplier_mcp.models import SupplierMcpToolInvocationRecord

    mcp_engine = new_engine(mcp_database_url)
    await _create_tables(mcp_engine, SupplierMcpToolInvocationRecord)
    await mcp_engine.dispose()

    with running_server(mcp_app, port=mcp_port):
        port = find_free_port()
        a2a_url = f"http://127.0.0.1:{port}"
        database_url = _fresh_sqlite_url("agent")
        app, model = _build_supplier_app(
            a2a_url=a2a_url, database_url=database_url, supplier_mcp_url=mcp_url
        )

        engine = new_engine(database_url)
        await _create_tables(engine, model)
        await engine.dispose()

        with running_server(app, port=port) as server:
            yield server


def _npm_executable() -> str:
    found = shutil.which("npm")
    if not found:
        pytest.skip("npm is not available in this environment.")
    return found


@pytest.fixture(scope="module")
def ts_package_installed() -> None:
    """Install the TS package's dependencies once per test module run --
    never committed node_modules, always a real `npm install` against
    the committed package.json/lockfile."""
    npm = _npm_executable()
    subprocess.run([npm, "install"], cwd=_TS_PACKAGE_DIR, check=True, capture_output=True, text=True)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_typescript_a2a_probe_against_real_supplier_agent(
    supplier_server, ts_package_installed
) -> None:
    """The actual gate: run the TypeScript probe as a subprocess, using
    the real @a2a-js/sdk client, against the real, unmodified
    supplier-agent. Asserts a real protocol version was negotiated and
    a real, schema-valid response was received -- not merely that the
    subprocess exited zero."""
    npx = shutil.which("npx")
    assert npx is not None, "npx must be available alongside npm"

    env = dict(os.environ)
    env["SUPPLIER_A2A_URL"] = supplier_server.base_url
    env["PROBE_BUYER_ORGANIZATION_ID"] = "org-probe-external"

    result = subprocess.run(
        [npx, "tsx", "src/probe.ts"],
        cwd=_TS_PACKAGE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"TypeScript A2A probe failed (wire-level incompatibility, not a test artifact).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "probe: PASS" in result.stdout
    assert "negotiated_protocol_version=" in result.stdout
    assert "product_availability_response" in result.stdout
