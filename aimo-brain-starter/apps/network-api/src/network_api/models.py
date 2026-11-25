"""Network-api's own ORM models.

These tables live only in the network database (``DATABASE_URL`` /
``aimo_network``). Buyer and supplier agents never read these tables
directly; they go through this service's API, per
``docs/ARCHITECTURE.md``: "No cross-organization SQL joins to exchange
business data." Full registry/capability/relationship tables land in M2;
M0 needs just enough schema to prove migrations and seeding work.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class OrganizationRecord(Base):
    __tablename__ = "organizations"

    organization_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: M6: the enterprise identity-provider tenant this organization's
    #: portal logins must come from (e.g. an Entra ID ``iss``/tenant id
    #: pair). Both nullable — an organization with no mapping set simply
    #: cannot resolve any portal session to itself yet; this is the
    #: actual anti-spoofing control (never an email domain, which is
    #: never consulted for this decision).
    oidc_issuer: Mapped[str | None] = mapped_column(String, nullable=True)
    oidc_tenant_id: Mapped[str | None] = mapped_column(String, nullable=True)


class OrganizationMemberRecord(Base):
    """One human's membership in one organization with one role (M6).

    A person holding two roles (e.g. ``admin`` and ``approver``) gets two
    rows, not a list column — this keeps "does X hold role Y for org Z"
    a trivial, directly-indexable query, matching this codebase's existing
    preference for explicit rows over packed fields. ``oidc_subject`` is
    the verified ``sub`` claim from that organization's mapped identity
    provider (see ``OrganizationRecord.oidc_issuer``/``oidc_tenant_id``) —
    never a self-asserted value.
    """

    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "oidc_subject", "role", name="uq_org_member_subject_role"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    oidc_subject: Mapped[str] = mapped_column(String, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TradingRelationshipRecord(Base):
    """A declared trading relationship between two organizations.

    This is the sole source of truth for "does org A have permission to
    discover org B's agents" (M2). ``registry-api`` never reads this table
    directly — it calls this service's ``GET /relationships`` endpoint,
    authenticated with a shared internal-service key, per the no-cross-
    service-database-access rule in docs/ARCHITECTURE.md.
    """

    __tablename__ = "trading_relationships"
    __table_args__ = (
        UniqueConstraint(
            "buyer_organization_id", "supplier_organization_id", name="uq_trading_relationship"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    buyer_organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    supplier_organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class OrganizationSigningKeyRecord(Base):
    """One organization's own Ed25519 *public* key (M5.1). network-api
    never stores or sees a private key — only the public half, which is
    enough to let any other organization verify a signature, never to
    forge one. ``GET /organizations/{id}/jwks.json`` serves every row
    with ``status in ("active", "rotating")`` and within its validity
    window as a standard JWKS; a revoked key is excluded immediately,
    not merely expired on schedule."""

    __tablename__ = "organization_signing_keys"
    __table_args__ = (UniqueConstraint("organization_id", "kid", name="uq_org_signing_key_kid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kid: Mapped[str] = mapped_column(String, nullable=False)
    public_key_jwk_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    not_before: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    not_after: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OrganizationKidNamespaceRecord(Base):
    """A pure namespace-reservation table (M8 slice 2, ADR-017) — holds no
    key material of its own. Both the root-key-rotation write path
    (``organization_signing_keys``) and the per-agent-delegation write
    path (``agent_signing_delegations``) insert one row here, in the
    same transaction as their own table's insert, before either commits.
    Its composite primary key is the one thing that makes the two
    keyspaces atomically, symmetrically collision-free: whichever write
    reaches the database first wins this row, and the loser's insert
    raises ``IntegrityError`` — turned into a deterministic 409 by the
    caller — regardless of which of the two write paths it came from.
    This is deliberately simpler than a row-level advisory lock: the
    unique constraint itself *is* the atomic check, with no separate
    lock-then-check-then-write sequence to get wrong."""

    __tablename__ = "organization_kid_namespace"

    organization_id: Mapped[str] = mapped_column(String, primary_key=True)
    kid: Mapped[str] = mapped_column(String, primary_key=True)
    #: "root" (organization_signing_keys) or "delegated"
    #: (agent_signing_delegations) — informational only, never used to
    #: bypass the uniqueness check itself.
    kind: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AgentSigningDelegationRecord(Base):
    """A per-agent signing-authority delegation (M8 slice 2, ADR-017).
    The signed JWS token (``delegation_jws``) is constructed and signed
    entirely inside the organization's own trust boundary — network-api
    never holds or sees a private key, only verifies this token against
    that organization's own currently-live root JWKS before storing it.
    ``revoked_at`` is deliberately *not* part of the signed claims (it is
    this service's own mutable, server-held fact about the token, not
    something the token asserts about itself) — see ADR-017's revocation
    section.

    ``(organization_id, delegated_kid)`` is permanently unique (never
    reused, even after revocation) — this is also what makes a
    revoke-then-republish-the-identical-token race safe: the original
    row still occupies that unique slot, so resubmitting the same token
    after revocation hits this constraint rather than resurrecting it.
    ``(organization_id, pubkey_thumbprint)`` is unique only among
    currently-*live* (unrevoked) rows, organization-wide across every
    agent — enforced by the partial unique index below — so the same
    key material can never be concurrently delegated to two different
    agents in one organization, while still allowing reuse once a prior
    delegation using that key has been explicitly revoked.
    """

    __tablename__ = "agent_signing_delegations"
    __table_args__ = (
        UniqueConstraint("organization_id", "delegated_kid", name="uq_org_delegated_kid"),
        # M8 slice 3 (ADR-018): a row stops blocking its key's thumbprint
        # from reuse when EITHER it is explicitly revoked (for cause,
        # unchanged since M8 slice 2) OR its reservation has been
        # separately released (routine expiry + grace period, via
        # ``thumbprint_reservation_released_at`` -- never by converting
        # expiry into a revocation, which would conflate two different
        # lifecycle meanings). Both conditions independently free the
        # slot; neither implies the other.
        Index(
            "uq_org_live_pubkey_thumbprint",
            "organization_id",
            "pubkey_thumbprint",
            unique=True,
            postgresql_where=text(
                "revoked_at IS NULL AND thumbprint_reservation_released_at IS NULL"
            ),
            sqlite_where=text("revoked_at IS NULL AND thumbprint_reservation_released_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    delegated_kid: Mapped[str] = mapped_column(String, nullable=False)
    delegated_pubkey_jwk_json: Mapped[str] = mapped_column(Text, nullable=False)
    pubkey_thumbprint: Mapped[str] = mapped_column(String, nullable=False)
    issuing_kid: Mapped[str] = mapped_column(String, nullable=False)
    audience: Mapped[str] = mapped_column(String, nullable=False)
    #: JSON: {"artifact_types": [...], "protocol_version": "..."}
    scope_json: Mapped[str] = mapped_column(Text, nullable=False)
    jti: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    delegation_jws: Mapped[str] = mapped_column(Text, nullable=False)
    not_before: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    not_after: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: M8 slice 3 (ADR-018 section 5): deliberately SEPARATE from
    #: ``revoked_at``. Set only by the reservation-release job, once a
    #: delegation is past ``not_after`` plus a configured grace period
    #: -- this is "the slot is free to be reused by a different agent,"
    #: never "this delegation was revoked for cause." Backfilled NULL
    #: (slot held) for every pre-existing row -- never backfilled as
    #: already-released, which would be exactly the silent-free failure
    #: this design forbids.
    thumbprint_reservation_released_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )


class KeyLifecycleEventRecord(Base):
    """Append-only ledger of every root-key and delegation lifecycle
    event (M8 slice 3, ADR-018 section 3) -- the one legitimate source
    for historical-evidence queries. Retains the full key material or
    delegation token itself (``key_material``), not just a status flag,
    so a historical artifact can actually be re-verified cryptographically
    against period-correct material, not merely have its status looked
    up.

    Two timestamps, not one: ``effective_time`` is when the event
    semantically took effect (a key's own ``not_after`` for an expiry
    event; the moment an operator chose for a revocation); ``recorded_at``
    is when THIS service's own process appended the row, which can
    legitimately be later -- a cleanup job may notice an expiry well
    after it actually happened. Nothing updates or deletes a row here
    once written; this service's own code never issues an UPDATE or
    DELETE against this table (enforced at the application layer --
    see ``docs/DECISIONS.md`` ADR-018 for the stated limitation that
    this is a trusted-operator record, not a tamper-proof external
    timestamping authority).

    A ``migration_snapshot`` event describes only CURRENT state as
    observed at migration time -- it is never a reconstructed historical
    ``issued``/``revoked`` event with a backdated timestamp."""

    __tablename__ = "key_lifecycle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kid: Mapped[str] = mapped_column(String, nullable=False, index=True)
    #: "root" or "delegated" -- which keyspace this event is about.
    kind: Mapped[str] = mapped_column(String, nullable=False)
    #: "issued" | "rotating" | "expired" | "revoked" | "reservation_released" | "migration_snapshot"
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    effective_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    #: The full public-key JWK (root) or the full signed delegation JWS
    #: (delegated) -- retained permanently so a historical artifact can
    #: still be cryptographically re-verified after this key/delegation
    #: is no longer live.
    key_material: Mapped[str] = mapped_column(Text, nullable=False)
    #: True only for a ``migration_snapshot`` row -- the historical-
    #: evidence evaluator must never treat this event as reaching any
    #: time before ``recorded_at``.
    is_reconstructed_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class KeyEventAccessLogRecord(Base):
    """Who queried which organization's historical key-event ledger,
    and when (M8 slice 3, ADR-018 section 3) -- the sensitivity of
    historical evidence justifies knowing who has been looking at it,
    independent of the admin-key boundary that already gates the read
    itself."""

    __tablename__ = "key_event_access_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    queried_organization_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    queried_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    #: This reference deployment authenticates every admin-boundary call
    #: with one shared global key (unchanged pre-existing limitation,
    #: ADR-016's same class of issue) -- so this can only ever record
    #: "the admin boundary was used," never a specific caller identity.
    #: Named here plainly rather than implying a stronger guarantee.
    caller_identifier: Mapped[str] = mapped_column(String, nullable=False, default="admin")
