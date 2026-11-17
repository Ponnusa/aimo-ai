"""Network-api's own ORM models.

These tables live only in the network database (``DATABASE_URL`` /
``aimo_network``). Buyer and supplier agents never read these tables
directly; they go through this service's API, per
``docs/ARCHITECTURE.md``: "No cross-organization SQL joins to exchange
business data." Full registry/capability/relationship tables land in M2;
M0 needs just enough schema to prove migrations and seeding work.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class OrganizationRecord(Base):
    __tablename__ = "organizations"

    organization_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
