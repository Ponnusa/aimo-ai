"""Mock supplier-side business tools.

Fictional data only. This module is the contract ``apps/supplier-mcp``
wraps as MCP tools starting M3. ``search_catalogue``/``check_inventory``
read the same fictional catalogue as ``catalogue.py``; pricing and
delivery options are deterministic, fictional lookups with no connection
to any Valio Aimo system.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from food_wholesale.adapters.mock.catalogue import FICTIONAL_CATALOGUE, MockProduct, search_products

#: Customer-specific discount, applied only for this one fictional buyer
#: organization, to make "customer price" meaningfully different from
#: catalogue list price in tests.
_FICTIONAL_CUSTOMER_DISCOUNTS: dict[str, Decimal] = {
    "org_fictional_bistro_01": Decimal("0.05"),
}


@dataclass(frozen=True, slots=True)
class CustomerPrice:
    product_ref: str
    buyer_organization_id: str
    unit_price: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class DeliveryOption:
    label: str
    earliest_date: str
    fee: Decimal
    currency: str


def _find_product(product_ref: str) -> MockProduct | None:
    for product in FICTIONAL_CATALOGUE:
        if product.product_ref == product_ref:
            return product
    return None


def search_catalogue(query: str) -> list[MockProduct]:
    """Case-insensitive substring search over the fictional catalogue."""
    return search_products(query)


def check_inventory(product_ref: str) -> MockProduct | None:
    """Return the fictional stock snapshot for ``product_ref``, or ``None``
    if the product is not in the fictional catalogue."""
    return _find_product(product_ref)


def get_customer_price(*, product_ref: str, buyer_organization_id: str) -> CustomerPrice | None:
    """Return the fictional customer-specific unit price for ``product_ref``,
    or ``None`` if the product is not in the fictional catalogue."""
    product = _find_product(product_ref)
    if product is None:
        return None
    discount = _FICTIONAL_CUSTOMER_DISCOUNTS.get(buyer_organization_id, Decimal("0"))
    unit_price = (product.unit_price * (Decimal("1") - discount)).quantize(Decimal("0.01"))
    return CustomerPrice(
        product_ref=product_ref,
        buyer_organization_id=buyer_organization_id,
        unit_price=unit_price,
        currency="EUR",
    )


def get_delivery_options(*, product_ref: str, quantity: str) -> list[DeliveryOption]:
    """Return fictional delivery options for ``quantity`` units of
    ``product_ref``. Returns an empty list for an unknown product."""
    if _find_product(product_ref) is None:
        return []
    return [
        DeliveryOption(
            label="Standard delivery",
            earliest_date="2026-11-20",
            fee=Decimal("0.00"),
            currency="EUR",
        ),
        DeliveryOption(
            label="Next-day delivery",
            earliest_date="2026-11-19",
            fee=Decimal("15.00"),
            currency="EUR",
        ),
    ]
