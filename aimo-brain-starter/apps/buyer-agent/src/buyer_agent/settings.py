from __future__ import annotations

from functools import lru_cache

from agent_runtime import ServiceSettings
from pydantic import Field
from pydantic_settings import SettingsConfigDict


class BuyerAgentSettings(ServiceSettings):
    """Settings for the buyer-agent service.

    Only ``BUYER_DATABASE_URL`` is read by this service. It never reads
    ``DATABASE_URL`` (network-api's) or ``SUPPLIER_DATABASE_URL`` — enforced
    by ``tests/contracts/test_database_isolation.py``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    buyer_database_url: str = Field(
        default="postgresql+psycopg://user:password@localhost:5432/aimo_buyer",
        validation_alias="BUYER_DATABASE_URL",
    )

    # This agent's own identity and public A2A endpoint. Distinct from the
    # supplier's equivalents; never read from a shared env var.
    buyer_agent_id: str = Field(
        default="agent_fictional_buyer_01",
        validation_alias="BUYER_AGENT_ID",
    )
    buyer_organization_id: str = Field(
        default="org_fictional_bistro_01",
        validation_alias="BUYER_ORGANIZATION_ID",
    )
    buyer_a2a_url: str = Field(
        default="http://localhost:8001",
        validation_alias="BUYER_A2A_URL",
    )
    supplier_a2a_url: str = Field(
        default="http://localhost:8002",
        validation_alias="SUPPLIER_A2A_URL",
    )
    registry_api_url: str = Field(
        default="http://localhost:8003",
        validation_alias="REGISTRY_API_URL",
    )

    # M3: buyer-mcp — purchasing-requirements/budget/approval tools over
    # the official MCP SDK. The credential is a pre-minted, scoped
    # "use_buyer_tools" service credential (see network-api's
    # /organizations/{id}/service-credentials), provided to this agent's
    # operator out of band — the agent never holds the admin key needed
    # to mint one itself.
    buyer_mcp_url: str = Field(
        default="http://localhost:8004/mcp",
        validation_alias="BUYER_MCP_URL",
    )
    buyer_mcp_service_credential: str = Field(
        default="",
        validation_alias="BUYER_MCP_SERVICE_CREDENTIAL",
    )


@lru_cache
def get_settings() -> BuyerAgentSettings:
    return BuyerAgentSettings()
