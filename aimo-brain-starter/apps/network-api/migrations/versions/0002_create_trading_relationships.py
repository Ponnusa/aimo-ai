"""create trading_relationships table

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18
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
        "trading_relationships",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("buyer_organization_id", sa.String(), nullable=False),
        sa.Column("supplier_organization_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "buyer_organization_id", "supplier_organization_id", name="uq_trading_relationship"
        ),
    )
    op.create_index(
        "ix_trading_relationships_buyer_organization_id",
        "trading_relationships",
        ["buyer_organization_id"],
    )
    op.create_index(
        "ix_trading_relationships_supplier_organization_id",
        "trading_relationships",
        ["supplier_organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_trading_relationships_supplier_organization_id", "trading_relationships")
    op.drop_index("ix_trading_relationships_buyer_organization_id", "trading_relationships")
    op.drop_table("trading_relationships")
