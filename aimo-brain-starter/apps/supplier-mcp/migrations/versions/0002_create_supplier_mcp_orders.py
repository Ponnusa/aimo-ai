"""create supplier_mcp_orders

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_mcp_orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column("agreement_id", sa.String(), nullable=False),
        sa.Column("buyer_organization_id", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("external_order_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_supplier_mcp_orders_agreement_id", "supplier_mcp_orders", ["agreement_id"])


def downgrade() -> None:
    op.drop_index("ix_supplier_mcp_orders_agreement_id", "supplier_mcp_orders")
    op.drop_table("supplier_mcp_orders")
