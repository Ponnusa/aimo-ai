"""supplier_mcp_orders.external_order_id becomes nullable (M7: pending/
delayed-ack orders have no reference yet)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    with op.batch_alter_table("supplier_mcp_orders") as batch_op:
        batch_op.alter_column("external_order_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("supplier_mcp_orders") as batch_op:
        batch_op.alter_column("external_order_id", existing_type=sa.String(), nullable=False)
