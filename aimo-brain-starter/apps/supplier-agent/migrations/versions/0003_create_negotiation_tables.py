"""create supplier_negotiations, supplier_proposals, supplier_processed_artifacts

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
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
    op.create_table(
        "supplier_negotiations",
        sa.Column("negotiation_id", sa.String(), primary_key=True),
        sa.Column("counterparty_organization_id", sa.String(), nullable=False),
        sa.Column("counterparty_agent_id", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("current_proposal_id", sa.String(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "supplier_proposals",
        sa.Column("proposal_id", sa.String(), primary_key=True),
        sa.Column("negotiation_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("artifact_type", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("supersedes_proposal_id", sa.String(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_supplier_proposals_negotiation_id", "supplier_proposals", ["negotiation_id"]
    )
    op.create_table(
        "supplier_processed_artifacts",
        sa.Column("artifact_id", sa.String(), primary_key=True),
        sa.Column("negotiation_id", sa.String(), nullable=False),
        sa.Column("response_payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_supplier_processed_artifacts_negotiation_id",
        "supplier_processed_artifacts",
        ["negotiation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_supplier_processed_artifacts_negotiation_id", "supplier_processed_artifacts")
    op.drop_table("supplier_processed_artifacts")
    op.drop_index("ix_supplier_proposals_negotiation_id", "supplier_proposals")
    op.drop_table("supplier_proposals")
    op.drop_table("supplier_negotiations")
