"""Deterministic, one-shot fault injection for the sandbox ERP (M7).

Not a real ERP capability — a clearly-labeled test/dev tool (mirrors
``identity.dev_oidc``'s environment gating) that lets integration tests
exercise transient failures, timeouts and delayed acknowledgement
without relying on flaky randomization. State is in-memory only and does
not survive a restart, which is fine: it is scaffolding for tests, never
part of the sandbox's own durable business data.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArmedFault:
    scenario: str  # "transient_error" | "timeout" | "delayed_ack"
    idempotency_key: str | None  # None means "the next matching call, any key"


class FaultInjectionStore:
    def __init__(self) -> None:
        self._armed: list[ArmedFault] = []
        self._pending_acks: set[str] = set()

    def arm(self, *, scenario: str, idempotency_key: str | None = None) -> None:
        self._armed.append(ArmedFault(scenario=scenario, idempotency_key=idempotency_key))

    def consume(self, *, idempotency_key: str | None) -> str | None:
        """Return and remove the first armed fault matching ``idempotency_key``
        (or an any-key fault), or ``None`` if nothing is armed."""
        for index, fault in enumerate(self._armed):
            if fault.idempotency_key is None or fault.idempotency_key == idempotency_key:
                del self._armed[index]
                return fault.scenario
        return None

    def mark_pending_ack(self, idempotency_key: str) -> None:
        self._pending_acks.add(idempotency_key)

    def is_pending_ack(self, idempotency_key: str) -> bool:
        return idempotency_key in self._pending_acks

    def resolve_ack(self, idempotency_key: str) -> bool:
        if idempotency_key in self._pending_acks:
            self._pending_acks.discard(idempotency_key)
            return True
        return False

    def reset(self) -> None:
        self._armed.clear()
        self._pending_acks.clear()
