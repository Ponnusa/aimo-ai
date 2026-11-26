"""M8 slice 5 (ADR-020 section 6): the published external-integration
artifact manifest (``docs/specs/external-integration-artifacts/``) must
accept exactly the same wire shapes the real Python agents actually
produce. This is the drift check: a representative, real artifact is
built with the real internal builders, then validated against the
PUBLISHED copy of each schema (never the internal one in
``industries/food-wholesale/schemas``) -- a future change to the
internal schema that is not mirrored into the published copy fails here,
the same class of check PR #7 added for the OpenAPI contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from food_wholesale.authorization import (
    build_authorization_evidence,
    build_order_commit_request,
    build_order_confirmation,
)
from food_wholesale.negotiation import build_accept_proposal, build_quote, build_rfq

MANIFEST_DIR = Path(__file__).parents[2] / "docs" / "specs" / "external-integration-artifacts"


def _published_schema(filename: str) -> dict:
    return json.loads((MANIFEST_DIR / "schemas" / filename).read_text(encoding="utf-8"))


def test_manifest_is_valid_json_and_lists_six_artifacts():
    manifest = json.loads((MANIFEST_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == "1.0.0"
    types = {entry["type"] for entry in manifest["artifacts"]}
    assert types == {
        "request_for_quote",
        "quote",
        "accept_proposal",
        "authorization_evidence",
        "order_commit_request",
        "order_confirmation",
    }
    for entry in manifest["artifacts"]:
        schema_path = MANIFEST_DIR / entry["schema"]
        assert schema_path.is_file(), f"{entry['type']}: {schema_path} does not exist"


def test_published_schemas_are_valid_json_schema():
    for filename in (
        "request_for_quote.schema.json",
        "quote.schema.json",
        "accept_proposal.schema.json",
        "authorization_evidence.schema.json",
        "order_commit_request.schema.json",
        "order_confirmation.schema.json",
    ):
        jsonschema.Draft202012Validator.check_schema(_published_schema(filename))


def test_published_request_for_quote_schema_matches_real_wire_artifact():
    artifact = build_rfq(
        artifact_id="artifact-1",
        transaction_id="txn-1",
        correlation_id="corr-1",
        buyer_agent_id="buyer-agent-1",
        supplier_agent_id="supplier-agent-1",
        buyer_organization_id="org-buyer",
        proposal_id="proposal-1",
        lines=[{"product_query": "milk-lactose-free-1l", "quantity": "100", "unit": "L"}],
        delivery_location_ref="loc-1",
    )
    jsonschema.validate(artifact, _published_schema("request_for_quote.schema.json"))


def test_published_quote_schema_matches_real_wire_artifact():
    artifact = build_quote(
        artifact_id="artifact-2",
        transaction_id="txn-1",
        correlation_id="corr-1",
        supplier_agent_id="supplier-agent-1",
        buyer_agent_id="buyer-agent-1",
        supplier_organization_id="org-supplier",
        proposal_id="proposal-2",
        supersedes_proposal_id="proposal-1",
        version=2,
        lines=[
            {
                "product_ref": "milk-lactose-free-1l",
                "quantity": "100",
                "unit": "L",
                "unit_price": "1.20",
            }
        ],
        currency="EUR",
    )
    jsonschema.validate(artifact, _published_schema("quote.schema.json"))


def test_published_accept_proposal_schema_matches_real_wire_artifact():
    artifact = build_accept_proposal(
        artifact_id="artifact-3",
        transaction_id="txn-1",
        correlation_id="corr-1",
        sender_agent_id="buyer-agent-1",
        recipient_agent_id="supplier-agent-1",
        organization_id="org-buyer",
        proposal_id="proposal-3",
        version=3,
        accepted_proposal_id="proposal-2",
        accepted_content_hash="deadbeef",
    )
    jsonschema.validate(artifact, _published_schema("accept_proposal.schema.json"))


def test_published_authorization_evidence_schema_matches_real_wire_artifact():
    artifact = build_authorization_evidence(
        artifact_id="artifact-4",
        transaction_id="txn-1",
        correlation_id="corr-1",
        sender_agent_id="buyer-agent-1",
        recipient_agent_id="supplier-agent-1",
        organization_id="org-buyer",
        proposal_id="proposal-4",
        version=1,
        agreement_id="txn-1",
        content_hash_value="deadbeef",
        allow=True,
        human_approval_required=False,
        deny_reason=None,
        policy_version="buyer-ts-policy-v1",
        policy_input_digest="abc123",
    )
    jsonschema.validate(artifact, _published_schema("authorization_evidence.schema.json"))


def test_published_order_commit_request_schema_matches_real_wire_artifact():
    artifact = build_order_commit_request(
        artifact_id="artifact-5",
        transaction_id="txn-1",
        correlation_id="corr-1",
        sender_agent_id="buyer-agent-1",
        recipient_agent_id="supplier-agent-1",
        organization_id="org-buyer",
        proposal_id="proposal-5",
        version=1,
        agreement_id="txn-1",
        content_hash_value="deadbeef",
        idempotency_key="idem-1",
    )
    jsonschema.validate(artifact, _published_schema("order_commit_request.schema.json"))


def test_published_order_confirmation_schema_matches_real_wire_artifact():
    artifact = build_order_confirmation(
        artifact_id="artifact-6",
        transaction_id="txn-1",
        correlation_id="corr-1",
        sender_agent_id="supplier-agent-1",
        recipient_agent_id="buyer-agent-1",
        organization_id="org-supplier",
        proposal_id="proposal-6",
        version=1,
        agreement_id="txn-1",
        idempotency_key="idem-1",
        status="CONFIRMED",
        external_order_id="erp-order-1",
    )
    jsonschema.validate(artifact, _published_schema("order_confirmation.schema.json"))


@pytest.mark.parametrize(
    "filename",
    [
        "request_for_quote.schema.json",
        "quote.schema.json",
        "accept_proposal.schema.json",
        "authorization_evidence.schema.json",
        "order_commit_request.schema.json",
        "order_confirmation.schema.json",
    ],
)
def test_published_schema_is_byte_identical_to_internal_source(filename: str):
    """Not merely schema-compatible -- the published copy must be the
    exact same bytes as the internal schema it documents, so there is
    only ever one place this shape is actually defined; this manifest's
    copy is a publication of it, never a second, independently-drifting
    definition."""
    internal = Path(__file__).parents[2] / "industries" / "food-wholesale" / "schemas" / filename
    published = MANIFEST_DIR / "schemas" / filename
    assert published.read_text(encoding="utf-8") == internal.read_text(encoding="utf-8")
