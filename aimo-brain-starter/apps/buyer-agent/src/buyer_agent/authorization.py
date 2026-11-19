"""Buyer-side independent policy authorization and human approval (M5).

Owns ``buyer_approvals``/``buyer_authorization_evidence`` — this
organization's own, independently-evaluated OPA decision and (if
required) human approval for one exact ``content_hash``. Never reads or
writes supplier-agent's database; the supplier's own evidence is only
ever accepted here after cryptographic verification of its agent-scoped
``commit`` credential (:func:`record_counterparty_evidence`) — the
payload's ``organization_id``/``sender_agent_id`` fields alone are never
sufficient (CLAUDE.md M5, closing the gap M4's ADR-010 left: payload-only
sender/organization binding).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from food_wholesale import agreement_terms_from_proposed_agreement, policy_bundle_dir, policy_query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from authorization import evaluate_binding_action
from buyer_agent.models import (
    BuyerApprovalRecord,
    BuyerAuthorizationEvidenceRecord,
    BuyerProposalRecord,
)
from buyer_agent.negotiation_store import NegotiationNotFoundError, get_negotiation
from buyer_agent.settings import BuyerAgentSettings
from identity import ServiceCredentialClaims, ServiceCredentialError, verify_service_credential

ROLE = "buyer"
#: A denied-for-approval-pending evidence is never persisted as the
#: finalized "own" row — see :func:`evaluate_and_request_approval`.
APPROVAL_PENDING_REASON = "human_approval_pending"


class StaleEvidenceError(Exception):
    """Raised when counterparty evidence references a different
    content_hash than this negotiation's current proposal — never
    silently accepted as authorizing a different agreement."""


class UnverifiedCounterpartyError(Exception):
    """Raised when the credential presented alongside counterparty
    evidence does not cryptographically match the organization/agent
    this negotiation actually belongs to."""


class ApprovalNotFoundError(Exception):
    pass


class ApprovalNotPendingError(Exception):
    """Raised when a decision is attempted on an approval that is not
    (or no longer) PENDING — already decided, expired, or invalidated."""


class UnauthorizedApproverError(Exception):
    """Raised when the presented credential is not a valid, matching
    ``approve``-scoped credential for this approval's organization and
    (if the approval already names one) approver."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class AuthorizationOutcome:
    allow: bool
    human_approval_required: bool
    deny_reason: str | None
    approval_id: int | None
    approval_status: str | None
    content_hash: str
    policy_version: str
    policy_input_digest: str


