"""Buyer-agent's own management API (M6, docs/ARCHITECTURE.md's M6
section).

A second, additive FastAPI router mounted on the same app as the A2A
routes (``buyer_agent.app.create_app``) — never a second service, never
a new database. Every endpoint here is authenticated with a
``manage_organization``-scoped service credential minted by network-api
for this agent's own organization; the portal is this router's only
intended caller, and it only ever calls the buyer-agent that is its own
organization's agent — there is no cross-tenant row to leak because this
agent only ever holds its own organization's data, unchanged from
M0–M5.1.

Every state-changing endpoint here calls straight into the same
``authorization``/``commitment`` functions a live A2A request already
uses — this router adds no second way to reach ``AUTHORIZED`` or
``COMMITTED``, only a read/decide surface over what those functions
already do.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from food_wholesale import list_policy_versions, policy_bundle_dir, policy_query
from pydantic import BaseModel
from sqlalchemy import select

from authorization import evaluate_binding_action
from buyer_agent.authorization import (
    ApprovalNotFoundError,
    ApprovalNotPendingError,
    UnauthorizedApproverError,
    decide_approval,
)
from buyer_agent.models import (
    BuyerActivePolicyPointerRecord,
    BuyerApprovalRecord,
    BuyerAuthorizationEvidenceRecord,
    BuyerNegotiationRecord,
    BuyerOrderReconciliationEventRecord,
    BuyerOrderRecord,
)
from buyer_agent.settings import BuyerAgentSettings
from identity import ServiceCredentialClaims, ServiceCredentialError, verify_service_credential

MANAGE_ORGANIZATION_SCOPE = "manage_organization"
ROLE = "buyer"


class NegotiationOut(BaseModel):
    negotiation_id: str
    counterparty_organization_id: str
    counterparty_agent_id: str
    state: str
    round_number: int
    created_at: str
    updated_at: str


class ApprovalOut(BaseModel):
    id: int
    negotiation_id: str
    content_hash: str
    status: str
    policy_version: str
    requested_at: str
    expires_at: str
    decided_at: str | None


class DecideApprovalIn(BaseModel):
    decision: str
    approval_token: str


class EvidenceOut(BaseModel):
    negotiation_id: str
    content_hash: str
    source: str
    allow: bool
    human_approval_required: bool
    deny_reason: str | None
    policy_version: str
    created_at: str


class OrderOut(BaseModel):
    negotiation_id: str
    content_hash: str
    idempotency_key: str
    state: str
    external_order_id: str | None
    attempts: int
    last_error: str | None = None


class IntegrationHealthOut(BaseModel):
    """M7: order-state health as this organization's own durable
    `buyer_orders` outbox sees it."""

    committing_orders: int
    reconciliation_required_orders: int
    failed_orders: int
    confirmed_orders: int
    total_orders: int


class ReconciliationEventOut(BaseModel):
    order_id: int
    negotiation_id: str
    previous_state: str
    new_state: str
    detail: str
    created_at: str


class PolicyCatalogOut(BaseModel):
    role: str
    active_version: str
    available_versions: list[str]


class PolicyDryRunIn(BaseModel):
    policy_version: str
    counterparty_organization_id: str
    terms: dict[str, Any]


class PolicyDryRunOut(BaseModel):
    allow: bool
    human_approval_required: bool
    deny_reason: str | None


class PolicyActivateIn(BaseModel):
    policy_version: str
    activated_by: str


def create_management_router(*, settings: BuyerAgentSettings, session_factory) -> APIRouter:
    router = APIRouter(prefix="/management", tags=["management"])

    def _require_credential(authorization: str | None) -> ServiceCredentialClaims:
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer service credential.")
        token = authorization.removeprefix("Bearer ")
        try:
            claims = verify_service_credential(
                token,
                signing_secret=settings.service_credential_signing_secret,
                required_scope=MANAGE_ORGANIZATION_SCOPE,
            )
        except ServiceCredentialError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if claims.organization_id != settings.buyer_organization_id:
            raise HTTPException(
                status_code=403,
                detail="Credential does not grant management of this organization.",
            )
        return claims

    def _require_admin_credential(authorization: str | None) -> ServiceCredentialClaims:
        """Policy activation needs the portal to additionally have
        confirmed (server-side, against network-api's membership
        records) that the signed-in human holds the ``admin`` role —
        the portal encodes that by only including ``admin_action=true``
        on the credential it mints for this call. Simpler reference
        implementations could add a distinct scope; this one reuses
        ``manage_organization`` plus an explicit ``agent_id`` field,
        set to a fixed sentinel only the portal's admin-gated code path
        ever sets."""
        claims = _require_credential(authorization)
        if claims.agent_id != "portal-admin-action":
            raise HTTPException(
                status_code=403,
                detail="This action requires the portal's admin-confirmed credential.",
            )
        return claims

    @router.get("/negotiations", response_model=list[NegotiationOut])
    async def list_negotiations(
        state: str | None = None, authorization: str | None = Header(default=None)
    ) -> list[NegotiationOut]:
        _require_credential(authorization)
        async with session_factory() as session:
            query = select(BuyerNegotiationRecord)
            if state is not None:
                query = query.where(BuyerNegotiationRecord.state == state)
            rows = (await session.execute(query)).scalars().all()
        return [
            NegotiationOut(
                negotiation_id=row.negotiation_id,
                counterparty_organization_id=row.counterparty_organization_id,
                counterparty_agent_id=row.counterparty_agent_id,
                state=row.state,
                round_number=row.round_number,
                created_at=row.created_at.isoformat(),
                updated_at=row.updated_at.isoformat(),
            )
            for row in rows
        ]

    @router.get("/approvals", response_model=list[ApprovalOut])
    async def list_approvals(
        status: str | None = "PENDING", authorization: str | None = Header(default=None)
    ) -> list[ApprovalOut]:
        _require_credential(authorization)
        async with session_factory() as session:
            query = select(BuyerApprovalRecord)
            if status is not None and status != "ALL":
                query = query.where(BuyerApprovalRecord.status == status)
            rows = (await session.execute(query)).scalars().all()
        return [
            ApprovalOut(
                id=row.id,
                negotiation_id=row.negotiation_id,
                content_hash=row.content_hash,
                status=row.status,
                policy_version=row.policy_version,
                requested_at=row.requested_at.isoformat(),
                expires_at=row.expires_at.isoformat(),
                decided_at=row.decided_at.isoformat() if row.decided_at else None,
            )
            for row in rows
        ]

    @router.get("/approvals/{approval_id}", response_model=ApprovalOut)
    async def get_approval(
        approval_id: int, authorization: str | None = Header(default=None)
    ) -> ApprovalOut:
        _require_credential(authorization)
        async with session_factory() as session:
            row = await session.get(BuyerApprovalRecord, approval_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Approval not found.")
        return ApprovalOut(
            id=row.id,
            negotiation_id=row.negotiation_id,
            content_hash=row.content_hash,
            status=row.status,
            policy_version=row.policy_version,
            requested_at=row.requested_at.isoformat(),
            expires_at=row.expires_at.isoformat(),
            decided_at=row.decided_at.isoformat() if row.decided_at else None,
        )

    @router.post("/approvals/{approval_id}/decide", response_model=ApprovalOut)
    async def decide(
        approval_id: int,
        body: DecideApprovalIn,
        authorization: str | None = Header(default=None),
    ) -> ApprovalOut:
        """Revalidates everything server-side: the service credential
        above only proves "a legitimate portal backend for this
        organization is calling," never "this decision is authorized" —
        that proof is the ``approval_token`` itself, independently
        verified and revalidated inside ``decide_approval`` (see
        docs/SECURITY.md's M6 section)."""
        _require_credential(authorization)
        try:
            record = await decide_approval(
                session_factory,
                settings=settings,
                approval_id=approval_id,
                decision=body.decision,
                approval_token=body.approval_token,
            )
        except ApprovalNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ApprovalNotPendingError, UnauthorizedApproverError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ApprovalOut(
            id=record.id,
            negotiation_id=record.negotiation_id,
            content_hash=record.content_hash,
            status=record.status,
            policy_version=record.policy_version,
            requested_at=record.requested_at.isoformat(),
            expires_at=record.expires_at.isoformat(),
            decided_at=record.decided_at.isoformat() if record.decided_at else None,
        )

    @router.get("/evidence", response_model=list[EvidenceOut])
    async def list_evidence(authorization: str | None = Header(default=None)) -> list[EvidenceOut]:
        _require_credential(authorization)
        async with session_factory() as session:
            rows = (await session.execute(select(BuyerAuthorizationEvidenceRecord))).scalars().all()
        return [
            EvidenceOut(
                negotiation_id=row.negotiation_id,
                content_hash=row.content_hash,
                source=row.source,
                allow=row.allow,
                human_approval_required=row.human_approval_required,
                deny_reason=row.deny_reason,
                policy_version=row.policy_version,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ]

    @router.get("/orders", response_model=list[OrderOut])
    async def list_orders(
        state: str | None = None, authorization: str | None = Header(default=None)
    ) -> list[OrderOut]:
        _require_credential(authorization)
        async with session_factory() as session:
            query = select(BuyerOrderRecord)
            if state is not None:
                query = query.where(BuyerOrderRecord.state == state)
            rows = (await session.execute(query)).scalars().all()
        return [
            OrderOut(
                negotiation_id=row.negotiation_id,
                content_hash=row.content_hash,
                idempotency_key=row.idempotency_key,
                state=row.state,
                external_order_id=row.external_order_id,
                attempts=row.attempts,
                last_error=row.last_error,
            )
            for row in rows
        ]

    @router.get("/integration/health", response_model=IntegrationHealthOut)
    async def integration_health(
        authorization: str | None = Header(default=None),
    ) -> IntegrationHealthOut:
        _require_credential(authorization)
        async with session_factory() as session:
            rows = (await session.execute(select(BuyerOrderRecord))).scalars().all()
        return IntegrationHealthOut(
            committing_orders=sum(1 for r in rows if r.state == "COMMITTING"),
            reconciliation_required_orders=sum(
                1 for r in rows if r.state == "RECONCILIATION_REQUIRED"
            ),
            failed_orders=sum(1 for r in rows if r.state == "FAILED"),
            confirmed_orders=sum(1 for r in rows if r.state == "CONFIRMED"),
            total_orders=len(rows),
        )

    @router.get("/integration/reconciliation", response_model=list[ReconciliationEventOut])
    async def integration_reconciliation_history(
        authorization: str | None = Header(default=None),
    ) -> list[ReconciliationEventOut]:
        _require_credential(authorization)
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(BuyerOrderReconciliationEventRecord).order_by(
                            BuyerOrderReconciliationEventRecord.id.desc()
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [
            ReconciliationEventOut(
                order_id=row.order_id,
                negotiation_id=row.negotiation_id,
                previous_state=row.previous_state,
                new_state=row.new_state,
                detail=row.detail,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ]

    @router.get("/policy/catalog", response_model=PolicyCatalogOut)
    async def policy_catalog(authorization: str | None = Header(default=None)) -> PolicyCatalogOut:
        _require_credential(authorization)
        async with session_factory() as session:
            pointer = await session.get(
                BuyerActivePolicyPointerRecord, settings.buyer_organization_id
            )
        active_version = pointer.policy_version if pointer else settings.buyer_policy_version
        return PolicyCatalogOut(
            role=ROLE,
            active_version=active_version,
            available_versions=list_policy_versions(ROLE),
        )

    @router.post("/policy/dry-run", response_model=PolicyDryRunOut)
    async def policy_dry_run(
        body: PolicyDryRunIn, authorization: str | None = Header(default=None)
    ) -> PolicyDryRunOut:
        """Read-only: never writes an evidence/approval/order row,
        regardless of outcome."""
        _require_credential(authorization)
        decision = evaluate_binding_action(
            action="authorize_agreement",
            organization_id=settings.buyer_organization_id,
            counterparty_organization_id=body.counterparty_organization_id,
            agreement_id="dry-run",
            content_hash="dry-run",
            terms=body.terms,
            bundle_dir=policy_bundle_dir(ROLE, body.policy_version),
            query=policy_query(ROLE, body.policy_version),
            policy_version=body.policy_version,
            opa_binary=settings.opa_binary,
        )
        return PolicyDryRunOut(
            allow=decision.allow,
            human_approval_required=decision.human_approval_required,
            deny_reason=decision.deny_reason,
        )

    @router.post("/policy/activate", response_model=PolicyCatalogOut)
    async def policy_activate(
        body: PolicyActivateIn, authorization: str | None = Header(default=None)
    ) -> PolicyCatalogOut:
        """Admin-only (see ``_require_admin_credential``). Changes which
        on-disk bundle version is evaluated on the *next* authorization
        decision — never edits a bundle's contents, and never touches
        any already-finalized evidence."""
        _require_admin_credential(authorization)
        available = list_policy_versions(ROLE)
        if body.policy_version not in available:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown policy version {body.policy_version!r}. Available: {available}.",
            )
        async with session_factory() as session:
            pointer = await session.get(
                BuyerActivePolicyPointerRecord, settings.buyer_organization_id
            )
            now = datetime.now(UTC).replace(tzinfo=None)
            if pointer is None:
                session.add(
                    BuyerActivePolicyPointerRecord(
                        organization_id=settings.buyer_organization_id,
                        policy_version=body.policy_version,
                        updated_by=body.activated_by,
                        updated_at=now,
                    )
                )
            else:
                pointer.policy_version = body.policy_version
                pointer.updated_by = body.activated_by
                pointer.updated_at = now
            await session.commit()
        return PolicyCatalogOut(
            role=ROLE,
            active_version=body.policy_version,
            available_versions=available,
        )

    return router
