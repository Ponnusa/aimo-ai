"""buyer-agent's own private ORM models (buyer-owned database only).

``BuyerTaskRecord`` is this agent's own business-level task log — distinct
from, and in addition to, the A2A SDK's own internal task persistence
(``a2a_sdk_tasks`` table, owned entirely by
``a2a.server.tasks.DatabaseTaskStore`` against this same database). This
table is what "Both agents independently record their task history" (M1
requirement 4) is backed by at the business layer.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class BuyerTaskRecord(Base):
    __tablename__ = "buyer_tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    skill_id: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="buyer")
    counterparty_agent_id: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BuyerNegotiationRecord(Base):
    """One row per negotiation this buyer has started (M4).

    ``negotiation_id`` doubles as the artifact envelope's ``transaction_id``
    — see ``food_wholesale`` schemas. ``state`` is a
    ``trade_protocol.TradeState`` value; only ``REQUESTED``, ``PROPOSED``,
    ``NEGOTIATING``, ``PENDING_AUTHORIZATION`` and the terminal
    ``REJECTED``/``EXPIRED``/``CANCELLED`` states are ever written here —
    nothing in this table or the code that writes it ever requests
    ``AUTHORIZED`` or later (that is M5 scope, after independent policy
    checks that do not exist yet).
    """

    __tablename__ = "buyer_negotiations"

    negotiation_id: Mapped[str] = mapped_column(String, primary_key=True)
    counterparty_organization_id: Mapped[str] = mapped_column(String, nullable=False)
    counterparty_agent_id: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    current_proposal_id: Mapped[str] = mapped_column(String, nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BuyerProposalRecord(Base):
    """One immutable row per proposal version exchanged within a
    negotiation (RFQ, quote, counterproposal, accept/reject, proposed
    agreement). ``payload_json`` is the full validated artifact, stored as
    text (``json.dumps``) per this codebase's existing convention — see
    ``registry_api.app``."""

    __tablename__ = "buyer_proposals"

    proposal_id: Mapped[str] = mapped_column(String, primary_key=True)
    negotiation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    supersedes_proposal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BuyerApprovalRecord(Base):
    """A durable human-approval request/decision (M5), bound to one exact
    ``negotiation_id``+``content_hash``. Any new proposal content_hash for
    the same agreement needs its own row — an old row is never reused or
    silently reinterpreted for different terms (docs/SECURITY.md)."""

    __tablename__ = "buyer_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    organization_id: Mapped[str] = mapped_column(String, nullable=False)
    approver_id: Mapped[str | None] = mapped_column(String, nullable=True)
    #: The dev-OIDC-shaped token's ``iss`` claim (always prefixed
    #: ``dev-oidc:`` — see ``buyer_agent.dev_oidc``), recorded for audit
    #: so it is always visible that no real identity provider was used.
    approver_issuer: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    policy_decision_json: Mapped[str] = mapped_column(Text, nullable=False)
    audit_trail_json: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BuyerAuthorizationEvidenceRecord(Base):
    """One organization's independent policy decision for one exact
    ``content_hash`` (M5): ``source="own"`` is this organization's own
    decision; ``source="counterparty"`` is the supplier's, received and
    credential-verified over A2A — never trusted from the payload alone.
    Unique per (negotiation_id, content_hash, source): a different hash
    needs a new row, never an update to this one."""

    __tablename__ = "buyer_authorization_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    organization_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    allow: Mapped[bool] = mapped_column(Boolean, nullable=False)
    human_approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    deny_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    policy_input_digest: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BuyerOrderRecord(Base):
    """Buyer's own outbox row for a commit attempt (M5). ``idempotency_key``
    is UNIQUE: writing this row (state=COMMITTING) happens in the same
    local transaction as the decision to attempt a commit, before any
    network call — so a crash after this write but before a response is
    always recoverable by retrying with the same key, never by assuming
    success or failure."""

    __tablename__ = "buyer_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    external_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BuyerSeenMessageProofRecord(Base):
    """Replay protection for every inbound, proof-signed artifact (M5.1):
    one row per signed message's ``jti``. ``jti`` is UNIQUE — a resent
    artifact with the exact same ``jti`` is rejected even if its
    signature is still otherwise cryptographically valid. Covers every
    artifact type, not just the M5 authorization/commitment ones,
    unlike M5's old per-record HMAC credential checks."""

    __tablename__ = "buyer_seen_message_proofs"

    jti: Mapped[str] = mapped_column(String, primary_key=True)
    negotiation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    organization_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
