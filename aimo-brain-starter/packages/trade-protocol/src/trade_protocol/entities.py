"""Role-neutral core entities.

Per CLAUDE.md: "Buyer and Supplier are roles, not hardcoded network
primitives." Nothing in this module mentions "buyer" or "supplier"; a role
is just a string capability/relationship attribute. These are plain
Pydantic models — transport/storage shape, not ORM models — used to
validate payloads crossing process boundaries (A2A artifacts, registry
API responses). Each service persists its own ORM representation derived
from these, per ``docs/ARCHITECTURE.md``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Organization(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: str
    display_name: str
    verified: bool = False


class Agent(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: str
    organization_id: str
    role: str  # e.g. "buyer", "supplier" — a value, never a type
    endpoint_url: str
    agent_card_version: str


class Capability(BaseModel):
    model_config = ConfigDict(frozen=True)

    capability_id: str
    agent_id: str
    skill_id: str  # e.g. "food.request_quote"
    domain: str  # e.g. "food-wholesale"


class Relationship(BaseModel):
    model_config = ConfigDict(frozen=True)

    relationship_id: str
    organization_a_id: str
    organization_b_id: str
    trusted: bool = False
    revoked: bool = False


class Task(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    correlation_id: str
    domain: str
    state: str
    created_at: datetime


class Proposal(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposal_id: str
    task_id: str
    version: int
    content_hash: str
    expires_at: datetime


class Agreement(BaseModel):
    model_config = ConfigDict(frozen=True)

    agreement_id: str
    proposal_id: str
    accepted_content_hash: str
    idempotency_key: str


class Authorization(BaseModel):
    model_config = ConfigDict(frozen=True)

    authorization_id: str
    scope: str
    approver_id: str
    decision: str  # "approved" | "denied"
    policy_version: str
    expires_at: datetime


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    task_id: str
    kind: str
    reference: str
    recorded_at: datetime
