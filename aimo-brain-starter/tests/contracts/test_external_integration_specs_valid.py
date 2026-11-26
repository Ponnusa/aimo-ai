"""M8 slice 4 completion: the published external-integration contract
documents must themselves be structurally valid -- a slice-5 implementer
has nothing else to build against, so a malformed document (undeclared
path parameters, missing security, etc.) is a real defect, not a style
nitpick. This runs the same pinned ``openapi-spec-validator`` CI also
runs directly, plus a JSON Schema structural check and an example-instance
check for the Trust Extension, so a regression fails fast in the normal
test suite rather than only in a separate CI step.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_SPEC_PATH = REPO_ROOT / "docs" / "specs" / "external-integration-openapi.yaml"
TRUST_EXTENSION_SCHEMA_PATH = REPO_ROOT / "docs" / "specs" / "trust-extension.schema.json"


def test_external_integration_openapi_spec_is_structurally_valid() -> None:
    with OPENAPI_SPEC_PATH.open(encoding="utf-8") as fh:
        spec = yaml.safe_load(fh)
    # Raises on any structural error (undeclared path parameter, missing
    # response, unresolved $ref, etc.) -- a clean return is the contract.
    validate_openapi_spec(spec)


def test_every_templated_path_parameter_is_declared() -> None:
    """Narrower, explicit regression test for the exact defect class the
    independent PR #7 review found: a path template naming a parameter
    (e.g. ``{organization_id}``) that the operation never declares. The
    general validator above already catches this, but this test pins the
    specific failure mode so a future regression's cause is obvious from
    the test name alone, not just a opaque validator traceback."""
    with OPENAPI_SPEC_PATH.open(encoding="utf-8") as fh:
        spec = yaml.safe_load(fh)

    import re

    template_param_re = re.compile(r"\{([^}]+)\}")

    for path, path_item in spec["paths"].items():
        templated = set(template_param_re.findall(path))
        if not templated:
            continue
        for method, operation in path_item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            declared_path_params = {
                p["name"] for p in operation.get("parameters", []) if p.get("in") == "path"
            }
            missing = templated - declared_path_params
            assert not missing, (
                f"{method.upper()} {path} uses templated parameter(s) {missing} "
                "that are never declared as `in: path` parameters."
            )


def test_trust_extension_schema_is_valid_json_schema() -> None:
    with TRUST_EXTENSION_SCHEMA_PATH.open(encoding="utf-8") as fh:
        schema = json.load(fh)
    Draft202012Validator.check_schema(schema)


def test_trust_extension_schema_accepts_a_well_formed_descriptor() -> None:
    with TRUST_EXTENSION_SCHEMA_PATH.open(encoding="utf-8") as fh:
        schema = json.load(fh)
    example = {
        "schema_version": "1.0.0",
        "organization_id": "org_example",
        "agent_id": "agent_example",
        "supported_capabilities": [
            "endpoint_control",
            "key_control",
            "signing_delegation",
            "trading_relationship",
        ],
        "verification_sources": {
            "discovery": {"endpoint": "https://example.org/.well-known/agent-card.json"},
            "identity": {
                "endpoint": "https://registry-api.example.aimo-brain.internal/agents/agent_example"
            },
            "delegation": {
                "endpoint": (
                    "https://network-api.example.aimo-brain.internal/organizations/"
                    "org_example/delegations.json"
                )
            },
            "trading_relationship": {
                "endpoint": (
                    "https://network-api.example.aimo-brain.internal/organizations/"
                    "org_example/relationship-status/{counterparty_organization_id}"
                )
            },
        },
        "freshness_policy": {
            "cache_ttl_seconds": 300,
            "commitment_adjacent_artifact_types": [
                "authorization_evidence",
                "order_commit_request",
                "order_confirmation",
            ],
        },
    }
    Draft202012Validator(schema).validate(example)


def test_trust_extension_schema_never_declares_a_self_asserted_status_field() -> None:
    """Pins the extension's one load-bearing design decision: no field
    named ``verified``/``active``/``authorized`` may exist anywhere in
    the schema, so a card can never carry a self-reported status a
    verifier could be tempted to trust directly."""
    with TRUST_EXTENSION_SCHEMA_PATH.open(encoding="utf-8") as fh:
        raw_text = fh.read()
    for forbidden in ('"verified"', '"active"', '"authorized"'):
        assert forbidden not in raw_text, (
            f"trust-extension.schema.json must never declare a {forbidden} field -- "
            "see docs/specs/trust-extension.md's 'one rule this whole document exists to state'."
        )
