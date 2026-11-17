"""Factory functions for per-service SQLAlchemy engines and sessions.

Each caller (network-api, buyer-agent, supplier-agent, worker) must pass its
own database URL. There is no default and no shared global engine, by
design: a service that forgets to configure its own URL fails fast instead
of silently connecting to another organization's database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for ORM models.

    Each service defines its own models inheriting from this base and its
    own Alembic migration chain. Sharing this base class only shares Python
    metaclass plumbing, not data, schema, or a database connection.
    """


def new_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create a new async engine bound to exactly one database URL."""
    if not database_url:
        raise ValueError("database_url must be a non-empty connection string")
    return create_async_engine(database_url, echo=echo, pool_pre_ping=True)


def new_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Provide a transactional session scope with commit/rollback handling."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