async def evaluate_and_request_approval(
    session_factory: async_sessionmaker,
    *,
    settings: BuyerAgentSettings,
    negotiation_id: str,
) -> AuthorizationOutcome:
    """Evaluate this organization's own OPA policy against the exact
    terms of ``negotiation_id``'s current ``proposed_agreement``. Pure
    deny -> finalizes an ``allow=False`` evidence row immediately (no
    approval needed to reject). Allow + no approval required ->
    finalizes an ``allow=True`` evidence row immediately. Allow +
    approval required -> creates (or returns the existing) durable
    approval request and does **not** finalize evidence until
    :func:`decide_approval` grants it.

    Idempotent: calling this again for the same negotiation/content_hash
    returns the existing outcome rather than re-evaluating or
    duplicating rows.
    """
    negotiation = await get_negotiation(session_factory, negotiation_id)
    if negotiation is None:
        raise NegotiationNotFoundError(negotiation_id)

    async with session_factory() as session:
        proposal = await session.get(BuyerProposalRecord, negotiation.current_proposal_id)
    if proposal is None or proposal.artifact_type != "proposed_agreement":
        raise ValueError(
            f"Negotiation {negotiation_id!r} has no current proposed_agreement to authorize."
        )
    artifact = json.loads(proposal.payload_json)
    content_hash_value = artifact["content_hash"]

    existing_evidence = await _get_evidence(
        session_factory,
        negotiation_id=negotiation_id,
        content_hash=content_hash_value,
        source="own",
    )
    if existing_evidence is not None:
        return AuthorizationOutcome(
            allow=existing_evidence.allow,
            human_approval_required=existing_evidence.human_approval_required,
            deny_reason=existing_evidence.deny_reason,
            approval_id=None,
            approval_status="APPROVED" if existing_evidence.allow else None,
            content_hash=content_hash_value,
            policy_version=existing_evidence.policy_version,
            policy_input_digest=existing_evidence.policy_input_digest,
        )

    existing_approval = await _get_approval(
        session_factory, negotiation_id=negotiation_id, content_hash=content_hash_value
    )
    if existing_approval is not None:
        return AuthorizationOutcome(
            allow=True,
            human_approval_required=True,
            deny_reason=None,
            approval_id=existing_approval.id,
            approval_status=existing_approval.status,
            content_hash=content_hash_value,
            policy_version=existing_approval.policy_version,
            policy_input_digest=json.loads(existing_approval.policy_decision_json)["input_digest"],
        )

    terms = agreement_terms_from_proposed_agreement(artifact)
    decision = evaluate_binding_action(
        action="authorize_agreement",
        organization_id=settings.buyer_organization_id,
        counterparty_organization_id=negotiation.counterparty_organization_id,
        agreement_id=negotiation_id,
        content_hash=content_hash_value,
        terms=terms,
        bundle_dir=policy_bundle_dir(ROLE, settings.buyer_policy_version),
        query=policy_query(ROLE, settings.buyer_policy_version),
        policy_version=settings.buyer_policy_version,
        opa_binary=settings.opa_binary,
    )

    if not decision.allow:
        await _finalize_own_evidence(
            session_factory,
            negotiation_id=negotiation_id,
            content_hash=content_hash_value,
            organization_id=settings.buyer_organization_id,
            agent_id=settings.buyer_agent_id,
            allow=False,
            human_approval_required=decision.human_approval_required,
            deny_reason=decision.deny_reason,
            policy_version=decision.policy_version,
            policy_input_digest=decision.input_digest,
        )
        return AuthorizationOutcome(
            allow=False,
            human_approval_required=decision.human_approval_required,
            deny_reason=decision.deny_reason,
            approval_id=None,
            approval_status=None,
            content_hash=content_hash_value,
            policy_version=decision.policy_version,
            policy_input_digest=decision.input_digest,
        )

    if not decision.human_approval_required:
        await _finalize_own_evidence(
            session_factory,
            negotiation_id=negotiation_id,
            content_hash=content_hash_value,
            organization_id=settings.buyer_organization_id,
            agent_id=settings.buyer_agent_id,
            allow=True,
            human_approval_required=False,
            deny_reason=None,
            policy_version=decision.policy_version,
            policy_input_digest=decision.input_digest,
        )
        return AuthorizationOutcome(
            allow=True,
            human_approval_required=False,
            deny_reason=None,
            approval_id=None,
            approval_status=None,
            content_hash=content_hash_value,
            policy_version=decision.policy_version,
            policy_input_digest=decision.input_digest,
        )

    approval_id = await _create_approval(
        session_factory,
        negotiation_id=negotiation_id,
        content_hash=content_hash_value,
        organization_id=settings.buyer_organization_id,
        policy_version=decision.policy_version,
        policy_input_digest=decision.input_digest,
    )
    return AuthorizationOutcome(
        allow=True,
        human_approval_required=True,
        deny_reason=None,
        approval_id=approval_id,
        approval_status="PENDING",
        content_hash=content_hash_value,
        policy_version=decision.policy_version,
        policy_input_digest=decision.input_digest,
    )


async def _get_evidence(
    session_factory: async_sessionmaker, *, negotiation_id: str, content_hash: str, source: str
) -> BuyerAuthorizationEvidenceRecord | None:
    async with session_factory() as session:
        result = await session.execute(
            select(BuyerAuthorizationEvidenceRecord).where(
                BuyerAuthorizationEvidenceRecord.negotiation_id == negotiation_id,
                BuyerAuthorizationEvidenceRecord.content_hash == content_hash,
                BuyerAuthorizationEvidenceRecord.source == source,
            )
        )
        return result.scalar_one_or_none()


