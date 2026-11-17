"""supplier-agent's own private ORM models (supplier-owned database only).

M0 scope: a minimal private task record proving this service persists its
own state independently of the network and buyer databases. The full
inbox/outbox and negotiation state land in M1/M4.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SupplierTaskRecord(Base):
    __tablename__ = "supplier_tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
