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
from network_api.credential_hash import generate_token, hash_token, verify_token
from network_api.models import (
    ORG_ADMIN_CREDENTIAL_SCOPES,
    AgentSigningDelegationRecord,
    KeyEventAccessLogRecord,
    KeyLifecycleEventRecord,
    OrganizationAdminCredentialRecord,
    OrganizationKidNamespaceRecord,
    OrganizationLifecycleAuditEventRecord,
    OrganizationMemberRecord,
    OrganizationRecord,
    OrganizationSigningKeyRecord,
    ServiceCredentialIssuanceRecord,
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


class IntrospectIn(BaseModel):
    token: str
    expected_organization_id: str | None = None
    expected_scope: str | None = None


class IntrospectOut(BaseModel):
    """ADR-019 section 4: only what a verifier legitimately needs —
    never a raw credential, never unrelated internal bookkeeping."""

    active: bool
    organization_id: str | None = None
    scope: str | None = None
    agent_id: str | None = None
    issued_at: str | None = None
    expires_at: str | None = None
    revoked: bool | None = None


class BootstrapExternalIn(BaseModel):
    pass


class BootstrapExternalOut(BaseModel):
    organization_id: str
    auth_mode: str
    admin_credential: str
    scopes: list[str]


class AdminCredentialRotateOut(BaseModel):
    organization_id: str
    admin_credential: str


class RecoveryActionOut(BaseModel):
    organization_id: str
    auth_mode: str
    detail: str


class CreateOrganizationIn(BaseModel):
    organization_id: str
    display_name: str


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


class KeyEventOut(BaseModel):
    organization_id: str
    kid: str
    kind: str
    event_type: str
    effective_time: str
    recorded_at: str
    is_reconstructed_snapshot: bool


class KeyEventsListOut(BaseModel):
    events: list[KeyEventOut]


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


def _require_operator(x_operator_key: str | None, *, expected: str) -> None:
    """ADR-019 section 3: the bootstrap/recovery authority. Deliberately
    never the same check as ``_require_admin`` or an organization's own
    scoped credential -- holding this key must never be confused with
    holding either of the authorities it arbitrates between."""
    if x_operator_key is None or x_operator_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing operator key.")


async def _resolve_org_authority(
    session,
    *,
    organization_id: str,
    required_scope: str,
    x_admin_key: str | None,
    authorization: str | None,
    settings,
) -> None:
    """ADR-019 section 1: the first thing every organization-management
    endpoint does, before inspecting any credential at all -- resolve
    this organization's server-owned ``auth_mode`` and enforce exactly
    one authority for it. An unrecognized/missing mode denies outright;
    it is never treated as "legacy_global_key" by default."""
    org = await session.get(OrganizationRecord, organization_id)
    if org is None:
        raise HTTPException(
            status_code=404, detail=f"Organization {organization_id!r} does not exist."
        )
    auth_mode = org.auth_mode
    if auth_mode == "legacy_global_key":
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        return
    if auth_mode == "org_scoped_credential":
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="This organization requires its own scoped admin credential, "
                "not the legacy admin key.",
            )
        token = authorization.removeprefix("Bearer ")
        record = (
            await session.execute(
                select(OrganizationAdminCredentialRecord).where(
                    OrganizationAdminCredentialRecord.organization_id == organization_id,
                    OrganizationAdminCredentialRecord.status == "active",
                )
            )
        ).scalar_one_or_none()
        if record is None or not verify_token(
            token, credential_hash=record.credential_hash, credential_salt=record.credential_salt
        ):
            raise HTTPException(status_code=401, detail="Invalid or revoked admin credential.")
        scopes = json.loads(record.scopes_json)
        if required_scope not in scopes:
            raise HTTPException(
                status_code=403,
                detail=f"This credential does not grant {required_scope!r}.",
            )
        return
    # Unrecognized/unset auth_mode: deny by default. Never fall back to
    # the historically-permissive legacy path (ADR-019 section 1).
    raise HTTPException(
        status_code=403,
        detail=f"Organization {organization_id!r} has no resolvable authentication mode.",
    )


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

    @app.post("/organizations", response_model=OrganizationOut, status_code=201)
    async def create_organization(
        body: CreateOrganizationIn,
        x_operator_key: str | None = Header(default=None),
    ) -> OrganizationOut:
        """ADR-019 section 3, step 1: creation is bootstrap/recovery
        authority only — never the global admin key, never any
        organization's own credential. Every new organization starts
        ``auth_mode="legacy_global_key"``; only ``bootstrap-external``
        below ever moves it to ``org_scoped_credential``."""
        _require_operator(x_operator_key, expected=settings.network_operator_key)
        async with session_factory() as session:
            if await session.get(OrganizationRecord, body.organization_id) is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Organization {body.organization_id!r} already exists.",
                )
            session.add(
                OrganizationRecord(
                    organization_id=body.organization_id,
                    display_name=body.display_name,
                    verified=False,
                    auth_mode="legacy_global_key",
                )
            )
            await session.commit()
        return OrganizationOut(
            organization_id=body.organization_id, display_name=body.display_name, verified=False
        )

    @app.post(
        "/organizations/{organization_id}/bootstrap-external",
        response_model=BootstrapExternalOut,
    )
    async def bootstrap_external(
        organization_id: str,
        x_operator_key: str | None = Header(default=None),
    ) -> BootstrapExternalOut:
        """ADR-019 sections 3 and 7: the ONLY code path that ever sets
        ``auth_mode="org_scoped_credential"``. Gated by BOTH the
        operator authority and ``external_onboarding_enabled`` — the
        master gate defaults false in every environment and must stay
        that way through this slice's own merge and CI; flipping it on
        is a separate, later deployment decision."""
        _require_operator(x_operator_key, expected=settings.network_operator_key)
        if not settings.external_onboarding_enabled:
            raise HTTPException(status_code=403, detail="External onboarding is disabled.")
        async with session_factory() as session:
            org = await session.get(OrganizationRecord, organization_id)
            if org is None:
                raise HTTPException(
                    status_code=404, detail=f"Organization {organization_id!r} does not exist."
                )
            if org.auth_mode == "org_scoped_credential":
                raise HTTPException(
                    status_code=409,
                    detail=f"Organization {organization_id!r} is already bootstrapped.",
                )
            org.auth_mode = "org_scoped_credential"
            raw_secret = generate_token()
            credential_hash, credential_salt = hash_token(raw_secret)
            now = datetime.now(UTC).replace(tzinfo=None)
            session.add(
                OrganizationAdminCredentialRecord(
                    organization_id=organization_id,
                    credential_hash=credential_hash,
                    credential_salt=credential_salt,
                    scopes_json=json.dumps(list(ORG_ADMIN_CREDENTIAL_SCOPES)),
                    status="active",
                    created_at=now,
                )
            )
            session.add(
                OrganizationLifecycleAuditEventRecord(
                    organization_id=organization_id,
                    event_type="bootstrapped_external",
                    detail="auth_mode set to org_scoped_credential; admin credential issued.",
                    created_at=now,
                )
            )
            await session.commit()
        return BootstrapExternalOut(
            organization_id=organization_id,
            auth_mode="org_scoped_credential",
            admin_credential=raw_secret,
            scopes=list(ORG_ADMIN_CREDENTIAL_SCOPES),
        )

    @app.post(
        "/organizations/{organization_id}/admin-credential/rotate",
        response_model=AdminCredentialRotateOut,
    )
    async def rotate_admin_credential(
        organization_id: str,
        authorization: str | None = Header(default=None),
    ) -> AdminCredentialRotateOut:
        """ADR-019 section 2: self-service — requires presenting the
        CURRENT valid credential. A revoked credential cannot be used to
        mint its own replacement; the old row is atomically marked
        revoked in the same transaction the new one is created in."""
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer admin credential.")
        token = authorization.removeprefix("Bearer ")
        async with session_factory() as session:
            record = (
                await session.execute(
                    select(OrganizationAdminCredentialRecord).where(
                        OrganizationAdminCredentialRecord.organization_id == organization_id,
                        OrganizationAdminCredentialRecord.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if record is None or not verify_token(
                token,
                credential_hash=record.credential_hash,
                credential_salt=record.credential_salt,
            ):
                raise HTTPException(status_code=401, detail="Invalid or revoked admin credential.")
            now = datetime.now(UTC).replace(tzinfo=None)
            record.status = "revoked"
            record.revoked_at = now
            record.revoked_reason = "self_rotated"
            new_secret = generate_token()
            credential_hash, credential_salt = hash_token(new_secret)
            session.add(
                OrganizationAdminCredentialRecord(
                    organization_id=organization_id,
                    credential_hash=credential_hash,
                    credential_salt=credential_salt,
                    scopes_json=record.scopes_json,
                    status="active",
                    created_at=now,
                )
            )
            await session.commit()
        return AdminCredentialRotateOut(
            organization_id=organization_id, admin_credential=new_secret
        )

    @app.post(
        "/organizations/{organization_id}/recovery/force-revoke-credential",
        response_model=RecoveryActionOut,
    )
    async def force_revoke_admin_credential(
        organization_id: str,
        x_operator_key: str | None = Header(default=None),
    ) -> RecoveryActionOut:
        """ADR-019 section 3: forced revocation never routes through the
        credential it revokes — a compromised or unresponsive holder
        cannot block this. Issues no replacement; a fresh bootstrap or
        an operator-assisted rotation is a separate, later action."""
        _require_operator(x_operator_key, expected=settings.network_operator_key)
        async with session_factory() as session:
            org = await session.get(OrganizationRecord, organization_id)
            if org is None:
                raise HTTPException(
                    status_code=404, detail=f"Organization {organization_id!r} does not exist."
                )
            now = datetime.now(UTC).replace(tzinfo=None)
            rows = (
                (
                    await session.execute(
                        select(OrganizationAdminCredentialRecord).where(
                            OrganizationAdminCredentialRecord.organization_id == organization_id,
                            OrganizationAdminCredentialRecord.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.status = "revoked"
                row.revoked_at = now
                row.revoked_reason = "operator_forced"
            session.add(
                OrganizationLifecycleAuditEventRecord(
                    organization_id=organization_id,
                    event_type="recovery_force_revoke",
                    detail=f"Forcibly revoked {len(rows)} active admin credential(s).",
                    created_at=now,
                )
            )
            await session.commit()
        return RecoveryActionOut(
            organization_id=organization_id,
            auth_mode=org.auth_mode,
            detail=f"Forcibly revoked {len(rows)} active admin credential(s).",
        )

    @app.post(
        "/organizations/{organization_id}/recovery/revert-to-legacy",
        response_model=RecoveryActionOut,
    )
    async def revert_to_legacy(
        organization_id: str,
        x_operator_key: str | None = Header(default=None),
    ) -> RecoveryActionOut:
        """ADR-019 section 3: an explicit, audited mode change — never an
        automatic consequence of the ``external_onboarding_enabled``
        flag (section 7). After this, the global key legitimately works
        for this one organization again, until it is bootstrapped
        forward a second time."""
        _require_operator(x_operator_key, expected=settings.network_operator_key)
        async with session_factory() as session:
            org = await session.get(OrganizationRecord, organization_id)
            if org is None:
                raise HTTPException(
                    status_code=404, detail=f"Organization {organization_id!r} does not exist."
                )
            now = datetime.now(UTC).replace(tzinfo=None)
            org.auth_mode = "legacy_global_key"
            rows = (
                (
                    await session.execute(
                        select(OrganizationAdminCredentialRecord).where(
                            OrganizationAdminCredentialRecord.organization_id == organization_id,
                            OrganizationAdminCredentialRecord.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.status = "revoked"
                row.revoked_at = now
                row.revoked_reason = "operator_forced"
            session.add(
                OrganizationLifecycleAuditEventRecord(
                    organization_id=organization_id,
                    event_type="recovery_revert_to_legacy",
                    detail="auth_mode reverted to legacy_global_key.",
                    created_at=now,
                )
            )
            await session.commit()
        return RecoveryActionOut(
            organization_id=organization_id,
            auth_mode="legacy_global_key",
            detail="auth_mode reverted to legacy_global_key.",
        )

    @app.post(
        "/organizations/{organization_id}/service-credentials",
        response_model=ServiceCredentialOut,
    )
    async def issue_agent_management_credential(
        organization_id: str,
        body: ServiceCredentialRequest,
        x_admin_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> ServiceCredentialOut:
        """Mint a short-lived credential an organization's own operator
        uses to authorize one scoped action on that organization's behalf:
        managing its agents with registry-api (``manage_agents``), or
        calling its own buyer/supplier MCP tool server
        (``use_buyer_tools``/``use_supplier_tools``, M3 — see
        docs/DECISIONS.md ADR-009).

        Authority is resolved per-organization (ADR-019 section 1): a
        ``legacy_global_key`` organization still uses the shared admin
        key exactly as before; an ``org_scoped_credential`` organization
        must present its own scoped credential instead, and the global
        key is rejected outright for it.

        For ``manage_agents`` specifically, the token minted is the new
        opaque, introspection-only reference (ADR-019 section 4) —
        registry-api never evaluates it locally. Every other scope keeps
        the existing self-contained HMAC format unchanged; this slice's
        forgery concern is specifically the one credential registry-api
        verifies without ever calling back to this service.
        """
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
            await _resolve_org_authority(
                session,
                organization_id=organization_id,
                required_scope="issue_service_credentials",
                x_admin_key=x_admin_key,
                authorization=authorization,
                settings=settings,
            )

            if body.scope == AGENT_MANAGEMENT_SCOPE:
                now = datetime.now(UTC).replace(tzinfo=None)
                reference_id = f"svc_{generate_token()}"
                raw_secret = generate_token()
                credential_hash, credential_salt = hash_token(raw_secret)
                session.add(
                    ServiceCredentialIssuanceRecord(
                        reference_id=reference_id,
                        credential_hash=credential_hash,
                        credential_salt=credential_salt,
                        organization_id=organization_id,
                        scope=body.scope,
                        agent_id=body.agent_id,
                        issued_at=now,
                        expires_at=now + timedelta(seconds=body.ttl_seconds),
                    )
                )
                await session.commit()
                token = f"{reference_id}.{raw_secret}"
            else:
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

    @app.post("/introspect", response_model=IntrospectOut)
    async def introspect(
        body: IntrospectIn,
        x_internal_service_key: str | None = Header(default=None),
    ) -> IntrospectOut:
        """ADR-019 section 4: the authoritative, live answer for an
        opaque ``manage_agents`` credential — never evaluated locally by
        any caller. Authenticates the CALLER (must hold this service's
        own internal service-to-service secret, distinct from both the
        global admin key and any organization's credential) before
        answering, returns only the fields a verifier actually needs
        (never raw bookkeeping), and never logs ``body.token`` — log
        statements reference ``reference_id`` only, extracted from the
        token's own prefix, never the secret half.

        Revocation-freshness bound: there is no cache here to go stale —
        the next introspection call always sees the current row. The
        residual risk is the same check-to-use race ADR-018 already
        named for delegations and explicitly declined to solve there: a
        credential introspected as valid a moment before a revocation
        lands can still complete whatever it was already authorizing.
        """
        _require_internal_service(
            x_internal_service_key, expected=settings.network_internal_service_key
        )
        try:
            reference_id, secret = body.token.split(".", 1)
        except ValueError:
            return IntrospectOut(active=False)
        async with session_factory() as session:
            record = await session.get(ServiceCredentialIssuanceRecord, reference_id)
        if record is None or not verify_token(
            secret, credential_hash=record.credential_hash, credential_salt=record.credential_salt
        ):
            return IntrospectOut(active=False)
        now = datetime.now(UTC).replace(tzinfo=None)
        revoked = record.revoked_at is not None
        expired = now >= record.expires_at
        matches_org = body.expected_organization_id is None or (
            body.expected_organization_id == record.organization_id
        )
        matches_scope = body.expected_scope is None or (body.expected_scope == record.scope)
        active = not revoked and not expired and matches_org and matches_scope
        return IntrospectOut(
            active=active,
            organization_id=record.organization_id,
            scope=record.scope,
            agent_id=record.agent_id,
            issued_at=record.issued_at.isoformat(),
            expires_at=record.expires_at.isoformat(),
            revoked=revoked,
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
        authorization: str | None = Header(default=None),
    ) -> SigningKeyOut:
        """Register a *public* Ed25519 key (JWK, RFC 8037) for an
        organization (M5.1). network-api never receives or stores a
        private key — only ever this. The caller's own operator
        generates the keypair out of band (see
        ``scripts/generate_signing_key.py``) and registers only the
        public half here. Authority resolved per-organization (ADR-019
        section 1): the global key for a ``legacy_global_key``
        organization, that organization's own ``rotate_signing_keys``-
        scoped credential otherwise.
        """
        async with session_factory() as session:
            await _resolve_org_authority(
                session,
                organization_id=organization_id,
                required_scope="rotate_signing_keys",
                x_admin_key=x_admin_key,
                authorization=authorization,
                settings=settings,
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
            # M8 slice 3 (ADR-018): the ledger append is atomic with the
            # lifecycle write it describes -- same transaction, same
            # commit-or-nothing outcome.
            session.add(
                KeyLifecycleEventRecord(
                    organization_id=organization_id,
                    kid=body.kid,
                    kind="root",
                    event_type="issued",
                    effective_time=now,
                    recorded_at=now,
                    key_material=record.public_key_jwk_json,
                    is_reconstructed_snapshot=False,
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
        organization_id: str,
        kid: str,
        x_admin_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> SigningKeyOut:
        """Immediate revocation: a revoked key is excluded from
        ``/jwks.json`` on the very next request, regardless of its
        ``not_after`` — the key compromise/rotation response this
        endpoint exists for cannot wait out a validity window."""
        async with session_factory() as session:
            await _resolve_org_authority(
                session,
                organization_id=organization_id,
                required_scope="rotate_signing_keys",
                x_admin_key=x_admin_key,
                authorization=authorization,
                settings=settings,
            )
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
            now = datetime.now(UTC).replace(tzinfo=None)
            record.status = "revoked"
            record.revoked_at = now
            session.add(
                KeyLifecycleEventRecord(
                    organization_id=organization_id,
                    kid=kid,
                    kind="root",
                    event_type="revoked",
                    effective_time=now,
                    recorded_at=now,
                    key_material=record.public_key_jwk_json,
                    is_reconstructed_snapshot=False,
                )
            )
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
        authorization: str | None = Header(default=None),
    ) -> DelegationOut:
        """Verify an organization-signed delegation token and store it.
        Authority resolved per-organization (ADR-019 section 1) — the
        same boundary that now also governs root-key rotation. Does not
        touch the separate, still-closed shared-HMAC registry-
        authentication gate (ADR-016). network-api never signs anything
        here; it only verifies ``body.jws`` against this organization's
        own current root JWKS and, if valid, persists it."""
        async with session_factory() as session:
            await _resolve_org_authority(
                session,
                organization_id=organization_id,
                required_scope="publish_delegations",
                x_admin_key=x_admin_key,
                authorization=authorization,
                settings=settings,
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
            session.add(
                KeyLifecycleEventRecord(
                    organization_id=organization_id,
                    kid=delegated_kid,
                    kind="delegated",
                    event_type="issued",
                    effective_time=now,
                    recorded_at=now,
                    key_material=body.jws,
                    is_reconstructed_snapshot=False,
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
        organization_id: str,
        jti: str,
        x_admin_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> DelegationOut:
        """Immediate revocation, mirroring ``/signing-keys/{kid}/revoke``:
        a revoked delegation is excluded from ``/delegations.json`` on
        the very next request. ``revoked_at`` lives here, as mutable
        server-held metadata — never inside the delegation's own signed
        claims (ADR-017)."""
        async with session_factory() as session:
            await _resolve_org_authority(
                session,
                organization_id=organization_id,
                required_scope="revoke_delegations",
                x_admin_key=x_admin_key,
                authorization=authorization,
                settings=settings,
            )
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
            now = datetime.now(UTC).replace(tzinfo=None)
            record.revoked_at = now
            session.add(
                KeyLifecycleEventRecord(
                    organization_id=organization_id,
                    kid=record.delegated_kid,
                    kind="delegated",
                    event_type="revoked",
                    effective_time=now,
                    recorded_at=now,
                    key_material=record.delegation_jws,
                    is_reconstructed_snapshot=False,
                )
            )
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

    @app.post(
        "/organizations/{organization_id}/delegations/{jti}/release-reservation",
        response_model=DelegationOut,
    )
    async def release_thumbprint_reservation(
        organization_id: str,
        jti: str,
        x_admin_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> DelegationOut:
        """M8 slice 3 (ADR-018 section 5): frees a delegation's key
        thumbprint for reuse by a DIFFERENT agent, once it is past its
        own ``not_after`` plus a grace period. Deliberately NEVER touches
        ``revoked_at`` -- a routine expiry is not a revocation, and
        conflating the two would erase the distinction the historical-
        evidence model (section 1) depends on. Fails closed: refuses
        (409) a delegation that is not yet past its grace period, so a
        failed/delayed run of this job always leaves the slot held,
        never silently frees it early."""
        now = datetime.now(UTC).replace(tzinfo=None)
        grace = timedelta(seconds=settings.thumbprint_reservation_grace_period_seconds)
        async with session_factory() as session:
            await _resolve_org_authority(
                session,
                organization_id=organization_id,
                required_scope="revoke_delegations",
                x_admin_key=x_admin_key,
                authorization=authorization,
                settings=settings,
            )
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
            if record.thumbprint_reservation_released_at is not None:
                # Idempotent no-op -- already released.
                pass
            elif now < record.not_after + grace:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Delegation {jti!r} is not yet past its expiry grace period; "
                        f"its thumbprint reservation cannot be released yet."
                    ),
                )
            else:
                record.thumbprint_reservation_released_at = now
                session.add(
                    KeyLifecycleEventRecord(
                        organization_id=organization_id,
                        kid=record.delegated_kid,
                        kind="delegated",
                        event_type="reservation_released",
                        effective_time=now,
                        recorded_at=now,
                        key_material=record.delegation_jws,
                        is_reconstructed_snapshot=False,
                    )
                )
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
                status="revoked" if record.revoked_at else "expired",
            )

    @app.get("/organizations/{organization_id}/key-events.json", response_model=KeyEventsListOut)
    async def get_key_events(
        organization_id: str, x_admin_key: str | None = Header(default=None)
    ) -> KeyEventsListOut:
        """The append-only historical-evidence ledger (M8 slice 3,
        ADR-018 section 3) -- never used by, or reachable from, this
        service's live JWKS/delegations endpoints above. Gated by the
        same admin-key boundary as other organization-management calls,
        plus its own access log: the sensitivity of historical evidence
        justifies knowing who has been looking at it, independent of
        the (unchanged, still global) admin-key boundary itself."""
        _require_admin(x_admin_key, expected=settings.network_admin_api_key)
        async with session_factory() as session:
            session.add(
                KeyEventAccessLogRecord(
                    queried_organization_id=organization_id,
                    queried_at=datetime.now(UTC).replace(tzinfo=None),
                    caller_identifier="admin",
                )
            )
            await session.commit()
            rows = (
                (
                    await session.execute(
                        select(KeyLifecycleEventRecord)
                        .where(KeyLifecycleEventRecord.organization_id == organization_id)
                        .order_by(KeyLifecycleEventRecord.id)
                    )
                )
                .scalars()
                .all()
            )
            return KeyEventsListOut(
                events=[
                    KeyEventOut(
                        organization_id=row.organization_id,
                        kid=row.kid,
                        kind=row.kind,
                        event_type=row.event_type,
                        effective_time=row.effective_time.isoformat(),
                        recorded_at=row.recorded_at.isoformat(),
                        is_reconstructed_snapshot=row.is_reconstructed_snapshot,
                    )
                    for row in rows
                ]
            )

    # --- Organization membership and roles (M6) -------------------------

    @app.post("/organizations/{organization_id}/members", response_model=MemberOut)
    async def add_member(
        organization_id: str,
        body: MemberIn,
        x_admin_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> MemberOut:
        """Grant one role to one human for this organization. Authority
        resolved per-organization (ADR-019 section 1): for a
        ``legacy_global_key`` organization, the portal forwards this
        call only after independently confirming (via its own verified
        session) that the requester already holds the ``admin`` role —
        see docs/ARCHITECTURE.md's M6 section; for an
        ``org_scoped_credential`` organization, its own ``manage_members``
        credential is required instead, and the global key is rejected.
        """
        if body.role not in ORGANIZATION_MEMBER_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown role {body.role!r}. Allowed: {sorted(ORGANIZATION_MEMBER_ROLES)}.",
            )
        async with session_factory() as session:
            await _resolve_org_authority(
                session,
                organization_id=organization_id,
                required_scope="manage_members",
                x_admin_key=x_admin_key,
                authorization=authorization,
                settings=settings,
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
        organization_id: str,
        member_id: int,
        x_admin_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> MemberOut:
        async with session_factory() as session:
            await _resolve_org_authority(
                session,
                organization_id=organization_id,
                required_scope="manage_members",
                x_admin_key=x_admin_key,
                authorization=authorization,
                settings=settings,
            )
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
