from __future__ import annotations

from decimal import Decimal

from buyer_agent.negotiation import evaluate_quote_against_budget


def test_within_budget_quote_is_accepted_as_is() -> None:
    evaluation = evaluate_quote_against_budget(
        lines=[{"product_ref": "sku_1", "quantity": "10", "unit_price": "1.00"}],
        remaining_budget=Decimal("500.00"),
    )
    assert evaluation.within_budget is True
    assert evaluation.counter_lines is None
    assert evaluation.total_cost == Decimal("10.00")


def test_over_budget_quote_produces_scaled_counter_lines() -> None:
    evaluation = evaluate_quote_against_budget(
        lines=[{"product_ref": "sku_1", "quantity": "1000", "unit_price": "1.19"}],
        remaining_budget=Decimal("500.00"),
    )
    assert evaluation.within_budget is False
    assert evaluation.counter_lines is not None
    counter_unit_price = Decimal(evaluation.counter_lines[0]["unit_price"])
    assert counter_unit_price < Decimal("1.19")
    assert counter_unit_price > Decimal("0")


def test_counter_lines_preserve_non_price_fields() -> None:
    evaluation = evaluate_quote_against_budget(
        lines=[
            {
                "product_ref": "sku_1",
                "quantity": "1000",
                "unit": "L",
                "unit_price": "1.19",
            }
        ],
        remaining_budget=Decimal("1.00"),
    )
    assert evaluation.counter_lines is not None
    assert evaluation.counter_lines[0]["product_ref"] == "sku_1"
    assert evaluation.counter_lines[0]["unit"] == "L"


def test_counter_price_never_goes_to_zero_or_negative() -> None:
    evaluation = evaluate_quote_against_budget(
        lines=[{"product_ref": "sku_1", "quantity": "1000", "unit_price": "1.19"}],
        remaining_budget=Decimal("0.00"),
    )
    assert evaluation.counter_lines is not None
    assert Decimal(evaluation.counter_lines[0]["unit_price"]) > Decimal("0")


def test_generic_over_multiple_arbitrary_lines() -> None:
    evaluation = evaluate_quote_against_budget(
        lines=[
            {"product_ref": "sku_a", "quantity": "10", "unit_price": "5.00"},
            {"product_ref": "sku_b", "quantity": "20", "unit_price": "3.00"},
        ],
        remaining_budget=Decimal("50.00"),
    )
    assert evaluation.within_budget is False
    assert evaluation.counter_lines is not None
    assert len(evaluation.counter_lines) == 2
