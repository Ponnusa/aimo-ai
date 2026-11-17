"""Binding policy decision package boundary.

**M0 status: scaffold only.** Real OPA-backed policy evaluation,
human-approval binding to an exact proposal hash, and deny-by-default
enforcement land in M5 (``docs/ROADMAP.md``). Per CLAUDE.md's non-negotiable
architecture, no binding action may ever be approved by default or by an
LLM; :func:`evaluate_binding_action` raises rather than returning a fake
allow/deny so no M0/M1 code can accidentally treat an unimplemented check
as a passed one.
"""

from __future__ import annotations


def evaluate_binding_action(*, action: str, proposal_hash: str) -> None:
    raise NotImplementedError(
        "OPA-backed binding policy evaluation lands in M5. Not implemented in M0. "
        "No action may be treated as authorized before this is implemented."
    )
