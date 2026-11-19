"""create buyer_approvals, buyer_authorization_evidence, buyer_orders

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19
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
    op.create_table(
        "buyer_approvals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("negotiation_id", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("approver_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("policy_decision_json", sa.Text(), nullable=False),
        sa.Column("audit_trail_json", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_buyer_approvals_negotiation_id", "buyer_approvals", ["negotiation_id"])

    op.create_table(
        "buyer_authorization_evidence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("negotiation_id", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("allow", sa.Boolean(), nullable=False),
        sa.Column("human_approval_required", sa.Boolean(), nullable=False),
        sa.Column("deny_reason", sa.String(), nullable=True),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("policy_input_digest", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_buyer_authorization_evidence_negotiation_id",
        "buyer_authorization_evidence",
        ["negotiation_id"],
    )

    op.create_table(
        "buyer_orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("negotiation_id", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("external_order_id", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_buyer_orders_negotiation_id", "buyer_orders", ["negotiation_id"])


def downgrade() -> None:
    op.drop_index("ix_buyer_orders_negotiation_id", "buyer_orders")
    op.drop_table("buyer_orders")
    op.drop_index("ix_buyer_authorization_evidence_negotiation_id", "buyer_authorization_evidence")
    op.drop_table("buyer_authorization_evidence")
    op.drop_index("ix_buyer_approvals_negotiation_id", "buyer_approvals")
    op.drop_table("buyer_approvals")
