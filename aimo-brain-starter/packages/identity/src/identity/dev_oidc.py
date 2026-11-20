"""A clearly-labeled development identity adapter for human approval
(M5.1, docs/DECISIONS.md ADR-012).

This is **not** a real identity provider. There is no login, session,
SSO or MFA behind it — it issues a self-signed, ID-token-shaped JWT
asserting organization membership and an approval role, reusing that
organization's own Ed25519 signing key (the same one used for message
proofs) rather than inventing a second trust mechanism. The issuer
claim is always prefixed ``"dev-oidc:"`` so a token from this adapter
can never be mistaken for one from a real OIDC provider, and callers
must still check :func:`identity.dev_oidc.is_dev_identity_provider_allowed`
before accepting one at all — it is refused outright outside
local/staging environments (see each agent's own
``decide_approval``/``settings.environment`` gate).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ISSUER_PREFIX = "dev-oidc:"
DEFAULT_AUDIENCE = "aimo-brain-approval"
APPROVER_ROLE = "approver"
ALGORITHM = "EdDSA"


class DevOidcError(Exception):
    """Base class for every reason a dev-OIDC approval token is rejected."""


class DisabledInProductionError(DevOidcError):
    """Raised when this adapter is used outside local/staging — the
    explicit "keep production approval disabled" requirement."""


class MissingApproverRoleError(DevOidcError):
    pass


@dataclass(frozen=True)
class DevApprovalClaims:
    approver_id: str
    organization_id: str
    roles: list[str]
    issuer: str


#: Environments where this adapter may be used at all. Anything else
#: (most importantly "production") refuses every approval outright.
_ALLOWED_ENVIRONMENTS = frozenset({"local", "staging", "test"})


def is_dev_identity_provider_allowed(environment: str) -> bool:
    return environment in _ALLOWED_ENVIRONMENTS


def issue_dev_approval_token(
    *,
    organization_id: str,
    approver_id: str,
    private_key: Ed25519PrivateKey,
    kid: str,
    audience: str = DEFAULT_AUDIENCE,
    validity_seconds: int = 900,
) -> str:
    """Self-issue an ID-token-shaped approval token for
    ``organization_id``'s own ``approver_id``, signed with that
    organization's own key. Meant to be called by a small operator-
    facing tool, never by the agent process acting on its own."""
    now = int(time.time())
    claims = {
        "iss": f"{ISSUER_PREFIX}{organization_id}",
        "sub": approver_id,
        "organization_id": organization_id,
        "roles": [APPROVER_ROLE],
        "aud": audience,
        "iat": now,
        "exp": now + validity_seconds,
    }
    return jwt.encode(claims, private_key, algorithm=ALGORITHM, headers={"kid": kid})


def verify_dev_approval_token(
    token: str,
    *,
    environment: str,
    organization_id: str,
    public_key: Ed25519PublicKey,
    audience: str = DEFAULT_AUDIENCE,
) -> DevApprovalClaims:
    """Verify a dev-OIDC approval token against ``organization_id``'s
    own public key (the approver and the verifying agent always belong
    to the same organization — no cross-organization JWKS lookup is
    needed here, unlike per-artifact message proofs)."""
    if not is_dev_identity_provider_allowed(environment):
        raise DisabledInProductionError(
            f"No production identity provider is configured; the development "
            f"approval adapter is disabled outside local/staging (environment="
            f"{environment!r})."
        )

    expected_issuer = f"{ISSUER_PREFIX}{organization_id}"
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=[ALGORITHM],
            audience=audience,
            issuer=expected_issuer,
        )
    except jwt.exceptions.PyJWTError as exc:
        raise DevOidcError(f"Invalid approval token: {exc}") from exc

    roles = claims.get("roles", [])
    if APPROVER_ROLE not in roles:
        raise MissingApproverRoleError(
            f"Token for {claims.get('sub')!r} does not carry the 'approver' role."
        )

    return DevApprovalClaims(
        approver_id=claims["sub"],
        organization_id=claims["organization_id"],
        roles=roles,
        issuer=claims["iss"],
    )


__all__ = [
    "APPROVER_ROLE",
    "DEFAULT_AUDIENCE",
    "ISSUER_PREFIX",
    "DevApprovalClaims",
    "DevOidcError",
    "DisabledInProductionError",
    "MissingApproverRoleError",
    "is_dev_identity_provider_allowed",
    "issue_dev_approval_token",
    "verify_dev_approval_token",
]
