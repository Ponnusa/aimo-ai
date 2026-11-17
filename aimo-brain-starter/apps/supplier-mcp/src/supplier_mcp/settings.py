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


@lru_cache
def get_settings() -> SupplierMcpSettings:
    return SupplierMcpSettings()
