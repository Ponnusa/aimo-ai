"""The Valio Aimo adapter *boundary* (M7) — not an implementation.

Per ``CLAUDE.md``: "Never access Valio Aimo production systems, scrape
protected data, or submit live orders without explicit authorization and
credentials." No such authorization or API access exists at the time
this module was written, so every method below raises
``NotImplementedError`` naming the exact missing decision/access —
nothing here guesses at an endpoint shape, an identifier format or an
authentication scheme Valio has not documented or granted.

This class exists only to prove the adapter boundary is real: once
Valio Aimo grants sandbox API access, each method's body is replaced
with a real HTTP call (following the same pattern as
``food_wholesale.adapters.sandbox.SandboxErpAdapter``) — nothing in
``apps/supplier-mcp`` or any other caller needs to change, because both
adapters satisfy the same
``business_system_integration.SupplierBusinessSystemAdapter`` protocol.

See ``docs/integrations/valio-aimo-adapter-contract.md`` for the exact
access, identifiers, mappings and semantics each method below would
need, and ``docs/integrations/valio-aimo-pilot-readiness.md`` for the
partner-facing pilot-readiness package.
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

_NOT_AUTHORIZED = (
    "Valio Aimo API access is not yet authorized or documented for AIMO BRAIN. "
    "See docs/integrations/valio-aimo-adapter-contract.md for exactly what is "
    "required before this method can be implemented: {detail}"
)


class ValioAimoAdapter:
    """Structurally real, functionally unimplemented until Valio Aimo
    grants sandbox API access. Every method names the exact missing
    decision in ``docs/integrations/valio-aimo-adapter-contract.md``."""

    adapter_name = "valio_aimo"

    def __init__(self, *, base_url: str | None = None, token: str | None = None) -> None:
        # Deliberately accepts the same shape as SandboxErpAdapter's
        # constructor so a future real implementation is a drop-in
        # replacement with no caller changes — see the module docstring.
        self._base_url = base_url
        self._token = token

    async def search_catalogue(self, *, query: str) -> list[ProductSummary]:
        """Would require: Valio Aimo's catalogue/product-search endpoint,
        its product identifier format, and confirmation of which fields
        map to ``product_ref``/``name``/``unit`` — see "Catalogue
        mapping" in the adapter contract doc."""
        raise NotImplementedError(_NOT_AUTHORIZED.format(detail="catalogue search endpoint"))

    async def get_product_details(self, *, product_ref: str) -> ProductDetails | None:
        """Would require: Valio Aimo's product-detail endpoint and its
        packaging/unit-of-measure representation — see "Product
        identifiers" and "Catalogue mapping" in the adapter contract
        doc."""
        raise NotImplementedError(_NOT_AUTHORIZED.format(detail="product detail endpoint"))

    async def get_customer_price(
        self, *, product_ref: str, buyer_organization_id: str
    ) -> CustomerPrice | None:
        """Would require: Valio Aimo's customer-pricing endpoint, the
        customer identifier it expects (likely Valio's own customer
        number, not AIMO's ``buyer_organization_id`` directly — a
        mapping table would be needed), and confirmation of currency and
        rounding behavior — see "Customer identifiers" and "Pricing
        mapping" in the adapter contract doc."""
        raise NotImplementedError(_NOT_AUTHORIZED.format(detail="customer pricing endpoint"))

    async def check_availability(
        self, *, product_ref: str, quantity: str
    ) -> AvailabilityStatus | None:
        """Would require: Valio Aimo's stock/availability endpoint,
        confirmation of whether it reflects real-time or batch-updated
        stock, and its own MOQ/substitution semantics — see "Stock
        mapping" in the adapter contract doc."""
        raise NotImplementedError(_NOT_AUTHORIZED.format(detail="availability endpoint"))

    async def get_delivery_options(
        self, *, product_ref: str, quantity: str, delivery_location_ref: str | None = None
    ) -> list[DeliveryOption]:
        """Would require: Valio Aimo's delivery-options/route-planning
        endpoint and its delivery-location identifier format — see
        "Delivery and substitution constraints" in the adapter contract
        doc."""
        raise NotImplementedError(_NOT_AUTHORIZED.format(detail="delivery options endpoint"))

    async def submit_order(
        self,
        *,
        idempotency_key: str,
        buyer_organization_id: str,
        lines: list[dict[str, str]],
    ) -> OrderSubmissionResult:
        """Would require: explicit written authorization to submit a
        live order, Valio Aimo's order-submission endpoint, its own
        idempotency/duplicate-detection semantics (does it accept AIMO's
        key, or does it need its own?), and its order acknowledgement
        model — see "Ordering and status semantics" in the adapter
        contract doc. Never implemented without that authorization, per
        CLAUDE.md."""
        raise NotImplementedError(_NOT_AUTHORIZED.format(detail="order submission endpoint"))

    async def get_order_status(self, *, idempotency_key: str) -> OrderStatusResult:
        """Would require: Valio Aimo's order-status/tracking endpoint and
        its terminal-vs-pending status vocabulary — see "Ordering and
        status semantics" in the adapter contract doc."""
        raise NotImplementedError(_NOT_AUTHORIZED.format(detail="order status endpoint"))
