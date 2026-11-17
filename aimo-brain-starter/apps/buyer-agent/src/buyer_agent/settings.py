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


@lru_cache
def get_settings() -> BuyerAgentSettings:
    return BuyerAgentSettings()
