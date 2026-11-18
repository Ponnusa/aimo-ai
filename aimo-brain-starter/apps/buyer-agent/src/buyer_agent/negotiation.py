"""Buyer-side negotiation policy (M4).

Pure, deterministic evaluation of a supplier's quote against this
organization's own MCP-sourced remaining budget. No A2A or MCP I/O
happens here (that lives in ``buyer_agent.client`` and
``buyer_agent.mcp_client``) and no LLM is consulted for the decision
itself — per CLAUDE.md, "LLM outputs are untrusted proposals" and must
never decide a negotiation outcome. Works over an arbitrary set of line
items; nothing here is written against one specific product.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

#: When countering, target slightly under the remaining budget rather
#: than exactly at it, so a counteroffer that is accepted as-is does not
#: immediately exhaust the budget to the last cent.
COUNTER_TARGET_MARGIN_FRACTION = Decimal("0.95")

#: Never counter with a unit price at or below zero.
_MINIMUM_UNIT_PRICE = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class BudgetEvaluation:
    within_budget: bool
    total_cost: Decimal
    counter_lines: list[dict[str, Any]] | None


def total_cost_of(lines: list[dict[str, Any]]) -> Decimal:
    return sum(
        (Decimal(line["unit_price"]) * Decimal(line["quantity"]) for line in lines),
        start=Decimal("0"),
    )


def evaluate_quote_against_budget(
    *,
    lines: list[dict[str, Any]],
    remaining_budget: Decimal,
) -> BudgetEvaluation:
    """Return whether ``lines`` (at their currently offered unit prices)
    fit within ``remaining_budget``, and if not, a scaled-down
    counteroffer: every line's unit price is reduced by the same factor
    so the new total lands at ``COUNTER_TARGET_MARGIN_FRACTION`` of the
    budget."""
    total_cost = total_cost_of(lines)
    if total_cost <= remaining_budget:
        return BudgetEvaluation(within_budget=True, total_cost=total_cost, counter_lines=None)

    target_total = (remaining_budget * COUNTER_TARGET_MARGIN_FRACTION).quantize(Decimal("0.01"))
    scale = (target_total / total_cost) if total_cost > 0 else Decimal("0")

    counter_lines = []
    for line in lines:
        scaled_price = (Decimal(line["unit_price"]) * scale).quantize(Decimal("0.01"))
        counter_lines.append({**line, "unit_price": str(max(scaled_price, _MINIMUM_UNIT_PRICE))})
    return BudgetEvaluation(within_budget=False, total_cost=total_cost, counter_lines=counter_lines)
