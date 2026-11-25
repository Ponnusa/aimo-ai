"""create agent_signing_delegations and organization_kid_namespace tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-25
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
    op.create_table(
        "organization_kid_namespace",
        sa.Column("organization_id", sa.String(), primary_key=True),
        sa.Column("kid", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "agent_signing_delegations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("delegated_kid", sa.String(), nullable=False),
        sa.Column("delegated_pubkey_jwk_json", sa.Text(), nullable=False),
        sa.Column("pubkey_thumbprint", sa.String(), nullable=False),
        sa.Column("issuing_kid", sa.String(), nullable=False),
        sa.Column("audience", sa.String(), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False),
        sa.Column("jti", sa.String(), nullable=False),
        sa.Column("delegation_jws", sa.Text(), nullable=False),
        sa.Column("not_before", sa.DateTime(), nullable=False),
        sa.Column("not_after", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("organization_id", "delegated_kid", name="uq_org_delegated_kid"),
        sa.UniqueConstraint("jti", name="uq_agent_signing_delegations_jti"),
    )
    op.create_index(
        "ix_agent_signing_delegations_organization_id",
        "agent_signing_delegations",
        ["organization_id"],
    )
    op.create_index(
        "ix_agent_signing_delegations_agent_id", "agent_signing_delegations", ["agent_id"]
    )
    # Partial unique index: the public-key thumbprint is unique only
    # among currently-live (unrevoked) rows, organization-wide — see
    # docs/DECISIONS.md ADR-017.
    op.execute(
        "CREATE UNIQUE INDEX uq_org_live_pubkey_thumbprint "
        "ON agent_signing_delegations (organization_id, pubkey_thumbprint) "
        "WHERE revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_org_live_pubkey_thumbprint")
    op.drop_index("ix_agent_signing_delegations_agent_id", table_name="agent_signing_delegations")
    op.drop_index(
        "ix_agent_signing_delegations_organization_id", table_name="agent_signing_delegations"
    )
    op.drop_table("agent_signing_delegations")
    op.drop_table("organization_kid_namespace")
