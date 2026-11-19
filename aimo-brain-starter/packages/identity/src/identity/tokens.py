"""Short-lived, scoped service credentials (M2).

These are not OIDC tokens and not a JWT library dependency — just an
HMAC-signed, expiring, scope-bound credential that one AIMO BRAIN service
(``network-api``, the owner of organization identity) issues and another
(``registry-api``) verifies, without either service needing a live call to
the other or a shared database. Both services must be configured with the
same ``signing_secret`` (an environment-provided shared secret); rotating
it invalidates every outstanding credential immediately.

This intentionally does not implement human authentication (OIDC) or
long-lived API keys — those remain out of scope until the portal/human
approval milestones.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

_SEPARATOR = "."


class ServiceCredentialError(Exception):
    """Base class for all service-credential verification failures."""


class ServiceCredentialMalformedError(ServiceCredentialError):
    """The token is not a well-formed credential at all."""


class ServiceCredentialSignatureError(ServiceCredentialError):
    """The signature does not match — the token was altered or was signed
    with a different secret."""


class ServiceCredentialExpiredError(ServiceCredentialError):
    """The token's ``exp`` claim is in the past."""


class ServiceCredentialScopeError(ServiceCredentialError):
    """The token does not carry the scope the caller requires."""


@dataclass(frozen=True)
class ServiceCredentialClaims:
    organization_id: str
    scope: str
    issued_at: int
    expires_at: int
    agent_id: str | None = None
    approver_id: str | None = None


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(payload_b64: str, *, signing_secret: str) -> str:
    digest = hmac.new(signing_secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256)
    return _b64url_encode(digest.digest())


def issue_service_credential(
    *,
    organization_id: str,
    scope: str,
    signing_secret: str,
    ttl_seconds: int = 3600,
    now: int | None = None,
    agent_id: str | None = None,
    approver_id: str | None = None,
) -> str:
    """Issue a short-lived credential scoping its bearer to one
    organization and one permitted action.

    ``agent_id`` (M5) additionally binds the credential to one specific
    registered agent identity — required for the ``negotiate``/``commit``
    scopes, so a receiving agent can cryptographically verify *which*
    agent sent a message, not merely trust the claimed sender fields in
    the A2A payload. ``approver_id`` (M5) binds an ``approve``-scoped
    credential to one specific pre-registered human approver.
    """
    if not signing_secret:
        raise ValueError("signing_secret must be non-empty")
    issued_at = now if now is not None else int(time.time())
    payload: dict[str, object] = {
        "organization_id": organization_id,
        "scope": scope,
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
    }
    if agent_id is not None:
        payload["agent_id"] = agent_id
    if approver_id is not None:
        payload["approver_id"] = approver_id
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature_b64 = _sign(payload_b64, signing_secret=signing_secret)
    return f"{payload_b64}{_SEPARATOR}{signature_b64}"


def verify_service_credential(
    token: str,
    *,
    signing_secret: str,
    required_scope: str | None = None,
    required_agent_id: str | None = None,
    now: int | None = None,
) -> ServiceCredentialClaims:
    """Verify signature, expiry and (optionally) scope.

    Raises a specific :class:`ServiceCredentialError` subclass describing
    exactly why the token was rejected — callers must not treat any of
    these as "maybe valid."
    """
    if not signing_secret:
        raise ValueError("signing_secret must be non-empty")
    try:
        payload_b64, signature_b64 = token.split(_SEPARATOR)
    except ValueError as exc:
        raise ServiceCredentialMalformedError("Token is not in the expected format.") from exc

    expected_signature_b64 = _sign(payload_b64, signing_secret=signing_secret)
    if not hmac.compare_digest(signature_b64, expected_signature_b64):
        raise ServiceCredentialSignatureError("Token signature does not match.")

    try:
        payload = json.loads(_b64url_decode(payload_b64))
        claims = ServiceCredentialClaims(
            organization_id=payload["organization_id"],
            scope=payload["scope"],
            issued_at=int(payload["iat"]),
            expires_at=int(payload["exp"]),
            agent_id=payload.get("agent_id"),
            approver_id=payload.get("approver_id"),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ServiceCredentialMalformedError("Token payload is not well-formed.") from exc

    current_time = now if now is not None else int(time.time())
    if current_time >= claims.expires_at:
        raise ServiceCredentialExpiredError(
            f"Token expired at {claims.expires_at}, current time {current_time}."
        )

    if required_scope is not None and claims.scope != required_scope:
        raise ServiceCredentialScopeError(
            f"Token scope {claims.scope!r} does not grant required scope {required_scope!r}."
        )

    if required_agent_id is not None and claims.agent_id != required_agent_id:
        raise ServiceCredentialScopeError(
            f"Token agent_id {claims.agent_id!r} does not match required agent_id "
            f"{required_agent_id!r}."
        )

    return claims
