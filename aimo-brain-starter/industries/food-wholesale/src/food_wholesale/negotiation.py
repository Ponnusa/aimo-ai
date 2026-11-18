"""Food-wholesale RFQ/quote/counterproposal/proposed-agreement builders.

This is the domain-specific half of M4's negotiation framework: it knows
what a "line item" and "unit price" are. The generic, industry-independent
half (content hashing, state-transition guards) lives in
``trade_protocol.negotiation`` and is only ever called from here, never
re-implemented.

**Everything in this module is explicitly nonbinding** (per
``docs/ROADMAP.md`` M4 and ``PROJECT.md``): nothing here creates an order,
reservation or payment, and ``build_proposed_agreement`` always stamps its
result ``"status": "NONBINDING"``. Deciding whether, and how, to actually
commit to a proposed agreement is M5 scope (OPA policy + human approval).

Supplier-side quote/counter-quote data must come from the supplier's own
MCP tools (see ``supplier_agent.mcp_client``) — nothing here invents a
price or stock figure; callers pass already-fetched line data in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from trade_protocol import content_hash

from food_wholesale.schemas import validate_against_schema

_SCHEMA_BASE = "https://schemas.aimobrain.example/food-wholesale"

#: Supplier never concedes below this fraction of its own catalogue price
#: when counter-offering — a generic margin floor, not specific to any
#: one product.
MINIMUM_MARGIN_FRACTION = "0.90"

#: Hard cap on negotiation rounds before the supplier rejects outright,
#: so a negotiation cannot loop forever.
MAX_NEGOTIATION_ROUNDS = 4


def _envelope(
    *,
    artifact_id: str,
    transaction_id: str,
    correlation_id: str,
    sender_agent_id: str,
    recipient_agent_id: str,
    organization_id: str,
    artifact_type: str,
    version: int,
    proposal_id: str,
    payload: dict[str, Any],
    validity: timedelta,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    artifact: dict[str, Any] = {
        "schema": f"{_SCHEMA_BASE}/{artifact_type}/v0.1",
        "artifact_id": artifact_id,
        "transaction_id": transaction_id,
        "correlation_id": correlation_id,
        "sender_agent_id": sender_agent_id,
        "recipient_agent_id": recipient_agent_id,
        "organization_id": organization_id,
        "domain": "food-wholesale",
        "type": artifact_type,
        "version": version,
        "proposal_id": proposal_id,
        "created_at": now.isoformat(),
        "expires_at": (now + validity).isoformat(),
        "payload": payload,
    }
    if extra:
        artifact.update(extra)
    return artifact


def build_rfq(
    *,
    artifact_id: str,
    transaction_id: str,
    correlation_id: str,
    buyer_agent_id: str,
    supplier_agent_id: str,
    buyer_organization_id: str,
    proposal_id: str,
    lines: list[dict[str, str]],
    delivery_location_ref: str,
    validity: timedelta = timedelta(days=1),
) -> dict[str, Any]:
    """Build and validate a ``request_for_quote`` artifact. Always
    proposal version 1 of its negotiation."""
    artifact = _envelope(
        artifact_id=artifact_id,
        transaction_id=transaction_id,
        correlation_id=correlation_id,
        sender_agent_id=buyer_agent_id,
        recipient_agent_id=supplier_agent_id,
        organization_id=buyer_organization_id,
        artifact_type="request_for_quote",
        version=1,
        proposal_id=proposal_id,
        payload={"lines": lines, "delivery_location_ref": delivery_location_ref},
        validity=validity,
    )
    validate_against_schema("request_for_quote", artifact)
    return artifact


def _quote_terms(
    lines: list[dict[str, str]], *, currency: str, delivery_terms: str | None
) -> dict[str, Any]:
    terms: dict[str, Any] = {"lines": lines, "currency": currency}
    if delivery_terms is not None:
        terms["delivery_terms"] = delivery_terms
    return terms


def build_quote(
    *,
    artifact_id: str,
    transaction_id: str,
    correlation_id: str,
    supplier_agent_id: str,
    buyer_agent_id: str,
    supplier_organization_id: str,
    proposal_id: str,
    supersedes_proposal_id: str | None,
    version: int,
    lines: list[dict[str, str]],
    currency: str,
    delivery_terms: str | None = None,
    validity: timedelta = timedelta(hours=4),
) -> dict[str, Any]:
    """Build and validate a ``quote`` artifact from already-sourced line
    data (from the supplier's own MCP tools — never invented here)."""
    terms = _quote_terms(lines, currency=currency, delivery_terms=delivery_terms)
    artifact = _envelope(
        artifact_id=artifact_id,
        transaction_id=transaction_id,
        correlation_id=correlation_id,
        sender_agent_id=supplier_agent_id,
        recipient_agent_id=buyer_agent_id,
        organization_id=supplier_organization_id,
        artifact_type="quote",
        version=version,
        proposal_id=proposal_id,
        payload=terms,
        validity=validity,
        extra={
            "supersedes_proposal_id": supersedes_proposal_id,
            "content_hash": content_hash(terms),
        },
    )
    validate_against_schema("quote", artifact)
    return artifact


def build_counterproposal(
    *,
    artifact_id: str,
    transaction_id: str,
    correlation_id: str,
    buyer_agent_id: str,
    supplier_agent_id: str,
    buyer_organization_id: str,
    proposal_id: str,
    supersedes_proposal_id: str,
    version: int,
    lines: list[dict[str, str]],
    currency: str,
    delivery_terms: str | None = None,
    validity: timedelta = timedelta(hours=4),
) -> dict[str, Any]:
    """Build and validate a ``counterproposal`` artifact: the buyer's
    revised terms, binding to the exact quote/counter it is revising."""
    terms = _quote_terms(lines, currency=currency, delivery_terms=delivery_terms)
    artifact = _envelope(
        artifact_id=artifact_id,
        transaction_id=transaction_id,
        correlation_id=correlation_id,
        sender_agent_id=buyer_agent_id,
        recipient_agent_id=supplier_agent_id,
        organization_id=buyer_organization_id,
        artifact_type="counterproposal",
        version=version,
        proposal_id=proposal_id,
        payload=terms,
        validity=validity,
        extra={
            "supersedes_proposal_id": supersedes_proposal_id,
            "content_hash": content_hash(terms),
        },
    )
    validate_against_schema("counterproposal", artifact)
    return artifact


def build_accept_proposal(
    *,
    artifact_id: str,
    transaction_id: str,
    correlation_id: str,
    sender_agent_id: str,
    recipient_agent_id: str,
    organization_id: str,
    proposal_id: str,
    version: int,
    accepted_proposal_id: str,
    accepted_content_hash: str,
    validity: timedelta = timedelta(hours=1),
) -> dict[str, Any]:
    """Build and validate an ``accept_proposal`` artifact: binds to one
    exact proposal_id and content_hash, never a vague acceptance."""
    artifact = _envelope(
        artifact_id=artifact_id,
        transaction_id=transaction_id,
        correlation_id=correlation_id,
        sender_agent_id=sender_agent_id,
        recipient_agent_id=recipient_agent_id,
        organization_id=organization_id,
        artifact_type="accept_proposal",
        version=version,
        proposal_id=proposal_id,
        payload={
            "accepted_proposal_id": accepted_proposal_id,
            "accepted_content_hash": accepted_content_hash,
        },
        validity=validity,
    )
    validate_against_schema("accept_proposal", artifact)
    return artifact


def build_reject_proposal(
    *,
    artifact_id: str,
    transaction_id: str,
    correlation_id: str,
    sender_agent_id: str,
    recipient_agent_id: str,
    organization_id: str,
    proposal_id: str,
    version: int,
    rejected_proposal_id: str,
    reason: str,
    validity: timedelta = timedelta(hours=1),
) -> dict[str, Any]:
    """Build and validate a ``reject_proposal`` artifact: terminal,
    explicit rejection or cancellation of a negotiation."""
    artifact = _envelope(
        artifact_id=artifact_id,
        transaction_id=transaction_id,
        correlation_id=correlation_id,
        sender_agent_id=sender_agent_id,
        recipient_agent_id=recipient_agent_id,
        organization_id=organization_id,
        artifact_type="reject_proposal",
        version=version,
        proposal_id=proposal_id,
        payload={"rejected_proposal_id": rejected_proposal_id, "reason": reason},
        validity=validity,
    )
    validate_against_schema("reject_proposal", artifact)
    return artifact


def build_proposed_agreement(
    *,
    artifact_id: str,
    transaction_id: str,
    correlation_id: str,
    sender_agent_id: str,
    recipient_agent_id: str,
    organization_id: str,
    proposal_id: str,
    version: int,
    agreement_of_proposal_id: str,
    lines: list[dict[str, str]],
    currency: str,
    delivery_terms: str | None,
    validity: timedelta = timedelta(hours=1),
) -> dict[str, Any]:
    """Build and validate a ``proposed_agreement`` artifact: the exact
    canonical terms both parties converged on, explicitly NONBINDING."""
    terms = _quote_terms(lines, currency=currency, delivery_terms=delivery_terms)
    payload = dict(terms)
    payload["status"] = "NONBINDING"
    artifact = _envelope(
        artifact_id=artifact_id,
        transaction_id=transaction_id,
        correlation_id=correlation_id,
        sender_agent_id=sender_agent_id,
        recipient_agent_id=recipient_agent_id,
        organization_id=organization_id,
        artifact_type="proposed_agreement",
        version=version,
        proposal_id=proposal_id,
        payload=payload,
        validity=validity,
        extra={
            "agreement_of_proposal_id": agreement_of_proposal_id,
            "content_hash": content_hash(terms),
        },
    )
    validate_against_schema("proposed_agreement", artifact)
    return artifact


def evaluate_counterproposal(
    *,
    countered_lines: list[dict[str, str]],
    catalogue_unit_prices: dict[str, Any],
    round_number: int,
) -> tuple[str, list[dict[str, str]] | None]:
    """Deterministic, product-agnostic evaluation of a buyer's
    counterproposal against the supplier's own reference price for each
    line. Returns ``(decision, counter_lines)`` where ``decision`` is one
    of ``"accept"``, ``"counter"`` or ``"reject"``.

    ``catalogue_unit_prices`` maps ``product_ref -> Decimal`` reference
    unit price (the supplier's own last quoted price for that line,
    sourced over MCP by the caller — this function never fetches or
    invents a price itself). The floor for any one line is
    ``MINIMUM_MARGIN_FRACTION`` of that reference price; a below-floor
    offer is always countered at exactly the floor price (the supplier
    never offers to go lower, so there is nothing to split a difference
    over). This holds for an arbitrary set of line items; nothing here
    is written against one specific product.
    """
    if round_number > MAX_NEGOTIATION_ROUNDS:
        return "reject", None

    floor_fraction = Decimal(MINIMUM_MARGIN_FRACTION)
    counter_lines: list[dict[str, str]] = []
    all_at_or_above_floor = True

    for line in countered_lines:
        product_ref = line["product_ref"]
        offered_price = Decimal(line["unit_price"])
        catalogue_price = catalogue_unit_prices.get(product_ref)
        if catalogue_price is None:
            # Unknown product: cannot evaluate it responsibly — reject
            # the whole negotiation rather than guess.
            return "reject", None
        floor_price = (catalogue_price * floor_fraction).quantize(Decimal("0.01"))
        if offered_price < floor_price:
            all_at_or_above_floor = False
            counter_lines.append({**line, "unit_price": str(floor_price)})
        else:
            counter_lines.append(dict(line))

    if all_at_or_above_floor:
        return "accept", None
    return "counter", counter_lines
