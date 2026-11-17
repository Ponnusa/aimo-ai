from dataclasses import dataclass

import pytest
from mcp_integration.client import _error_text


@dataclass
class _FakeBlock:
    text: str | None


@dataclass
class _FakeResult:
    content: list[_FakeBlock]


def test_error_text_returns_first_text_block() -> None:
    result = _FakeResult(content=[_FakeBlock(text=None), _FakeBlock(text="Unauthorized: nope")])
    assert _error_text(result) == "Unauthorized: nope"


def test_error_text_falls_back_when_no_text_block() -> None:
    result = _FakeResult(content=[_FakeBlock(text=None)])
    assert _error_text(result) == "MCP tool call failed with no error detail."


@pytest.mark.asyncio
async def test_call_tool_rejects_non_positive_max_attempts() -> None:
    from mcp_integration.client import call_tool

    with pytest.raises(ValueError):
        await call_tool(
            server_url="http://127.0.0.1:1",
            token="irrelevant",
            tool_name="irrelevant",
            max_attempts=0,
        )


@pytest.mark.asyncio
async def test_call_tool_raises_server_unavailable_when_unreachable() -> None:
    from mcp_integration.client import McpServerUnavailableError, call_tool

    with pytest.raises(McpServerUnavailableError):
        await call_tool(
            server_url="http://127.0.0.1:1",
            token="irrelevant",
            tool_name="irrelevant",
            request_timeout_seconds=1.0,
            max_attempts=1,
        )
