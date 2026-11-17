"""Identity package boundary.

**M2 status:** scoped, short-lived service-credential issuance and
verification (:mod:`identity.tokens`) is real — see ``docs/DECISIONS.md``
ADR-008. ``network-api`` issues credentials scoped to one organization and
one action; ``registry-api`` verifies them without a live call back to
``network-api`` or a shared database.

Human (OIDC) authentication is still a scaffold: it is a portal/approval
concern (``docs/ROADMAP.md`` M6/M7), not needed for M2's service-to-service
boundary. Calling :func:`verify_human_token` before then raises
``NotImplementedError`` rather than returning a fake "verified" result, per
CLAUDE.md rule 1.
"""

from __future__ import annotations

from identity.tokens import (
    ServiceCredentialClaims,
    ServiceCredentialError,
    ServiceCredentialExpiredError,
    ServiceCredentialMalformedError,
    ServiceCredentialScopeError,
    ServiceCredentialSignatureError,
    issue_service_credential,
    verify_service_credential,
)

__all__ = [
    "ServiceCredentialClaims",
    "ServiceCredentialError",
    "ServiceCredentialExpiredError",
    "ServiceCredentialMalformedError",
    "ServiceCredentialScopeError",
    "ServiceCredentialSignatureError",
    "issue_service_credential",
    "verify_service_credential",
    "verify_human_token",
]


def verify_human_token(token: str) -> None:
    raise NotImplementedError(
        "OIDC human token verification is a portal/approval concern, scoped to M6/M7. "
        "Not implemented yet."
    )
