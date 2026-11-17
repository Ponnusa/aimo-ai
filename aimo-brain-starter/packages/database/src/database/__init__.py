"""Per-service SQLAlchemy construction helpers.

This package intentionally holds no engine, session, or connection as
module-level state. Every service constructs its own engine from its own
``*_DATABASE_URL`` environment variable so that no two services can
accidentally share a connection pool or database. See
``docs/ARCHITECTURE.md`` ("Persistence") and ``docs/SECURITY.md`` for the
isolation rules this package exists to support.
"""

from database.engine import Base, new_engine, new_session_factory

__all__ = ["Base", "new_engine", "new_session_factory"]
