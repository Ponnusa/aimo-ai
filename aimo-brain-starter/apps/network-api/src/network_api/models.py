"""Network-api's own ORM models.

These tables live only in the network database (``DATABASE_URL`` /
``aimo_network``). Buyer and supplier agents never read these tables
directly; they go through this service's API, per
``docs/ARCHITECTURE.md``: "No cross-organization SQL joins to exchange
business data." Full registry/capability/relationship tables land in M2;
M0 needs just enough schema to prove migrations and seeding work.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class OrganizationRecord(Base):
    __tablename__ = "organizations"

    organization_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TradingRelationshipRecord(Base):
    """A declared trading relationship between two organizations.

    This is the sole source of truth for "does org A have permission to
    discover org B's agents" (M2). ``registry-api`` never reads this table
    directly — it calls this service's ``GET /relationships`` endpoint,
    authenticated with a shared internal-service key, per the no-cross-
    service-database-access rule in docs/ARCHITECTURE.md.
    """

    __tablename__ = "trading_relationships"
    __table_args__ = (
        UniqueConstraint(
            "buyer_organization_id", "supplier_organization_id", name="uq_trading_relationship"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    buyer_organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    supplier_organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
