"""key lifecycle ledger, access log, and thumbprint-reservation release

M8 slice 3 (ADR-018): adds the append-only key-event ledger and its
access log, adds ``thumbprint_reservation_released_at`` to
``agent_signing_delegations``, and migrates the live-thumbprint partial
unique index to also respect it (a row now stops blocking reuse of its
key's thumbprint when EITHER revoked OR its reservation is separately
released -- not by converting routine expiry into a revocation).

Backfill is deliberately conservative: every existing
``agent_signing_delegations`` row gets
``thumbprint_reservation_released_at = NULL`` (slot held -- the column
default already does this, so no explicit UPDATE is needed, but it is
asserted here as a deliberate decision, not an accident of the
column's default). Every existing ``organization_signing_keys`` and
``agent_signing_delegations`` row receives exactly one
``migration_snapshot`` ledger event, describing only its CURRENT state
as observed at migration time -- never a fabricated historical
``issued``/``revoked`` event.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column(
        "agent_signing_delegations",
        sa.Column("thumbprint_reservation_released_at", sa.DateTime(), nullable=True),
    )

    op.execute("DROP INDEX uq_org_live_pubkey_thumbprint")
    op.execute(
        "CREATE UNIQUE INDEX uq_org_live_pubkey_thumbprint "
        "ON agent_signing_delegations (organization_id, pubkey_thumbprint) "
        "WHERE revoked_at IS NULL AND thumbprint_reservation_released_at IS NULL"
    )

    op.create_table(
        "key_lifecycle_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("kid", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("effective_time", sa.DateTime(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("key_material", sa.Text(), nullable=False),
        sa.Column(
            "is_reconstructed_snapshot", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index(
        "ix_key_lifecycle_events_organization_id", "key_lifecycle_events", ["organization_id"]
    )
    op.create_index("ix_key_lifecycle_events_kid", "key_lifecycle_events", ["kid"])

    op.create_table(
        "key_event_access_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("queried_organization_id", sa.String(), nullable=False),
        sa.Column("queried_at", sa.DateTime(), nullable=False),
        sa.Column("caller_identifier", sa.String(), nullable=False, server_default="admin"),
    )
    op.create_index(
        "ix_key_event_access_log_queried_organization_id",
        "key_event_access_log",
        ["queried_organization_id"],
    )

    # --- Conservative, labeled migration-snapshot backfill ---------------
    now = datetime.utcnow()
    bind = op.get_bind()
    key_events_table = sa.table(
        "key_lifecycle_events",
        sa.column("organization_id"),
        sa.column("kid"),
        sa.column("kind"),
        sa.column("event_type"),
        sa.column("effective_time"),
        sa.column("recorded_at"),
        sa.column("key_material"),
        sa.column("is_reconstructed_snapshot"),
    )

    root_rows = bind.execute(
        sa.text("SELECT organization_id, kid, public_key_jwk_json FROM organization_signing_keys")
    ).fetchall()
    delegation_rows = bind.execute(
        sa.text(
            "SELECT organization_id, delegated_kid, delegation_jws FROM agent_signing_delegations"
        )
    ).fetchall()

    snapshot_rows = [
        {
            "organization_id": row.organization_id,
            "kid": row.kid,
            "kind": "root",
            "event_type": "migration_snapshot",
            "effective_time": now,
            "recorded_at": now,
            "key_material": row.public_key_jwk_json,
            "is_reconstructed_snapshot": True,
        }
        for row in root_rows
    ] + [
        {
            "organization_id": row.organization_id,
            "kid": row.delegated_kid,
            "kind": "delegated",
            "event_type": "migration_snapshot",
            "effective_time": now,
            "recorded_at": now,
            "key_material": row.delegation_jws,
            "is_reconstructed_snapshot": True,
        }
        for row in delegation_rows
    ]
    if snapshot_rows:
        op.bulk_insert(key_events_table, snapshot_rows)


def downgrade() -> None:
    op.drop_index(
        "ix_key_event_access_log_queried_organization_id", table_name="key_event_access_log"
    )
    op.drop_table("key_event_access_log")
    op.drop_index("ix_key_lifecycle_events_kid", table_name="key_lifecycle_events")
    op.drop_index("ix_key_lifecycle_events_organization_id", table_name="key_lifecycle_events")
    op.drop_table("key_lifecycle_events")

    op.execute("DROP INDEX uq_org_live_pubkey_thumbprint")
    op.execute(
        "CREATE UNIQUE INDEX uq_org_live_pubkey_thumbprint "
        "ON agent_signing_delegations (organization_id, pubkey_thumbprint) "
        "WHERE revoked_at IS NULL"
    )
    op.drop_column("agent_signing_delegations", "thumbprint_reservation_released_at")
