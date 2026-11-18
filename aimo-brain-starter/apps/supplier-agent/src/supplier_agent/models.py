"""supplier-agent's own private ORM models (supplier-owned database only).

``SupplierTaskRecord`` is this agent's own business-level task log —
distinct from, and in addition to, the A2A SDK's own internal task
persistence (``a2a_sdk_tasks`` table, owned entirely by
``a2a.server.tasks.DatabaseTaskStore`` against this same database). This
table is what "Both agents independently record their task history" (M1
requirement 4) is backed by at the business layer: it is queryable with
our own ORM without depending on A2A SDK internals, and it never contains
any buyer-owned data.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SupplierTaskRecord(Base):
    __tablename__ = "supplier_tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, nullable=False)
    skill_id: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="supplier")
    counterparty_agent_id: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SupplierNegotiationRecord(Base):
    """One row per negotiation this supplier has received (M4). See the
    matching docstring on ``buyer_agent.models.BuyerNegotiationRecord`` —
    identical shape, independently persisted, never joined across the
    two services."""

    __tablename__ = "supplier_negotiations"

    negotiation_id: Mapped[str] = mapped_column(String, primary_key=True)
    counterparty_organization_id: Mapped[str] = mapped_column(String, nullable=False)
    counterparty_agent_id: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    current_proposal_id: Mapped[str] = mapped_column(String, nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SupplierProposalRecord(Base):
    """One immutable row per proposal version. See the matching docstring
    on ``buyer_agent.models.BuyerProposalRecord``."""

    __tablename__ = "supplier_proposals"

    proposal_id: Mapped[str] = mapped_column(String, primary_key=True)
    negotiation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    supersedes_proposal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SupplierProcessedArtifactRecord(Base):
    """Inbound-message deduplication (replay safety): one row per inbound
    artifact_id this supplier has already acted on. A replayed artifact_id
    (buyer retry after a timeout, network duplicate) returns the cached
    ``response_payload_json`` instead of being reprocessed — this is what
    makes the supplier's side of the exchange replay-safe, per
    ``docs/PROTOCOLS.md`` rule 5 ("receiving agents maintain deduplication
    records")."""

    __tablename__ = "supplier_processed_artifacts"

    artifact_id: Mapped[str] = mapped_column(String, primary_key=True)
    negotiation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    response_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
