import pytest
from mcp_integration import call_tool


def test_tool_call_not_yet_implemented() -> None:
    """M3 wires this to the official MCP SDK; M0 must not fake it."""
    with pytest.raises(NotImplementedError):
        call_tool(tool_name="food.search_products", arguments={"query": "milk"})
