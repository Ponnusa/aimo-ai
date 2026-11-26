"""M8 slice 5 implementation step 3 (ADR-020): the TypeScript reference
buyer agent's full happy path, against the real, unmodified
network-api, registry-api, supplier-mcp and supplier-agent -- all real
HTTP servers, never ASGITransport. The TypeScript processes (provision.ts
then buyerAgent.ts) use ONLY the published external contract and their
own generated keys/credentials; this harness is the only thing that
ever touches a database directly (the order-persistence assertion),
exactly as ADR-020 section 8 requires.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import httpx
import pytest
from database import Base, new_engine
from identity import issue_service_credential
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .a2a_server_harness import find_free_port, generate_and_register_signing_key, running_server

OPERATOR_KEY = "slice5-operator-key"
ADMIN_KEY = "slice5-admin-key-never-used-by-ts-buyer"
INTERNAL_KEY = "slice5-internal-key"
SIGNING_SECRET = "slice5-signing-secret"
SUPPLIER_ORG = "org_fictional_wholesale_01"
SUPPLIER_AGENT_ID = "agent_fictional_supplier_01"
NETWORK_AUDIENCE = "aimo-network:slice5-test"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TS_DIR = _REPO_ROOT / "apps" / "buyer-agent-ts"


def _fresh_sqlite_url(label: str) -> str:
    path = os.path.join(tempfile.gettempdir(), f"aimo_slice5_{label}_{uuid.uuid4().hex}.sqlite3")
    return f"sqlite+aiosqlite:///{path}"


def _build_network_app(*, database_url: str, monkeypatch):
    import network_api.app as network_app_module
    import network_api.settings as network_settings_module

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("NETWORK_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("NETWORK_OPERATOR_KEY", OPERATOR_KEY)
    monkeypatch.setenv("NETWORK_INTERNAL_SERVICE_KEY", INTERNAL_KEY)
    monkeypatch.setenv("SERVICE_CREDENTIAL_SIGNING_SECRET", SIGNING_SECRET)
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    network_settings_module.get_settings.cache_clear()
    return network_app_module.create_app()


def _build_registry_app(*, database_url: str, network_api_url: str, monkeypatch):
    import registry_api.app as registry_app_module
    import registry_api.settings as registry_settings_module

    monkeypatch.setenv("REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("SERVICE_CREDENTIAL_SIGNING_SECRET", SIGNING_SECRET)
    monkeypatch.setenv("NETWORK_API_URL", network_api_url)
    monkeypatch.setenv("NETWORK_INTERNAL_SERVICE_KEY", INTERNAL_KEY)
    monkeypatch.setenv("REGISTRY_ALLOW_PRIVATE_NETWORK_CARD_TARGETS", "true")
    registry_settings_module.get_settings.cache_clear()
    return registry_app_module.create_app()


def _build_supplier_mcp_app(*, database_url: str, monkeypatch):
    import supplier_mcp.app as supplier_mcp_app_module
    import supplier_mcp.settings as supplier_mcp_settings_module

    monkeypatch.setenv("SUPPLIER_MCP_DATABASE_URL", database_url)
    monkeypatch.setenv("SUPPLIER_MCP_ORGANIZATION_ID", SUPPLIER_ORG)
    monkeypatch.setenv("SERVICE_CREDENTIAL_SIGNING_SECRET", SIGNING_SECRET)
    supplier_mcp_settings_module.get_settings.cache_clear()
    return supplier_mcp_app_module.create_app()


def _build_supplier_app(
    *,
    a2a_url: str,
    database_url: str,
    supplier_mcp_url: str,
    network_api_url: str,
    registry_api_url: str,
    signing_private_key_pem: str,
    signing_kid: str,
    monkeypatch,
):
    import supplier_agent.app as supplier_app_module
    import supplier_agent.settings as supplier_settings_module
    from supplier_agent.models import SupplierTaskRecord

    monkeypatch.setenv("SUPPLIER_A2A_URL", a2a_url)
    monkeypatch.setenv("SUPPLIER_SIGNING_PRIVATE_KEY_PEM", signing_private_key_pem)
    monkeypatch.setenv("SUPPLIER_SIGNING_KID", signing_kid)
    monkeypatch.setenv("SUPPLIER_DATABASE_URL", database_url)
    monkeypatch.setenv("SUPPLIER_MCP_URL", supplier_mcp_url)
    monkeypatch.setenv(
        "SUPPLIER_MCP_SERVICE_CREDENTIAL",
        issue_service_credential(
            organization_id=SUPPLIER_ORG, scope="use_supplier_tools", signing_secret=SIGNING_SECRET
        ),
    )
    monkeypatch.setenv(
        "SUPPLIER_MCP_EXECUTION_CREDENTIAL",
        issue_service_credential(
            organization_id=SUPPLIER_ORG, scope="execute_order", signing_secret=SIGNING_SECRET
        ),
    )
    monkeypatch.setenv("NETWORK_API_URL", network_api_url)
    monkeypatch.setenv("REGISTRY_API_URL", registry_api_url)
    monkeypatch.setenv("NETWORK_AUDIENCE", NETWORK_AUDIENCE)
    monkeypatch.setenv("NETWORK_INTERNAL_SERVICE_KEY", INTERNAL_KEY)
    monkeypatch.setenv("SERVICE_CREDENTIAL_SIGNING_SECRET", SIGNING_SECRET)
    supplier_settings_module.get_settings.cache_clear()
    return supplier_app_module.create_app(), SupplierTaskRecord


async def _create_full_schema(database_url: str) -> None:
    engine = new_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _create_tables(database_url: str, *models) -> None:
    engine = new_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[m.__table__ for m in models])
    await engine.dispose()


def _npm_and_npx():
    npm = shutil.which("npm")
    npx = shutil.which("npx")
    if not npm or not npx:
        pytest.skip("npm/npx are not available in this environment.")
    return npm, npx


@pytest.fixture(scope="module")
def ts_installed():
    npm, _ = _npm_and_npx()
    subprocess.run([npm, "install"], cwd=_TS_DIR, check=True, capture_output=True, text=True)


def _run_node(npx: str, script: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [npx, "tsx", script],
        cwd=_TS_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
async def full_stack(monkeypatch):
    network_db = _fresh_sqlite_url("network")
    network_app = _build_network_app(database_url=network_db, monkeypatch=monkeypatch)
    await _create_full_schema(network_db)

    network_port = find_free_port()
    with running_server(network_app, port=network_port) as network_server:
        registry_db = _fresh_sqlite_url("registry")
        registry_app = _build_registry_app(
            database_url=registry_db, network_api_url=network_server.base_url, monkeypatch=monkeypatch
        )
        await _create_full_schema(registry_db)

        registry_port = find_free_port()
        with running_server(registry_app, port=registry_port) as registry_server:
            await _create_legacy_org(network_server.base_url, SUPPLIER_ORG)
            supplier_signing_kid = f"{SUPPLIER_ORG}-root-1"
            supplier_signing_pem = await generate_and_register_signing_key(
                network_api_base_url=network_server.base_url,
                organization_id=SUPPLIER_ORG,
                admin_key=ADMIN_KEY,
                kid=supplier_signing_kid,
            )

            mcp_db = _fresh_sqlite_url("mcp")
            mcp_app = _build_supplier_mcp_app(database_url=mcp_db, monkeypatch=monkeypatch)
            await _create_full_schema(mcp_db)
            mcp_port = find_free_port()
            with running_server(mcp_app, port=mcp_port):
                supplier_db = _fresh_sqlite_url("supplier")
                supplier_a2a_port = find_free_port()
                supplier_a2a_url = f"http://127.0.0.1:{supplier_a2a_port}"
                supplier_app, SupplierTaskRecord = _build_supplier_app(
                    a2a_url=supplier_a2a_url,
                    database_url=supplier_db,
                    supplier_mcp_url=f"http://127.0.0.1:{mcp_port}/mcp",
                    network_api_url=network_server.base_url,
                    registry_api_url=registry_server.base_url,
                    signing_private_key_pem=supplier_signing_pem,
                    signing_kid=supplier_signing_kid,
                    monkeypatch=monkeypatch,
                )
                await _create_full_schema(supplier_db)
                with running_server(supplier_app, port=supplier_a2a_port) as supplier_server:
                    yield {
                        "network": network_server,
                        "registry": registry_server,
                        "supplier": supplier_server,
                        "supplier_db": supplier_db,
                    }


async def _bootstrap_external_org(network_base_url: str, organization_id: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        create = await client.post(
            f"{network_base_url}/organizations",
            json={"organization_id": organization_id, "display_name": "Slice 5 External Buyer"},
            headers={"X-Operator-Key": OPERATOR_KEY},
        )
        assert create.status_code in (200, 201), create.text

        bootstrap = await client.post(
            f"{network_base_url}/organizations/{organization_id}/bootstrap-external",
            headers={"X-Operator-Key": OPERATOR_KEY},
        )
        assert bootstrap.status_code == 200, bootstrap.text
        return bootstrap.json()["admin_credential"]


async def _create_legacy_org(network_base_url: str, organization_id: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{network_base_url}/organizations",
            json={"organization_id": organization_id, "display_name": "Fictional Supplier Org"},
            headers={"X-Operator-Key": OPERATOR_KEY},
        )
        assert resp.status_code in (200, 201), resp.text


async def _create_relationship(network_base_url: str, buyer_org: str, supplier_org: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{network_base_url}/relationships",
            json={"buyer_organization_id": buyer_org, "supplier_organization_id": supplier_org},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert resp.status_code in (200, 201), resp.text


async def _order_rows(database_url: str):
    from supplier_agent.models import SupplierOrderRecord

    engine = new_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(select(SupplierOrderRecord))
        rows = result.scalars().all()
    await engine.dispose()
    return rows


@pytest.mark.integration
@pytest.mark.asyncio
async def test_typescript_buyer_completes_full_commit_against_real_supplier(full_stack, ts_installed):
    _, npx = _npm_and_npx()
    stack = full_stack
    # The supplier's real OPA policy (industries/food-wholesale/policies/
    # supplier/v1/data.json) only approves a fixed set of customer
    # organization ids -- using the same fictional approved customer id
    # the rest of this codebase's own fixtures use, rather than a random
    # one, so this test exercises the REAL policy decision rather than
    # bypassing it.
    buyer_org = "org_fictional_bistro_01"
    buyer_agent_id = f"agent_external_buyer_{uuid.uuid4().hex[:8]}"

    org_credential = await _bootstrap_external_org(stack["network"].base_url, buyer_org)
    await _create_relationship(stack["network"].base_url, buyer_org, SUPPLIER_ORG)

    provision_env = dict(os.environ)
    provision_env.update(
        {
            "NETWORK_API_URL": stack["network"].base_url,
            "BUYER_ORGANIZATION_ID": buyer_org,
            "ORG_SCOPED_CREDENTIAL": org_credential,
            "BUYER_AGENT_ID": buyer_agent_id,
            "DELEGATION_AUDIENCE": NETWORK_AUDIENCE,
        }
    )
    provisioned = _run_node(npx, "src/provision.ts", provision_env)
    assert provisioned.returncode == 0, f"provisioning failed:\n{provisioned.stdout}\n{provisioned.stderr}"
    provisioning_output = json.loads(provisioned.stdout)

    buyer_port = find_free_port()
    buyer_env = dict(os.environ)
    buyer_env.update(
        {
            "REGISTRY_API_URL": stack["registry"].base_url,
            "NETWORK_API_URL": stack["network"].base_url,
            "BUYER_ORGANIZATION_ID": buyer_org,
            "SUPPLIER_ORGANIZATION_ID": SUPPLIER_ORG,
            "ORG_SCOPED_CREDENTIAL": org_credential,
            "BUYER_AGENT_ID": buyer_agent_id,
            "SUPPLIER_A2A_URL": stack["supplier"].base_url,
            "SUPPLIER_AGENT_ID": SUPPLIER_AGENT_ID,
            "BUYER_SELF_PORT": str(buyer_port),
            "AGENT_PRIVATE_KEY_JWK": json.dumps(provisioning_output["agentPrivateJwk"]),
            "AGENT_PUBLIC_KEY_JWK": json.dumps(provisioning_output["agentPublicJwk"]),
            "DELEGATED_KID": provisioning_output["delegatedKid"],
        }
    )
    result = _run_node(npx, "src/buyerAgent.ts", buyer_env)
    assert result.returncode == 0, f"buyer agent failed:\n{result.stdout}\n{result.stderr}"

    output = json.loads(result.stdout.strip().splitlines()[-1])
    assert output["outcome"] == "committed", output
    assert output["confirmation"]["status"] == "CONFIRMED", output

    # --- Harness-only order-persistence assertion (ADR-020 section 8) --
    # The TypeScript buyer has no database access at all; only this
    # Python harness queries the supplier's own durable state directly.
    rows = await _order_rows(stack["supplier_db"])
    matching = [r for r in rows if r.idempotency_key == output["idempotencyKey"]]
    assert len(matching) == 1, f"expected exactly one order row, found {len(matching)}"
    assert matching[0].state == "CONFIRMED"
