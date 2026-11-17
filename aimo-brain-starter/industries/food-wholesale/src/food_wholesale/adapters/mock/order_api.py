"""Mock supplier order submission adapter.

Simulates an external supplier order system's acceptance behaviour
in-memory, with idempotency honoured. This is **not** a live order system;
no network call, no Valio Aimo system, and no persistence beyond the
process. Real, authorized integration is explicit M7 scope requiring
partner sign-off (``docs/ROADMAP.md``, ``PROJECT.md`` FR-07/M7).

The idempotency behaviour here exists so later milestones' duplicate-commit
tests have a realistic mock to test against, not because M0 wires this
into any agent workflow yet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OrderConfirmation:
    external_order_id: str
    idempotency_key: str
    status: str


class MockOrderApi:
    """In-memory mock order system honouring idempotency keys."""

    def __init__(self) -> None:
        self._orders_by_idempotency_key: dict[str, OrderConfirmation] = {}

    def submit_order(self, *, idempotency_key: str) -> OrderConfirmation:
        existing = self._orders_by_idempotency_key.get(idempotency_key)
        if existing is not None:
            return existing
        confirmation = OrderConfirmation(
            external_order_id=f"mock_order_{uuid.uuid4().hex[:12]}",
            idempotency_key=idempotency_key,
            status="confirmed",
        )
        self._orders_by_idempotency_key[idempotency_key] = confirmation
        return confirmation