async def _get_approval(
    session_factory: async_sessionmaker, *, negotiation_id: str, content_hash: str
) -> BuyerApprovalRecord | None:
    async with session_factory() as session:
        result = await session.execute(
            select(BuyerApprovalRecord).where(
                BuyerApprovalRecord.negotiation_id == negotiation_id,
                BuyerApprovalRecord.content_hash == content_hash,
            )
        )
        return result.scalar_one_or_none()


async def _create_approval(
    session_factory: async_sessionmaker,
    *,
    negotiation_id: str,
    content_hash: str,
    organization_id: str,
    policy_version: str,
    policy_input_digest: str,
    ttl: timedelta = timedelta(hours=24),
) -> int:
    now = _now()
    async with session_factory() as session:
        record = BuyerApprovalRecord(
            negotiation_id=negotiation_id,
            content_hash=content_hash,
            organization_id=organization_id,
            approver_id=None,
            status="PENDING",
            policy_version=policy_version,
            policy_decision_json=json.dumps(
                {
                    "allow": True,
                    "human_approval_required": True,
                    "input_digest": policy_input_digest,
                }
            ),
            audit_trail_json=json.dumps([{"event": "requested", "at": now.isoformat()}]),
            requested_at=now,
            decided_at=None,
            expires_at=now + ttl,
        )
        session.add(record)
        await session.commit()
        return record.id


async def _finalize_own_evidence(
    session_factory: async_sessionmaker,
    *,
    negotiation_id: str,
    content_hash: str,
    organization_id: str,
    agent_id: str,
    allow: bool,
    human_approval_required: bool,
    deny_reason: str | None,
    policy_version: str,
    policy_input_digest: str,
) -> None:
    async with session_factory() as session:
        session.add(
            BuyerAuthorizationEvidenceRecord(
                negotiation_id=negotiation_id,
                content_hash=content_hash,
                source="own",
                organization_id=organization_id,
                agent_id=agent_id,
                allow=allow,
                human_approval_required=human_approval_required,
                deny_reason=deny_reason,
                policy_version=policy_version,
                policy_input_digest=policy_input_digest,
                created_at=_now(),
            )
        )
        await session.commit()


async def decide_approval(
    session_factory: async_sessionmaker,
    *,
    settings: BuyerAgentSettings,
    approval_id: int,
    decision: str,
    approver_credential: str,
) -> BuyerApprovalRecord:
    """Only an authenticated, ``approve``-scoped credential holder may
    approve or reject. ``decision`` is ``"APPROVED"`` or ``"REJECTED"``.
    Approving finalizes the "own" evidence row at this exact
    content_hash; any later proposal with a *different* content_hash
    needs its own, brand-new approval — this one can never be reused."""
    try:
        claims: ServiceCredentialClaims = verify_service_credential(
            approver_credential,
            signing_secret=settings.service_credential_signing_secret,
            required_scope="approve",
        )
    except ServiceCredentialError as exc:
        raise UnauthorizedApproverError(str(exc)) from exc

    async with session_factory() as session:
        record = await session.get(BuyerApprovalRecord, approval_id)
        if record is None:
            raise ApprovalNotFoundError(approval_id)
        if claims.organization_id != record.organization_id:
            raise UnauthorizedApproverError(
                "Approver credential is not scoped to this approval's organization."
            )
        if record.status != "PENDING":
            raise ApprovalNotPendingError(
                f"Approval {approval_id} is {record.status}, not PENDING."
            )
        if _now() > record.expires_at:
            record.status = "EXPIRED"
            await session.merge(record)
            await session.commit()
            raise ApprovalNotPendingError(f"Approval {approval_id} has expired.")

        now = _now()
        record.status = decision
        record.approver_id = claims.approver_id
        record.decided_at = now
        audit_trail = json.loads(record.audit_trail_json)
        audit_trail.append(
            {"event": decision.lower(), "approver_id": claims.approver_id, "at": now.isoformat()}
        )
        record.audit_trail_json = json.dumps(audit_trail)
        await session.merge(record)
        await session.commit()

    if decision == "APPROVED":
        policy_decision = json.loads(record.policy_decision_json)
        await _finalize_own_evidence(
            session_factory,
            negotiation_id=record.negotiation_id,
            content_hash=record.content_hash,
            organization_id=record.organization_id,
            agent_id=settings.buyer_agent_id,
            allow=True,
            human_approval_required=True,
            deny_reason=None,
            policy_version=record.policy_version,
            policy_input_digest=policy_decision["input_digest"],
        )
    return record


