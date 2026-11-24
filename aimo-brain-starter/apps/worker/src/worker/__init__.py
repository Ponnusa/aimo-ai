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

Supplier role (real as of M7, closing the M5.1/ADR-012-documented gap):
retries ``supplier_orders`` rows stuck in ``RECONCILIATION_REQUIRED``
past a backoff threshold, bounded by the same per-row attempt count, by
re-invoking ``supplier_agent.commitment.reconcile_pending_order`` — the
same idempotent, re-callable shape as the buyer role, but re-asking the
business system's own order status rather than re-submitting a commit
(the supplier side never initiates a commit; it only ever resolves an
ambiguous ``execute_order`` outcome against supplier-mcp's durable
idempotency authority).
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
        return await _run_supplier_cycle(
            retry_backoff_seconds=retry_backoff_seconds, max_attempts=max_attempts
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


async def _run_supplier_cycle(
    *, retry_backoff_seconds: int, max_attempts: int
) -> DispatchCycleResult:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from supplier_agent.commitment import (
        RETRYABLE_SUPPLIER_ORDER_STATES,
        CommitmentError,
        reconcile_pending_order,
    )
    from supplier_agent.models import SupplierOrderRecord
    from supplier_agent.settings import get_settings

    from database import new_engine

    settings = get_settings()
    engine = new_engine(settings.supplier_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(seconds=retry_backoff_seconds)

    try:
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(SupplierOrderRecord).where(
                            SupplierOrderRecord.state.in_(RETRYABLE_SUPPLIER_ORDER_STATES),
                            SupplierOrderRecord.updated_at <= cutoff,
                        )
                    )
                )
                .scalars()
                .all()
            )
            pending = [row.id for row in rows if row.attempts < max_attempts]
            exhausted = sum(1 for row in rows if row.attempts >= max_attempts)

        recovered = 0
        still_pending = 0
        for order_id in pending:
            try:
                resolved_state = await reconcile_pending_order(
                    order_id, settings=settings, session_factory=session_factory
                )
                if resolved_state in ("CONFIRMED", "FAILED"):
                    recovered += 1
                    logger.info(
                        "worker(supplier): resolved order id=%s to %s", order_id, resolved_state
                    )
                else:
                    still_pending += 1
            except CommitmentError as exc:
                still_pending += 1
                logger.warning("worker(supplier): order id=%s still unresolved: %s", order_id, exc)

        if exhausted:
            logger.error(
                "worker(supplier): %d order(s) exceeded max_attempts=%d and are left "
                "RECONCILIATION_REQUIRED for manual attention.",
                exhausted,
                max_attempts,
            )

        return DispatchCycleResult(
            role="supplier",
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
