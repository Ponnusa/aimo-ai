"""Identity verification package boundary.

**M0 status: scaffold only.** This package does not yet verify OIDC tokens,
service credentials, or agent endpoint ownership — that work is scoped to
M2 (``docs/ROADMAP.md``: "Identity, registry and discovery"). Importing
this module is safe; calling :func:`verify_service_credential` or
:func:`verify_human_token` before M2 raises ``NotImplementedError`` rather
than returning a fake "verified" result, per CLAUDE.md rule 1: never pretend
unimplemented features work.
"""

from __future__ import annotations


def verify_service_credential(token: str) -> None:
    raise NotImplementedError("Service credential verification lands in M2. Not implemented in M0.")


def verify_human_token(token: str) -> None:
    raise NotImplementedError("OIDC human token verification lands in M2. Not implemented in M0.")
