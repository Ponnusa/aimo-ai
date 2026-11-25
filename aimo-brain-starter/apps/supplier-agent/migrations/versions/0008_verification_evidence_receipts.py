"""create supplier_verification_evidence_receipts

M8 slice 3 (ADR-018 section 2): additive to supplier_seen_message_proofs,
never a replacement -- records a receipt for both accepted and rejected
verification attempts.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_verification_evidence_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("jti", sa.String(), nullable=True),
        sa.Column("artifact_digest", sa.String(), nullable=True),
        sa.Column("kid_used", sa.String(), nullable=True),
        sa.Column("claimed_artifact_digest", sa.String(), nullable=True),
        sa.Column("claimed_kid", sa.String(), nullable=True),
        sa.Column("delegated_kid", sa.String(), nullable=True),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("artifact_type", sa.String(), nullable=True),
        sa.Column("attribution", sa.String(), nullable=True),
        sa.Column("verification_outcome", sa.String(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_supplier_verification_evidence_receipts_jti",
        "supplier_verification_evidence_receipts",
        ["jti"],
    )
    op.create_index(
        "ix_supplier_verification_evidence_receipts_organization_id",
        "supplier_verification_evidence_receipts",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_supplier_verification_evidence_receipts_organization_id",
        table_name="supplier_verification_evidence_receipts",
    )
    op.drop_index(
        "ix_supplier_verification_evidence_receipts_jti",
        table_name="supplier_verification_evidence_receipts",
    )
    op.drop_table("supplier_verification_evidence_receipts")
