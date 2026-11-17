"""registry-api's own ORM models (registry's own database only).

Table names are prefixed with ``registry_`` even though this is already
this service's own isolated database — the prefix only guards against a
name collision in test processes that import several services' models
into one shared SQLAlchemy ``Base.metadata`` (``database.Base`` is shared
Python plumbing, not shared data or a shared schema — see
``packages/database/src/database/engine.py``).

Organization identity and trading relationships are **not** modeled here:
those belong to network-api (see docs/DECISIONS.md ADR-008). This service
stores only what it owns: agent records, their indexed capabilities, and
its own audit trail.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

#: Lifecycle states. "pending" = registered but ownership not yet
#: verified; "active" = verified and discoverable; "suspended" = hidden
#: from discovery, reversible; "revoked" = hidden from discovery,
#: terminal (cannot be reactivated).
AGENT_STATUSES = ("pending", "active", "suspended", "revoked")


class RegistryAgentRecord(Base):
    __tablename__ = "registry_agents"

    agent_id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    a2a_card_url: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    card_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    verification_challenge: Mapped[str | None] = mapped_column(String, nullable=True)
    verification_challenge_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    registered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RegistryAgentCapabilityRecord(Base):
    __tablename__ = "registry_agent_capabilities"
    __table_args__ = (UniqueConstraint("agent_id", "skill_id", name="uq_agent_capability"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("registry_agents.agent_id"), nullable=False, index=True
    )
    skill_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    skill_name: Mapped[str] = mapped_column(String, nullable=False)


class RegistryAuditEventRecord(Base):
    __tablename__ = "registry_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    organization_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
