"""supplier-sandbox-erp's own ORM models (its own database only).

This is the "external system's" own data — stock, customer prices and
orders — deliberately separate from ``supplier_mcp_orders`` (AIMO's own
durable idempotency authority, unchanged by this milestone). Nothing
outside this service ever reads this database directly; it is reached
only over this service's own HTTP API, by ``supplier-mcp``'s sandbox
adapter.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SandboxProductRecord(Base):
    __tablename__ = "sandbox_erp_products"

    product_ref: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    packaging: Mapped[str] = mapped_column(String, nullable=False)
    base_unit_price: Mapped[str] = mapped_column(String, nullable=False)
    stock: Mapped[str] = mapped_column(String, nullable=False)
    minimum_order_quantity: Mapped[str] = mapped_column(String, nullable=False)
    delivery_cutoff_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    substitute_product_ref: Mapped[str | None] = mapped_column(String, nullable=True)


class SandboxCustomerPriceRecord(Base):
    __tablename__ = "sandbox_erp_customer_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_ref: Mapped[str] = mapped_column(String, nullable=False, index=True)
    buyer_organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    unit_price: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)


class SandboxOrderRecord(Base):
    """The sandbox system's own idea of an order — including the
    ``pending``/delayed-acknowledgement state a real ERP can exhibit.
    ``idempotency_key`` is UNIQUE: a duplicate submission always returns
    this exact row, never a second order — the sandbox's own equivalent
    of ``supplier_mcp_orders``'s guarantee, proving the pattern works one
    layer further out, against a genuinely separate system."""

    __tablename__ = "sandbox_erp_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    buyer_organization_id: Mapped[str] = mapped_column(String, nullable=False)
    lines: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    external_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
