"""M8 slice 3 (ADR-018 section 3): migration 0006 must backfill exactly
one ``migration_snapshot`` ledger event per pre-existing root key and
delegation row -- never a fabricated historical ``issued``/``revoked``
event -- and must leave ``thumbprint_reservation_released_at`` NULL
(reservation held) for every pre-existing delegation row, never
backfilled as already-released.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

_NETWORK_API_DIR = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    config = Config(str(_NETWORK_API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_NETWORK_API_DIR / "migrations"))
    return config


def test_migration_0006_backfills_labeled_snapshot_events_only() -> None:
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    os.remove(path)
    sync_db_url = f"sqlite:///{path}"
    # env.py (this service's own migration environment) reads
    # DATABASE_URL directly, as an async driver URL -- not
    # Config.set_main_option("sqlalchemy.url", ...).
    old_env = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
    try:
        config = _alembic_config()
        command.upgrade(config, "0005")

        engine = create_engine(sync_db_url)
        now = datetime.utcnow().isoformat(sep=" ")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO organization_signing_keys "
                    "(organization_id, kid, public_key_jwk_json, status, "
                    "not_before, not_after, created_at, revoked_at) "
                    "VALUES ('org_pre_existing', 'root-pre1', :jwk, 'active', "
                    ":now, :now, :now, NULL)"
                ),
                {"jwk": json.dumps({"kty": "OKP"}), "now": now},
            )
            conn.execute(
                text(
                    "INSERT INTO agent_signing_delegations "
                    "(organization_id, agent_id, delegated_kid, delegated_pubkey_jwk_json, "
                    "pubkey_thumbprint, issuing_kid, audience, scope_json, jti, delegation_jws, "
                    "not_before, not_after, created_at, revoked_at) "
                    "VALUES ('org_pre_existing', 'agent_pre1', 'deleg-pre1', :jwk, 'thumb-pre1', "
                    "'root-pre1', 'aimo-network:test', :scope, 'jti-pre1', 'fake.jws.token', "
                    ":now, :now, :now, NULL)"
                ),
                {
                    "jwk": json.dumps({"kty": "OKP"}),
                    "scope": json.dumps({"artifact_types": [], "protocol_version": "v1"}),
                    "now": now,
                },
            )

        command.upgrade(config, "0006")

        with engine.begin() as conn:
            events = conn.execute(
                text(
                    "SELECT organization_id, kid, kind, event_type, is_reconstructed_snapshot "
                    "FROM key_lifecycle_events"
                )
            ).fetchall()
            delegation_row = conn.execute(
                text(
                    "SELECT thumbprint_reservation_released_at FROM agent_signing_delegations "
                    "WHERE jti = 'jti-pre1'"
                )
            ).fetchone()

        assert len(events) == 2
        for row in events:
            assert row.event_type == "migration_snapshot"
            assert row.is_reconstructed_snapshot in (1, True)
        kinds = sorted(row.kind for row in events)
        assert kinds == ["delegated", "root"]
        # Never fabricated as already-released -- slot stays held.
        assert delegation_row.thumbprint_reservation_released_at is None

        command.downgrade(config, "0005")
        engine.dispose()
    finally:
        if old_env is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_env
        with __import__("contextlib").suppress(FileNotFoundError, PermissionError):
            os.remove(path)
