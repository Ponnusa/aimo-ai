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

from sqlalchemy import DateTime, Integer, String, Text
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
