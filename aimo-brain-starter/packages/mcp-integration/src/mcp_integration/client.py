"""Generic, domain-agnostic MCP tool-calling client (M3).

Wraps the official MCP Python SDK's Streamable HTTP client
(``mcp.client.streamable_http``) with: a scoped bearer credential on every
call, a bounded request timeout, a small number of bounded retries for
connection-level failures only (never for a tool's own business-logic
error), and a typed exception hierarchy so callers never have to inspect
raw SDK internals.

This module knows nothing about catalogues, budgets or any other
business concept — see ``industries/food_wholesale`` for the tool
definitions themselves and ``apps/buyer-mcp``/``apps/supplier-mcp`` for the
servers that expose them.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

#: Authentication/authorization failures surfaced by the server as a tool
#: error (see ``mcp_integration.server_auth``). Matched against the start
#: of the tool error's message so the client can distinguish "the server
#: rejected this caller" from "the tool's own business logic failed."
_AUTH_ERROR_PREFIX = "Unauthorized:"


class McpToolError(Exception):
    """Base class for all MCP tool-call failures."""


class McpUnauthorizedError(McpToolError):
    """The server rejected the caller's credential (missing, malformed,
    expired, wrong scope or wrong organization)."""


class McpInvalidArgumentsError(McpToolError):
    """The server rejected the call's arguments against the tool's input
    schema."""


class McpServerUnavailableError(McpToolError):
    """The MCP server could not be reached at all, or timed out, after
    exhausting bounded retries."""


class McpMalformedResponseError(McpToolError):
    """The server returned something that does not fit the tool's
    expected structured-output shape."""


async def call_tool(
    *,
    server_url: str,
    token: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    request_timeout_seconds: float = 10.0,
    max_attempts: int = 2,
    retry_backoff_seconds: float = 0.2,
) -> dict[str, Any] | None:
    """Call ``tool_name`` on the MCP server at ``server_url`` and return its
    structured content (``None`` if the tool defines no structured output).

    Raises :class:`McpUnauthorizedError`, :class:`McpInvalidArgumentsError`,
    :class:`McpServerUnavailableError` or :class:`McpMalformedResponseError`
    — never lets a raw SDK/transport exception escape.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last_unavailable_error: McpServerUnavailableError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await _call_once(
                server_url=server_url,
                token=token,
                tool_name=tool_name,
                arguments=arguments or {},
                request_timeout_seconds=request_timeout_seconds,
            )
        except McpServerUnavailableError as exc:
            last_unavailable_error = exc
            if attempt < max_attempts:
                await asyncio.sleep(retry_backoff_seconds)
            continue

    assert last_unavailable_error is not None  # loop always sets it before exhausting
    raise last_unavailable_error


async def _call_once(
    *,
    server_url: str,
    token: str,
    tool_name: str,
    arguments: dict[str, Any],
    request_timeout_seconds: float,
) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=request_timeout_seconds,
        ) as httpx_client:
            async with streamable_http_client(server_url, http_client=httpx_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
    except (httpx.HTTPError, OSError, TimeoutError, ExceptionGroup) as exc:
        raise McpServerUnavailableError(
            f"Could not complete MCP call to {server_url!r} tool {tool_name!r}: {exc}"
        ) from exc

    if result.isError:
        message = _error_text(result)
        # The MCP SDK wraps a tool function's raised exception as
        # "Error executing tool <name>: <original message>" (see
        # mcp.server.fastmcp.tools.base.Tool.run) before this ever
        # reaches the client, so the original prefix is no longer at the
        # start of the string — match it anywhere instead.
        if _AUTH_ERROR_PREFIX in message:
            raise McpUnauthorizedError(message)
        if "validation error" in message.lower():
            raise McpInvalidArgumentsError(message)
        raise McpToolError(message)

    return result.structuredContent


def _error_text(result: Any) -> str:
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            return text
    return "MCP tool call failed with no error detail."
