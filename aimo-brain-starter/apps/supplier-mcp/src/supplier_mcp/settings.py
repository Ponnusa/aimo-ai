from __future__ import annotations

from functools import lru_cache

from agent_runtime import ServiceSettings
from pydantic import Field
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


@lru_cache
def get_settings() -> SupplierMcpSettings:
    return SupplierMcpSettings()
