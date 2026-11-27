"""Resolves the configured ``SupplierBusinessSystemAdapter`` (M7).

``SUPPLIER_BUSINESS_SYSTEM_ADAPTER`` selects which business system this
deployment talks to. Defaults to ``"mock"`` — the exact M0–M6 behavior,
unchanged — so no existing deployment or test changes behavior without
an explicit opt-in. ``"sandbox"`` points at the realistic sandbox ERP
service (``apps/supplier-sandbox-erp``) for M7 pilot-readiness testing.
``"valio_aimo"`` is registered but fails closed at construction time
unless explicitly authorized and configured — see
``food_wholesale.adapters.valio_aimo.ValioAimoAdapter`` and
``SupplierMcpSettings.valio_aimo_integration_authorized``.
"""

from __future__ import annotations

from business_system_integration import (
    AdapterAuthorizationMissingError,
    SupplierBusinessSystemAdapter,
)
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
        # Fails closed here, at construction -- before this process ever
        # starts serving traffic -- rather than letting the server come
        # up "healthy" and only discovering the missing authorization on
        # the first real call. Authorization alone still does not make
        # this adapter functional: every method raises NotImplementedError
        # until a real integration is built against Valio Aimo's actual,
        # granted API (see ValioAimoAdapter's own docstring).
        if not settings.valio_aimo_integration_authorized:
            raise AdapterAuthorizationMissingError(
                "SUPPLIER_BUSINESS_SYSTEM_ADAPTER=valio_aimo was selected, but "
                "VALIO_AIMO_INTEGRATION_AUTHORIZED is not set to true. Selecting "
                "this adapter requires a separately recorded decision that Valio "
                "Aimo has actually authorized this integration -- see "
                "docs/integrations/valio-aimo-adapter-contract.md."
            )
        if not settings.valio_aimo_base_url or not settings.valio_aimo_api_credential:
            raise AdapterAuthorizationMissingError(
                "SUPPLIER_BUSINESS_SYSTEM_ADAPTER=valio_aimo is authorized but "
                "VALIO_AIMO_BASE_URL and/or VALIO_AIMO_API_CREDENTIAL is not set."
            )
        return ValioAimoAdapter(
            base_url=settings.valio_aimo_base_url, token=settings.valio_aimo_api_credential
        )
    raise NotImplementedError(
        f"SUPPLIER_BUSINESS_SYSTEM_ADAPTER={kind!r} is not a recognized adapter. "
        "Valid values: 'mock', 'sandbox', 'valio_aimo'."
    )
