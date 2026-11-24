"""create sandbox erp tables

Revision ID: 0001
Revises:
Create Date: 2026-11-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "sandbox_erp_products",
        sa.Column("product_ref", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("packaging", sa.String(), nullable=False),
        sa.Column("base_unit_price", sa.String(), nullable=False),
        sa.Column("stock", sa.String(), nullable=False),
        sa.Column("minimum_order_quantity", sa.String(), nullable=False),
        sa.Column("delivery_cutoff_passed", sa.Boolean(), nullable=False),
        sa.Column("substitute_product_ref", sa.String(), nullable=True),
    )
    op.create_table(
        "sandbox_erp_customer_prices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("product_ref", sa.String(), nullable=False),
        sa.Column("buyer_organization_id", sa.String(), nullable=False),
        sa.Column("unit_price", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
    )
    op.create_index(
        "ix_sandbox_erp_customer_prices_product_ref",
        "sandbox_erp_customer_prices",
        ["product_ref"],
    )
    op.create_index(
        "ix_sandbox_erp_customer_prices_buyer_organization_id",
        "sandbox_erp_customer_prices",
        ["buyer_organization_id"],
    )
    op.create_table(
        "sandbox_erp_orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column("buyer_organization_id", sa.String(), nullable=False),
        sa.Column("lines", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("external_order_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_sandbox_erp_orders_idempotency_key", "sandbox_erp_orders", ["idempotency_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_sandbox_erp_orders_idempotency_key", "sandbox_erp_orders")
    op.drop_table("sandbox_erp_orders")
    op.drop_index(
        "ix_sandbox_erp_customer_prices_buyer_organization_id", "sandbox_erp_customer_prices"
    )
    op.drop_index("ix_sandbox_erp_customer_prices_product_ref", "sandbox_erp_customer_prices")
    op.drop_table("sandbox_erp_customer_prices")
    op.drop_table("sandbox_erp_products")
