"""Normalized, industry-independent data shapes exchanged with a supplier
business-system adapter (M7).

Every monetary and quantity field is a decimal string, never a binary
float, per ``docs/PROTOCOLS.md``'s money/quantity rules. Nothing here
assumes a food-wholesale SKU, a Valio Aimo identifier, or any other
business-specific concept — ``product_ref``/``unit``/``quantity`` are
generic commerce concepts any industry adapter can map its own real
identifiers onto.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ProductSummary:
    """One row of a catalogue search result."""

    product_ref: str
    name: str
    unit: str


@dataclass(frozen=True, slots=True)
class ProductDetails:
    """Full product/packaging detail for one ``product_ref``."""

    product_ref: str
    name: str
    unit: str
    packaging: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CustomerPrice:
    """Customer-specific price for one product, scoped to one buyer
    organization — never a flat list price."""

    product_ref: str
    buyer_organization_id: str
    unit_price: str
    currency: str


@dataclass(frozen=True, slots=True)
class AvailabilityStatus:
    """Inventory/availability for one product at a requested quantity.

    ``available`` already accounts for any substitution/MOQ decision the
    adapter made — see ``substituted_for_product_ref``. ``cutoff_passed``
    signals that the requested delivery window can no longer be met.
    """

    product_ref: str
    quantity_available: str
    moq: str | None = None
    cutoff_passed: bool = False
    substituted_for_product_ref: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryOption:
    label: str
    earliest_date: str
    fee: str
    currency: str
    restrictions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OrderLine:
    product_ref: str
    quantity: str
    unit: str
    unit_price: str


@dataclass(frozen=True, slots=True)
class OrderSubmissionResult:
    """Result of submitting an order to the business system.

    ``status`` is one of ``"confirmed"`` (a definite external order
    exists), ``"pending"`` (the system accepted the request but has not
    yet acknowledged it — a delayed-ack system; never treat this as a
    failure) or ``"rejected"`` (the system definitively refused the
    order — e.g. a real business-rule rejection, not a transport fault).
    A transport-level fault is never represented here — it is raised as
    an ``AdapterUnavailableError`` instead, so callers can tell "the
    system said no" apart from "we don't know yet."
    """

    idempotency_key: str
    status: str
    external_order_id: str | None = None
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class OrderStatusResult:
    found: bool
    idempotency_key: str
    status: str | None = None
    external_order_id: str | None = None
