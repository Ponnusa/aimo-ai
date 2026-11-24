"""supplier_orders gains attempts/last_error; new
supplier_order_reconciliation_events (M7: real supplier-side
reconciliation, portal "reconciliation history")

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    with op.batch_alter_table("supplier_orders") as batch_op:
        batch_op.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("last_error", sa.Text(), nullable=True))

    op.create_table(
        "supplier_order_reconciliation_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("negotiation_id", sa.String(), nullable=False),
        sa.Column("previous_state", sa.String(), nullable=False),
        sa.Column("new_state", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_supplier_order_reconciliation_events_order_id",
        "supplier_order_reconciliation_events",
        ["order_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_supplier_order_reconciliation_events_order_id",
        "supplier_order_reconciliation_events",
    )
    op.drop_table("supplier_order_reconciliation_events")
    with op.batch_alter_table("supplier_orders") as batch_op:
        batch_op.drop_column("last_error")
        batch_op.drop_column("attempts")
