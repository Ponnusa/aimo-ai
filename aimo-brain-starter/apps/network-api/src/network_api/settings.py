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


@lru_cache
def get_settings() -> NetworkApiSettings:
    return NetworkApiSettings()
