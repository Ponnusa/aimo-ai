"""create buyer_tasks table

Revision ID: 0001
Revises:
Create Date: 2026-11-17
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
        "buyer_tasks",
        sa.Column("task_id", sa.String(), primary_key=True),
        sa.Column("state", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("buyer_tasks")
