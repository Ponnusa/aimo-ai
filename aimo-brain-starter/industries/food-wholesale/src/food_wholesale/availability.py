"""Product availability request/response: food-wholesale domain logic.

This is where "food wholesale" actually lives for M1's reference
interaction. ``buyer_agent`` and ``supplier_agent`` call into this module
rather than encoding any product/catalogue knowledge themselves — per
CLAUDE.md: "no SKU assumptions in core." The agent runtime only knows it is
calling some domain handler for some skill; it does not know what a
"product" or a "unit" is.

Scope note: this is a lighter precursor to the full RFQ/quote negotiation
lifecycle (``docs/PROTOCOLS.md``, M4). There is no price commitment,
validity window, or negotiation state here — just "do you have this."
"""

from __future__ import annotations

from typing import Any

from food_wholesale.adapters.mock import search_products
from food_wholesale.schemas import validate_against_schema

_SCHEMA_BASE = "https://schemas.aimobrain.example/food-wholesale"


def build_request(
    lines: list[dict[str, str]],
    *,
    buyer_organization_id: str,
) -> dict[str, Any]:
    """Build and validate a ``product_availability_request`` payload.

    ``lines`` is a list of ``{"product_query": ..., "quantity": ..., "unit": ...}``.
    """
    payload: dict[str, Any] = {
        "schema": f"{_SCHEMA_BASE}/product_availability_request/v0.1",
        "type": "product_availability_request",
        "version": 1,
        "buyer_organization_id": buyer_organization_id,
        "lines": lines,
    }
    validate_against_schema("product_availability_request", payload)
    return payload


def handle_request(
    payload: dict[str, Any],
    *,
    supplier_organization_id: str,
) -> dict[str, Any]:
    """Handle a validated ``product_availability_request``, using the mock
    catalogue, and return a validated ``product_availability_response``.

    Raises ``jsonschema.ValidationError`` if ``payload`` does not conform to
    the request schema — callers (the supplier's AgentExecutor) are
    expected to turn that into an A2A task failure, never to silently
    reinterpret malformed input.
    """
    validate_against_schema("product_availability_request", payload)

    results: list[dict[str, Any]] = []
    for line in payload["lines"]:
        matches = search_products(line["product_query"])
        results.append(
            {
                "product_query": line["product_query"],
                "matched": bool(matches),
                "lines": [
                    {
                        "product_ref": p.product_ref,
                        "name": p.name,
                        "unit": p.unit,
                        "unit_price": str(p.unit_price),
                        "stock_today": str(p.stock_today),
                        "stock_tomorrow": str(p.stock_tomorrow),
                    }
                    for p in matches
                ],
            }
        )

    response: dict[str, Any] = {
        "schema": f"{_SCHEMA_BASE}/product_availability_response/v0.1",
        "type": "product_availability_response",
        "version": 1,
        "supplier_organization_id": supplier_organization_id,
        "results": results,
    }
    validate_against_schema("product_availability_response", response)
    return response


def assemble_response_from_matches(
    payload: dict[str, Any],
    *,
    supplier_organization_id: str,
    matches_by_query: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build and validate a ``product_availability_response`` from
    already-fetched match data, keyed by ``product_query``.

    Unlike :func:`handle_request`, this never touches a catalogue/stock
    adapter itself — callers (``supplier_agent.mcp_client``) are expected
    to have sourced ``matches_by_query`` over MCP. This is the schema-only
    half of :func:`handle_request`, split out so the MCP-sourced path and
    the direct-mock path share identical envelope/validation logic.
    """
    validate_against_schema("product_availability_request", payload)

    results: list[dict[str, Any]] = []
    for line in payload["lines"]:
        matches = matches_by_query.get(line["product_query"], [])
        results.append(
            {
                "product_query": line["product_query"],
                "matched": bool(matches),
                "lines": matches,
            }
        )

    response: dict[str, Any] = {
        "schema": f"{_SCHEMA_BASE}/product_availability_response/v0.1",
        "type": "product_availability_response",
        "version": 1,
        "supplier_organization_id": supplier_organization_id,
        "results": results,
    }
    validate_against_schema("product_availability_response", response)
    return response


def validate_response(payload: dict[str, Any]) -> None:
    """Raise ``jsonschema.ValidationError`` if ``payload`` is not a valid
    ``product_availability_response``. Used by the buyer side to validate
    what it received before trusting it."""
    validate_against_schema("product_availability_response", payload)
