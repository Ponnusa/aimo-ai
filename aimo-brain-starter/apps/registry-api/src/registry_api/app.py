"""registry-api FastAPI application.

M2 scope: agent registration, Agent Card fetch/validation (SSRF-safe),
capability indexing, endpoint-ownership verification, lifecycle
(pending/active/suspended/revoked), capability-indexed discovery gated by
a trading relationship looked up from network-api, and an audit trail.

Registry-api never proxies an A2A conversation: discovery returns the
supplier's real A2A endpoint URL, and the buyer talks to it directly with
the official A2A SDK (see buyer_agent.client). Registry-api also never
reads another service's database — the one cross-service call it makes
(network-api's relationship lookup) is over HTTP, authenticated with a
shared internal-service key, never a database connection.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from registry_api.card_fetch import (
    CardFetchError,
    UnsafeCardUrlError,
    fetch_and_validate_agent_card,
    fetch_text_safely,
)
from registry_api.models import (
    RegistryAgentCapabilityRecord,
    RegistryAgentRecord,
    RegistryAuditEventRecord,
)
from registry_api.network_client import RelationshipLookupError, has_active_relationship
from registry_api.settings import get_settings
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database import new_engine
from identity import ServiceCredentialClaims, ServiceCredentialError, verify_service_credential

AGENT_MANAGEMENT_SCOPE = "manage_agents"
CHALLENGE_WELL_KNOWN_PATH = "/.well-known/aimo-brain-challenge"


class HealthOut(BaseModel):
    status: str
    service: str


class ReadyOut(BaseModel):
    status: str
    service: str
    database: str


class RegisterAgentIn(BaseModel):
    agent_id: str
    organization_id: str
    a2a_card_url: str
    display_name: str | None = None


class AgentManagementOut(BaseModel):
    agent_id: str
    organization_id: str
    display_name: str
    a2a_card_url: str
    status: str
    card_fingerprint: str
    capabilities: list[str]
    registered_at: datetime
    updated_at: datetime
    last_verified_at: datetime | None


class VerificationStartOut(BaseModel):
    agent_id: str
    challenge: str
    challenge_path: str
    expires_at: datetime


class DiscoveredAgentOut(BaseModel):
    """Minimal, public discovery projection. Deliberately excludes the
    raw Agent Card payload, the verification challenge and any credential
    material — docs/SECURITY.md requires discovery not leak private Agent
    Card metadata or credentials."""

    agent_id: str
    organization_id: str
    display_name: str
    status: str
    capabilities: list[str]
    a2a_endpoint_url: str


class AgentSenderVerificationOut(BaseModel):
    """M5.1: result of confirming a verified message's claimed sender is
    a registered, active agent with an active trading relationship to
    the counterparty verifying it."""

    agent_id: str
    organization_id: str
    status: str


def _canonical_fingerprint(card) -> str:
    skill_ids = sorted(skill.id for skill in card.skills)
    canonical = json.dumps({"name": card.name, "skills": skill_ids}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_app() -> FastAPI:
    settings = get_settings()
    engine = new_engine(settings.registry_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    def require_org_bearer(
        organization_id: str, authorization: str | None
    ) -> ServiceCredentialClaims:
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer service credential.")
        token = authorization.removeprefix("Bearer ")
        try:
            claims = verify_service_credential(
                token,
                signing_secret=settings.service_credential_signing_secret,
                required_scope=AGENT_MANAGEMENT_SCOPE,
            )
        except ServiceCredentialError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if claims.organization_id != organization_id:
            raise HTTPException(
                status_code=403,
                detail="Credential does not grant management of this organization's agents.",
            )
        return claims

    async def _audit(
        session: AsyncSession,
        *,
        event_type: str,
        agent_id: str | None,
        organization_id: str | None,
        detail: dict,
    ) -> None:
        session.add(
            RegistryAuditEventRecord(
                event_type=event_type,
                agent_id=agent_id,
                organization_id=organization_id,
                detail=json.dumps(detail),
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )

    async def _get_owned_agent(
        session: AsyncSession, agent_id: str, organization_id: str
    ) -> RegistryAgentRecord:
        record = await session.get(RegistryAgentRecord, agent_id)
        if record is None or record.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="Agent not found for this organization.")
        return record

    async def _capabilities_for(session: AsyncSession, agent_id: str) -> list[str]:
        rows = (
            (
                await session.execute(
                    select(RegistryAgentCapabilityRecord.skill_id).where(
                        RegistryAgentCapabilityRecord.agent_id == agent_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    def _management_out(record: RegistryAgentRecord, capabilities: list[str]) -> AgentManagementOut:
        return AgentManagementOut(
            agent_id=record.agent_id,
            organization_id=record.organization_id,
            display_name=record.display_name,
            a2a_card_url=record.a2a_card_url,
            status=record.status,
            card_fingerprint=record.card_fingerprint,
            capabilities=capabilities,
            registered_at=record.registered_at,
            updated_at=record.updated_at,
            last_verified_at=record.last_verified_at,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="AIMO BRAIN Registry API", version="0.1.0", lifespan=lifespan)

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        return HealthOut(status="ok", service="registry-api")

    @app.get("/ready", response_model=ReadyOut)
    async def ready() -> ReadyOut:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return ReadyOut(status="ok", service="registry-api", database="reachable")

    @app.post("/agents", response_model=AgentManagementOut)
    async def register_agent(
        body: RegisterAgentIn, authorization: str | None = Header(default=None)
    ) -> AgentManagementOut:
        require_org_bearer(body.organization_id, authorization)

        async with session_factory() as session:
            existing = await session.get(RegistryAgentRecord, body.agent_id)
            if existing is not None and existing.organization_id != body.organization_id:
                raise HTTPException(
                    status_code=409,
                    detail="This agent_id is already registered to a different organization.",
                )

            try:
                card = await fetch_and_validate_agent_card(
                    body.a2a_card_url,
                    allow_private_network_targets=(
                        settings.registry_allow_private_network_card_targets
                    ),
                    fetch_timeout_seconds=settings.registry_card_fetch_timeout_seconds,
                )
            except UnsafeCardUrlError as exc:
                raise HTTPException(status_code=400, detail=f"Unsafe card URL: {exc}") from exc
            except CardFetchError as exc:
                raise HTTPException(
                    status_code=400, detail=f"Could not fetch/validate Agent Card: {exc}"
                ) from exc

            fingerprint = _canonical_fingerprint(card)
            now = datetime.now(UTC).replace(tzinfo=None)
            display_name = body.display_name or card.name
            is_new = existing is None

            record = existing or RegistryAgentRecord(
                agent_id=body.agent_id,
                organization_id=body.organization_id,
                display_name=display_name,
                a2a_card_url=body.a2a_card_url,
                status="pending",
                card_fingerprint=fingerprint,
                registered_at=now,
                updated_at=now,
            )
            if not is_new:
                record.display_name = display_name
                record.a2a_card_url = body.a2a_card_url
                record.card_fingerprint = fingerprint
                record.status = "pending"  # re-registration requires re-verification
                record.last_verified_at = None
                record.updated_at = now
            session.add(record)

            await session.execute(
                RegistryAgentCapabilityRecord.__table__.delete().where(
                    RegistryAgentCapabilityRecord.agent_id == body.agent_id
                )
            )
            for skill in card.skills:
                session.add(
                    RegistryAgentCapabilityRecord(
                        agent_id=body.agent_id, skill_id=skill.id, skill_name=skill.name
                    )
                )

            await _audit(
                session,
                event_type="agent.registered" if is_new else "agent.re_registered",
                agent_id=body.agent_id,
                organization_id=body.organization_id,
                detail={"a2a_card_url": body.a2a_card_url, "fingerprint": fingerprint},
            )
            await session.commit()

            capabilities = await _capabilities_for(session, body.agent_id)
            return _management_out(record, capabilities)

    @app.post("/agents/{agent_id}/verification/start", response_model=VerificationStartOut)
    async def start_verification(
        agent_id: str, organization_id: str, authorization: str | None = Header(default=None)
    ) -> VerificationStartOut:
        require_org_bearer(organization_id, authorization)
        async with session_factory() as session:
            record = await _get_owned_agent(session, agent_id, organization_id)
            if record.status == "revoked":
                raise HTTPException(status_code=409, detail="Agent is revoked; cannot verify.")

            challenge = secrets.token_urlsafe(32)
            now = datetime.now(UTC).replace(tzinfo=None)
            expires_at = now + timedelta(
                seconds=settings.registry_verification_challenge_ttl_seconds
            )
            record.verification_challenge = challenge
            record.verification_challenge_expires_at = expires_at
            record.updated_at = now
            await _audit(
                session,
                event_type="agent.verification_started",
                agent_id=agent_id,
                organization_id=organization_id,
                detail={},
            )
            await session.commit()

        return VerificationStartOut(
            agent_id=agent_id,
            challenge=challenge,
            challenge_path=CHALLENGE_WELL_KNOWN_PATH,
            expires_at=expires_at,
        )

    @app.post("/agents/{agent_id}/verification/confirm", response_model=AgentManagementOut)
    async def confirm_verification(
        agent_id: str, organization_id: str, authorization: str | None = Header(default=None)
    ) -> AgentManagementOut:
        require_org_bearer(organization_id, authorization)
        async with session_factory() as session:
            record = await _get_owned_agent(session, agent_id, organization_id)
            challenge_pending = (
                record.verification_challenge is not None
                and record.verification_challenge_expires_at is not None
            )
            if not challenge_pending:
                raise HTTPException(
                    status_code=409, detail="No verification challenge is pending for this agent."
                )
            now = datetime.now(UTC).replace(tzinfo=None)
            if now >= record.verification_challenge_expires_at:
                raise HTTPException(status_code=409, detail="Verification challenge has expired.")

            from urllib.parse import urljoin

            challenge_url = urljoin(record.a2a_card_url, CHALLENGE_WELL_KNOWN_PATH)
            try:
                served_value = await fetch_text_safely(
                    challenge_url,
                    allow_private_network_targets=(
                        settings.registry_allow_private_network_card_targets
                    ),
                    fetch_timeout_seconds=settings.registry_card_fetch_timeout_seconds,
                )
            except (UnsafeCardUrlError, CardFetchError) as exc:
                raise HTTPException(
                    status_code=409, detail=f"Could not fetch ownership challenge: {exc}"
                ) from exc

            if served_value.strip() != record.verification_challenge:
                await _audit(
                    session,
                    event_type="agent.verification_failed",
                    agent_id=agent_id,
                    organization_id=organization_id,
                    detail={},
                )
                await session.commit()
                raise HTTPException(
                    status_code=409, detail="Challenge value served by the agent did not match."
                )

            record.status = "active"
            record.last_verified_at = now
            record.verification_challenge = None
            record.verification_challenge_expires_at = None
            record.updated_at = now
            await _audit(
                session,
                event_type="agent.verified",
                agent_id=agent_id,
                organization_id=organization_id,
                detail={},
            )
            await session.commit()
            capabilities = await _capabilities_for(session, agent_id)
            return _management_out(record, capabilities)

    async def _transition(
        agent_id: str, organization_id: str, authorization: str | None, *, new_status: str
    ) -> AgentManagementOut:
        require_org_bearer(organization_id, authorization)
        async with session_factory() as session:
            record = await _get_owned_agent(session, agent_id, organization_id)
            if record.status == "revoked":
                raise HTTPException(status_code=409, detail="Revoked agents cannot change state.")
            record.status = new_status
            record.updated_at = datetime.now(UTC).replace(tzinfo=None)
            await _audit(
                session,
                event_type=f"agent.{new_status}",
                agent_id=agent_id,
                organization_id=organization_id,
                detail={},
            )
            await session.commit()
            capabilities = await _capabilities_for(session, agent_id)
            return _management_out(record, capabilities)

    @app.post("/agents/{agent_id}/suspend", response_model=AgentManagementOut)
    async def suspend_agent(
        agent_id: str, organization_id: str, authorization: str | None = Header(default=None)
    ) -> AgentManagementOut:
        return await _transition(agent_id, organization_id, authorization, new_status="suspended")

    @app.post("/agents/{agent_id}/reactivate", response_model=AgentManagementOut)
    async def reactivate_agent(
        agent_id: str, organization_id: str, authorization: str | None = Header(default=None)
    ) -> AgentManagementOut:
        return await _transition(agent_id, organization_id, authorization, new_status="active")

    @app.post("/agents/{agent_id}/revoke", response_model=AgentManagementOut)
    async def revoke_agent(
        agent_id: str, organization_id: str, authorization: str | None = Header(default=None)
    ) -> AgentManagementOut:
        return await _transition(agent_id, organization_id, authorization, new_status="revoked")

    @app.get("/organizations/{organization_id}/agents", response_model=list[AgentManagementOut])
    async def list_owned_agents(
        organization_id: str, authorization: str | None = Header(default=None)
    ) -> list[AgentManagementOut]:
        require_org_bearer(organization_id, authorization)
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(RegistryAgentRecord).where(
                            RegistryAgentRecord.organization_id == organization_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            out = []
            for record in rows:
                capabilities = await _capabilities_for(session, record.agent_id)
                out.append(_management_out(record, capabilities))
            return out

    @app.get("/agents", response_model=list[DiscoveredAgentOut])
    async def discover_agents(
        requesting_organization_id: str, skill_id: str | None = None
    ) -> list[DiscoveredAgentOut]:
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(RegistryAgentRecord).where(RegistryAgentRecord.status == "active")
                    )
                )
                .scalars()
                .all()
            )

            results: list[DiscoveredAgentOut] = []
            relationship_cache: dict[str, bool] = {}
            for record in rows:
                if record.organization_id == requesting_organization_id:
                    allowed = True
                else:
                    if record.organization_id not in relationship_cache:
                        try:
                            relationship_cache[
                                record.organization_id
                            ] = await has_active_relationship(
                                network_api_url=settings.network_api_url,
                                internal_service_key=settings.network_internal_service_key,
                                buyer_organization_id=requesting_organization_id,
                                supplier_organization_id=record.organization_id,
                            )
                        except RelationshipLookupError:
                            relationship_cache[record.organization_id] = False
                    allowed = relationship_cache[record.organization_id]

                if not allowed:
                    continue

                capabilities = await _capabilities_for(session, record.agent_id)
                if skill_id is not None and skill_id not in capabilities:
                    continue

                results.append(
                    DiscoveredAgentOut(
                        agent_id=record.agent_id,
                        organization_id=record.organization_id,
                        display_name=record.display_name,
                        status=record.status,
                        capabilities=capabilities,
                        a2a_endpoint_url=record.a2a_card_url,
                    )
                )
            return results

    @app.get("/agents/{agent_id}", response_model=DiscoveredAgentOut)
    async def discover_agent_detail(
        agent_id: str, requesting_organization_id: str
    ) -> DiscoveredAgentOut:
        matches = await discover_agents(requesting_organization_id=requesting_organization_id)
        for match in matches:
            if match.agent_id == agent_id:
                return match
        raise HTTPException(status_code=404, detail="Agent not discoverable.")

    @app.get("/agents/{agent_id}/verify-sender", response_model=AgentSenderVerificationOut)
    async def verify_sender(
        agent_id: str, claimed_organization_id: str, counterparty_organization_id: str
    ) -> AgentSenderVerificationOut:
        """M5.1: confirm a cryptographically-verified A2A message's
        claimed sender is a registered, ``active`` agent of
        ``claimed_organization_id``, *and* that an active trading
        relationship exists between it and ``counterparty_organization_id``
        -- in either direction, unlike ``/agents`` (M2 discovery), which
        deliberately only ever treats the requester as the buyer side.
        Message authentication has no "buyer" or "supplier" role of its
        own: either side may be verifying the other."""
        async with session_factory() as session:
            record = await session.get(RegistryAgentRecord, agent_id)
        if (
            record is None
            or record.status != "active"
            or record.organization_id != claimed_organization_id
        ):
            raise HTTPException(
                status_code=404,
                detail=f"Agent {agent_id!r} is not an active agent of {claimed_organization_id!r}.",
            )

        try:
            related = await has_active_relationship(
                network_api_url=settings.network_api_url,
                internal_service_key=settings.network_internal_service_key,
                buyer_organization_id=claimed_organization_id,
                supplier_organization_id=counterparty_organization_id,
            ) or await has_active_relationship(
                network_api_url=settings.network_api_url,
                internal_service_key=settings.network_internal_service_key,
                buyer_organization_id=counterparty_organization_id,
                supplier_organization_id=claimed_organization_id,
            )
        except RelationshipLookupError as exc:
            raise HTTPException(
                status_code=502, detail=f"Could not verify trading relationship: {exc}"
            ) from exc

        if not related:
            raise HTTPException(
                status_code=404,
                detail=f"No active trading relationship between {claimed_organization_id!r} "
                f"and {counterparty_organization_id!r}.",
            )

        return AgentSenderVerificationOut(
            agent_id=agent_id, organization_id=claimed_organization_id, status=record.status
        )

    return app


app = create_app()
