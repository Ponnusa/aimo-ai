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

import base64
import hashlib
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import FastAPI, Header, HTTPException
from message_integrity.keys import jwk_thumbprint, public_key_from_jwk, validate_ed25519_public_jwk
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
    RegistryOrganizationPolicyRecord,
)
from registry_api.network_client import RelationshipLookupError, has_active_relationship
from registry_api.settings import get_settings
from sqlalchemy import select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database import new_engine
from identity import ServiceCredentialClaims, ServiceCredentialError, verify_service_credential

AGENT_MANAGEMENT_SCOPE = "manage_agents"
CHALLENGE_WELL_KNOWN_PATH = "/.well-known/aimo-brain-challenge"
CHALLENGE_PROTOCOL_VERSION = 1
CHALLENGE_AUDIENCE = "aimo-brain-registry"
CHALLENGE_PURPOSE_ENDPOINT = "endpoint-control"
CHALLENGE_PURPOSE_KEY = "key-control"


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
    #: Optional Ed25519 JWK (RFC 8037) the registrant claims to control.
    #: When present, activation additionally requires proving control of
    #: the matching private key (see ``ConfirmVerificationIn``) — M8,
    #: additive to the existing endpoint-ownership challenge, never a
    #: replacement for it. Agents that omit this keep the M0-M7
    #: endpoint-control-only verification behavior unchanged, UNLESS
    #: their organization has been classified "external" (ADR-015
    #: section A), in which case omitting it is a 400, not an opt-out.
    #: There is deliberately no "classification"/"is_external" field
    #: here at all: that server-controlled fact is looked up from
    #: ``RegistryOrganizationPolicyRecord``, never accepted from the
    #: caller — a freely caller-selectable classification would let any
    #: registrant simply claim "legacy" to skip key-control proof.
    signing_public_key_jwk: dict[str, str] | None = None


class ClassifyOrganizationIn(BaseModel):
    requires_key_proof: bool
    note: str | None = None


class ConfirmVerificationIn(BaseModel):
    #: Base64 (standard, padded) encoding of the raw Ed25519 signature
    #: over the UTF-8 bytes of the pending verification challenge,
    #: produced with the private key matching the ``signing_public_key_jwk``
    #: supplied at registration. Required only when that key was supplied.
    key_proof_signature: str | None = None


class AgentManagementOut(BaseModel):
    agent_id: str
    organization_id: str
    display_name: str
    a2a_card_url: str
    status: str
    agent_classification: str
    card_fingerprint: str
    capabilities: list[str]
    registered_at: datetime
    updated_at: datetime
    last_verified_at: datetime | None
    has_signing_key: bool


class VerificationStartOut(BaseModel):
    agent_id: str
    #: Served verbatim at ``challenge_path`` to prove endpoint control.
    endpoint_challenge: str
    #: ``None`` unless the agent registered a signing key. Signed with
    #: that key's matching private key to prove key control -- a
    #: separate challenge from ``endpoint_challenge``, never the same
    #: string used for both purposes (ADR-015 section D).
    key_challenge: str | None
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


def _encode_signing_jwk(jwk: dict[str, str] | None) -> str | None:
    """Deterministic encoding so the same JWK always compares equal to
    itself regardless of key order in the request body -- used both for
    storage and for the key-substitution-guard comparison."""
    if jwk is None:
        return None
    return json.dumps(jwk, sort_keys=True)


