from __future__ import annotations

from functools import lru_cache

from agent_runtime import ServiceSettings
from pydantic import Field
from pydantic_settings import SettingsConfigDict


class NetworkApiSettings(ServiceSettings):
    """Settings for the network-api service.

    Only ``DATABASE_URL`` is read by this service. It never reads
    ``BUYER_DATABASE_URL`` or ``SUPPLIER_DATABASE_URL`` — enforced by
    ``tests/contracts/test_database_isolation.py``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    network_database_url: str = Field(
        default="postgresql+psycopg://user:password@localhost:5432/aimo_network",
        validation_alias="DATABASE_URL",
    )

    # Bootstrap admin secret for organization/relationship management
    # endpoints. M2 has no human OIDC/approval flow yet (that is M6/M7), so
    # this shared secret stands in for it; rotate it to immediately revoke
    # all admin access.
    network_admin_api_key: str = Field(
        default="dev-only-admin-key-change-me",
        validation_alias="NETWORK_ADMIN_API_KEY",
    )

    # Shared secret used only by other AIMO BRAIN services (registry-api)
    # calling this service's internal, read-only relationship-lookup
    # endpoint. Never exposed to the public internet by design.
    network_internal_service_key: str = Field(
        default="dev-only-internal-key-change-me",
        validation_alias="NETWORK_INTERNAL_SERVICE_KEY",
    )

    # Shared HMAC secret used to issue and verify short-lived service
    # credentials (see packages/identity). Must match registry-api's
    # SERVICE_CREDENTIAL_SIGNING_SECRET exactly, or every credential this
    # service issues will be rejected.
    service_credential_signing_secret: str = Field(
        default="dev-only-signing-secret-change-me",
        validation_alias="SERVICE_CREDENTIAL_SIGNING_SECRET",
    )

    # M8 slice 3 (ADR-018 section 5): how long past a delegation's own
    # not_after the reservation-release job must wait before it may free
    # that key's thumbprint for reuse by a different agent. Deliberately
    # separate from revocation, which is immediate and for-cause.
    thumbprint_reservation_grace_period_seconds: int = Field(
        default=86400,
        validation_alias="THUMBPRINT_RESERVATION_GRACE_PERIOD_SECONDS",
    )

    # M8 slice 4 (ADR-019 section 3): authenticates the bootstrap/
    # recovery sequence ONLY -- organization creation, bootstrapping an
    # organization to auth_mode="org_scoped_credential", and forced
    # recovery actions. Deliberately a THIRD secret, distinct from both
    # network_admin_api_key (the legacy per-call authority it is slowly
    # being separated from) and any organization's own scoped credential
    # (the new authority it issues) -- holding one must never imply
    # holding another.
    network_operator_key: str = Field(
        default="dev-only-operator-key-change-me",
        validation_alias="NETWORK_OPERATOR_KEY",
    )

    # M8 slice 4 (ADR-019 sections 1, 3, 7): the master gate on bootstrapping
    # ANY new organization to auth_mode="org_scoped_credential". Defaults
    # false in every environment, including CI -- enabling it is a
    # separate deployment decision made after the operator-authentication,
    # recovery, TLS and introspection configuration it depends on is
    # actually in place, never an automatic consequence of this slice
    # merging. Disabling it after an organization is already bootstrapped
    # changes nothing about that organization -- see ADR-019 section 7.
    external_onboarding_enabled: bool = Field(
        default=False,
        validation_alias="EXTERNAL_ONBOARDING_ENABLED",
    )


@lru_cache
def get_settings() -> NetworkApiSettings:
    return NetworkApiSettings()
