"""The generic supplier business-system adapter interface (M7).

A real ERP/API integration (``industries/food-wholesale/adapters/sandbox``,
and eventually a Valio Aimo adapter) implements this ``Protocol``.
``apps/supplier-mcp`` depends only on this interface, never on a concrete
implementation — swapping the configured adapter never requires touching
the MCP tool surface, schemas or database. This mirrors
``agent_runtime.llm.LLMAdapter``'s "Protocol + swappable implementation"
pattern.

Nothing in this module is food-wholesale-, Valio Aimo- or any other
business-specific shape. Per ``CLAUDE.md``: keep domain-specific schemas
under ``industries/<domain>``; no SKU assumptions in core.
"""

from __future__ import annotations

from typing import Protocol

from business_system_integration.types import (
    AvailabilityStatus,
    CustomerPrice,
    DeliveryOption,
    OrderStatusResult,
    OrderSubmissionResult,
    ProductDetails,
    ProductSummary,
)


class SupplierBusinessSystemAdapter(Protocol):
    """Everything ``apps/supplier-mcp`` needs from a supplier's own
    business system, independent of what that system actually is."""

    #: Human/machine-readable identifier reported to the management
    #: portal's integration-health view (e.g. ``"mock"``, ``"sandbox"``,
    #: ``"valio_aimo"``). Never a secret, never a connection string.
    adapter_name: str

    async def search_catalogue(self, *, query: str) -> list[ProductSummary]:
        """Free-text catalogue search."""
        ...

    async def get_product_details(self, *, product_ref: str) -> ProductDetails | None:
        """Full product/packaging detail, or ``None`` if unknown."""
        ...

    async def get_customer_price(
        self, *, product_ref: str, buyer_organization_id: str
    ) -> CustomerPrice | None:
        """Customer-specific price, or ``None`` if the product is unknown
        to this adapter for this buyer."""
        ...

    async def check_availability(
        self, *, product_ref: str, quantity: str
    ) -> AvailabilityStatus | None:
        """Inventory/availability for ``quantity`` units, or ``None`` if
        the product is unknown."""
        ...

    async def get_delivery_options(
        self, *, product_ref: str, quantity: str, delivery_location_ref: str | None = None
    ) -> list[DeliveryOption]:
        """Delivery options and restrictions for this product/quantity."""
        ...

    async def submit_order(
        self,
        *,
        idempotency_key: str,
        buyer_organization_id: str,
        lines: list[dict[str, str]],
    ) -> OrderSubmissionResult:
        """Submit an order. Must be idempotent on ``idempotency_key``: a
        retried call with the same key returns the original result
        (``duplicate=True``), never a second order. Raises
        ``AdapterUnavailableError`` on a transport-level fault (never
        represented as a ``status``), never silently swallowed."""
        ...

    async def get_order_status(self, *, idempotency_key: str) -> OrderStatusResult:
        """Look up a previously submitted order by its idempotency key,
        for reconciliation of a ``"pending"`` or otherwise unresolved
        submission."""
        ...
