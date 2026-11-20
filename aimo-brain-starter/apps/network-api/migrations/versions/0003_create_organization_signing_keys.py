"""create organization_signing_keys table

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20
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
        "organization_signing_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("kid", sa.String(), nullable=False),
        sa.Column("public_key_jwk_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("not_before", sa.DateTime(), nullable=False),
        sa.Column("not_after", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("organization_id", "kid", name="uq_org_signing_key_kid"),
    )
    op.create_index(
        "ix_organization_signing_keys_organization_id",
        "organization_signing_keys",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_organization_signing_keys_organization_id", "organization_signing_keys")
    op.drop_table("organization_signing_keys")
