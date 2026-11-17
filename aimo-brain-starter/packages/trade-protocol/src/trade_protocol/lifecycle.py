"""Domain-independent trade lifecycle, per ``docs/PROTOCOLS.md``.

``DISCOVERED -> REQUESTED -> PROPOSED <-> NEGOTIATING -> PENDING_AUTHORIZATION
-> AUTHORIZED -> COMMITTING -> COMMITTED -> FULFILLING -> VERIFIED``, with
terminal/exceptional states ``REJECTED``, ``EXPIRED``, ``CANCELLED``,
``FAILED``, ``RECONCILIATION_REQUIRED``.

The full negotiation/commit state machine with actors and preconditions is
M4 scope. M0 only needs the enum and the transition table to exist so later
milestones and the food-wholesale module can depend on a stable import
rather than re-inventing state names.
"""

from __future__ import annotations

from enum import StrEnum


class TradeState(StrEnum):
    DISCOVERED = "DISCOVERED"
    REQUESTED = "REQUESTED"
    PROPOSED = "PROPOSED"
    NEGOTIATING = "NEGOTIATING"
    PENDING_AUTHORIZATION = "PENDING_AUTHORIZATION"
    AUTHORIZED = "AUTHORIZED"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    FULFILLING = "FULFILLING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


TERMINAL_STATES: frozenset[TradeState] = frozenset(
    {
        TradeState.VERIFIED,
        TradeState.REJECTED,
        TradeState.EXPIRED,
        TradeState.CANCELLED,
        TradeState.FAILED,
        TradeState.RECONCILIATION_REQUIRED,
    }
)

VALID_TRANSITIONS: dict[TradeState, frozenset[TradeState]] = {
    TradeState.DISCOVERED: frozenset({TradeState.REQUESTED, TradeState.CANCELLED}),
    TradeState.REQUESTED: frozenset(
        {TradeState.PROPOSED, TradeState.REJECTED, TradeState.EXPIRED, TradeState.CANCELLED}
    ),
    TradeState.PROPOSED: frozenset(
        {
            TradeState.NEGOTIATING,
            TradeState.PENDING_AUTHORIZATION,
            TradeState.REJECTED,
            TradeState.EXPIRED,
            TradeState.CANCELLED,
        }
    ),
    TradeState.NEGOTIATING: frozenset(
        {
            TradeState.PROPOSED,
            TradeState.PENDING_AUTHORIZATION,
            TradeState.REJECTED,
            TradeState.EXPIRED,
            TradeState.CANCELLED,
        }
    ),
    TradeState.PENDING_AUTHORIZATION: frozenset(
        {TradeState.AUTHORIZED, TradeState.REJECTED, TradeState.EXPIRED, TradeState.CANCELLED}
    ),
    TradeState.AUTHORIZED: frozenset({TradeState.COMMITTING, TradeState.CANCELLED}),
    TradeState.COMMITTING: frozenset(
        {TradeState.COMMITTED, TradeState.FAILED, TradeState.RECONCILIATION_REQUIRED}
    ),
    TradeState.COMMITTED: frozenset({TradeState.FULFILLING, TradeState.RECONCILIATION_REQUIRED}),
    TradeState.FULFILLING: frozenset({TradeState.VERIFIED, TradeState.RECONCILIATION_REQUIRED}),
    TradeState.VERIFIED: frozenset(),
    TradeState.REJECTED: frozenset(),
    TradeState.EXPIRED: frozenset(),
    TradeState.CANCELLED: frozenset(),
    TradeState.FAILED: frozenset(),
    TradeState.RECONCILIATION_REQUIRED: frozenset(),
}


def is_valid_transition(current: TradeState, target: TradeState) -> bool:
    return target in VALID_TRANSITIONS.get(current, frozenset())
