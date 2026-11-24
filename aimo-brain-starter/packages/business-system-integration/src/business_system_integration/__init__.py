"""Supplier business-system adapter interface (M7).

Industry-independent: a Protocol (:class:`SupplierBusinessSystemAdapter`),
normalized wire-agnostic data shapes, and a typed error hierarchy that any
real ERP/API integration implements. ``apps/supplier-mcp`` depends only on
this package, never on a concrete adapter.
"""

from __future__ import annotations

from business_system_integration.adapter import SupplierBusinessSystemAdapter
from business_system_integration.errors import (
    AdapterAuthenticationError,
    AdapterError,
    AdapterInvalidRequestError,
    AdapterMalformedResponseError,
    AdapterUnavailableError,
)
from business_system_integration.types import (
    AvailabilityStatus,
    CustomerPrice,
    DeliveryOption,
    OrderLine,
    OrderStatusResult,
    OrderSubmissionResult,
    ProductDetails,
    ProductSummary,
)

__all__ = [
    "AdapterAuthenticationError",
    "AdapterError",
    "AdapterInvalidRequestError",
    "AdapterMalformedResponseError",
    "AdapterUnavailableError",
    "AvailabilityStatus",
    "CustomerPrice",
    "DeliveryOption",
    "OrderLine",
    "OrderStatusResult",
    "OrderSubmissionResult",
    "ProductDetails",
    "ProductSummary",
    "SupplierBusinessSystemAdapter",
]
