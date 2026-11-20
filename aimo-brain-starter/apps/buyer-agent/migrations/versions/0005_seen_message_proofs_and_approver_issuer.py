"""create buyer_seen_message_proofs, add buyer_approvals.approver_issuer

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column("buyer_approvals", sa.Column("approver_issuer", sa.String(), nullable=True))
    op.create_table(
        "buyer_seen_message_proofs",
        sa.Column("jti", sa.String(), primary_key=True),
        sa.Column("negotiation_id", sa.String(), nullable=False),
        sa.Column("artifact_type", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("seen_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_buyer_seen_message_proofs_negotiation_id",
        "buyer_seen_message_proofs",
        ["negotiation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_buyer_seen_message_proofs_negotiation_id", "buyer_seen_message_proofs")
    op.drop_table("buyer_seen_message_proofs")
    op.drop_column("buyer_approvals", "approver_issuer")
