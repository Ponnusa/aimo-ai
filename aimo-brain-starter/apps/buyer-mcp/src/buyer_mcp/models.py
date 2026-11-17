"""buyer-mcp's own ORM models (buyer-mcp's own database only).

Table name is prefixed with ``buyer_mcp_`` only to guard against a name
collision in test processes that import several services' models into
one shared SQLAlchemy ``Base.metadata`` — see the matching comment in
``registry_api.models``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class BuyerMcpToolInvocationRecord(Base):
    """One row per tool call attempt (successful or rejected).

    ``detail`` never contains the bearer credential or signing secret —
    see docs/SECURITY.md ("Logs and traces do not contain secrets").
    """

    __tablename__ = "buyer_mcp_tool_invocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    organization_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    success: Mapped[bool] = mapped_column(nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
