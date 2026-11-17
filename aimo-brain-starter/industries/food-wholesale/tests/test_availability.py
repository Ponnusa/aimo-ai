import jsonschema
import pytest
from food_wholesale.availability import build_request, handle_request, validate_response


def test_build_request_produces_valid_payload() -> None:
    payload = build_request(
        [{"product_query": "lactose-free milk", "quantity": "100", "unit": "L"}],
        buyer_organization_id="org_fictional_bistro_01",
    )
    assert payload["type"] == "product_availability_request"


def test_handle_request_matches_fictional_catalogue() -> None:
    request = build_request(
        [{"product_query": "lactose-free milk", "quantity": "100", "unit": "L"}],
        buyer_organization_id="org_fictional_bistro_01",
    )
    response = handle_request(request, supplier_organization_id="org_fictional_wholesale_01")
    validate_response(response)
    assert response["results"][0]["matched"] is True
    assert response["results"][0]["lines"][0]["product_ref"] == "sku_fictional_milk_lactose_free"


def test_handle_request_reports_unmatched_product() -> None:
    request = build_request(
        [{"product_query": "nonexistent fictional caviar", "quantity": "1", "unit": "kg"}],
        buyer_organization_id="org_fictional_bistro_01",
    )
    response = handle_request(request, supplier_organization_id="org_fictional_wholesale_01")
    assert response["results"][0]["matched"] is False
    assert response["results"][0]["lines"] == []


def test_handle_request_rejects_malformed_payload() -> None:
    with pytest.raises(jsonschema.ValidationError):
        handle_request({"not": "a valid request"}, supplier_organization_id="org_x")


def test_validate_response_rejects_malformed_payload() -> None:
    with pytest.raises(jsonschema.ValidationError):
        validate_response({"not": "a valid response"})
