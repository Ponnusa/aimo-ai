"""Mock supplier catalogue/stock adapter.

All product data below is fictional (per CLAUDE.md: "seed data is
fictional"; README.md: "Start with simulated data"). This module has the
contract the real MCP catalogue/stock tool will expose starting M3 — a
synchronous-shaped, typed search function — so the supplier agent's future
tool wiring can swap this for a real MCP client call without changing
callers. There is no network I/O here and no connection to any Valio Aimo
system.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MockProduct:
    product_ref: str
    name: str
    unit: str
    unit_price: Decimal
    stock_today: Decimal
    stock_tomorrow: Decimal


FICTIONAL_CATALOGUE: tuple[MockProduct, ...] = (
    MockProduct(
        product_ref="sku_fictional_milk_lactose_free",
        name="Fictional lactose-free milk",
        unit="L",
        unit_price=Decimal("1.25"),
        stock_today=Decimal("80"),
        stock_tomorrow=Decimal("200"),
    ),
    MockProduct(
        product_ref="sku_fictional_oat_milk",
        name="Fictional oat milk",
        unit="L",
        unit_price=Decimal("1.60"),
        stock_today=Decimal("50"),
        stock_tomorrow=Decimal("120"),
    ),
    MockProduct(
        product_ref="sku_fictional_butter_unsalted",
        name="Fictional unsalted butter",
        unit="kg",
        unit_price=Decimal("5.40"),
        stock_today=Decimal("30"),
        stock_tomorrow=Decimal("30"),
    ),
)


def search_products(query: str) -> list[MockProduct]:
    """Case-insensitive substring search over the fictional catalogue."""
    needle = query.strip().lower()
    if not needle:
        return []
    return [p for p in FICTIONAL_CATALOGUE if needle in p.name.lower()]
