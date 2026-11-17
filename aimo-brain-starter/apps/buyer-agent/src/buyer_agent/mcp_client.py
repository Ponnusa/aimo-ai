"""Buyer-side MCP tool client (M3).

The only place in ``buyer_agent`` that touches purchase requirements,
budget, approved-supplier or approval data — it calls ``buyer-mcp`` over
the official MCP SDK's Streamable HTTP transport (via ``mcp_integration``),
never a direct function import, per CLAUDE.md's "use the official MCP SDK
for agent-to-tool integration."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp_integration import call_tool

from buyer_agent.settings import BuyerAgentSettings


@dataclass(frozen=True, slots=True)
class PurchasingContext:
    requirements: list[dict[str, Any]]
    budget: dict[str, Any]


async def fetch_purchasing_context(*, settings: BuyerAgentSettings) -> PurchasingContext:
    """Fetch this organization's outstanding purchase requirements and
    remaining budget from its own buyer-mcp server, before any A2A
    request is sent.

    Propagates :class:`mcp_integration.McpToolError` subclasses on any
    tool-call failure — callers decide whether that should block the
    request or only be logged (M3 does not gate the A2A request on this;
    budget/requirement-driven blocking is M4/M5 policy scope).
    """
    requirements_result = await call_tool(
        server_url=settings.buyer_mcp_url,
        token=settings.buyer_mcp_service_credential,
        tool_name="get_purchase_requirements",
    )
    budget_result = await call_tool(
        server_url=settings.buyer_mcp_url,
        token=settings.buyer_mcp_service_credential,
        tool_name="get_budget",
        arguments={"organization_id": settings.buyer_organization_id},
    )
    requirements = (requirements_result or {}).get("result", [])
    return PurchasingContext(requirements=requirements, budget=budget_result or {})
