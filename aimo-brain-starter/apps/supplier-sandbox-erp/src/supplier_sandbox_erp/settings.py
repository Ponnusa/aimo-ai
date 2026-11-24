from __future__ import annotations

from functools import lru_cache

from agent_runtime import ServiceSettings
from pydantic import Field
from pydantic_settings import SettingsConfigDict


class SupplierSandboxErpSettings(ServiceSettings):
    """Settings for the supplier sandbox ERP service (M7).

    Only ``SUPPLIER_SANDBOX_ERP_DATABASE_URL`` is read by this service —
    its own, separate persistence, never ``SUPPLIER_MCP_DATABASE_URL`` or
    any other service's database. Enforced by
    ``tests/contracts/test_database_isolation.py``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supplier_sandbox_erp_database_url: str = Field(
        default="postgresql+psycopg://user:password@localhost:5432/aimo_supplier_sandbox_erp",
        validation_alias="SUPPLIER_SANDBOX_ERP_DATABASE_URL",
    )

    # Must match network-api's SERVICE_CREDENTIAL_SIGNING_SECRET exactly —
    # the same intra-organization HMAC trust root every other AIMO-internal
    # service call uses (see packages/identity). supplier-mcp is the only
    # intended caller, authenticated with a `use_sandbox_erp`-scoped
    # credential.
    service_credential_signing_secret: str = Field(
        default="dev-only-signing-secret-change-me",
        validation_alias="SERVICE_CREDENTIAL_SIGNING_SECRET",
    )

    # The one supplier organization this sandbox instance plays the role
    # of. A credential scoped to any other organization is rejected.
    supplier_sandbox_erp_organization_id: str = Field(
        default="org_fictional_wholesale_01",
        validation_alias="SUPPLIER_SANDBOX_ERP_ORGANIZATION_ID",
    )

    # `environment` (inherited from ServiceSettings, read from
    # ENVIRONMENT) gates the `/admin/fault-injection` endpoints
    # (deterministic test tooling, not a real ERP capability) — refused
    # outside local/staging/test, mirroring identity.dev_oidc's gating.

    #: How long a `"timeout"`-armed call blocks before responding, used to
    #: force a real client-side timeout deterministically in tests rather
    #: than relying on an unreachable host.
    fault_injection_timeout_seconds: float = Field(default=5.0)


@lru_cache
def get_settings() -> SupplierSandboxErpSettings:
    return SupplierSandboxErpSettings()
