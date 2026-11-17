"""MCP integration package boundary.

**M3 status:** real. Wraps the official MCP Python SDK
(https://github.com/modelcontextprotocol/python-sdk, pinned ``mcp==1.30.0``
after introspecting its real API) for scoped, authenticated,
bounded-retry tool calls over Streamable HTTP. See :mod:`mcp_integration.client`
for the client used by ``buyer-agent``/``supplier-agent``, and
:mod:`mcp_integration.server_auth` for the server-side credential check
shared by ``apps/buyer-mcp``/``apps/supplier-mcp``.
"""

from __future__ import annotations

from mcp_integration.client import (
    McpInvalidArgumentsError,
    McpMalformedResponseError,
    McpServerUnavailableError,
    McpToolError,
    McpUnauthorizedError,
    call_tool,
)
from mcp_integration.server_auth import require_tool_scope

__all__ = [
    "McpInvalidArgumentsError",
    "McpMalformedResponseError",
    "McpServerUnavailableError",
    "McpToolError",
    "McpUnauthorizedError",
    "call_tool",
    "require_tool_scope",
]
