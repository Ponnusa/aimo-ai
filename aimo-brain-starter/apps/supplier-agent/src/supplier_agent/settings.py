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

    # M5: authorization/commitment. ``supplier_commit_service_credential``
    # is a pre-minted, agent-scoped "commit" credential (network-api,
    # agent_id=supplier_agent_id) attached to every authorization_evidence/
    # order_confirmation A2A message this agent sends — see the matching
    # field docstring on buyer_agent.settings.
    supplier_commit_service_credential: str = Field(
        default="",
        validation_alias="SUPPLIER_COMMIT_SERVICE_CREDENTIAL",
    )
    service_credential_signing_secret: str = Field(
        default="dev-only-signing-secret-change-me",
        validation_alias="SERVICE_CREDENTIAL_SIGNING_SECRET",
    )
    supplier_policy_version: str = Field(
        default="v1",
        validation_alias="SUPPLIER_POLICY_VERSION",
    )
    opa_binary: str = Field(default="opa", validation_alias="OPA_BINARY")

    # M5: supplier-mcp's narrow execution tool
    # (execute_order/get_order_status) — a separate, pre-minted
    # "execute_order"-scoped credential, distinct from
    # supplier_mcp_service_credential's read-only "use_supplier_tools"
    # scope. A compromised read-only credential cannot place orders.
    supplier_mcp_execution_credential: str = Field(
        default="",
        validation_alias="SUPPLIER_MCP_EXECUTION_CREDENTIAL",
    )


@lru_cache
def get_settings() -> SupplierAgentSettings:
    return SupplierAgentSettings()
