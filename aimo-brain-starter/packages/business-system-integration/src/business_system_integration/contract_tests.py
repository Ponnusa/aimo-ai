"""A reusable behavioral contract any ``SupplierBusinessSystemAdapter``
implementation must satisfy (M7).

Both the mock adapter and the sandbox ERP adapter run this same suite
(see ``industries/food-wholesale/tests/test_adapter_contract.py``) to
prove they are interchangeable from ``apps/supplier-mcp``'s point of
view — the whole reason the generic interface exists. A future Valio
Aimo adapter would run it too, once real sandbox access exists.

This module is test-support code, not production logic, but lives in
the package (not ``tests/``) so any adapter implementation anywhere in
the workspace can import and reuse it without a test-only dependency
edge back into this package.
"""

from __future__ import annotations

import uuid

from business_system_integration.adapter import SupplierBusinessSystemAdapter


async def assert_adapter_contract(
    adapter: SupplierBusinessSystemAdapter,
    *,
    known_product_ref: str,
    known_product_query: str,
    unknown_product_ref: str,
    buyer_organization_id: str,
    order_quantity: str,
) -> None:
    """Run the shared adapter contract against ``adapter``.

    Raises ``AssertionError`` on the first violated expectation.
    """
    assert isinstance(adapter.adapter_name, str) and adapter.adapter_name

    matches = await adapter.search_catalogue(query=known_product_query)
    assert any(m.product_ref == known_product_ref for m in matches), (
        f"search_catalogue({known_product_query!r}) did not return {known_product_ref!r}"
    )
    assert await adapter.search_catalogue(query="no such product anywhere") == []

    details = await adapter.get_product_details(product_ref=known_product_ref)
    assert details is not None and details.product_ref == known_product_ref
    assert await adapter.get_product_details(product_ref=unknown_product_ref) is None

    price = await adapter.get_customer_price(
        product_ref=known_product_ref, buyer_organization_id=buyer_organization_id
    )
    assert price is not None and price.product_ref == known_product_ref
    assert (
        await adapter.get_customer_price(
            product_ref=unknown_product_ref, buyer_organization_id=buyer_organization_id
        )
        is None
    )

    availability = await adapter.check_availability(
        product_ref=known_product_ref, quantity=order_quantity
    )
    assert availability is not None and availability.product_ref == known_product_ref
    assert (
        await adapter.check_availability(product_ref=unknown_product_ref, quantity=order_quantity)
        is None
    )

    options = await adapter.get_delivery_options(
        product_ref=known_product_ref, quantity=order_quantity
    )
    assert isinstance(options, list)

    idempotency_key = f"contract-test-{uuid.uuid4().hex}"
    lines = [
        {
            "product_ref": known_product_ref,
            "quantity": order_quantity,
            "unit": details.unit,
            "unit_price": price.unit_price,
        }
    ]
    first = await adapter.submit_order(
        idempotency_key=idempotency_key,
        buyer_organization_id=buyer_organization_id,
        lines=lines,
    )
    assert first.idempotency_key == idempotency_key
    assert first.status in {"confirmed", "pending"}
    assert first.duplicate is False

    second = await adapter.submit_order(
        idempotency_key=idempotency_key,
        buyer_organization_id=buyer_organization_id,
        lines=lines,
    )
    assert second.idempotency_key == idempotency_key
    assert second.duplicate is True
    assert second.external_order_id == first.external_order_id
    assert second.status == first.status, (
        "a retried submit_order with the same idempotency_key must return the exact same "
        "result, not a fresh status"
    )

    status = await adapter.get_order_status(idempotency_key=idempotency_key)
    assert status.found is True
    assert status.idempotency_key == idempotency_key

    unknown_status = await adapter.get_order_status(idempotency_key="no-such-key-ever-submitted")
    assert unknown_status.found is False
