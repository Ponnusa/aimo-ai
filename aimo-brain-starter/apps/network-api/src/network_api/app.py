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
from pydantic import BaseModel
from sqlalchemy import select

from database import new_engine, new_session_factory
from identity import issue_service_credential
from network_api.models import (
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
ALLOWED_SERVICE_CREDENTIAL_SCOPES = frozenset(
    {
        AGENT_MANAGEMENT_SCOPE,
        BUYER_TOOLS_SCOPE,
        SUPPLIER_TOOLS_SCOPE,
        EXECUTE_ORDER_SCOPE,
    }
)


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


class RelationshipIn(BaseModel):
    buyer_organization_id: str
    supplier_organization_id: str


class RelationshipOut(BaseModel):
    id: int
    buyer_organization_id: str
    supplier_organization_id: str
    status: str


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
            await session.commit()
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

    return app


app = create_app()
