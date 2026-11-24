"""Fictional sandbox-ERP seed data (M7).

Mirrors the fictional products in
``food_wholesale.adapters.mock.catalogue`` so the sandbox adapter and the
mock adapter can be pointed at the same ``product_ref``s in tests, but
this data belongs to the sandbox service, not to ``industries/food-wholesale``
— a real ERP would not borrow another layer's catalogue.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from supplier_sandbox_erp.models import SandboxCustomerPriceRecord, SandboxProductRecord

FICTIONAL_PRODUCTS: tuple[dict[str, object], ...] = (
    {
        "product_ref": "sandbox_sku_milk_lactose_free",
        "name": "Fictional lactose-free milk",
        "unit": "L",
        "packaging": "12x1L crate",
        "base_unit_price": "1.25",
        "stock": "80",
        "minimum_order_quantity": "12",
        "delivery_cutoff_passed": False,
        "substitute_product_ref": "sandbox_sku_oat_milk",
    },
    {
        "product_ref": "sandbox_sku_oat_milk",
        "name": "Fictional oat milk",
        "unit": "L",
        "packaging": "12x1L crate",
        "base_unit_price": "1.60",
        "stock": "200",
        "minimum_order_quantity": "12",
        "delivery_cutoff_passed": False,
        "substitute_product_ref": None,
    },
    {
        "product_ref": "sandbox_sku_butter_unsalted",
        "name": "Fictional unsalted butter",
        "unit": "kg",
        "packaging": "10kg box",
        "base_unit_price": "5.40",
        "stock": "10",
        "minimum_order_quantity": "10",
        "delivery_cutoff_passed": True,
        "substitute_product_ref": None,
    },
)

FICTIONAL_CUSTOMER_PRICES: tuple[dict[str, object], ...] = (
    {
        "product_ref": "sandbox_sku_milk_lactose_free",
        "buyer_organization_id": "org_fictional_bistro_01",
        "unit_price": "1.19",
        "currency": "EUR",
    },
)


async def seed_if_empty(session: AsyncSession) -> None:
    from sqlalchemy import select

    existing = (await session.execute(select(SandboxProductRecord))).scalars().first()
    if existing is not None:
        return
    for product in FICTIONAL_PRODUCTS:
        session.add(SandboxProductRecord(**product))
    for price in FICTIONAL_CUSTOMER_PRICES:
        session.add(SandboxCustomerPriceRecord(**price))
    await session.commit()
