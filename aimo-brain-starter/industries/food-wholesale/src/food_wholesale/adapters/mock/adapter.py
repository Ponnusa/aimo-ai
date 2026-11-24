"""A thin ``SupplierBusinessSystemAdapter`` wrapper around the existing
mock catalogue/order functions (M7).

This is the M0–M6 behavior, unchanged, now exposed through the generic
adapter interface so ``apps/supplier-mcp`` can select it from the same
adapter factory as the sandbox/Valio adapters — a pure refactor, no
behavioral difference from calling ``supplier_tools``/``order_api``
directly.
"""

from __future__ import annotations

from business_system_integration import (
    AvailabilityStatus,
    CustomerPrice,
    DeliveryOption,
    OrderStatusResult,
    OrderSubmissionResult,
    ProductDetails,
    ProductSummary,
)

from food_wholesale.adapters.mock import order_api, supplier_tools


class MockSupplierAdapter:
    adapter_name = "mock"

    def __init__(self) -> None:
        self._order_api = order_api.MockOrderApi()
        self._orders_by_key: dict[str, str] = {}

    async def search_catalogue(self, *, query: str) -> list[ProductSummary]:
        return [
            ProductSummary(product_ref=p.product_ref, name=p.name, unit=p.unit)
            for p in supplier_tools.search_catalogue(query)
        ]

    async def get_product_details(self, *, product_ref: str) -> ProductDetails | None:
        product = supplier_tools.check_inventory(product_ref)
        if product is None:
            return None
        return ProductDetails(product_ref=product.product_ref, name=product.name, unit=product.unit)

    async def get_customer_price(
        self, *, product_ref: str, buyer_organization_id: str
    ) -> CustomerPrice | None:
        price = supplier_tools.get_customer_price(
            product_ref=product_ref, buyer_organization_id=buyer_organization_id
        )
        if price is None:
            return None
        return CustomerPrice(
            product_ref=price.product_ref,
            buyer_organization_id=price.buyer_organization_id,
            unit_price=str(price.unit_price),
            currency=price.currency,
        )

    async def check_availability(
        self, *, product_ref: str, quantity: str
    ) -> AvailabilityStatus | None:
        product = supplier_tools.check_inventory(product_ref)
        if product is None:
            return None
        return AvailabilityStatus(
            product_ref=product_ref, quantity_available=str(product.stock_today)
        )

    async def get_delivery_options(
        self, *, product_ref: str, quantity: str, delivery_location_ref: str | None = None
    ) -> list[DeliveryOption]:
        options = supplier_tools.get_delivery_options(product_ref=product_ref, quantity=quantity)
        return [
            DeliveryOption(
                label=o.label, earliest_date=o.earliest_date, fee=str(o.fee), currency=o.currency
            )
            for o in options
        ]

    async def submit_order(
        self,
        *,
        idempotency_key: str,
        buyer_organization_id: str,
        lines: list[dict[str, str]],
    ) -> OrderSubmissionResult:
        duplicate = idempotency_key in self._orders_by_key
        confirmation = self._order_api.submit_order(idempotency_key=idempotency_key)
        self._orders_by_key[idempotency_key] = confirmation.external_order_id
        return OrderSubmissionResult(
            idempotency_key=idempotency_key,
            status=confirmation.status,
            external_order_id=confirmation.external_order_id,
            duplicate=duplicate,
        )

    async def get_order_status(self, *, idempotency_key: str) -> OrderStatusResult:
        external_order_id = self._orders_by_key.get(idempotency_key)
        if external_order_id is None:
            return OrderStatusResult(found=False, idempotency_key=idempotency_key)
        return OrderStatusResult(
            found=True,
            idempotency_key=idempotency_key,
            status="confirmed",
            external_order_id=external_order_id,
        )
