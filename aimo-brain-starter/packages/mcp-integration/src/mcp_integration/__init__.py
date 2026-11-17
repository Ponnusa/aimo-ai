"""MCP integration package boundary.

**M0 status: scaffold only.** This package will wrap the official MCP
Python SDK (https://github.com/modelcontextprotocol/python-sdk) for
server-side-authorized tool calls (catalogue search, stock, pricing, order
draft/submit) starting M3 (``docs/ROADMAP.md``). No tool call path exists
yet; nothing here can be mistaken for a real, authorized tool invocation.
"""

from __future__ import annotations


def call_tool(*, tool_name: str, arguments: dict) -> None:
    raise NotImplementedError("Authenticated MCP tool calls land in M3. Not implemented in M0.")
