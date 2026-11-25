"""add signing key, purpose-bound challenges, classification and org policy

M8 slice 1 (ADR-015). This migration has never shipped to main, so it is
edited in place rather than followed by a 0003 — there is no production
data anywhere depending on the previous shape of this file.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24
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
        "registry_agents",
        sa.Column("agent_classification", sa.String(), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "registry_agents",
        sa.Column("signing_public_key_jwk", sa.Text(), nullable=True),
    )
    op.add_column(
        "registry_agents",
        sa.Column("endpoint_challenge", sa.Text(), nullable=True),
    )
    op.add_column(
        "registry_agents",
        sa.Column("key_challenge", sa.Text(), nullable=True),
    )
    op.add_column(
        "registry_agents",
        sa.Column("challenge_expires_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        "UPDATE registry_agents SET endpoint_challenge = verification_challenge, "
        "challenge_expires_at = verification_challenge_expires_at"
    )
    op.drop_column("registry_agents", "verification_challenge")
    op.drop_column("registry_agents", "verification_challenge_expires_at")

    op.create_table(
        "registry_organization_policies",
        sa.Column("organization_id", sa.String(), primary_key=True),
        sa.Column("requires_key_proof", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("set_by_note", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("registry_organization_policies")

    op.add_column(
        "registry_agents",
        sa.Column("verification_challenge", sa.String(), nullable=True),
    )
    op.add_column(
        "registry_agents",
        sa.Column("verification_challenge_expires_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        "UPDATE registry_agents SET verification_challenge = endpoint_challenge, "
        "verification_challenge_expires_at = challenge_expires_at"
    )
    op.drop_column("registry_agents", "challenge_expires_at")
    op.drop_column("registry_agents", "key_challenge")
    op.drop_column("registry_agents", "endpoint_challenge")
    op.drop_column("registry_agents", "signing_public_key_jwk")
    op.drop_column("registry_agents", "agent_classification")
