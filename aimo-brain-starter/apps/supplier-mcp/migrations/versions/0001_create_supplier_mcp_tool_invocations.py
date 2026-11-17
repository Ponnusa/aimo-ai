"""create supplier_mcp_tool_invocations

Revision ID: 0001
Revises:
Create Date: 2026-11-18
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
        "supplier_mcp_tool_invocations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_supplier_mcp_tool_invocations_tool_name", "supplier_mcp_tool_invocations", ["tool_name"]
    )
    op.create_index(
        "ix_supplier_mcp_tool_invocations_organization_id",
        "supplier_mcp_tool_invocations",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_supplier_mcp_tool_invocations_organization_id", "supplier_mcp_tool_invocations"
    )
    op.drop_index("ix_supplier_mcp_tool_invocations_tool_name", "supplier_mcp_tool_invocations")
    op.drop_table("supplier_mcp_tool_invocations")
