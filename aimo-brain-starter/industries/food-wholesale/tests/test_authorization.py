from __future__ import annotations

from decimal import Decimal

from food_wholesale import (
    agreement_terms_from_proposed_agreement,
    build_authorization_evidence,
    build_order_commit_request,
    build_order_confirmation,
    build_proposed_agreement,
    compute_idempotency_key,
)

_LINES = [
    {
        "product_ref": "sku_fictional_milk_lactose_free",
        "quantity": "100",
        "unit": "L",
        "unit_price": "1.20",
    }
]


def _proposed_agreement() -> dict:
    return build_proposed_agreement(
        artifact_id="art_pa_01",
        transaction_id="txn_01",
        correlation_id="corr_01",
        sender_agent_id="agent_supplier_01",
        recipient_agent_id="agent_buyer_01",
        organization_id="org_fictional_wholesale_01",
        proposal_id="prop_pa_01",
        version=3,
        agreement_of_proposal_id="prop_02",
        lines=_LINES,
        currency="EUR",
        delivery_terms=None,
    )


def test_agreement_terms_computes_total_amount() -> None:
    terms = agreement_terms_from_proposed_agreement(_proposed_agreement())
    assert terms["total_amount"] == "120.00"
    assert terms["currency"] == "EUR"
    assert terms["lines"] == _LINES


def test_idempotency_key_is_deterministic_and_order_independent_of_call_count() -> None:
    key_a = compute_idempotency_key(
        content_hash_value="sha256:abc",
        buyer_organization_id="org_buyer",
        supplier_organization_id="org_supplier",
    )
    key_b = compute_idempotency_key(
        content_hash_value="sha256:abc",
        buyer_organization_id="org_buyer",
        supplier_organization_id="org_supplier",
    )
    assert key_a == key_b
    assert len(key_a) == 64


def test_idempotency_key_differs_for_different_hash() -> None:
    key_a = compute_idempotency_key(
        content_hash_value="sha256:abc",
        buyer_organization_id="org_buyer",
        supplier_organization_id="org_supplier",
    )
    key_b = compute_idempotency_key(
        content_hash_value="sha256:def",
        buyer_organization_id="org_buyer",
        supplier_organization_id="org_supplier",
    )
    assert key_a != key_b


def test_build_authorization_evidence_is_schema_valid() -> None:
    artifact = build_authorization_evidence(
        artifact_id="art_ae_01",
        transaction_id="txn_01",
        correlation_id="corr_01",
        sender_agent_id="agent_buyer_01",
        recipient_agent_id="agent_supplier_01",
        organization_id="org_fictional_bistro_01",
        proposal_id="prop_ae_01",
        version=4,
        agreement_id="txn_01",
        content_hash_value="sha256:abc",
        allow=True,
        human_approval_required=False,
        deny_reason=None,
        policy_version="v1",
        policy_input_digest="sha256:digest",
        credential="dummy-credential",
    )
    assert artifact["type"] == "authorization_evidence"
    assert artifact["payload"]["allow"] is True


def test_build_order_commit_request_is_schema_valid() -> None:
    artifact = build_order_commit_request(
        artifact_id="art_ocr_01",
        transaction_id="txn_01",
        correlation_id="corr_01",
        sender_agent_id="agent_buyer_01",
        recipient_agent_id="agent_supplier_01",
        organization_id="org_fictional_bistro_01",
        proposal_id="prop_ocr_01",
        version=5,
        agreement_id="txn_01",
        content_hash_value="sha256:abc",
        idempotency_key="idem_01",
        credential="dummy-credential",
    )
    assert artifact["type"] == "order_commit_request"
    assert artifact["payload"]["idempotency_key"] == "idem_01"


def test_build_order_confirmation_is_schema_valid() -> None:
    artifact = build_order_confirmation(
        artifact_id="art_oc_01",
        transaction_id="txn_01",
        correlation_id="corr_01",
        sender_agent_id="agent_supplier_01",
        recipient_agent_id="agent_buyer_01",
        organization_id="org_fictional_wholesale_01",
        proposal_id="prop_oc_01",
        version=6,
        agreement_id="txn_01",
        idempotency_key="idem_01",
        status="CONFIRMED",
        external_order_id="mock_order_abc123",
        credential="dummy-credential",
    )
    assert artifact["type"] == "order_confirmation"
    assert artifact["payload"]["status"] == "CONFIRMED"


def test_total_amount_quantizes_to_two_decimal_places() -> None:
    artifact = _proposed_agreement()
    artifact["payload"]["lines"] = [
        {"product_ref": "sku_1", "quantity": "3", "unit": "L", "unit_price": "0.333"}
    ]
    terms = agreement_terms_from_proposed_agreement(artifact)
    assert Decimal(terms["total_amount"]) == Decimal("1.00")
