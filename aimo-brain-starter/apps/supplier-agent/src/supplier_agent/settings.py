from __future__ import annotations

from functools import lru_cache

from agent_runtime import ServiceSettings
from pydantic import Field
from pydantic_settings import SettingsConfigDict


class SupplierAgentSettings(ServiceSettings):
    """Settings for the supplier-agent service.

    Only ``SUPPLIER_DATABASE_URL`` is read by this service. It never reads
    ``DATABASE_URL`` (network-api's) or ``BUYER_DATABASE_URL`` — enforced
    by ``tests/contracts/test_database_isolation.py``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supplier_database_url: str = Field(
        default="postgresql+psycopg://user:password@localhost:5432/aimo_supplier",
        validation_alias="SUPPLIER_DATABASE_URL",
    )

    # This agent's own identity and public A2A endpoint. Distinct from the
    # buyer's equivalents; never read from a shared env var.
    supplier_agent_id: str = Field(
        default="agent_fictional_supplier_01",
        validation_alias="SUPPLIER_AGENT_ID",
    )
    supplier_organization_id: str = Field(
        default="org_fictional_wholesale_01",
        validation_alias="SUPPLIER_ORGANIZATION_ID",
    )
    supplier_a2a_url: str = Field(
        default="http://localhost:8002",
        validation_alias="SUPPLIER_A2A_URL",
    )

    # M3: supplier-mcp — catalogue/inventory/pricing tools over the
    # official MCP SDK. The credential is a pre-minted, scoped
    # "use_supplier_tools" service credential (see network-api's
    # /organizations/{id}/service-credentials), provided to this agent's
    # operator out of band — the agent never holds the admin key needed
    # to mint one itself.
    supplier_mcp_url: str = Field(
        default="http://localhost:8005/mcp",
        validation_alias="SUPPLIER_MCP_URL",
    )
    supplier_mcp_service_credential: str = Field(
        default="",
        validation_alias="SUPPLIER_MCP_SERVICE_CREDENTIAL",
    )


@lru_cache
def get_settings() -> SupplierAgentSettings:
    return SupplierAgentSettings()
