"""Portal-issued human-approval assertions (M6, docs/DECISIONS.md ADR-013).

Unlike :mod:`identity.dev_oidc` (refused outright outside local/staging),
this adapter is meant to be trusted in every environment, including
production — it is the thing that replaces the dev adapter once a real
human has actually logged in. It deliberately reuses the Ed25519/JWS
trust mechanism already built for M5.1 message proofs rather than
inventing a second cryptographic primitive: the portal holds its own
keypair (generated the same way an organization's is, via
``scripts/generate_signing_key.py``), publishes the public half through
network-api's existing JWKS endpoints exactly like any organization
does, and signs a short-lived assertion after it has (a) independently
verified a real OIDC session and (b) looked up, fresh, that the signed-in
subject currently holds the ``approver`` role for the approval's own
organization (``network-api``'s ``GET /members/lookup``).

The agent that ultimately decides the approval (``buyer_agent.
authorization.decide_approval`` / the supplier equivalent) does **not**
stop at verifying this assertion's signature — it repeats that same
fresh membership lookup itself before honoring the decision. A portal
assertion is deliberately *not* sufficient proof on its own; it is
"the portal says a verified human asked for this," not "this human may
do this." See docs/SECURITY.md's M6 section.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ISSUER = "aimo-portal"
APPROVER_ROLE = "approver"
ALGORITHM = "EdDSA"
#: Deliberately short — this assertion is minted for one exact decide
#: call, never held or reused like a session token.
DEFAULT_VALIDITY_SECONDS = 120


class PortalApprovalError(Exception):
    """Base class for every reason a portal approval assertion is
    rejected."""


@dataclass(frozen=True)
class PortalApprovalClaims:
    approver_subject: str
    organization_id: str
    negotiation_id: str
    content_hash: str
    issuer: str


def peek_issuer(token: str) -> str | None:
    """Read the unverified ``iss`` claim to decide which verifier
    (``dev_oidc`` or this module) a presented approval token belongs to
    — callers must still fully verify with the matching function before
    trusting anything else in the token."""
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.exceptions.PyJWTError:
        return None
    issuer = claims.get("iss")
    return issuer if isinstance(issuer, str) else None


def peek_kid(token: str) -> str | None:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.exceptions.PyJWTError:
        return None
    kid = header.get("kid")
    return kid if isinstance(kid, str) else None


def issue_portal_approval_token(
    *,
    organization_id: str,
    approver_subject: str,
    negotiation_id: str,
    content_hash: str,
    private_key: Ed25519PrivateKey,
    kid: str,
    validity_seconds: int = DEFAULT_VALIDITY_SECONDS,
) -> str:
    """Mint a one-decision assertion, signed with the portal's own key.
    Meant to be called only by the portal backend, immediately before
    calling an agent's ``POST /management/approvals/{id}/decide``, never
    held or reused for a different negotiation/content_hash."""
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": approver_subject,
        "aud": organization_id,
        "organization_id": organization_id,
        "negotiation_id": negotiation_id,
        "content_hash": content_hash,
        "roles": [APPROVER_ROLE],
        "iat": now,
        "exp": now + validity_seconds,
    }
    return jwt.encode(claims, private_key, algorithm=ALGORITHM, headers={"kid": kid})


def verify_portal_approval_token(
    token: str,
    *,
    public_key: Ed25519PublicKey,
    organization_id: str,
    negotiation_id: str,
    content_hash: str,
) -> PortalApprovalClaims:
    """Verify signature, audience/organization binding, the exact
    negotiation/content_hash this decision is for, expiry and the
    ``approver`` role. Does **not** check that the subject still
    actively holds that role for this organization — the caller must do
    that itself via a fresh ``network-api`` lookup (see module
    docstring); this function only proves the assertion is genuinely
    from the portal and unmodified."""
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=[ALGORITHM],
            audience=organization_id,
            issuer=ISSUER,
        )
    except jwt.exceptions.PyJWTError as exc:
        raise PortalApprovalError(f"Invalid portal approval assertion: {exc}") from exc

    if claims.get("organization_id") != organization_id:
        raise PortalApprovalError("Assertion is not scoped to this approval's organization.")
    if claims.get("negotiation_id") != negotiation_id:
        raise PortalApprovalError("Assertion is not scoped to this approval's negotiation.")
    if claims.get("content_hash") != content_hash:
        raise PortalApprovalError("Assertion is not scoped to this approval's exact content_hash.")
    if APPROVER_ROLE not in claims.get("roles", []):
        raise PortalApprovalError(
            f"Assertion for {claims.get('sub')!r} does not carry the 'approver' role."
        )

    return PortalApprovalClaims(
        approver_subject=claims["sub"],
        organization_id=claims["organization_id"],
        negotiation_id=claims["negotiation_id"],
        content_hash=claims["content_hash"],
        issuer=claims["iss"],
    )


__all__ = [
    "ALGORITHM",
    "APPROVER_ROLE",
    "DEFAULT_VALIDITY_SECONDS",
    "ISSUER",
    "PortalApprovalClaims",
    "PortalApprovalError",
    "issue_portal_approval_token",
    "peek_issuer",
    "peek_kid",
    "verify_portal_approval_token",
]
