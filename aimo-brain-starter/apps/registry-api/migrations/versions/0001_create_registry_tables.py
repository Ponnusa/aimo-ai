"""create registry_agents, registry_agent_capabilities, registry_audit_events

Revision ID: 0001
Revises:
Create Date: 2026-11-18
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
        "registry_agents",
        sa.Column("agent_id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("a2a_card_url", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("card_fingerprint", sa.String(), nullable=False),
        sa.Column("verification_challenge", sa.String(), nullable=True),
        sa.Column("verification_challenge_expires_at", sa.DateTime(), nullable=True),
        sa.Column("registered_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_registry_agents_organization_id", "registry_agents", ["organization_id"])

    op.create_table(
        "registry_agent_capabilities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "agent_id", sa.String(), sa.ForeignKey("registry_agents.agent_id"), nullable=False
        ),
        sa.Column("skill_id", sa.String(), nullable=False),
        sa.Column("skill_name", sa.String(), nullable=False),
        sa.UniqueConstraint("agent_id", "skill_id", name="uq_agent_capability"),
    )
    op.create_index(
        "ix_registry_agent_capabilities_agent_id", "registry_agent_capabilities", ["agent_id"]
    )
    op.create_index(
        "ix_registry_agent_capabilities_skill_id", "registry_agent_capabilities", ["skill_id"]
    )

    op.create_table(
        "registry_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_registry_audit_events_agent_id", "registry_audit_events", ["agent_id"])
    op.create_index(
        "ix_registry_audit_events_organization_id", "registry_audit_events", ["organization_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_registry_audit_events_organization_id", "registry_audit_events")
    op.drop_index("ix_registry_audit_events_agent_id", "registry_audit_events")
    op.drop_table("registry_audit_events")

    op.drop_index("ix_registry_agent_capabilities_skill_id", "registry_agent_capabilities")
    op.drop_index("ix_registry_agent_capabilities_agent_id", "registry_agent_capabilities")
    op.drop_table("registry_agent_capabilities")

    op.drop_index("ix_registry_agents_organization_id", "registry_agents")
    op.drop_table("registry_agents")
