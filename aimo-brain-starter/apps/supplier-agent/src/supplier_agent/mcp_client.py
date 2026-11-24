"""Supplier-side MCP tool client (M3).

This is the only place in ``supplier_agent`` that touches catalogue,
inventory or pricing data — it calls ``supplier-mcp`` over the official
MCP SDK's Streamable HTTP transport (via ``mcp_integration``) rather than
importing ``food_wholesale.adapters.mock`` directly, per CLAUDE.md's "use
the official MCP SDK for agent-to-tool integration" and the M3 technical
requirement that agents use MCP clients, not direct tool imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp_integration import McpMalformedResponseError, call_tool

from supplier_agent.settings import SupplierAgentSettings


@dataclass(frozen=True)
class OrderExecutionResult:
    idempotency_key: str
    external_order_id: str
    status: str
    duplicate: bool


async def execute_order(
    *,
    settings: SupplierAgentSettings,
    idempotency_key: str,
    agreement_id: str,
    content_hash: str,
    buyer_organization_id: str,
    lines: list[dict[str, str]] | None = None,
) -> OrderExecutionResult:
    """Call supplier-mcp's narrow ``execute_order`` tool (M5) with the
    separate execution credential — never the read-only
    ``use_supplier_tools`` one. ``lines`` (M7) is passed through to
    whichever business-system adapter supplier-mcp is configured with —
    the mock adapter ignores it, a realistic one (sandbox/Valio) may use
    it (e.g. to decrement its own stock). Propagates
    :class:`mcp_integration.McpToolError` on any failure; callers decide
    how to turn that into ``FAILED``/``RECONCILIATION_REQUIRED``."""
    result = await call_tool(
        server_url=settings.supplier_mcp_url,
        token=settings.supplier_mcp_execution_credential,
        tool_name="execute_order",
        arguments={
            "idempotency_key": idempotency_key,
            "agreement_id": agreement_id,
            "content_hash": content_hash,
            "buyer_organization_id": buyer_organization_id,
            "lines": lines or [],
        },
    )
    try:
        if result is None:
            raise TypeError("execute_order returned no result")
        return OrderExecutionResult(
            idempotency_key=result["idempotency_key"],
            external_order_id=result["external_order_id"],
            status=result["status"],
            duplicate=result["duplicate"],
        )
    except (KeyError, TypeError) as exc:
        raise McpMalformedResponseError(f"Malformed execute_order response: {exc}") from exc


async def get_order_status(
    *, settings: SupplierAgentSettings, idempotency_key: str
) -> dict[str, Any]:
    """Reconciliation lookup (M5) -- same narrow execution credential."""
    return (
        await call_tool(
            server_url=settings.supplier_mcp_url,
            token=settings.supplier_mcp_execution_credential,
            tool_name="get_order_status",
            arguments={"idempotency_key": idempotency_key},
        )
        or {}
    )


async def fetch_availability_matches(
    *, settings: SupplierAgentSettings, lines: list[dict[str, Any]], buyer_organization_id: str
) -> dict[str, list[dict[str, Any]]]:
    """For each requested line's ``product_query``, search the catalogue,
    then enrich each match with live inventory and this buyer's
    customer-specific price, over MCP.

    Returns ``{product_query: [enriched_line, ...]}``. Propagates
    :class:`mcp_integration.McpToolError` subclasses on any tool-call
    failure — callers (the AgentExecutor) turn that into a failed A2A
    task, never a silent empty result.
    """
    matches_by_query: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        query = line["product_query"]
        catalogue_result = await call_tool(
            server_url=settings.supplier_mcp_url,
            token=settings.supplier_mcp_service_credential,
            tool_name="search_catalogue",
            arguments={"query": query},
        )
        catalogue_matches = (catalogue_result or {}).get("result", [])

        try:
            enriched: list[dict[str, Any]] = []
            for match in catalogue_matches:
                product_ref = match["product_ref"]
                inventory = (
                    await call_tool(
                        server_url=settings.supplier_mcp_url,
                        token=settings.supplier_mcp_service_credential,
                        tool_name="check_inventory",
                        arguments={"product_ref": product_ref},
                    )
                    or {}
                )
                price = (
                    await call_tool(
                        server_url=settings.supplier_mcp_url,
                        token=settings.supplier_mcp_service_credential,
                        tool_name="get_customer_price",
                        arguments={
                            "product_ref": product_ref,
                            "buyer_organization_id": buyer_organization_id,
                        },
                    )
                    or {}
                )
                enriched.append(
                    {
                        "product_ref": product_ref,
                        "name": match["name"],
                        "unit": match["unit"],
                        "unit_price": price["unit_price"]
                        if price["found"]
                        else match["unit_price"],
                        "stock_today": inventory["stock_today"]
                        if inventory["found"]
                        else match["stock_today"],
                        "stock_tomorrow": inventory["stock_tomorrow"]
                        if inventory["found"]
                        else match["stock_tomorrow"],
                    }
                )
        except (KeyError, TypeError) as exc:
            # A tool response missing an expected field, or of the wrong
            # shape entirely — whether from a bug or a compromised/
            # malicious MCP server — must surface as a clear, typed
            # failure, never an opaque crash or a silently-wrong result.
            raise McpMalformedResponseError(
                f"Malformed response from supplier-mcp while resolving {query!r}: {exc}"
            ) from exc
        matches_by_query[query] = enriched

    return matches_by_query
