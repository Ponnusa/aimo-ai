from trade_protocol.lifecycle import TERMINAL_STATES, TradeState, is_valid_transition


def test_discovered_can_move_to_requested() -> None:
    assert is_valid_transition(TradeState.DISCOVERED, TradeState.REQUESTED)


def test_cannot_skip_authorization_before_committing() -> None:
    assert not is_valid_transition(TradeState.PROPOSED, TradeState.COMMITTING)


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for state in TERMINAL_STATES:
        assert is_valid_transition(state, TradeState.REQUESTED) is False


def test_verified_is_terminal() -> None:
    assert TradeState.VERIFIED in TERMINAL_STATES
