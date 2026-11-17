import pytest
from a2a_integration import build_agent_card


def test_agent_card_construction_not_yet_implemented() -> None:
    """M1 wires this to the official A2A SDK; M0 must not fake it."""
    with pytest.raises(NotImplementedError):
        build_agent_card(agent_id="agent_1", endpoint_url="https://example.invalid/a2a")
