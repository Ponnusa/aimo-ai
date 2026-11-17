"""Mock buyer-side business tools.

Fictional data only (per CLAUDE.md: "seed data is fictional"). This module
is the contract ``apps/buyer-mcp`` wraps as MCP tools starting M3 — plain,
typed, synchronous-shaped functions with no network I/O and no connection
to any Valio Aimo system. ``request_purchase_approval`` returns a
deterministic mock decision; it is **not** OPA and **not** a binding
authorization (that is M5 scope).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PurchaseRequirement:
    product_query: str
    quantity: str
    unit: str
    needed_by: str


@dataclass(frozen=True, slots=True)
class Budget:
    organization_id: str
    currency: str
    remaining_amount: Decimal


@dataclass(frozen=True, slots=True)
class ApprovedSupplier:
    organization_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class PurchaseApprovalDecision:
    decision: str
    reason: str


_FICTIONAL_REQUIREMENTS: tuple[PurchaseRequirement, ...] = (
    PurchaseRequirement(
        product_query="lactose-free milk",
        quantity="100",
        unit="L",
        needed_by="2026-11-19",
    ),
)

_FICTIONAL_BUDGETS: dict[str, Budget] = {
    "org_fictional_bistro_01": Budget(
        organization_id="org_fictional_bistro_01",
        currency="EUR",
        remaining_amount=Decimal("500.00"),
    ),
}

_FICTIONAL_APPROVED_SUPPLIERS: dict[str, tuple[ApprovedSupplier, ...]] = {
    "org_fictional_bistro_01": (
        ApprovedSupplier(
            organization_id="org_fictional_wholesale_01",
            display_name="Fictional Wholesale Supplier Co",
        ),
    ),
}


def get_purchase_requirements() -> list[PurchaseRequirement]:
    """Return this buyer's outstanding fictional purchase requirements."""
    return list(_FICTIONAL_REQUIREMENTS)


def get_budget(organization_id: str) -> Budget:
    """Return the fictional remaining purchasing budget for ``organization_id``.

    Unknown organizations get a zero-remaining budget rather than an
    error — there is no real ERP connection to consult here, so "unknown"
    and "exhausted" are deliberately indistinguishable at this mock layer.
    """
    return _FICTIONAL_BUDGETS.get(
        organization_id,
        Budget(organization_id=organization_id, currency="EUR", remaining_amount=Decimal("0.00")),
    )


def list_approved_suppliers(organization_id: str) -> list[ApprovedSupplier]:
    """Return the fictional list of suppliers this buyer org has pre-approved."""
    return list(_FICTIONAL_APPROVED_SUPPLIERS.get(organization_id, ()))


def evaluate_purchase_approval(
    *, organization_id: str, amount: Decimal, currency: str
) -> PurchaseApprovalDecision:
    """Return a deterministic mock approval decision for a proposed spend.

    This is a stand-in business-tool response only — it is never consulted
    as the authorization check before a binding commit. OPA policy
    evaluation and human-approval binding are M5 scope
    (``docs/ROADMAP.md``); nothing here enforces anything.
    """
    budget = get_budget(organization_id)
    if currency != budget.currency:
        return PurchaseApprovalDecision(
            decision="denied", reason=f"Currency mismatch: expected {budget.currency}."
        )
    if amount > budget.remaining_amount:
        return PurchaseApprovalDecision(
            decision="denied", reason="Amount exceeds remaining budget."
        )
    return PurchaseApprovalDecision(decision="approved", reason="Within remaining budget.")
