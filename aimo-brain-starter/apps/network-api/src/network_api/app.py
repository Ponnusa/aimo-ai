"""network-api FastAPI application.

Organization identities, organization-level trading relationships, and the
service-credential minting endpoint that lets an organization authorize
registry-api to manage its own agents (M2 — see docs/DECISIONS.md
ADR-008). This service never reaches into registry-api's database or
buyer/supplier-agent's databases, and registry-api never reaches into
this one's: the only crossing point is this service's own HTTP API,
authenticated with a shared internal-service key for the read-only
relationship lookup registry-api needs for discovery.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Header, HTTPException
from message_integrity import DelegationError, KeyNotFoundError
from message_integrity.delegations import delegated_pubkey_thumbprint, verify_delegation_token
from message_integrity.jwks import JwksResolver
from message_integrity.keys import public_key_from_jwk
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import new_engine, new_session_factory
from identity import issue_service_credential
from network_api.models import (
    AgentSigningDelegationRecord,
    OrganizationKidNamespaceRecord,
    OrganizationMemberRecord,
    OrganizationRecord,
    OrganizationSigningKeyRecord,
    TradingRelationshipRecord,
)
from network_api.settings import get_settings

#: Every service credential minted by this endpoint grants exactly one of
#: these actions. A broader "manage anything" scope is deliberately not
#: offered. ``manage_agents`` (M2) authorizes registry-api agent
#: management; ``use_buyer_tools``/``use_supplier_tools`` (M3) authorize
#: calling that organization's own MCP tool server only — see
#: docs/DECISIONS.md ADR-009.
AGENT_MANAGEMENT_SCOPE = "manage_agents"
BUYER_TOOLS_SCOPE = "use_buyer_tools"
SUPPLIER_TOOLS_SCOPE = "use_supplier_tools"
#: supplier-mcp's own narrow execution-tool scope (M5), separate from
#: the read-only ``use_supplier_tools`` scope. Organization-scoped only
#: (no agent_id binding) — this credential never crosses an
#: organization boundary, unlike the M5 ``commit``/``negotiate``/
#: ``approve`` scopes, which M5.1 removed in favor of per-artifact JWS
#: signatures and a dev-OIDC-shaped approval token (see
#: docs/DECISIONS.md ADR-012). The shared HMAC signing secret backing
#: this mechanism remains appropriate here because it is intra-
#: organization (an agent calling its own org's MCP server), not
#: cross-organization.
EXECUTE_ORDER_SCOPE = "execute_order"
#: M6: the portal's own backend calls each organization's buyer-agent/
#: supplier-agent management API with a credential scoped this way —
#: intra-organization only (the portal always acts on behalf of the
#: organization that is itself logged in), never crossing an
#: organization boundary, so the shared-HMAC mechanism remains the
#: right tool here exactly as it is for ``execute_order``.
MANAGE_ORGANIZATION_SCOPE = "manage_organization"
ALLOWED_SERVICE_CREDENTIAL_SCOPES = frozenset(
    {
        AGENT_MANAGEMENT_SCOPE,
        BUYER_TOOLS_SCOPE,
        SUPPLIER_TOOLS_SCOPE,
        EXECUTE_ORDER_SCOPE,
        MANAGE_ORGANIZATION_SCOPE,
    }
)

#: M6 organization-membership roles. Not cumulative tiers — a member can
#: hold more than one (one row per role; see ``OrganizationMemberRecord``).
ORGANIZATION_MEMBER_ROLES = frozenset({"admin", "operator", "approver", "auditor"})


class OrganizationOut(BaseModel):
    organization_id: str
    display_name: str
    verified: bool


class HealthOut(BaseModel):
    status: str
    service: str


class ServiceCredentialRequest(BaseModel):
    ttl_seconds: int = 3600
    scope: str = AGENT_MANAGEMENT_SCOPE
    #: M6: the only agent_id this endpoint will ever bind to a
    #: credential. The portal sets it, after independently confirming
    #: (against this service's own ``organization_members`` table) that
    #: the signed-in human holds the ``admin`` role, so an agent's
    #: management API can require this exact sentinel on
    #: admin-only actions (policy activation) without reopening M5's
    #: general-purpose agent_id-bound credential mechanism that M5.1
    #: removed. Rejected for every other scope and every other value.
    agent_id: str | None = None


class ServiceCredentialOut(BaseModel):
    token: str
    organization_id: str
    scope: str
    expires_in_seconds: int


class SigningKeyIn(BaseModel):
    kid: str
    public_key_jwk: dict[str, str]
    validity_days: int = 365


class SigningKeyOut(BaseModel):
    organization_id: str
    kid: str
    status: str
    not_before: str
    not_after: str


class MemberIn(BaseModel):
    oidc_subject: str
    email: str
    display_name: str
    role: str


class MemberOut(BaseModel):
    id: int
    organization_id: str
    oidc_subject: str
    email: str
    display_name: str
    role: str
    status: str


class MemberLookupOut(BaseModel):
    organization_id: str
    oidc_subject: str
    role: str
    active: bool


class OidcMappingIn(BaseModel):
    oidc_issuer: str
    oidc_tenant_id: str


class OidcMappingOut(BaseModel):
    organization_id: str
    oidc_issuer: str
    oidc_tenant_id: str


class OrganizationByTenantOut(BaseModel):
    organization_id: str
    display_name: str


class RelationshipIn(BaseModel):
    buyer_organization_id: str
    supplier_organization_id: str


class RelationshipOut(BaseModel):
    id: int
    buyer_organization_id: str
    supplier_organization_id: str
    status: str


class DelegationPublishIn(BaseModel):
    #: The compact JWS the organization constructed and signed entirely
    #: within its own trust boundary (M8 slice 2, ADR-017). network-api
    #: never signs this and never receives a private key — it only
    #: cryptographically verifies this token against the organization's
    #: own currently-live root JWKS before storing and serving it.
    jws: str


class DelegationOut(BaseModel):
    organization_id: str
    agent_id: str
    delegated_kid: str
    audience: str
    scope: dict[str, object]
    jti: str
    not_before: str
    not_after: str
    status: str


class DelegationsListOut(BaseModel):
    delegations: list[str]


class _LocalOrganizationRootKeyResolver(JwksResolver):
    """A :class:`message_integrity.jwks.JwksResolver` backed directly by
    this service's own database — used only for network-api's own
    publish-time verification of a submitted delegation, never over
    HTTP (network-api is the one party that already has direct access
    to ``organization_signing_keys``, so looping back through its own
    ``/jwks.json`` endpoint would be pointless indirection)."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def invalidate(self, organization_id: str) -> None:
        return None

    async def get_key(self, *, organization_id: str, kid: str, force_fresh: bool = False):
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(OrganizationSigningKeyRecord).where(
                        OrganizationSigningKeyRecord.organization_id == organization_id,
                        OrganizationSigningKeyRecord.kid == kid,
                        OrganizationSigningKeyRecord.status != "revoked",
                        OrganizationSigningKeyRecord.not_before <= now,
                        OrganizationSigningKeyRecord.not_after >= now,
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            raise KeyNotFoundError(
                f"No valid root key {kid!r} for organization {organization_id!r}."
            )
        return public_key_from_jwk(json.loads(row.public_key_jwk_json))


def _require_admin(x_admin_key: str | None, *, expected: str) -> None:
    if x_admin_key is None or x_admin_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing admin key.")


def _require_internal_service(x_internal_service_key: str | None, *, expected: str) -> None:
    if x_internal_service_key is None or x_internal_service_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing internal service key.")


def create_app() -> FastAPI:
    settings = get_settings()
    engine = new_engine(settings.network_database_url)
    session_factory = new_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="AIMO BRAIN Network API", version="0.1.0", lifespan=lifespan)

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        return HealthOut(status="ok", service="network-api")

    @app.get("/organizations", response_model=list[OrganizationOut])
    async def list_organizations() -> list[OrganizationOut]:
        async with session_factory() as session:
            rows = (await session.execute(select(OrganizationRecord))).scalars().all()
            return [
                OrganizationOut(
                    organization_id=row.organization_id,
                    display_name=row.display_name,
                    verified=row.verified,
                )
                for row in rows
            ]

    @app.post(
        "/organizations/{organization_id}/service-credentials",
        response_model=ServiceCredentialOut,
    )
    async def issue_agent_management_credential(
        organization_id: str,
        body: ServiceCredentialRequest,
        x_admin_key: str | None = Header(default=None),
    ) -> ServiceCredentialOut:
        """Mint a short-lived credential an organization's own operator
        uses to authorize one scoped action on that organization's behalf:
        managing its agents with registry-api (``manage_agents``), or
        calling its own buyer/supplier MCP tool server
        (``use_buyer_tools``/``use_supplier_tools``, M3 — see
        docs/DECISIONS.md ADR-009).

        Protected by a shared admin key, not human OIDC — see the field
        docstring on ``network_admin_api_key``. A real deployment replaces
        this with an authenticated organization-admin session before M6.
        """
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        if body.scope not in ALLOWED_SERVICE_CREDENTIAL_SCOPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown scope {body.scope!r}. "
                    f"Allowed: {sorted(ALLOWED_SERVICE_CREDENTIAL_SCOPES)}."
                ),
            )
        if body.agent_id is not None and (
            body.scope != MANAGE_ORGANIZATION_SCOPE or body.agent_id != "portal-admin-action"
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "agent_id may only be 'portal-admin-action', and only for "
                    "'manage_organization'."
                ),
            )
        async with session_factory() as session:
            org = await session.get(OrganizationRecord, organization_id)
            if org is None:
                raise HTTPException(
                    status_code=404, detail=f"Organization {organization_id!r} does not exist."
                )

        token = issue_service_credential(
            organization_id=organization_id,
            scope=body.scope,
            signing_secret=settings.service_credential_signing_secret,
            ttl_seconds=body.ttl_seconds,
            agent_id=body.agent_id,
        )
        return ServiceCredentialOut(
            token=token,
            organization_id=organization_id,
            scope=body.scope,
            expires_in_seconds=body.ttl_seconds,
        )

    @app.post("/relationships", response_model=RelationshipOut)
    async def create_relationship(
        body: RelationshipIn,
        x_admin_key: str | None = Header(default=None),
    ) -> RelationshipOut:
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        async with session_factory() as session:
            for org_id in (body.buyer_organization_id, body.supplier_organization_id):
                if await session.get(OrganizationRecord, org_id) is None:
                    raise HTTPException(
                        status_code=404, detail=f"Organization {org_id!r} does not exist."
                    )

            existing = (
                await session.execute(
                    select(TradingRelationshipRecord).where(
                        TradingRelationshipRecord.buyer_organization_id
                        == body.buyer_organization_id,
                        TradingRelationshipRecord.supplier_organization_id
                        == body.supplier_organization_id,
                    )
                )
            ).scalar_one_or_none()
            now = datetime.now(UTC).replace(tzinfo=None)
            if existing is not None:
                existing.status = "active"
                existing.updated_at = now
                await session.commit()
                return RelationshipOut(
                    id=existing.id,
                    buyer_organization_id=existing.buyer_organization_id,
                    supplier_organization_id=existing.supplier_organization_id,
                    status=existing.status,
                )

            record = TradingRelationshipRecord(
                buyer_organization_id=body.buyer_organization_id,
                supplier_organization_id=body.supplier_organization_id,
                status="active",
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            await session.commit()
            return RelationshipOut(
                id=record.id,
                buyer_organization_id=record.buyer_organization_id,
                supplier_organization_id=record.supplier_organization_id,
                status=record.status,
            )

    async def _transition_relationship(relationship_id: int, *, new_status: str) -> RelationshipOut:
        async with session_factory() as session:
            record = await session.get(TradingRelationshipRecord, relationship_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Relationship not found.")
            record.status = new_status
            record.updated_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            return RelationshipOut(
                id=record.id,
                buyer_organization_id=record.buyer_organization_id,
                supplier_organization_id=record.supplier_organization_id,
                status=record.status,
            )

    @app.post("/relationships/{relationship_id}/suspend", response_model=RelationshipOut)
    async def suspend_relationship(
        relationship_id: int, x_admin_key: str | None = Header(default=None)
    ) -> RelationshipOut:
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        return await _transition_relationship(relationship_id, new_status="suspended")

    @app.post("/relationships/{relationship_id}/revoke", response_model=RelationshipOut)
    async def revoke_relationship(
        relationship_id: int, x_admin_key: str | None = Header(default=None)
    ) -> RelationshipOut:
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        return await _transition_relationship(relationship_id, new_status="revoked")

    @app.post("/relationships/{relationship_id}/reactivate", response_model=RelationshipOut)
    async def reactivate_relationship(
        relationship_id: int, x_admin_key: str | None = Header(default=None)
    ) -> RelationshipOut:
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        return await _transition_relationship(relationship_id, new_status="active")

    @app.get("/relationships", response_model=list[RelationshipOut])
    async def get_relationships(
        buyer_organization_id: str,
        supplier_organization_id: str,
        x_internal_service_key: str | None = Header(default=None),
    ) -> list[RelationshipOut]:
        """Internal, service-to-service only: registry-api calls this to
        decide whether a buyer org may discover a supplier org's agents.
        Never exposed to the public internet / never called by the portal.
        """
        _require_internal_service(
            x_internal_service_key, expected=settings.network_internal_service_key
        )
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TradingRelationshipRecord).where(
                            TradingRelationshipRecord.buyer_organization_id
                            == buyer_organization_id,
                            TradingRelationshipRecord.supplier_organization_id
                            == supplier_organization_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [
                RelationshipOut(
                    id=row.id,
                    buyer_organization_id=row.buyer_organization_id,
                    supplier_organization_id=row.supplier_organization_id,
                    status=row.status,
                )
                for row in rows
            ]

    @app.post("/organizations/{organization_id}/signing-keys", response_model=SigningKeyOut)
    async def register_signing_key(
        organization_id: str,
        body: SigningKeyIn,
        x_admin_key: str | None = Header(default=None),
    ) -> SigningKeyOut:
        """Register a *public* Ed25519 key (JWK, RFC 8037) for an
        organization (M5.1). network-api never receives or stores a
        private key — only ever this. The caller's own operator
        generates the keypair out of band (see
        ``scripts/generate_signing_key.py``) and registers only the
        public half here, protected by the same admin key as every
        other organization-management action.
        """
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        async with session_factory() as session:
            if await session.get(OrganizationRecord, organization_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"Organization {organization_id!r} does not exist."
                )
            existing = (
                await session.execute(
                    select(OrganizationSigningKeyRecord).where(
                        OrganizationSigningKeyRecord.organization_id == organization_id,
                        OrganizationSigningKeyRecord.kid == body.kid,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise HTTPException(
                    status_code=409, detail=f"Key id {body.kid!r} is already registered."
                )

            now = datetime.now(UTC).replace(tzinfo=None)
            record = OrganizationSigningKeyRecord(
                organization_id=organization_id,
                kid=body.kid,
                public_key_jwk_json=json.dumps(body.public_key_jwk),
                status="active",
                not_before=now,
                not_after=now + timedelta(days=body.validity_days),
                created_at=now,
                revoked_at=None,
            )
            session.add(record)
            # M8 slice 2 (ADR-017): both this write path and delegation
            # publication insert into the shared kid-namespace table in
            # the same transaction as their own row, before either
            # commits — the table's composite primary key is what makes
            # the two keyspaces atomically, symmetrically collision-free.
            session.add(
                OrganizationKidNamespaceRecord(
                    organization_id=organization_id, kid=body.kid, kind="root", created_at=now
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Key id {body.kid!r} collides with an existing root or "
                        f"delegated kid for this organization."
                    ),
                ) from exc
            return SigningKeyOut(
                organization_id=organization_id,
                kid=body.kid,
                status=record.status,
                not_before=record.not_before.isoformat(),
                not_after=record.not_after.isoformat(),
            )

    @app.post(
        "/organizations/{organization_id}/signing-keys/{kid}/revoke",
        response_model=SigningKeyOut,
    )
    async def revoke_signing_key(
        organization_id: str, kid: str, x_admin_key: str | None = Header(default=None)
    ) -> SigningKeyOut:
        """Immediate revocation: a revoked key is excluded from
        ``/jwks.json`` on the very next request, regardless of its
        ``not_after`` — the key compromise/rotation response this
        endpoint exists for cannot wait out a validity window."""
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        async with session_factory() as session:
            record = (
                await session.execute(
                    select(OrganizationSigningKeyRecord).where(
                        OrganizationSigningKeyRecord.organization_id == organization_id,
                        OrganizationSigningKeyRecord.kid == kid,
                    )
                )
            ).scalar_one_or_none()
            if record is None:
                raise HTTPException(status_code=404, detail="Signing key not found.")
            record.status = "revoked"
            record.revoked_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            return SigningKeyOut(
                organization_id=organization_id,
                kid=kid,
                status=record.status,
                not_before=record.not_before.isoformat(),
                not_after=record.not_after.isoformat(),
            )

    @app.get("/organizations/{organization_id}/jwks.json")
    async def get_jwks(organization_id: str) -> dict[str, list[dict[str, str]]]:
        """Public, unauthenticated: a standard JWKS (RFC 7517) of every
        currently valid (``active``/``rotating``, not revoked, within
        its validity window) key for this organization. This is the
        *only* cross-organization identity material network-api ever
        serves — losing this endpoint's data to an attacker lets them
        read public keys, never forge a signature."""
        now = datetime.now(UTC).replace(tzinfo=None)
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(OrganizationSigningKeyRecord).where(
                            OrganizationSigningKeyRecord.organization_id == organization_id,
                            OrganizationSigningKeyRecord.status != "revoked",
                            OrganizationSigningKeyRecord.not_before <= now,
                            OrganizationSigningKeyRecord.not_after >= now,
                        )
                    )
                )
                .scalars()
                .all()
            )
            return {"keys": [json.loads(row.public_key_jwk_json) for row in rows]}

    # --- Per-agent signing delegations (M8 slice 2, ADR-017) -------------

    @app.post("/organizations/{organization_id}/delegations", response_model=DelegationOut)
    async def publish_delegation(
        organization_id: str,
        body: DelegationPublishIn,
        x_admin_key: str | None = Header(default=None),
    ) -> DelegationOut:
        """Verify an organization-signed delegation token and store it.
        Authenticated by the same admin-key boundary that already
        governs root-key rotation (``/signing-keys``) — this is not a
        new trust surface, and does not touch the separate,
        still-closed shared-HMAC registry-authentication gate
        (ADR-016). network-api never signs anything here; it only
        verifies ``body.jws`` against this organization's own current
        root JWKS and, if valid, persists it."""
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        async with session_factory() as session:
            if await session.get(OrganizationRecord, organization_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"Organization {organization_id!r} does not exist."
                )

        resolver = _LocalOrganizationRootKeyResolver(session_factory)
        try:
            verified = await verify_delegation_token(body.jws, jwks_resolver=resolver)
        except DelegationError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid delegation token: {exc}") from exc

        if verified.organization_id != organization_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Delegation issuer {verified.organization_id!r} does not match "
                    f"the organization {organization_id!r} this was submitted to."
                ),
            )

        delegated_kid = verified.delegated_kid
        delegated_pubkey_jwk = verified.delegated_pubkey_jwk
        thumbprint = delegated_pubkey_thumbprint(delegated_pubkey_jwk)

        now = datetime.now(UTC).replace(tzinfo=None)
        async with session_factory() as session:
            existing_by_kid = (
                await session.execute(
                    select(AgentSigningDelegationRecord).where(
                        AgentSigningDelegationRecord.organization_id == organization_id,
                        AgentSigningDelegationRecord.delegated_kid == delegated_kid,
                    )
                )
            ).scalar_one_or_none()
            if existing_by_kid is not None:
                # Permanently unique, by design (ADR-017) — this is also
                # what makes a revoke-then-republish-the-identical-token
                # race safe: the original row still occupies this slot.
                raise HTTPException(
                    status_code=409,
                    detail=f"Delegated kid {delegated_kid!r} is already registered for this "
                    "organization and cannot be reused, even by its original issuer.",
                )

            record = AgentSigningDelegationRecord(
                organization_id=organization_id,
                agent_id=verified.agent_id,
                delegated_kid=delegated_kid,
                delegated_pubkey_jwk_json=json.dumps(delegated_pubkey_jwk),
                pubkey_thumbprint=thumbprint,
                issuing_kid=verified.issuing_kid,
                audience=verified.audience,
                scope_json=json.dumps(verified.scope.to_claim()),
                jti=verified.jti,
                delegation_jws=body.jws,
                not_before=datetime.fromtimestamp(verified.issued_at, tz=UTC).replace(tzinfo=None),
                not_after=datetime.fromtimestamp(verified.expires_at, tz=UTC).replace(tzinfo=None),
                created_at=now,
                revoked_at=None,
            )
            session.add(record)
            session.add(
                OrganizationKidNamespaceRecord(
                    organization_id=organization_id,
                    kid=delegated_kid,
                    kind="delegated",
                    created_at=now,
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Delegated kid {delegated_kid!r} collides with an existing root "
                        f"or delegated kid, or key material {thumbprint!r} is already "
                        f"delegated to a different, currently-live agent in this "
                        f"organization."
                    ),
                ) from exc

            return DelegationOut(
                organization_id=organization_id,
                agent_id=record.agent_id,
                delegated_kid=delegated_kid,
                audience=record.audience,
                scope=json.loads(record.scope_json),
                jti=record.jti,
                not_before=record.not_before.isoformat(),
                not_after=record.not_after.isoformat(),
                status="active",
            )

    @app.get("/organizations/{organization_id}/delegations.json", response_model=DelegationsListOut)
    async def get_delegations(organization_id: str) -> DelegationsListOut:
        """Public, unauthenticated, exactly like ``/jwks.json``: every
        currently-live (unrevoked, within its validity window)
        delegation token for this organization, served as the original
        signed JWS strings — a resolver re-verifies each one
        cryptographically before trusting it; storing/serving this data
        never lets anyone forge a new delegation, only redistribute
        ones the organization itself already signed."""
        now = datetime.now(UTC).replace(tzinfo=None)
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentSigningDelegationRecord).where(
                            AgentSigningDelegationRecord.organization_id == organization_id,
                            AgentSigningDelegationRecord.revoked_at.is_(None),
                            AgentSigningDelegationRecord.not_before <= now,
                            AgentSigningDelegationRecord.not_after >= now,
                        )
                    )
                )
                .scalars()
                .all()
            )
            return DelegationsListOut(delegations=[row.delegation_jws for row in rows])

    @app.post(
        "/organizations/{organization_id}/delegations/{jti}/revoke", response_model=DelegationOut
    )
    async def revoke_delegation(
        organization_id: str, jti: str, x_admin_key: str | None = Header(default=None)
    ) -> DelegationOut:
        """Immediate revocation, mirroring ``/signing-keys/{kid}/revoke``:
        a revoked delegation is excluded from ``/delegations.json`` on
        the very next request. ``revoked_at`` lives here, as mutable
        server-held metadata — never inside the delegation's own signed
        claims (ADR-017)."""
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        async with session_factory() as session:
            record = (
                await session.execute(
                    select(AgentSigningDelegationRecord).where(
                        AgentSigningDelegationRecord.organization_id == organization_id,
                        AgentSigningDelegationRecord.jti == jti,
                    )
                )
            ).scalar_one_or_none()
            if record is None:
                raise HTTPException(status_code=404, detail="Delegation not found.")
            record.revoked_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            return DelegationOut(
                organization_id=organization_id,
                agent_id=record.agent_id,
                delegated_kid=record.delegated_kid,
                audience=record.audience,
                scope=json.loads(record.scope_json),
                jti=record.jti,
                not_before=record.not_before.isoformat(),
                not_after=record.not_after.isoformat(),
                status="revoked",
            )

    # --- Organization membership and roles (M6) -------------------------

    @app.post("/organizations/{organization_id}/members", response_model=MemberOut)
    async def add_member(
        organization_id: str,
        body: MemberIn,
        x_admin_key: str | None = Header(default=None),
    ) -> MemberOut:
        """Grant one role to one human for this organization. Protected
        by the same admin key as every other organization-management
        action in this reference deployment; the portal forwards this
        call only after independently confirming (via its own verified
        session) that the requester already holds the ``admin`` role for
        this organization — see docs/ARCHITECTURE.md's M6 section."""
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        if body.role not in ORGANIZATION_MEMBER_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown role {body.role!r}. Allowed: {sorted(ORGANIZATION_MEMBER_ROLES)}.",
            )
        async with session_factory() as session:
            if await session.get(OrganizationRecord, organization_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"Organization {organization_id!r} does not exist."
                )
            existing = (
                await session.execute(
                    select(OrganizationMemberRecord).where(
                        OrganizationMemberRecord.organization_id == organization_id,
                        OrganizationMemberRecord.oidc_subject == body.oidc_subject,
                        OrganizationMemberRecord.role == body.role,
                    )
                )
            ).scalar_one_or_none()
            now = datetime.now(UTC).replace(tzinfo=None)
            if existing is not None:
                existing.status = "active"
                existing.revoked_at = None
                existing.email = body.email
                existing.display_name = body.display_name
                await session.commit()
                record = existing
            else:
                record = OrganizationMemberRecord(
                    organization_id=organization_id,
                    oidc_subject=body.oidc_subject,
                    email=body.email,
                    display_name=body.display_name,
                    role=body.role,
                    status="active",
                    created_at=now,
                    revoked_at=None,
                )
                session.add(record)
                await session.commit()
            return MemberOut(
                id=record.id,
                organization_id=record.organization_id,
                oidc_subject=record.oidc_subject,
                email=record.email,
                display_name=record.display_name,
                role=record.role,
                status=record.status,
            )

    @app.post(
        "/organizations/{organization_id}/members/{member_id}/revoke", response_model=MemberOut
    )
    async def revoke_member(
        organization_id: str, member_id: int, x_admin_key: str | None = Header(default=None)
    ) -> MemberOut:
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        async with session_factory() as session:
            record = await session.get(OrganizationMemberRecord, member_id)
            if record is None or record.organization_id != organization_id:
                raise HTTPException(status_code=404, detail="Member not found.")
            record.status = "revoked"
            record.revoked_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            return MemberOut(
                id=record.id,
                organization_id=record.organization_id,
                oidc_subject=record.oidc_subject,
                email=record.email,
                display_name=record.display_name,
                role=record.role,
                status=record.status,
            )

    @app.get("/organizations/{organization_id}/members", response_model=list[MemberOut])
    async def list_members(
        organization_id: str, x_admin_key: str | None = Header(default=None)
    ) -> list[MemberOut]:
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(OrganizationMemberRecord).where(
                            OrganizationMemberRecord.organization_id == organization_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [
                MemberOut(
                    id=row.id,
                    organization_id=row.organization_id,
                    oidc_subject=row.oidc_subject,
                    email=row.email,
                    display_name=row.display_name,
                    role=row.role,
                    status=row.status,
                )
                for row in rows
            ]

    @app.get("/members/lookup", response_model=MemberLookupOut)
    async def lookup_member(
        organization_id: str,
        oidc_subject: str,
        role: str,
        x_internal_service_key: str | None = Header(default=None),
    ) -> MemberLookupOut:
        """Internal, service-to-service only: used both by the portal at
        login (resolving a verified identity's role for an organization)
        and by buyer-agent/supplier-agent to independently revalidate a
        portal-issued approval assertion's claimed approver before
        deciding an approval (docs/ARCHITECTURE.md's M6 section) — never
        trusting the assertion's own claim of an active role alone."""
        _require_internal_service(
            x_internal_service_key, expected=settings.network_internal_service_key
        )
        async with session_factory() as session:
            record = (
                await session.execute(
                    select(OrganizationMemberRecord).where(
                        OrganizationMemberRecord.organization_id == organization_id,
                        OrganizationMemberRecord.oidc_subject == oidc_subject,
                        OrganizationMemberRecord.role == role,
                    )
                )
            ).scalar_one_or_none()
            active = record is not None and record.status == "active"
            return MemberLookupOut(
                organization_id=organization_id,
                oidc_subject=oidc_subject,
                role=role,
                active=active,
            )

    # --- OIDC tenant mapping (M6) ----------------------------------------

    @app.post("/organizations/{organization_id}/oidc-mapping", response_model=OidcMappingOut)
    async def set_oidc_mapping(
        organization_id: str,
        body: OidcMappingIn,
        x_admin_key: str | None = Header(default=None),
    ) -> OidcMappingOut:
        """Bind this organization to exactly one identity-provider
        tenant. The portal resolves a logged-in human's organization
        from this mapping (issuer + tenant id from the validated ID
        token), never from an email domain."""
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        async with session_factory() as session:
            org = await session.get(OrganizationRecord, organization_id)
            if org is None:
                raise HTTPException(
                    status_code=404, detail=f"Organization {organization_id!r} does not exist."
                )
            org.oidc_issuer = body.oidc_issuer
            org.oidc_tenant_id = body.oidc_tenant_id
            await session.commit()
            return OidcMappingOut(
                organization_id=organization_id,
                oidc_issuer=body.oidc_issuer,
                oidc_tenant_id=body.oidc_tenant_id,
            )

    @app.get("/organizations/by-oidc-tenant", response_model=OrganizationByTenantOut)
    async def get_organization_by_oidc_tenant(
        oidc_issuer: str,
        oidc_tenant_id: str,
        x_internal_service_key: str | None = Header(default=None),
    ) -> OrganizationByTenantOut:
        _require_internal_service(
            x_internal_service_key, expected=settings.network_internal_service_key
        )
        async with session_factory() as session:
            org = (
                await session.execute(
                    select(OrganizationRecord).where(
                        OrganizationRecord.oidc_issuer == oidc_issuer,
                        OrganizationRecord.oidc_tenant_id == oidc_tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if org is None:
                raise HTTPException(
                    status_code=404,
                    detail="No organization is mapped to this identity-provider tenant.",
                )
            return OrganizationByTenantOut(
                organization_id=org.organization_id, display_name=org.display_name
            )

    # --- Organization-scoped relationship listing (M6) -------------------

    @app.get("/organizations/{organization_id}/relationships", response_model=list[RelationshipOut])
    async def list_relationships_for_organization(
        organization_id: str, x_internal_service_key: str | None = Header(default=None)
    ) -> list[RelationshipOut]:
        """Every relationship where ``organization_id`` is either side —
        for the portal's own trading-relationships page. Internal-
        service-key gated like the existing discovery lookup; the portal
        is a trusted backend caller, same as registry-api."""
        _require_internal_service(
            x_internal_service_key, expected=settings.network_internal_service_key
        )
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TradingRelationshipRecord).where(
                            (TradingRelationshipRecord.buyer_organization_id == organization_id)
                            | (
                                TradingRelationshipRecord.supplier_organization_id
                                == organization_id
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [
                RelationshipOut(
                    id=row.id,
                    buyer_organization_id=row.buyer_organization_id,
                    supplier_organization_id=row.supplier_organization_id,
                    status=row.status,
                )
                for row in rows
            ]

    return app


app = create_app()
