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

    # M5.1: per-artifact JWS signing (docs/DECISIONS.md ADR-012) — see
    # the matching field docstring on buyer_agent.settings. Replaces
    # M5's cross-organization HMAC "commit"/"negotiate" credentials
    # entirely.
    supplier_signing_private_key_pem: str = Field(
        default="",
        validation_alias="SUPPLIER_SIGNING_PRIVATE_KEY_PEM",
    )
    supplier_signing_kid: str = Field(
        default="",
        validation_alias="SUPPLIER_SIGNING_KID",
    )
    network_api_url: str = Field(
        default="http://localhost:8000",
        validation_alias="NETWORK_API_URL",
    )
    # M8 slice 2 (ADR-017): see buyer_agent.settings.BuyerAgentSettings'
    # identical fields for the full rationale — a delegation's ``aud``
    # is semantically distinct from a business-artifact proof's ``aud``.
    network_audience: str = Field(
        default="aimo-network:dev",
        validation_alias="NETWORK_AUDIENCE",
    )
    aimo_protocol_version: str = Field(
        default="v1",
        validation_alias="AIMO_PROTOCOL_VERSION",
    )
    # M5.1: registry-api lookup to confirm an inbound message's verified
    # sender is a registered, active agent with an active trading
    # relationship (this agent never called registry-api directly
    # before — only buyer-agent discovered through it).
    registry_api_url: str = Field(
        default="http://localhost:8003",
        validation_alias="REGISTRY_API_URL",
    )

    # M6: see the matching field docstrings on buyer_agent.settings.
    service_credential_signing_secret: str = Field(
        default="dev-only-signing-secret-change-me",
        validation_alias="SERVICE_CREDENTIAL_SIGNING_SECRET",
    )
    network_internal_service_key: str = Field(
        default="dev-only-internal-key-change-me",
        validation_alias="NETWORK_INTERNAL_SERVICE_KEY",
    )
    portal_organization_id: str = Field(
        default="org_aimo_portal",
        validation_alias="PORTAL_ORGANIZATION_ID",
    )
    supplier_require_second_approver_above_amount: str | None = Field(
        default=None,
        validation_alias="SUPPLIER_REQUIRE_SECOND_APPROVER_ABOVE_AMOUNT",
    )


@lru_cache
def get_settings() -> SupplierAgentSettings:
    return SupplierAgentSettings()
