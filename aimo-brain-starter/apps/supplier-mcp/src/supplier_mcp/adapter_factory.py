"""Resolves the configured ``SupplierBusinessSystemAdapter`` (M7).

``SUPPLIER_BUSINESS_SYSTEM_ADAPTER`` selects which business system this
deployment talks to. Defaults to ``"mock"`` — the exact M0–M6 behavior,
unchanged — so no existing deployment or test changes behavior without
an explicit opt-in. ``"sandbox"`` points at the realistic sandbox ERP
service (``apps/supplier-sandbox-erp``) for M7 pilot-readiness testing.
``"valio_aimo"`` is registered but will raise on first real call — see
``food_wholesale.adapters.valio_aimo.ValioAimoAdapter``.
"""

from __future__ import annotations

from business_system_integration import SupplierBusinessSystemAdapter
from food_wholesale.adapters.mock import MockSupplierAdapter
from food_wholesale.adapters.sandbox import SandboxErpAdapter
from food_wholesale.adapters.valio_aimo import ValioAimoAdapter

from supplier_mcp.settings import SupplierMcpSettings


def get_adapter(settings: SupplierMcpSettings) -> SupplierBusinessSystemAdapter:
    kind = settings.supplier_business_system_adapter
    if kind == "mock":
        return MockSupplierAdapter()
    if kind == "sandbox":
        return SandboxErpAdapter(
            base_url=settings.sandbox_erp_base_url,
            token=settings.sandbox_erp_service_credential,
        )
    if kind == "valio_aimo":
        return ValioAimoAdapter()
    raise NotImplementedError(
        f"SUPPLIER_BUSINESS_SYSTEM_ADAPTER={kind!r} is not a recognized adapter. "
        "Valid values: 'mock', 'sandbox', 'valio_aimo'."
    )
