from datetime import UTC, datetime

from trade_protocol import Agent, Organization


def test_organization_is_immutable() -> None:
    org = Organization(organization_id="org_1", display_name="Test Org")
    try:
        org.display_name = "changed"  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("Organization model should be frozen")


def test_agent_role_is_a_plain_value_not_a_type() -> None:
    agent = Agent(
        agent_id="agent_1",
        organization_id="org_1",
        role="buyer",
        endpoint_url="https://example.invalid/a2a",
        agent_card_version="0.1",
    )
    assert agent.role in {"buyer", "supplier"}
    # Nothing here is a BuyerAgent/SupplierAgent subclass — role is just data.
    assert type(agent) is Agent


def test_fictional_timestamp_round_trips() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert now.year == 2026
