"""create organization_members table and add oidc mapping columns

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-20
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
    op.add_column("organizations", sa.Column("oidc_issuer", sa.String(), nullable=True))
    op.add_column("organizations", sa.Column("oidc_tenant_id", sa.String(), nullable=True))

    op.create_table(
        "organization_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("oidc_subject", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "oidc_subject", "role", name="uq_org_member_subject_role"
        ),
    )
    op.create_index(
        "ix_organization_members_organization_id", "organization_members", ["organization_id"]
    )
    op.create_index(
        "ix_organization_members_oidc_subject", "organization_members", ["oidc_subject"]
    )


def downgrade() -> None:
    op.drop_index("ix_organization_members_oidc_subject", "organization_members")
    op.drop_index("ix_organization_members_organization_id", "organization_members")
    op.drop_table("organization_members")
    op.drop_column("organizations", "oidc_tenant_id")
    op.drop_column("organizations", "oidc_issuer")
