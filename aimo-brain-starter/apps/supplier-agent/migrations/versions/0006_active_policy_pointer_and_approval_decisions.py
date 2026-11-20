"""create supplier_active_policy_pointer and supplier_approval_decisions

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_active_policy_pointer",
        sa.Column("organization_id", sa.String(), primary_key=True),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "supplier_approval_decisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("approval_id", sa.Integer(), nullable=False),
        sa.Column("approver_id", sa.String(), nullable=False),
        sa.Column("approver_issuer", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("approval_id", "approver_id", name="uq_supplier_approval_decision"),
    )
    op.create_index(
        "ix_supplier_approval_decisions_approval_id",
        "supplier_approval_decisions",
        ["approval_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_supplier_approval_decisions_approval_id", "supplier_approval_decisions")
    op.drop_table("supplier_approval_decisions")
    op.drop_table("supplier_active_policy_pointer")
