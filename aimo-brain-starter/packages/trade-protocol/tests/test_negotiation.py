from __future__ import annotations

import pytest
from trade_protocol import NegotiationTransitionError, assert_valid_transition, content_hash
from trade_protocol.lifecycle import TradeState


def test_content_hash_is_deterministic_regardless_of_key_order() -> None:
    terms_a = {"currency": "EUR", "lines": [{"product_ref": "sku_1", "unit_price": "1.19"}]}
    terms_b = {"lines": [{"unit_price": "1.19", "product_ref": "sku_1"}], "currency": "EUR"}
    assert content_hash(terms_a) == content_hash(terms_b)


def test_content_hash_differs_for_different_terms() -> None:
    terms_a = {"currency": "EUR", "lines": [{"unit_price": "1.19"}]}
    terms_b = {"currency": "EUR", "lines": [{"unit_price": "1.20"}]}
    assert content_hash(terms_a) != content_hash(terms_b)


def test_valid_transition_does_not_raise() -> None:
    assert_valid_transition(TradeState.REQUESTED, TradeState.PROPOSED)
    assert_valid_transition(TradeState.PROPOSED, TradeState.NEGOTIATING)
    assert_valid_transition(TradeState.NEGOTIATING, TradeState.PENDING_AUTHORIZATION)


def test_invalid_transition_raises() -> None:
    with pytest.raises(NegotiationTransitionError):
        assert_valid_transition(TradeState.REQUESTED, TradeState.PENDING_AUTHORIZATION)


def test_negotiating_cannot_skip_straight_to_committed() -> None:
    with pytest.raises(NegotiationTransitionError):
        assert_valid_transition(TradeState.NEGOTIATING, TradeState.COMMITTED)


# Note: PENDING_AUTHORIZATION -> AUTHORIZED is a *legal* transition in the
# generic lifecycle table (it is how M5's policy-approved path proceeds).
# M4's own guarantee that no negotiation code path reaches it is enforced
# by static scanning of the M4 modules themselves (never constructing
# TradeState.AUTHORIZED or later) — see
# tests/security/test_negotiation_security.py.
