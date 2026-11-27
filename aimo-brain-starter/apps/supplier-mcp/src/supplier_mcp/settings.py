from __future__ import annotations

from functools import lru_cache

from agent_runtime import ServiceSettings
from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict


class SupplierMcpSettings(ServiceSettings):
    """Settings for the supplier-mcp service.

    Only ``SUPPLIER_MCP_DATABASE_URL`` is read by this service. It never
    reads ``DATABASE_URL``, ``BUYER_DATABASE_URL``, ``SUPPLIER_DATABASE_URL``,
    ``REGISTRY_DATABASE_URL`` or ``BUYER_MCP_DATABASE_URL`` — enforced by
    ``tests/contracts/test_database_isolation.py``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supplier_mcp_database_url: str = Field(
        default="postgresql+psycopg://user:password@localhost:5432/aimo_supplier_mcp",
        validation_alias="SUPPLIER_MCP_DATABASE_URL",
    )

    # The one organization this deployment exclusively serves tools for.
    # A service credential scoped to any other organization is rejected —
    # see packages/mcp_integration/server_auth.py.
    supplier_mcp_organization_id: str = Field(
        default="org_fictional_wholesale_01",
        validation_alias="SUPPLIER_MCP_ORGANIZATION_ID",
    )

    # Must match network-api's SERVICE_CREDENTIAL_SIGNING_SECRET exactly:
    # this is how supplier-mcp verifies a "use_supplier_tools" credential
    # without a live call back to network-api.
    service_credential_signing_secret: str = Field(
        default="dev-only-signing-secret-change-me",
        validation_alias="SERVICE_CREDENTIAL_SIGNING_SECRET",
    )

    # M7: which SupplierBusinessSystemAdapter this deployment uses.
    # "mock" (default) is the unchanged M0-M6 in-process fictional
    # catalogue/order behavior. "sandbox" talks to the realistic
    # supplier-sandbox-erp service over real HTTP. "valio_aimo" is
    # registered but unimplemented pending authorized API access — see
    # food_wholesale.adapters.valio_aimo.
    supplier_business_system_adapter: str = Field(
        default="mock", validation_alias="SUPPLIER_BUSINESS_SYSTEM_ADAPTER"
    )

    # Only read when supplier_business_system_adapter == "sandbox".
    sandbox_erp_base_url: str = Field(
        default="http://localhost:8007", validation_alias="SANDBOX_ERP_BASE_URL"
    )

    # A long-lived, use_sandbox_erp-scoped credential this deployment
    # presents to supplier-sandbox-erp — the same "pre-provisioned
    # intra-organization credential" pattern as
    # buyer_agent.settings.buyer_mcp_service_credential, not a per-call
    # mint against network-api.
    sandbox_erp_service_credential: str = Field(
        default="", validation_alias="SANDBOX_ERP_SERVICE_CREDENTIAL"
    )

    # Only read when supplier_business_system_adapter == "valio_aimo".
    # Every field here defaults empty/False, and adapter_factory.get_adapter
    # fails closed at construction time (before this service ever starts
    # serving traffic) unless valio_aimo_integration_authorized is
    # explicitly true AND both the base URL and credential are set --
    # see docs/integrations/valio-aimo-adapter-contract.md and CLAUDE.md:
    # "Never access Valio Aimo production systems... without explicit
    # authorization and credentials." Authorization alone does not make
    # the adapter functional: every ValioAimoAdapter method still raises
    # NotImplementedError until a real integration is built against
    # Valio Aimo's actual, granted API -- this flag only gates whether a
    # deployment can even select the adapter at all.
    valio_aimo_integration_authorized: bool = Field(
        default=False, validation_alias="VALIO_AIMO_INTEGRATION_AUTHORIZED"
    )
    valio_aimo_base_url: str = Field(default="", validation_alias="VALIO_AIMO_BASE_URL")
    valio_aimo_api_credential: str = Field(default="", validation_alias="VALIO_AIMO_API_CREDENTIAL")

    @model_validator(mode="after")
    def _sandbox_and_production_config_must_not_collide(self) -> SupplierMcpSettings:
        """A copy-paste configuration mistake must never let the sandbox
        adapter's base URL/credential double as the production Valio
        Aimo ones, or vice versa -- the two are meant to be reachable
        over entirely separate networks with entirely separate
        credentials, never interchangeable. Only checked when both
        sides are actually configured (non-empty); an unset field is
        never compared."""
        if self.sandbox_erp_base_url and self.sandbox_erp_base_url == self.valio_aimo_base_url:
            raise ValueError(
                "SANDBOX_ERP_BASE_URL and VALIO_AIMO_BASE_URL must never be the same "
                "value -- the sandbox and a production business system must be "
                "reachable over separate networks."
            )
        if (
            self.sandbox_erp_service_credential
            and self.sandbox_erp_service_credential == self.valio_aimo_api_credential
        ):
            raise ValueError(
                "SANDBOX_ERP_SERVICE_CREDENTIAL and VALIO_AIMO_API_CREDENTIAL must never "
                "be the same value -- a sandbox credential must never also be a valid "
                "production credential, or vice versa."
            )
        return self


@lru_cache
def get_settings() -> SupplierMcpSettings:
    return SupplierMcpSettings()
