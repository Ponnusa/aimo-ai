"""Proves the mock adapter satisfies the generic adapter contract (M7).

See ``tests/integration/test_supplier_sandbox_adapter_contract.py`` for
the same suite run against the sandbox ERP adapter over real HTTP.
"""

from __future__ import annotations

import pytest
from business_system_integration.contract_tests import assert_adapter_contract
from food_wholesale.adapters.mock import MockSupplierAdapter


@pytest.mark.asyncio
async def test_mock_adapter_satisfies_the_contract() -> None:
    adapter = MockSupplierAdapter()
    await assert_adapter_contract(
        adapter,
        known_product_ref="sku_fictional_milk_lactose_free",
        known_product_query="lactose-free milk",
        unknown_product_ref="sku_does_not_exist",
        buyer_organization_id="org_fictional_bistro_01",
        order_quantity="10",
    )
