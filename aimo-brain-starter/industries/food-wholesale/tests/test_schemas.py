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
        "proposal_id": "prop_01",
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


def test_rfq_missing_proposal_id_fails_validation() -> None:
    payload = _rfq_payload()
    del payload["proposal_id"]
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema("request_for_quote", payload)


def test_rfq_wrong_domain_fails_validation() -> None:
    payload = _rfq_payload()
    payload["domain"] = "construction"
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema("request_for_quote", payload)


def _quote_payload() -> dict:
    now = datetime.now(UTC)
    return {
        "schema": "https://schemas.aimobrain.example/core/artifact/v0.1",
        "artifact_id": "art_02",
        "transaction_id": "txn_01",
        "correlation_id": "corr_01",
        "sender_agent_id": "agent_supplier_01",
        "recipient_agent_id": "agent_buyer_01",
        "organization_id": "org_fictional_wholesale_01",
        "domain": "food-wholesale",
        "type": "quote",
        "version": 2,
        "proposal_id": "prop_02",
        "supersedes_proposal_id": "prop_01",
        "content_hash": "deadbeef",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "payload": {
            "lines": [
                {
                    "product_ref": "sku_fictional_milk_lactose_free",
                    "quantity": "100",
                    "unit": "L",
                    "unit_price": "1.19",
                }
            ],
            "currency": "EUR",
        },
    }


def test_valid_quote_passes_schema_validation() -> None:
    validate_against_schema("quote", _quote_payload())


def test_quote_missing_content_hash_fails_validation() -> None:
    payload = _quote_payload()
    del payload["content_hash"]
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema("quote", payload)


def _counterproposal_payload() -> dict:
    payload = _quote_payload()
    payload.update(
        type="counterproposal",
        version=3,
        proposal_id="prop_03",
        supersedes_proposal_id="prop_02",
    )
    return payload


def test_valid_counterproposal_passes_schema_validation() -> None:
    validate_against_schema("counterproposal", _counterproposal_payload())


def test_counterproposal_without_supersedes_fails_validation() -> None:
    payload = _counterproposal_payload()
    del payload["supersedes_proposal_id"]
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema("counterproposal", payload)


def _accept_proposal_payload() -> dict:
    now = datetime.now(UTC)
    return {
        "schema": "https://schemas.aimobrain.example/core/artifact/v0.1",
        "artifact_id": "art_04",
        "transaction_id": "txn_01",
        "correlation_id": "corr_01",
        "sender_agent_id": "agent_buyer_01",
        "recipient_agent_id": "agent_supplier_01",
        "organization_id": "org_fictional_bistro_01",
        "domain": "food-wholesale",
        "type": "accept_proposal",
        "version": 4,
        "proposal_id": "prop_04",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "payload": {"accepted_proposal_id": "prop_02", "accepted_content_hash": "deadbeef"},
    }


def test_valid_accept_proposal_passes_schema_validation() -> None:
    validate_against_schema("accept_proposal", _accept_proposal_payload())


def test_accept_proposal_missing_accepted_hash_fails_validation() -> None:
    payload = _accept_proposal_payload()
    del payload["payload"]["accepted_content_hash"]
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema("accept_proposal", payload)


def _proposed_agreement_payload() -> dict:
    now = datetime.now(UTC)
    return {
        "schema": "https://schemas.aimobrain.example/core/artifact/v0.1",
        "artifact_id": "art_05",
        "transaction_id": "txn_01",
        "correlation_id": "corr_01",
        "sender_agent_id": "agent_supplier_01",
        "recipient_agent_id": "agent_buyer_01",
        "organization_id": "org_fictional_wholesale_01",
        "domain": "food-wholesale",
        "type": "proposed_agreement",
        "version": 5,
        "proposal_id": "prop_05",
        "agreement_of_proposal_id": "prop_02",
        "content_hash": "deadbeef",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "payload": {
            "lines": [
                {
                    "product_ref": "sku_fictional_milk_lactose_free",
                    "quantity": "100",
                    "unit": "L",
                    "unit_price": "1.19",
                }
            ],
            "currency": "EUR",
            "status": "NONBINDING",
        },
    }


def test_valid_proposed_agreement_passes_schema_validation() -> None:
    validate_against_schema("proposed_agreement", _proposed_agreement_payload())


def test_proposed_agreement_must_be_nonbinding() -> None:
    payload = _proposed_agreement_payload()
    payload["payload"]["status"] = "BINDING"
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema("proposed_agreement", payload)


def _reject_proposal_payload() -> dict:
    now = datetime.now(UTC)
    return {
        "schema": "https://schemas.aimobrain.example/core/artifact/v0.1",
        "artifact_id": "art_06",
        "transaction_id": "txn_01",
        "correlation_id": "corr_01",
        "sender_agent_id": "agent_buyer_01",
        "recipient_agent_id": "agent_supplier_01",
        "organization_id": "org_fictional_bistro_01",
        "domain": "food-wholesale",
        "type": "reject_proposal",
        "version": 3,
        "proposal_id": "prop_06",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "payload": {"rejected_proposal_id": "prop_02", "reason": "Price above approved budget."},
    }


def test_valid_reject_proposal_passes_schema_validation() -> None:
    validate_against_schema("reject_proposal", _reject_proposal_payload())
