from food_wholesale.adapters.mock import MockOrderApi, search_products


def test_search_products_finds_fictional_milk() -> None:
    results = search_products("lactose-free milk")
    assert len(results) == 1
    assert results[0].product_ref == "sku_fictional_milk_lactose_free"


def test_search_products_empty_query_returns_nothing() -> None:
    assert search_products("") == []


def test_search_products_no_match_returns_empty() -> None:
    assert search_products("nonexistent fictional product") == []


def test_mock_order_api_is_idempotent() -> None:
    api = MockOrderApi()
    first = api.submit_order(idempotency_key="idem_1")
    second = api.submit_order(idempotency_key="idem_1")
    assert first == second
    assert first.external_order_id == second.external_order_id


def test_mock_order_api_distinct_keys_create_distinct_orders() -> None:
    api = MockOrderApi()
    first = api.submit_order(idempotency_key="idem_1")
    second = api.submit_order(idempotency_key="idem_2")
    assert first.external_order_id != second.external_order_id