async def get_own_evidence(
    session_factory: async_sessionmaker, *, negotiation_id: str, content_hash: str
) -> BuyerAuthorizationEvidenceRecord | None:
    return await _get_evidence(
        session_factory, negotiation_id=negotiation_id, content_hash=content_hash, source="own"
    )


async def record_counterparty_evidence(
    session_factory: async_sessionmaker,
    *,
    settings: BuyerAgentSettings,
    negotiation_id: str,
    content_hash: str,
    artifact: dict[str, Any],
) -> BuyerAuthorizationEvidenceRecord:
    """Verify the supplier's agent-scoped ``commit`` credential (carried
    inside ``artifact["payload"]["credential"]`` — it must survive a
    synchronous Task-artifact reply, which has no sender message
    metadata of its own) and the artifact's own content_hash before
    trusting it as the supplier's evidence. Never trusts
    ``artifact["organization_id"]``/``artifact["sender_agent_id"]`` alone
    (CLAUDE.md M5)."""
    negotiation = await get_negotiation(session_factory, negotiation_id)
    if negotiation is None:
        raise NegotiationNotFoundError(negotiation_id)

    bearer_credential = artifact["payload"].get("credential")
    if bearer_credential is None:
        raise UnverifiedCounterpartyError("No credential presented with evidence.")
    try:
        claims = verify_service_credential(
            bearer_credential,
            signing_secret=settings.service_credential_signing_secret,
            required_scope="commit",
        )
    except ServiceCredentialError as exc:
        raise UnverifiedCounterpartyError(str(exc)) from exc

    if (
        claims.organization_id != negotiation.counterparty_organization_id
        or claims.agent_id != negotiation.counterparty_agent_id
    ):
        raise UnverifiedCounterpartyError(
            "Credential does not match this negotiation's counterparty organization/agent."
        )

    payload = artifact["payload"]
    if payload["content_hash"] != content_hash or payload["agreement_id"] != negotiation_id:
        raise StaleEvidenceError(
            "Counterparty evidence references a different content_hash or agreement_id."
        )

    existing = await _get_evidence(
        session_factory,
        negotiation_id=negotiation_id,
        content_hash=content_hash,
        source="counterparty",
    )
    # Already recorded as allowed: nothing to update, and the exact
    # answer never changes for a fixed content_hash once allowed. A
    # not-yet-allowed existing row (e.g. the counterparty's human
    # approval was still pending) is refreshed in place below, since a
    # later message for the *same* content_hash can legitimately report
    # a newer decision (approval granted since).
    if existing is not None and existing.allow:
        return existing

    async with session_factory() as session:
        if existing is not None:
            record = await session.get(BuyerAuthorizationEvidenceRecord, existing.id)
        else:
            record = BuyerAuthorizationEvidenceRecord(
                negotiation_id=negotiation_id, content_hash=content_hash, source="counterparty"
            )
        record.organization_id = claims.organization_id
        record.agent_id = claims.agent_id
        record.allow = payload["allow"]
        record.human_approval_required = payload["human_approval_required"]
        record.deny_reason = payload.get("deny_reason")
        record.policy_version = payload["policy_version"]
        record.policy_input_digest = payload["policy_input_digest"]
        record.created_at = _now()
        await session.merge(record)
        await session.commit()
        return record


async def is_mutually_authorized(
    session_factory: async_sessionmaker, *, negotiation_id: str, content_hash: str
) -> bool:
    """True only once both this organization's own evidence and the
    counterparty's evidence exist for the exact same content_hash and
    both allow the agreement. Never true from one side alone."""
    own = await _get_evidence(
        session_factory, negotiation_id=negotiation_id, content_hash=content_hash, source="own"
    )
    counterparty = await _get_evidence(
        session_factory,
        negotiation_id=negotiation_id,
        content_hash=content_hash,
        source="counterparty",
    )
    return (
        own is not None
        and counterparty is not None
        and own.allow is True
        and counterparty.allow is True
    )
