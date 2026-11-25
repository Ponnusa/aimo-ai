"""registry-api's own ORM models (registry's own database only).

Table names are prefixed with ``registry_`` even though this is already
this service's own isolated database — the prefix only guards against a
name collision in test processes that import several services' models
into one shared SQLAlchemy ``Base.metadata`` (``database.Base`` is shared
Python plumbing, not shared data or a shared schema — see
``packages/database/src/database/engine.py``).

Organization identity and trading relationships are **not** modeled here:
those belong to network-api (see docs/DECISIONS.md ADR-008). This service
stores only what it owns: agent records, their indexed capabilities, its
own per-organization onboarding classification, and its own audit trail.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

#: Lifecycle states. "pending" = registered but ownership not yet
#: verified; "active" = verified and discoverable; "suspended" = hidden
#: from discovery, reversible (only via an explicit ``/reactivate`` call
#: on a currently-``suspended`` agent, never implicitly through
#: re-registration); "revoked" = hidden from discovery, terminal (cannot
#: be reactivated, and cannot be resurrected through re-registration
#: either — see ADR-015's lifecycle transition table).
AGENT_STATUSES = ("pending", "active", "suspended", "revoked")

#: Server-controlled onboarding classification (ADR-015, M8 remediation).
#: "legacy" is the default for every agent unless its organization has
#: been explicitly flagged by a registry operator (never by the
#: registering caller itself — see ``RegistryOrganizationPolicyRecord``).
#: "external" organizations must supply and prove control of a signing
#: key before any of their agents can become active.
AGENT_CLASSIFICATIONS = ("legacy", "external")


class RegistryOrganizationPolicyRecord(Base):
    """Per-organization onboarding policy, set only through the
    operator-only ``/organizations/{id}/classification`` endpoint
    (``require_registry_admin``, a distinct secret never distributed to
    any organization) — never by the organization's own
    ``manage_agents``-scoped service credential. Absence of a row means
    "legacy" (the M0-M7 default, endpoint-control-only verification
    remains sufficient). A row with ``requires_key_proof=True`` makes a
    signing key mandatory at registration for every agent of that
    organization, and private-key-control proof mandatory at
    verification — see ADR-015 section "A. Mandatory external-agent
    verification"."""

    __tablename__ = "registry_organization_policies"

    organization_id: Mapped[str] = mapped_column(String, primary_key=True)
    requires_key_proof: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    set_by_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RegistryAgentRecord(Base):
    __tablename__ = "registry_agents"

    agent_id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    a2a_card_url: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    #: Server-computed at registration time from
    #: ``RegistryOrganizationPolicyRecord`` — never settable by the
    #: registering caller. Denormalized onto the agent row purely for
    #: observability/audit (what regime this agent was actually held to);
    #: the policy row remains the source of truth for future
    #: registrations.
    agent_classification: Mapped[str] = mapped_column(
        String, nullable=False, default="legacy", server_default="legacy"
    )
    card_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    #: Optional Ed25519 JWK (RFC 8037, JSON-encoded) the registrant claims
    #: to control. Mandatory (enforced in the application layer, not by a
    #: DB constraint) once the owning organization is classified
    #: "external". When set, ``verification/confirm`` additionally
    #: requires a signature over ``key_challenge`` made with the matching
    #: private key (M8 private-key-control check) before activation —
    #: endpoint control alone is no longer sufficient for agents that
    #: carry a key. Nullable so "legacy" registrants keep working
    #: unchanged (endpoint-control-only).
    signing_public_key_jwk: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Structured, purpose-bound challenge text served at
    #: ``/.well-known/aimo-brain-challenge`` and compared verbatim at
    #: confirm time. Distinct from ``key_challenge`` — see ADR-015
    #: section "D. Cryptographic challenge binding".
    endpoint_challenge: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Structured, purpose-bound challenge text the registrant signs with
    #: its claimed private key. ``None`` whenever ``signing_public_key_jwk``
    #: is ``None`` (no key control to prove).
    key_challenge: Mapped[str | None] = mapped_column(Text, nullable=True)
    challenge_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
