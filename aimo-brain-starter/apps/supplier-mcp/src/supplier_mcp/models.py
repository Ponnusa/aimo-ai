"""supplier-mcp's own ORM models (supplier-mcp's own database only).

Table name is prefixed with ``supplier_mcp_`` only to guard against a
name collision in test processes that import several services' models
into one shared SQLAlchemy ``Base.metadata`` — see the matching comment
in ``registry_api.models``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SupplierMcpToolInvocationRecord(Base):
    """One row per tool call attempt (successful or rejected).

    ``detail`` never contains the bearer credential or signing secret —
    see docs/SECURITY.md ("Logs and traces do not contain secrets").
    """

    __tablename__ = "supplier_mcp_tool_invocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    organization_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    success: Mapped[bool] = mapped_column(nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SupplierMcpOrderRecord(Base):
    """The approved mock order adapter's own durable idempotency record
    (M5). ``idempotency_key`` is UNIQUE: ``execute_order`` always checks
    this table first, so a retried commit (same key) returns the exact
    prior result instead of calling the mock adapter — and therefore
    never creating a second order — a database constraint, not just
    in-process logic that a restart could lose."""

    __tablename__ = "supplier_mcp_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    agreement_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    buyer_organization_id: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    #: Nullable as of M7: a delayed-ack adapter (sandbox/Valio) can
    #: report ``status="pending"`` with no reference yet -- never
    #: fabricated. ``get_order_status`` fills this in once the adapter
    #: resolves the order.
    external_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    #: True only for a row ``get_order_status``'s adapter-recovery path
    #: created (M7 hardening) -- that path never receives agreement_id/
    #: buyer_organization_id/content_hash, so those three columns are
    #: explicit empty-string placeholders, not the real transaction
    #: identity, until a genuine ``execute_order`` retry backfills them.
    #: Never an ordinary, fully-linked duplicate while this is true.
    recovered_without_request_context: Mapped[bool] = mapped_column(nullable=False, default=False)
