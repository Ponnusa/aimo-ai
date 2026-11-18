"""Domain-independent negotiation primitives, per ``docs/PROTOCOLS.md``.

Food-wholesale (and future industries) build versioned RFQ/quote/
counterproposal/proposed-agreement artifacts on top of these two things:

1. A deterministic content hash, so two independently-hosted organizations
   can verify they converged on *exactly* the same terms without trusting
   either side's own claim of agreement.
2. A guard around :func:`trade_protocol.lifecycle.is_valid_transition`, so
   "no M4 code path reaches a binding state" is a fact enforced at every
   call site, not a convention.

Nothing here knows what a "line item" or "SKU" is — that is the domain
module's job.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from trade_protocol.lifecycle import TradeState, is_valid_transition


class NegotiationTransitionError(Exception):
    """Raised when a requested negotiation state transition is not legal
    from the negotiation's current state."""


def canonicalize_terms(terms: dict[str, Any]) -> str:
    """Deterministic JSON serialization: sorted keys, no incidental
    whitespace. The same logical terms always produce the same string,
    regardless of which organization's code constructed the dict."""
    return json.dumps(terms, sort_keys=True, separators=(",", ":"))


def content_hash(terms: dict[str, Any]) -> str:
    """SHA-256 hex digest of :func:`canonicalize_terms`. Used to bind a
    response to an exact proposal version and to verify both sides
    converged on identical terms before a ``proposed_agreement`` is
    recorded — never to prove legal enforceability."""
    return hashlib.sha256(canonicalize_terms(terms).encode("utf-8")).hexdigest()


def assert_valid_transition(current: TradeState, target: TradeState) -> None:
    """Raise :class:`NegotiationTransitionError` if ``target`` is not a
    legal transition from ``current``, per
    :data:`trade_protocol.lifecycle.VALID_TRANSITIONS`."""
    if not is_valid_transition(current, target):
        raise NegotiationTransitionError(
            f"Illegal negotiation transition: {current.value} -> {target.value}"
        )
