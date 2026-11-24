"""Proves the sandbox ERP adapter satisfies the same generic adapter
contract as the mock adapter (M7) — the whole reason the generic
``SupplierBusinessSystemAdapter`` interface exists: ``apps/supplier-mcp``
can swap one for the other with no caller changes.

Boots a real ``supplier-sandbox-erp`` server (real uvicorn, real HTTP,
own SQLite file) and drives ``SandboxErpAdapter`` against it — never
``ASGITransport``, per this repo's standing test convention.
"""

from __future__ import annotations

import contextlib
import os
import tempfile

import pytest
from business_system_integration.contract_tests import assert_adapter_contract
from food_wholesale.adapters.sandbox import SandboxErpAdapter
from tests.integration.a2a_server_harness import find_free_port, running_server

from database import Base, new_engine
from identity import issue_service_credential

_db_file = os.path.join(tempfile.gettempdir(), "aimo_sandbox_erp_contract_test.sqlite3")
with contextlib.suppress(FileNotFoundError):
    os.remove(_db_file)
os.environ.setdefault("SUPPLIER_SANDBOX_ERP_DATABASE_URL", f"sqlite+aiosqlite:///{_db_file}")
os.environ.setdefault("SERVICE_CREDENTIAL_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("SUPPLIER_SANDBOX_ERP_ORGANIZATION_ID", "org_fictional_wholesale_01")
os.environ.setdefault("ENVIRONMENT", "test")


@pytest.mark.asyncio
async def test_sandbox_adapter_satisfies_the_contract() -> None:
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
    token = issue_service_credential(
        organization_id="org_fictional_wholesale_01",
        scope="use_sandbox_erp",
        signing_secret=settings.service_credential_signing_secret,
    )
    with running_server(app, port=port):
        adapter = SandboxErpAdapter(base_url=f"http://127.0.0.1:{port}", token=token)
        await assert_adapter_contract(
            adapter,
            known_product_ref="sandbox_sku_oat_milk",
            known_product_query="oat milk",
            unknown_product_ref="sandbox_sku_does_not_exist",
            buyer_organization_id="org_fictional_bistro_01",
            order_quantity="12",
        )
