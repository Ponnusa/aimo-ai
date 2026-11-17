from datetime import UTC, datetime, timedelta

import jsonschema
import pytest
from food_wholesale import validate_against_schema


def _rfq_payload() -> dict:
    now = datetime.now(UTC)
    return {
        "schema": "https://schemas.aimobrain.example/core/artifact/v0.1",
        "artifact_id": "art_01",
        "transaction_id": "txn_01",
        "correlation_id": "corr_01",
        "sender_agent_id": "agent_buyer_01",
        "recipient_agent_id": "agent_supplier_01",
        "organization_id": "org_fictional_bistro_01",
        "domain": "food-wholesale",
        "type": "request_for_quote",
        "version": 1,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
        "payload": {
            "lines": [{"product_query": "lactose-free milk", "quantity": "100", "unit": "L"}],
            "delivery_location_ref": "site_01",
        },
    }


def test_valid_rfq_passes_schema_validation() -> None:
    validate_against_schema("request_for_quote", _rfq_payload())


def test_rfq_missing_required_field_fails_validation() -> None:
    payload = _rfq_payload()
    del payload["payload"]["delivery_location_ref"]
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema("request_for_quote", payload)


def test_rfq_wrong_domain_fails_validation() -> None:
    payload = _rfq_payload()
    payload["domain"] = "construction"
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema("request_for_quote", payload)
