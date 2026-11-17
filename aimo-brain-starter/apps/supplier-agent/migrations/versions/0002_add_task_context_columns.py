"""add skill_id, role, counterparty_agent_id, created_at to supplier_tasks

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
    op.add_column(
        "supplier_tasks",
        sa.Column("skill_id", sa.String(), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "supplier_tasks",
        sa.Column("role", sa.String(), nullable=False, server_default="supplier"),
    )
    op.add_column("supplier_tasks", sa.Column("counterparty_agent_id", sa.String(), nullable=True))
    op.add_column(
        "supplier_tasks",
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_column("supplier_tasks", "created_at")
    op.drop_column("supplier_tasks", "counterparty_agent_id")
    op.drop_column("supplier_tasks", "role")
    op.drop_column("supplier_tasks", "skill_id")
