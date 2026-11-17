"""Common settings base class.

Each service defines its own ``Settings`` subclass with its own database URL
field name (e.g. ``buyer_database_url`` vs ``supplier_database_url``) so
that no two services can accidentally read the same environment variable
and end up pointed at the same database.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    """Fields common to every AIMO BRAIN service."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "fake"
    environment: str = "local"
