"""Worker service boundary.

**M0 status: scaffold only.** Real retry/timeout/outbox dispatch and
reconciliation logic (FR-08) is implemented alongside the durable trade
protocol in M4 (``docs/ROADMAP.md``). :func:`run_dispatch_cycle` exists so
``apps/worker`` is an installable, independently deployable package from
day one, without pretending any dispatch logic runs yet.
"""

from __future__ import annotations


def run_dispatch_cycle() -> None:
    raise NotImplementedError(
        "Inbox/outbox dispatch and reconciliation land in M4. Not implemented in M0."
    )