def _build_challenge(
    *,
    purpose: str,
    organization_id: str,
    agent_id: str,
    jwk_thumb: str | None,
    now: datetime,
    expires_at: datetime,
) -> str:
    """A structured, purpose-bound challenge (ADR-015 section D) -- never
    a bare random token. Binds: purpose (so a signature/fetch proving
    one kind of control can never be mistaken for proving the other),
    protocol version, organization + agent id (so a challenge minted for
    one agent cannot be replayed against another), the registering
    key's RFC 7638 thumbprint (so a challenge is bound to the *exact*
    key, not just "some key"), a fresh random request id and nonce, an
    explicit issuance/expiry window, and the intended audience. The
    canonical JSON text itself is what gets served (endpoint-control) or
    signed (key-control) -- the whole structure is what is bound, not
    any single field in isolation."""
    payload = {
        "purpose": purpose,
        "protocol_version": CHALLENGE_PROTOCOL_VERSION,
        "organization_id": organization_id,
        "agent_id": agent_id,
        "jwk_thumbprint": jwk_thumb,
        "request_id": uuid.uuid4().hex,
        "nonce": secrets.token_urlsafe(32),
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "audience": CHALLENGE_AUDIENCE,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _parse_and_validate_challenge(
    raw: str, *, purpose: str, organization_id: str, agent_id: str
) -> dict[str, object]:
    """Defense in depth: re-parse a stored challenge and confirm its
    bound fields actually match the context it is being used in, rather
    than trusting that an exact-string comparison/signature check alone
    is sufficient. Raises :class:`ValueError` on any mismatch."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Stored challenge is not valid JSON: {exc}") from exc
    if (
        parsed.get("purpose") != purpose
        or parsed.get("protocol_version") != CHALLENGE_PROTOCOL_VERSION
        or parsed.get("organization_id") != organization_id
        or parsed.get("agent_id") != agent_id
        or parsed.get("audience") != CHALLENGE_AUDIENCE
    ):
        raise ValueError("Challenge's bound fields do not match the current request context.")
    return parsed


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
            agent_classification=record.agent_classification,
            card_fingerprint=record.card_fingerprint,
            capabilities=capabilities,
            registered_at=record.registered_at,
            updated_at=record.updated_at,
            last_verified_at=record.last_verified_at,
            has_signing_key=record.signing_public_key_jwk is not None,
        )

    def require_registry_admin(authorization: str | None) -> None:
        """Authenticates ONLY the organization-classification endpoint,
        with a secret distinct from and never derived from
        ``service_credential_signing_secret`` -- no organization's own
        credential, however scoped, can satisfy this. See ADR-015
        section E: this is an interim, operator-driven control, not a
        general-purpose registry authentication mechanism."""
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer admin token.")
        token = authorization.removeprefix("Bearer ")
        if not secrets.compare_digest(token, settings.registry_admin_token):
            raise HTTPException(status_code=403, detail="Invalid registry admin token.")

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

    @app.post("/organizations/{organization_id}/classification", response_model=dict)
    async def classify_organization(
        organization_id: str,
        body: ClassifyOrganizationIn,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Operator-only (ADR-015 section A/E): flags ``organization_id``
        as requiring mandatory signing-key registration and
        private-key-control proof for every one of its agents. This is
        never reachable with an organization's own ``manage_agents``
        credential -- only with the separate, operator-held admin token
        -- so classification can never be a privilege the registering
        caller grants itself."""
        require_registry_admin(authorization)
        async with session_factory() as session:
            policy = await session.get(RegistryOrganizationPolicyRecord, organization_id)
            now = datetime.now(UTC).replace(tzinfo=None)
            if policy is None:
                policy = RegistryOrganizationPolicyRecord(
                    organization_id=organization_id,
                    requires_key_proof=body.requires_key_proof,
                    set_by_note=body.note,
                    updated_at=now,
                )
                session.add(policy)
            else:
                policy.requires_key_proof = body.requires_key_proof
                policy.set_by_note = body.note
                policy.updated_at = now
            await _audit(
                session,
                event_type="organization.classified",
                agent_id=None,
                organization_id=organization_id,
                detail={"requires_key_proof": body.requires_key_proof, "note": body.note},
            )
            await session.commit()
        return {"organization_id": organization_id, "requires_key_proof": body.requires_key_proof}

    @app.post("/agents", response_model=AgentManagementOut)
    async def register_agent(
        body: RegisterAgentIn, authorization: str | None = Header(default=None)
    ) -> AgentManagementOut:
        require_org_bearer(body.organization_id, authorization)

        if body.signing_public_key_jwk is not None:
            try:
                validate_ed25519_public_jwk(body.signing_public_key_jwk)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail=f"Invalid signing_public_key_jwk: {exc}"
                ) from exc

        async with session_factory() as session:
            policy = await session.get(RegistryOrganizationPolicyRecord, body.organization_id)
            requires_key_proof = policy is not None and policy.requires_key_proof
            classification = "external" if requires_key_proof else "legacy"
            if requires_key_proof and body.signing_public_key_jwk is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "This organization is classified for external onboarding "
                        "(ADR-015 section A): signing_public_key_jwk is required at "
                        "registration, not optional."
                    ),
                )

            existing = await session.get(RegistryAgentRecord, body.agent_id)
            if existing is not None:
                import sys as _sys

                print(
                    f"[DIAG] unexpected existing row for agent_id={body.agent_id!r}: "
                    f"org={existing.organization_id!r} status={existing.status!r} "
                    f"classification={existing.agent_classification!r} "
                    f"has_key={existing.signing_public_key_jwk is not None} "
                    f"registered_at={existing.registered_at!r}",
                    file=_sys.stderr,
                )
            if existing is not None and existing.organization_id != body.organization_id:
                raise HTTPException(
                    status_code=409,
                    detail="This agent_id is already registered to a different organization.",
                )
            if existing is not None and existing.status == "revoked":
                # Lifecycle protection (ADR-015 section B): revoked is
                # terminal. Ordinary re-registration must never resurrect
                # a revoked agent -- administrative reinstatement, if it
                # is ever built, is a separate, explicitly-authorized
                # operation, not a side effect of POST /agents.
                raise HTTPException(
                    status_code=409,
                    detail="Agent is revoked; it cannot be re-registered.",
                )

            new_key_encoded = _encode_signing_jwk(body.signing_public_key_jwk)
            if (
                existing is not None
                and existing.signing_public_key_jwk is not None
                and new_key_encoded is not None
                and new_key_encoded != existing.signing_public_key_jwk
            ):
                # Key substitution guard (M8): once an agent has a signing
                # key on file, re-registration cannot silently swap it for
                # a different one -- that would let anyone who can pass
                # the (comparatively weak) endpoint-ownership challenge
                # hijack an already-established signing identity. Proving
                # control of the *current* key to authorize a rotation is
                # a separate M8 follow-up, not yet implemented; today the
                # only way to change a registered signing key is to keep
                # registering the same one. This guard applies regardless
                # of the agent's current status (active/suspended/pending)
                # -- there is no status under which substitution is
                # permitted via this endpoint.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This agent already has a signing key on file; changing it via "
                        "re-registration is not supported. Key rotation is a planned M8 "
                        "follow-up."
                    ),
                )

            if new_key_encoded is not None:
                # Residual, best-effort hygiene check -- not the primary
                # security boundary (that is the substitution guard
                # above, and it IS race-safe; this check does not need to
                # be, since a successful race here only means two agents
                # briefly share a key hint, not that anyone's identity
                # was hijacked). Decision (ADR-015 section C): the same
                # public key must not be knowingly registered for two
                # different agent_ids, to avoid ambiguous key-to-identity
                # mapping during audit/incident response.
                reused = await session.execute(
                    select(RegistryAgentRecord.agent_id).where(
                        RegistryAgentRecord.signing_public_key_jwk == new_key_encoded,
                        RegistryAgentRecord.agent_id != body.agent_id,
                    )
                )
                if reused.scalar_one_or_none() is not None:
                    raise HTTPException(
                        status_code=409,
                        detail="This signing key is already registered to a different agent_id.",
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

            if is_new:
                record = RegistryAgentRecord(
                    agent_id=body.agent_id,
                    organization_id=body.organization_id,
                    display_name=display_name,
                    a2a_card_url=body.a2a_card_url,
                    status="pending",
                    agent_classification=classification,
                    card_fingerprint=fingerprint,
                    registered_at=now,
                    updated_at=now,
                    signing_public_key_jwk=new_key_encoded,
                )
                session.add(record)
                for skill in card.skills:
                    session.add(
                        RegistryAgentCapabilityRecord(
                            agent_id=body.agent_id, skill_id=skill.id, skill_name=skill.name
                        )
                    )
                await _audit(
                    session,
                    event_type="agent.registered",
                    agent_id=body.agent_id,
                    organization_id=body.organization_id,
                    detail={"a2a_card_url": body.a2a_card_url, "fingerprint": fingerprint},
                )
                try:
                    await session.commit()
                except IntegrityError as exc:
                    # Atomicity (ADR-015 section C): two concurrent
                    # first-time registrations for the same brand-new
                    # agent_id race on the primary key. Only one can win;
                    # the other gets a deterministic, documented 409
                    # rather than silently overwriting anything.
                    await session.rollback()
                    raise HTTPException(
                        status_code=409,
                        detail="Concurrent registration for this agent_id; retry.",
                    ) from exc
            else:
                assert existing is not None
                establishing_key_now = (
                    existing.signing_public_key_jwk is None and new_key_encoded is not None
                )
                if establishing_key_now:
                    # Atomicity (ADR-015 section C): do NOT rely on the
                    # unlocked read above to decide this is safe. Two
                    # concurrent requests attempting to establish the
                    # FIRST key for the same existing, keyless agent are
                    # resolved by a conditional UPDATE whose WHERE clause
                    # re-checks the precondition at write time -- only
                    # one can match and advance; the loser gets rowcount
                    # 0, never a silently-overwritten key.
                    cas = (
                        update(RegistryAgentRecord.__table__)
                        .where(RegistryAgentRecord.agent_id == body.agent_id)
                        .where(RegistryAgentRecord.signing_public_key_jwk.is_(None))
                        .values(signing_public_key_jwk=new_key_encoded)
                    )
                    result = cast(CursorResult[Any], await session.execute(cas))
                    if result.rowcount != 1:
                        await session.rollback()
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "A signing key was concurrently established for this agent "
                                "by another request; retry to confirm the current key."
                            ),
                        )
                    await session.commit()
                    # The Core-level UPDATE above bypassed the ORM, so
                    # the already-loaded ``existing`` object's attribute
                    # is now stale in memory even though the row in the
                    # database is correct -- set it explicitly rather
                    # than re-``session.get`` it (which would return the
                    # same stale cached identity-map object, not re-query
                    # the database).
                    existing.signing_public_key_jwk = new_key_encoded

                card_url_changed = existing.a2a_card_url != body.a2a_card_url
                metadata_unchanged = (
                    not card_url_changed
                    and existing.display_name == display_name
                    and existing.card_fingerprint == fingerprint
                )
                truly_noop = metadata_unchanged and not establishing_key_now

                record = existing
                record.display_name = display_name
                record.a2a_card_url = body.a2a_card_url
                record.card_fingerprint = fingerprint
                record.agent_classification = classification
                record.updated_at = now

                # Lifecycle protection (ADR-015 section B): a same-data
                # retry must be idempotent and must NEVER reset lifecycle
                # state -- an already-verified ("active") agent is not
                # silently downgraded back to "pending" just because its
                # own organization re-POSTed identical data. Re-pointing
                # the card to a genuinely different URL is a real change
                # that legitimately requires re-proving endpoint control,
                # so (and only so) it does reset an "active" agent to
                # "pending". A "suspended" agent never becomes "active" or
                # "pending" through ordinary registration at all --
                # reactivation is a separate, explicitly-authorized
                # operation (``/reactivate``, and only from "suspended").
                if not truly_noop and card_url_changed and record.status == "active":
                    record.status = "pending"
                    record.last_verified_at = None

                if card_url_changed or establishing_key_now:
                    # Invalidate outstanding challenges (ADR-015 section
                    # D/B): a challenge bound to the old card URL or to
                    # "no key yet" is no longer meaningful once either
                    # changes.
                    record.endpoint_challenge = None
                    record.key_challenge = None
                    record.challenge_expires_at = None

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
                    event_type="agent.re_registered_noop" if truly_noop else "agent.re_registered",
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
            if record.status not in ("pending",):
                # Lifecycle protection (ADR-015 section B): a fresh
                # verification round only ever makes sense while pending.
                # An "active"/"suspended" agent has either already proven
                # control or must go through /reactivate, never through
                # a brand-new challenge issued mid-flight.
                raise HTTPException(
                    status_code=409,
                    detail=f"Agent status is {record.status!r}; only a 'pending' agent can "
                    "start verification.",
                )

            now = datetime.now(UTC).replace(tzinfo=None)
            expires_at = now + timedelta(
                seconds=settings.registry_verification_challenge_ttl_seconds
            )
            thumb = (
                jwk_thumbprint(json.loads(record.signing_public_key_jwk))
                if record.signing_public_key_jwk is not None
                else None
            )
            endpoint_challenge = _build_challenge(
                purpose=CHALLENGE_PURPOSE_ENDPOINT,
                organization_id=organization_id,
                agent_id=agent_id,
                jwk_thumb=thumb,
                now=now,
                expires_at=expires_at,
            )
            key_challenge = (
                _build_challenge(
                    purpose=CHALLENGE_PURPOSE_KEY,
                    organization_id=organization_id,
                    agent_id=agent_id,
                    jwk_thumb=thumb,
                    now=now,
                    expires_at=expires_at,
                )
                if record.signing_public_key_jwk is not None
                else None
            )
            record.endpoint_challenge = endpoint_challenge
            record.key_challenge = key_challenge
            record.challenge_expires_at = expires_at
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
            endpoint_challenge=endpoint_challenge,
            key_challenge=key_challenge,
            challenge_path=CHALLENGE_WELL_KNOWN_PATH,
            expires_at=expires_at,
        )

    @app.post("/agents/{agent_id}/verification/confirm", response_model=AgentManagementOut)
    async def confirm_verification(
        agent_id: str,
        organization_id: str,
        authorization: str | None = Header(default=None),
        body: ConfirmVerificationIn | None = None,
    ) -> AgentManagementOut:
        require_org_bearer(organization_id, authorization)
        async with session_factory() as session:
            record = await _get_owned_agent(session, agent_id, organization_id)
            if record.status != "pending":
                # Lifecycle protection (ADR-015 section B): confirming
                # verification only ever makes sense from "pending" --
                # this also closes the concurrent-confirm race's
                # lifecycle half (the atomic UPDATE below closes the
                # rest): a second confirm racing just behind a first
                # successful one now sees "active", not "pending", and
                # is rejected deterministically even before reaching the
                # atomic step.
                raise HTTPException(
                    status_code=409,
                    detail=f"Agent status is {record.status!r}; nothing to confirm.",
                )
            challenge_pending = (
                record.endpoint_challenge is not None and record.challenge_expires_at is not None
            )
            if not challenge_pending:
                raise HTTPException(
                    status_code=409, detail="No verification challenge is pending for this agent."
                )
            now = datetime.now(UTC).replace(tzinfo=None)
            if now >= record.challenge_expires_at:
                raise HTTPException(status_code=409, detail="Verification challenge has expired.")
            endpoint_challenge_value = record.endpoint_challenge
            assert endpoint_challenge_value is not None  # guaranteed by challenge_pending above

            # Private-key-control check (M8): additive to, never a
            # replacement for, the endpoint-ownership challenge below --
            # and bound to its OWN challenge (key_challenge), never the
            # same string endpoint control is proven with. Only agents
            # that registered a signing_public_key_jwk are held to it --
            # an M0-M7 "legacy" agent that never supplied one keeps the
            # original endpoint-control-only behavior unchanged.
            if record.signing_public_key_jwk is not None:
                signature_b64 = body.key_proof_signature if body is not None else None
                if not signature_b64:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "This agent registered a signing key; confirming verification "
                            "requires key_proof_signature (a signature over key_challenge "
                            "made with the matching private key)."
                        ),
                    )
                key_challenge_value = record.key_challenge
                if key_challenge_value is None:
                    raise HTTPException(
                        status_code=409, detail="No key-control challenge is pending."
                    )
                try:
                    _parse_and_validate_challenge(
                        key_challenge_value,
                        purpose=CHALLENGE_PURPOSE_KEY,
                        organization_id=organization_id,
                        agent_id=agent_id,
                    )
                    public_key = public_key_from_jwk(json.loads(record.signing_public_key_jwk))
                    signature = base64.b64decode(signature_b64, validate=True)
                    public_key.verify(signature, key_challenge_value.encode("utf-8"))
                except Exception as exc:
                    await _audit(
                        session,
                        event_type="agent.key_proof_failed",
                        agent_id=agent_id,
                        organization_id=organization_id,
                        detail={"error": str(exc)},
                    )
                    await session.commit()
                    raise HTTPException(
                        status_code=409,
                        detail="Signature did not verify against the registered signing key.",
                    ) from exc

            try:
                _parse_and_validate_challenge(
                    endpoint_challenge_value,
                    purpose=CHALLENGE_PURPOSE_ENDPOINT,
                    organization_id=organization_id,
                    agent_id=agent_id,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=409, detail=f"Stored endpoint challenge is invalid: {exc}"
                ) from exc

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

            if served_value.strip() != endpoint_challenge_value:
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

            # Atomic challenge consumption (ADR-015 section C): the WHERE
            # clause re-checks status=="pending" AND the exact challenge
            # text we just validated against, at the moment of the
            # write. If another request already consumed it (or the
            # agent's state otherwise changed) between our read and this
            # UPDATE, rowcount is 0 and nothing is double-activated --
            # a concurrent confirm can only ever win once.
            cas = (
                update(RegistryAgentRecord.__table__)
                .where(RegistryAgentRecord.agent_id == agent_id)
                .where(RegistryAgentRecord.status == "pending")
                .where(RegistryAgentRecord.endpoint_challenge == endpoint_challenge_value)
                .values(
                    status="active",
                    endpoint_challenge=None,
                    key_challenge=None,
                    challenge_expires_at=None,
                    last_verified_at=now,
                    updated_at=now,
                )
            )
            result = cast(CursorResult[Any], await session.execute(cas))
            if result.rowcount != 1:
                await session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="Verification challenge was already consumed or the agent's state "
                    "changed concurrently; retry.",
                )
            # Same staleness concern as the key-establishment CAS in
            # register_agent: the Core-level UPDATE bypassed the ORM, so
            # ``record`` (already loaded above) must be updated in
            # memory explicitly rather than re-fetched.
            record.status = "active"
            record.endpoint_challenge = None
            record.key_challenge = None
            record.challenge_expires_at = None
            record.last_verified_at = now
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

    #: Lifecycle transition table (ADR-015 section B), enforced below --
    #: any transition not listed here is rejected with 409:
    #:   pending   -> suspended (via /suspend)
    #:   active    -> suspended (via /suspend)
    #:   suspended -> active    (via /reactivate, ONLY from suspended --
    #:                           never from pending, which must go
    #:                           through verification instead)
    #:   pending/active/suspended -> revoked (via /revoke, terminal)
    #:   revoked   -> (nothing; terminal, no administrative reinstatement
    #:                 exists in this slice -- see ADR-015 section B)
    _ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        "suspend": {"pending", "active"},
        "reactivate": {"suspended"},
        "revoke": {"pending", "active", "suspended"},
    }

    async def _transition(
        agent_id: str,
        organization_id: str,
        authorization: str | None,
        *,
        new_status: str,
        action: str,
    ) -> AgentManagementOut:
        require_org_bearer(organization_id, authorization)
        async with session_factory() as session:
            record = await _get_owned_agent(session, agent_id, organization_id)
            if record.status not in _ALLOWED_TRANSITIONS[action]:
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot {action} an agent whose status is {record.status!r}.",
                )
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
        return await _transition(
            agent_id, organization_id, authorization, new_status="suspended", action="suspend"
        )

    @app.post("/agents/{agent_id}/reactivate", response_model=AgentManagementOut)
    async def reactivate_agent(
        agent_id: str, organization_id: str, authorization: str | None = Header(default=None)
    ) -> AgentManagementOut:
        return await _transition(
            agent_id, organization_id, authorization, new_status="active", action="reactivate"
        )

    @app.post("/agents/{agent_id}/revoke", response_model=AgentManagementOut)
    async def revoke_agent(
        agent_id: str, organization_id: str, authorization: str | None = Header(default=None)
    ) -> AgentManagementOut:
        return await _transition(
            agent_id, organization_id, authorization, new_status="revoked", action="revoke"
        )

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
