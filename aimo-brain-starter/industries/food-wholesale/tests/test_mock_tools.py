from decimal import Decimal

from food_wholesale.adapters.mock import (
    check_inventory,
    evaluate_purchase_approval,
    get_budget,
    get_customer_price,
    get_delivery_options,
    get_purchase_requirements,
    list_approved_suppliers,
    search_catalogue,
)


def test_get_purchase_requirements_returns_fictional_requirement() -> None:
    requirements = get_purchase_requirements()
    assert len(requirements) == 1
    assert requirements[0].product_query == "lactose-free milk"


def test_get_budget_known_organization() -> None:
    budget = get_budget("org_fictional_bistro_01")
    assert budget.currency == "EUR"
    assert budget.remaining_amount == Decimal("500.00")


def test_get_budget_unknown_organization_is_zero() -> None:
    budget = get_budget("org_unknown")
    assert budget.remaining_amount == Decimal("0.00")


def test_list_approved_suppliers_known_organization() -> None:
    suppliers = list_approved_suppliers("org_fictional_bistro_01")
    assert len(suppliers) == 1
    assert suppliers[0].organization_id == "org_fictional_wholesale_01"


def test_list_approved_suppliers_unknown_organization_is_empty() -> None:
    assert list_approved_suppliers("org_unknown") == []


def test_evaluate_purchase_approval_within_budget() -> None:
    decision = evaluate_purchase_approval(
        organization_id="org_fictional_bistro_01", amount=Decimal("100.00"), currency="EUR"
    )
    assert decision.decision == "approved"


def test_evaluate_purchase_approval_exceeds_budget() -> None:
    decision = evaluate_purchase_approval(
        organization_id="org_fictional_bistro_01", amount=Decimal("5000.00"), currency="EUR"
    )
    assert decision.decision == "denied"


def test_evaluate_purchase_approval_currency_mismatch() -> None:
    decision = evaluate_purchase_approval(
        organization_id="org_fictional_bistro_01", amount=Decimal("10.00"), currency="USD"
    )
    assert decision.decision == "denied"


def test_search_catalogue_finds_fictional_milk() -> None:
    results = search_catalogue("lactose-free milk")
    assert len(results) == 1


def test_check_inventory_known_product() -> None:
    product = check_inventory("sku_fictional_milk_lactose_free")
    assert product is not None
    assert product.stock_today == Decimal("80")


def test_check_inventory_unknown_product_is_none() -> None:
    assert check_inventory("sku_does_not_exist") is None


def test_get_customer_price_applies_fictional_discount() -> None:
    price = get_customer_price(
        product_ref="sku_fictional_milk_lactose_free",
        buyer_organization_id="org_fictional_bistro_01",
    )
    assert price is not None
    assert price.unit_price == Decimal("1.19")  # 1.25 * 0.95, rounded


def test_get_customer_price_no_discount_for_unknown_buyer() -> None:
    price = get_customer_price(
        product_ref="sku_fictional_milk_lactose_free", buyer_organization_id="org_unknown"
    )
    assert price is not None
    assert price.unit_price == Decimal("1.25")


def test_get_customer_price_unknown_product_is_none() -> None:
    assert get_customer_price(product_ref="sku_missing", buyer_organization_id="org_x") is None


def test_get_delivery_options_known_product() -> None:
    options = get_delivery_options(product_ref="sku_fictional_milk_lactose_free", quantity="10")
    assert len(options) == 2


def test_get_delivery_options_unknown_product_is_empty() -> None:
    assert get_delivery_options(product_ref="sku_missing", quantity="10") == []
