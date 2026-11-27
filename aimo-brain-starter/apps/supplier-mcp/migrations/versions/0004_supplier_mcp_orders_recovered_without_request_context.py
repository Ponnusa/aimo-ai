"""supplier_mcp_orders.recovered_without_request_context (M7: a row
created by get_order_status's adapter-recovery path, which never
receives agreement_id/buyer_organization_id/content_hash, so those
three columns are explicit empty-string placeholders rather than the
real transaction identity -- this flag makes that state inspectable
instead of indistinguishable from an ordinary, fully-linked order)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    with op.batch_alter_table("supplier_mcp_orders") as batch_op:
        batch_op.add_column(
            sa.Column(
                "recovered_without_request_context",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("supplier_mcp_orders") as batch_op:
        batch_op.drop_column("recovered_without_request_context")
