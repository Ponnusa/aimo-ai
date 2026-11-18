from decimal import Decimal

import jsonschema
import pytest
from food_wholesale import (
    build_accept_proposal,
    build_counterproposal,
    build_proposed_agreement,
    build_quote,
    build_reject_proposal,
    build_rfq,
    evaluate_counterproposal,
)
from trade_protocol import content_hash

_LINES = [
    {
        "product_ref": "sku_fictional_milk_lactose_free",
        "quantity": "100",
        "unit": "L",
        "unit_price": "1.19",
    }
]


def _rfq() -> dict:
    return build_rfq(
        artifact_id="art_01",
        transaction_id="txn_01",
        correlation_id="corr_01",
        buyer_agent_id="agent_buyer_01",
        supplier_agent_id="agent_supplier_01",
        buyer_organization_id="org_fictional_bistro_01",
        proposal_id="prop_01",
        lines=[{"product_query": "lactose-free milk", "quantity": "100", "unit": "L"}],
        delivery_location_ref="site_01",
    )


def test_build_rfq_is_schema_valid_and_version_one() -> None:
    rfq = _rfq()
    assert rfq["version"] == 1
    assert rfq["type"] == "request_for_quote"


def test_build_quote_computes_content_hash_matching_trade_protocol() -> None:
    quote = build_quote(
        artifact_id="art_02",
        transaction_id="txn_01",
        correlation_id="corr_01",
        supplier_agent_id="agent_supplier_01",
        buyer_agent_id="agent_buyer_01",
        supplier_organization_id="org_fictional_wholesale_01",
        proposal_id="prop_02",
        supersedes_proposal_id="prop_01",
        version=2,
        lines=_LINES,
        currency="EUR",
    )
    expected = content_hash({"lines": _LINES, "currency": "EUR"})
    assert quote["content_hash"] == expected
    assert quote["supersedes_proposal_id"] == "prop_01"


def test_build_counterproposal_requires_supersedes_and_is_schema_valid() -> None:
    counter = build_counterproposal(
        artifact_id="art_03",
        transaction_id="txn_01",
        correlation_id="corr_01",
        buyer_agent_id="agent_buyer_01",
        supplier_agent_id="agent_supplier_01",
        buyer_organization_id="org_fictional_bistro_01",
        proposal_id="prop_03",
        supersedes_proposal_id="prop_02",
        version=3,
        lines=[{**_LINES[0], "unit_price": "1.00"}],
        currency="EUR",
    )
    assert counter["supersedes_proposal_id"] == "prop_02"
    assert counter["type"] == "counterproposal"


def test_build_accept_proposal_binds_exact_proposal_and_hash() -> None:
    accept = build_accept_proposal(
        artifact_id="art_04",
        transaction_id="txn_01",
        correlation_id="corr_01",
        sender_agent_id="agent_buyer_01",
        recipient_agent_id="agent_supplier_01",
        organization_id="org_fictional_bistro_01",
        proposal_id="prop_04",
        version=4,
        accepted_proposal_id="prop_02",
        accepted_content_hash="deadbeef",
    )
    assert accept["payload"]["accepted_proposal_id"] == "prop_02"
    assert accept["payload"]["accepted_content_hash"] == "deadbeef"


def test_build_reject_proposal_is_schema_valid() -> None:
    rejection = build_reject_proposal(
        artifact_id="art_05",
        transaction_id="txn_01",
        correlation_id="corr_01",
        sender_agent_id="agent_buyer_01",
        recipient_agent_id="agent_supplier_01",
        organization_id="org_fictional_bistro_01",
        proposal_id="prop_05",
        version=3,
        rejected_proposal_id="prop_02",
        reason="Price above approved budget.",
    )
    assert rejection["type"] == "reject_proposal"


def test_build_proposed_agreement_is_always_nonbinding() -> None:
    agreement = build_proposed_agreement(
        artifact_id="art_06",
        transaction_id="txn_01",
        correlation_id="corr_01",
        sender_agent_id="agent_supplier_01",
        recipient_agent_id="agent_buyer_01",
        organization_id="org_fictional_wholesale_01",
        proposal_id="prop_06",
        version=5,
        agreement_of_proposal_id="prop_02",
        lines=_LINES,
        currency="EUR",
        delivery_terms=None,
    )
    assert agreement["payload"]["status"] == "NONBINDING"
    assert agreement["content_hash"] == content_hash({"lines": _LINES, "currency": "EUR"})


def test_evaluate_counterproposal_accepts_when_at_or_above_floor() -> None:
    decision, counter_lines = evaluate_counterproposal(
        countered_lines=[{**_LINES[0], "unit_price": "1.15"}],
        catalogue_unit_prices={"sku_fictional_milk_lactose_free": Decimal("1.19")},
        round_number=1,
    )
    assert decision == "accept"
    assert counter_lines is None


def test_evaluate_counterproposal_counters_when_below_floor() -> None:
    decision, counter_lines = evaluate_counterproposal(
        countered_lines=[{**_LINES[0], "unit_price": "0.50"}],
        catalogue_unit_prices={"sku_fictional_milk_lactose_free": Decimal("1.19")},
        round_number=1,
    )
    assert decision == "counter"
    assert counter_lines is not None
    assert Decimal(counter_lines[0]["unit_price"]) > Decimal("0.50")


def test_evaluate_counterproposal_rejects_past_max_rounds() -> None:
    decision, counter_lines = evaluate_counterproposal(
        countered_lines=[{**_LINES[0], "unit_price": "0.50"}],
        catalogue_unit_prices={"sku_fictional_milk_lactose_free": Decimal("1.19")},
        round_number=99,
    )
    assert decision == "reject"
    assert counter_lines is None


def test_evaluate_counterproposal_rejects_unknown_product() -> None:
    decision, counter_lines = evaluate_counterproposal(
        countered_lines=[{**_LINES[0], "product_ref": "sku_does_not_exist"}],
        catalogue_unit_prices={},
        round_number=1,
    )
    assert decision == "reject"
    assert counter_lines is None


def test_evaluate_counterproposal_works_for_arbitrary_multi_line_products() -> None:
    """Generic over any set of line items, not hardcoded to one product."""
    lines = [
        {"product_ref": "sku_a", "quantity": "10", "unit": "kg", "unit_price": "2.00"},
        {"product_ref": "sku_b", "quantity": "5", "unit": "kg", "unit_price": "0.40"},
    ]
    decision, counter_lines = evaluate_counterproposal(
        countered_lines=lines,
        catalogue_unit_prices={"sku_a": Decimal("2.00"), "sku_b": Decimal("1.00")},
        round_number=1,
    )
    assert decision == "counter"
    assert counter_lines is not None
    by_ref = {line["product_ref"]: line for line in counter_lines}
    assert Decimal(by_ref["sku_a"]["unit_price"]) == Decimal("2.00")
    assert Decimal(by_ref["sku_b"]["unit_price"]) > Decimal("0.40")


def test_rfq_missing_lines_raises_validation_error() -> None:
    with pytest.raises(jsonschema.ValidationError):
        build_rfq(
            artifact_id="art_07",
            transaction_id="txn_01",
            correlation_id="corr_01",
            buyer_agent_id="agent_buyer_01",
            supplier_agent_id="agent_supplier_01",
            buyer_organization_id="org_fictional_bistro_01",
            proposal_id="prop_07",
            lines=[],
            delivery_location_ref="site_01",
        )
