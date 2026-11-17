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


@lru_cache
def get_settings() -> SupplierAgentSettings:
    return SupplierAgentSettings()
