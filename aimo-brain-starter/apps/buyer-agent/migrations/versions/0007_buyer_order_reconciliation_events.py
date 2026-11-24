"""new buyer_order_reconciliation_events (M7: portal "reconciliation
history" -- buyer_orders already has attempts/last_error since M5)

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
    op.create_table(
        "buyer_order_reconciliation_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("negotiation_id", sa.String(), nullable=False),
        sa.Column("previous_state", sa.String(), nullable=False),
        sa.Column("new_state", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_buyer_order_reconciliation_events_order_id",
        "buyer_order_reconciliation_events",
        ["order_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_buyer_order_reconciliation_events_order_id", "buyer_order_reconciliation_events"
    )
    op.drop_table("buyer_order_reconciliation_events")
