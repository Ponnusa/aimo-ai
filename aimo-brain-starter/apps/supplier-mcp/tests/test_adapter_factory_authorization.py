"""M7 production-ERP-boundary hardening: adapter selection must fail
closed, at construction time, when the ``valio_aimo`` (production)
adapter is chosen without an explicit, separately recorded
authorization -- never silently come up "healthy" against a business
system nobody has actually agreed to let this deployment reach. See
``docs/SECURITY.md``'s "Authorised production ERP integration" section.

Also covers the settings-level guard against a sandbox/production
configuration collision -- a copy-paste mistake pointing the sandbox
adapter at what was meant to be the production URL/credential, or
vice versa.
"""

from __future__ import annotations

import pytest
from business_system_integration import AdapterAuthorizationMissingError
from food_wholesale.adapters.mock import MockSupplierAdapter
from food_wholesale.adapters.sandbox import SandboxErpAdapter
from food_wholesale.adapters.valio_aimo import ValioAimoAdapter
from supplier_mcp.adapter_factory import get_adapter
from supplier_mcp.settings import SupplierMcpSettings


def _settings(**overrides) -> SupplierMcpSettings:
    base = {
        "supplier_mcp_database_url": "sqlite+aiosqlite:///:memory:",
    }
    base.update(overrides)
    return SupplierMcpSettings(**base)


def test_mock_adapter_is_selected_by_default() -> None:
    assert isinstance(get_adapter(_settings()), MockSupplierAdapter)


def test_sandbox_adapter_is_unaffected_by_the_valio_aimo_gate() -> None:
    adapter = get_adapter(
        _settings(
            supplier_business_system_adapter="sandbox",
            sandbox_erp_base_url="http://sandbox.example",
            sandbox_erp_service_credential="sandbox-token",
        )
    )
    assert isinstance(adapter, SandboxErpAdapter)


def test_valio_aimo_fails_closed_when_not_authorized() -> None:
    with pytest.raises(AdapterAuthorizationMissingError, match="not set to true"):
        get_adapter(
            _settings(
                supplier_business_system_adapter="valio_aimo",
                valio_aimo_base_url="https://valio.example",
                valio_aimo_api_credential="a-real-looking-credential",
            )
        )


def test_valio_aimo_fails_closed_when_authorized_but_unconfigured() -> None:
    with pytest.raises(AdapterAuthorizationMissingError, match="not set"):
        get_adapter(
            _settings(
                supplier_business_system_adapter="valio_aimo",
                valio_aimo_integration_authorized=True,
            )
        )


def test_valio_aimo_constructs_once_authorized_and_configured() -> None:
    """Authorization + configuration only gets past the gate -- every
    ValioAimoAdapter method still raises NotImplementedError until a
    real integration exists (see food_wholesale.adapters.valio_aimo),
    a distinct failure reached only by actually calling it."""
    adapter = get_adapter(
        _settings(
            supplier_business_system_adapter="valio_aimo",
            valio_aimo_integration_authorized=True,
            valio_aimo_base_url="https://valio.example",
            valio_aimo_api_credential="a-real-looking-credential",
        )
    )
    assert isinstance(adapter, ValioAimoAdapter)


def test_unrecognized_adapter_kind_fails_closed() -> None:
    with pytest.raises(NotImplementedError):
        get_adapter(_settings(supplier_business_system_adapter="not_a_real_adapter"))


def test_sandbox_and_valio_aimo_base_url_collision_is_rejected() -> None:
    with pytest.raises(ValueError, match="never be the same"):
        _settings(
            sandbox_erp_base_url="https://same.example",
            valio_aimo_base_url="https://same.example",
        )


def test_sandbox_and_valio_aimo_credential_collision_is_rejected() -> None:
    with pytest.raises(ValueError, match="never be the same"):
        _settings(
            sandbox_erp_service_credential="shared-secret",
            valio_aimo_api_credential="shared-secret",
        )


def test_distinct_sandbox_and_valio_aimo_configuration_is_accepted() -> None:
    settings = _settings(
        sandbox_erp_base_url="https://sandbox.example",
        sandbox_erp_service_credential="sandbox-token",
        valio_aimo_base_url="https://valio.example",
        valio_aimo_api_credential="valio-token",
    )
    assert settings.sandbox_erp_base_url != settings.valio_aimo_base_url
