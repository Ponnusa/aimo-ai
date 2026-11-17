"""supplier-agent's own private ORM models (supplier-owned database only).

``SupplierTaskRecord`` is this agent's own business-level task log —
distinct from, and in addition to, the A2A SDK's own internal task
persistence (``a2a_sdk_tasks`` table, owned entirely by
``a2a.server.tasks.DatabaseTaskStore`` against this same database). This
table is what "Both agents independently record their task history" (M1
requirement 4) is backed by at the business layer: it is queryable with
our own ORM without depending on A2A SDK internals, and it never contains
any buyer-owned data.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SupplierTaskRecord(Base):
    __tablename__ = "supplier_tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    skill_id: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="supplier")
    counterparty_agent_id: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
