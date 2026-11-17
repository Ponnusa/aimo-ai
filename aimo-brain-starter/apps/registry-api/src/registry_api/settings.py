from __future__ import annotations

from functools import lru_cache

from agent_runtime import ServiceSettings
from pydantic import Field
from pydantic_settings import SettingsConfigDict


class RegistryApiSettings(ServiceSettings):
    """Settings for the registry-api service.

    Only ``REGISTRY_DATABASE_URL`` is read by this service. It never reads
    ``DATABASE_URL`` (network-api's), ``BUYER_DATABASE_URL`` or
    ``SUPPLIER_DATABASE_URL`` — enforced by
    ``tests/contracts/test_database_isolation.py``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    registry_database_url: str = Field(
        default="postgresql+psycopg://user:password@localhost:5432/aimo_registry",
        validation_alias="REGISTRY_DATABASE_URL",
    )

    # Must match network-api's SERVICE_CREDENTIAL_SIGNING_SECRET exactly:
    # this is how registry-api verifies an organization's agent-management
    # credential without a live call back to network-api.
    service_credential_signing_secret: str = Field(
        default="dev-only-signing-secret-change-me",
        validation_alias="SERVICE_CREDENTIAL_SIGNING_SECRET",
    )

    # Where to ask (read-only, internal-service-key authenticated) whether
    # a buyer organization has an active trading relationship with a
    # supplier organization, for discovery. Registry-api never reads
    # network-api's database directly.
    network_api_url: str = Field(
        default="http://localhost:8000",
        validation_alias="NETWORK_API_URL",
    )
    network_internal_service_key: str = Field(
        default="dev-only-internal-key-change-me",
        validation_alias="NETWORK_INTERNAL_SERVICE_KEY",
    )

    # SSRF defense for Agent Card fetch/re-fetch. MUST remain false for any
    # real deployment; local dev/CI set it true because buyer/supplier
    # agents run on localhost there. See docs/DECISIONS.md ADR-008.
    registry_allow_private_network_card_targets: bool = Field(
        default=False,
        validation_alias="REGISTRY_ALLOW_PRIVATE_NETWORK_CARD_TARGETS",
    )

    registry_card_fetch_timeout_seconds: float = Field(
        default=5.0,
        validation_alias="REGISTRY_CARD_FETCH_TIMEOUT_SECONDS",
    )

    registry_verification_challenge_ttl_seconds: int = Field(
        default=600,
        validation_alias="REGISTRY_VERIFICATION_CHALLENGE_TTL_SECONDS",
    )


@lru_cache
def get_settings() -> RegistryApiSettings:
    return RegistryApiSettings()
