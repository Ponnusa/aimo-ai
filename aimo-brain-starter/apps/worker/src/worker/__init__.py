"""Idempotent, role-parameterized commitment-recovery worker (M5.1,
docs/DECISIONS.md ADR-012).

This is **not** a third codebase that reimplements commitment logic —
it only calls back into the configured role's own agent package
(``buyer_agent.commitment``/``supplier_agent.commitment``), which is
already idempotent (same idempotency_key, bounded by the receiving
side's own UNIQUE constraint). Each deployment of this worker runs for
exactly **one** organization's own agent (``WORKER_ROLE=buyer`` or
``WORKER_ROLE=supplier``), against only that agent's own database —
never both in the same process, preserving the same "separate
deployable process" boundary ``buyer_agent``/``supplier_agent``
themselves hold (two different *configurations* of this package, not
one process that imports both roles' business logic at once).

Buyer role: retries ``buyer_orders`` rows stuck in ``COMMITTING`` or
``RECONCILIATION_REQUIRED`` past a backoff threshold, bounded by a max
attempt count, by re-invoking
``buyer_agent.commitment.run_authorization_and_commitment`` — the exact
same idempotent entry point a live request already uses. A crash at any
point recovers on the next cycle without creating a duplicate order,
because the idempotency_key never changes and the supplier's own
``supplier_mcp_orders`` UNIQUE constraint is the actual enforcement
point regardless of how many times this is retried.

Supplier role: currently a no-op placeholder (supplier-side commit
handling is synchronous, request-driven, and already records
``RECONCILIATION_REQUIRED`` on an ambiguous ``execute_order`` outcome;
a supplier-initiated retry loop is a documented follow-up, not yet
needed since the buyer is the only side that initiates a commit).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

#: Order states the worker considers worth retrying. "FAILED"/"CONFIRMED"
#: are terminal and never retried.
RETRYABLE_STATES: tuple[str, ...] = ("COMMITTING", "RECONCILIATION_REQUIRED")
DEFAULT_RETRY_BACKOFF_SECONDS = 30
DEFAULT_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class DispatchCycleResult:
    role: str
    attempted: int
    recovered: int
    still_pending: int
    exhausted: int


def get_worker_role() -> str:
    role = os.environ.get("WORKER_ROLE", "")
    if role not in ("buyer", "supplier"):
        raise ValueError(
            f"WORKER_ROLE must be 'buyer' or 'supplier', got {role!r}. Each worker "
            "deployment serves exactly one organization's own agent."
        )
    return role


async def run_dispatch_cycle(
    *,
    role: str | None = None,
    retry_backoff_seconds: int = DEFAULT_RETRY_BACKOFF_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> DispatchCycleResult:
    """Run one reconciliation pass for the configured role. Idempotent
    and safe to call on any schedule — every retry re-checks durable
    state before acting, never assumes anything about a prior attempt's
    outcome."""
    resolved_role = role or get_worker_role()
    if resolved_role == "buyer":
        return await _run_buyer_cycle(
            retry_backoff_seconds=retry_backoff_seconds, max_attempts=max_attempts
        )
    if resolved_role == "supplier":
        return DispatchCycleResult(
            role="supplier", attempted=0, recovered=0, still_pending=0, exhausted=0
        )
    raise ValueError(f"Unknown worker role {resolved_role!r}.")


async def _run_buyer_cycle(*, retry_backoff_seconds: int, max_attempts: int) -> DispatchCycleResult:
    from buyer_agent.commitment import CommitmentError, run_authorization_and_commitment
    from buyer_agent.models import BuyerOrderRecord
    from buyer_agent.settings import get_settings
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from database import new_engine

    settings = get_settings()
    engine = new_engine(settings.buyer_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(seconds=retry_backoff_seconds)

    try:
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(BuyerOrderRecord).where(
                            BuyerOrderRecord.state.in_(RETRYABLE_STATES),
                            BuyerOrderRecord.updated_at <= cutoff,
                        )
                    )
                )
                .scalars()
                .all()
            )
            pending = [
                (row.negotiation_id, row.attempts) for row in rows if row.attempts < max_attempts
            ]
            exhausted = sum(1 for row in rows if row.attempts >= max_attempts)

        recovered = 0
        still_pending = 0
        for negotiation_id, _attempts in pending:
            try:
                confirmation = await run_authorization_and_commitment(
                    negotiation_id, settings=settings, session_factory=session_factory
                )
                if confirmation["payload"]["status"] == "CONFIRMED":
                    recovered += 1
                    logger.info("worker(buyer): recovered negotiation %s", negotiation_id)
                else:
                    still_pending += 1
            except CommitmentError as exc:
                still_pending += 1
                logger.warning(
                    "worker(buyer): negotiation %s still unresolved: %s", negotiation_id, exc
                )

        if exhausted:
            logger.error(
                "worker(buyer): %d order(s) exceeded max_attempts=%d and are left "
                "RECONCILIATION_REQUIRED for manual attention.",
                exhausted,
                max_attempts,
            )

        return DispatchCycleResult(
            role="buyer",
            attempted=len(pending),
            recovered=recovered,
            still_pending=still_pending,
            exhausted=exhausted,
        )
    finally:
        await engine.dispose()


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "RETRYABLE_STATES",
    "DispatchCycleResult",
    "get_worker_role",
    "run_dispatch_cycle",
]
