"""external-agent onboarding: server-owned auth_mode, org-scoped admin
credentials, lifecycle audit trail, opaque service-credential issuances

M8 slice 4 (ADR-019): adds ``organizations.auth_mode``, defaulting
every existing row to ``"legacy_global_key"`` (conservative backfill --
never silently upgrading an existing organization to the new model),
plus three new tables: the org-scoped admin credential itself (hashed,
never recoverable plaintext), the bootstrap/recovery audit trail, and
the opaque ``manage_agents`` service-credential issuance record that
backs the new ``/introspect`` endpoint.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("auth_mode", sa.String(), nullable=False, server_default="legacy_global_key"),
    )

    op.create_table(
        "organization_admin_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("credential_hash", sa.String(), nullable=False),
        sa.Column("credential_salt", sa.String(), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_reason", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_organization_admin_credentials_organization_id",
        "organization_admin_credentials",
        ["organization_id"],
    )

    op.create_table(
        "organization_lifecycle_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_organization_lifecycle_audit_events_organization_id",
        "organization_lifecycle_audit_events",
        ["organization_id"],
    )

    op.create_table(
        "service_credential_issuances",
        sa.Column("reference_id", sa.String(), primary_key=True),
        sa.Column("credential_hash", sa.String(), nullable=False),
        sa.Column("credential_salt", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_service_credential_issuances_organization_id",
        "service_credential_issuances",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_service_credential_issuances_organization_id",
        table_name="service_credential_issuances",
    )
    op.drop_table("service_credential_issuances")
    op.drop_index(
        "ix_organization_lifecycle_audit_events_organization_id",
        table_name="organization_lifecycle_audit_events",
    )
    op.drop_table("organization_lifecycle_audit_events")
    op.drop_index(
        "ix_organization_admin_credentials_organization_id",
        table_name="organization_admin_credentials",
    )
    op.drop_table("organization_admin_credentials")
    op.drop_column("organizations", "auth_mode")
