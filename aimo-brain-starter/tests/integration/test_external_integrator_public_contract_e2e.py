"""M8 slice 4 completion: proves an external integrator can follow the
published public contract (``docs/specs/external-integration-openapi.yaml``)
end to end using ONLY what that contract documents.

This is the verification exercise ADR-019's "finish slice 4 before slice 5"
sequencing calls for: if slice 5's independent reference agent were built
before this were proven, it would only show "an agent built with knowledge
of AIMO's internals can talk to AIMO," not genuine interoperability.

Everything after the operator's own bootstrap call (which legitimately
requires the OPERATOR's authority, not the integrator's -- onboarding is not
self-service) is performed using ONLY:

- real HTTP calls to the exact routes the published OpenAPI document lists;
- the integrator's own org-scoped admin credential, never the global
  ``network_admin_api_key`` or the shared ``service_credential_signing_secret``;
- ``cryptography`` and ``pyjwt`` directly for key generation, JWK encoding and
  JWS signing -- the same third-party primitives any independent, non-Python
  implementer could use, reading only ``docs/specs/jws-artifact-signatures.md``
  and ``docs/specs/external-integration-openapi.yaml``. No AIMO package
  (``message_integrity``, ``trade_protocol``, ``identity``, etc.) is imported
  anywhere in the walkthrough below.
- no direct database access of any kind.

If any step genuinely required something outside this contract, that would
be reported as a blocking finding rather than quietly worked around -- no
such step was found.

This is also not just a hard-coded "this route exists" assertion: every
post-bootstrap HTTP response is checked, programmatically, against the
published OpenAPI document itself (``_assert_response_conforms_to_contract``
below) -- the method, path, required parameters and response status all
have to actually appear in ``external-integration-openapi.yaml``, or the
test fails. This is what the PR #7 independent review found missing: the
first draft of this file made exactly this flow's calls but never checked
them against the spec, so a real gap (``POST /agents`` undocumented) went
unnoticed. ``test_contract_conformance_check_catches_an_undocumented_route``
below proves this checker actually has teeth, by removing that same route
from a loaded copy of the spec and confirming the check then fails.
"""

from __future__ import annotations

import base64
import copy
import os
import re
import tempfile
import uuid
from pathlib import Path

import httpx
import jwt
import pytest
import yaml
from a2a.types import AgentCapabilities, AgentCard, AgentInterface
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from google.protobuf.json_format import MessageToDict

from database import Base, new_engine

from .a2a_server_harness import find_free_port, running_server

OPERATOR_KEY = "e2e-operator-key"
ADMIN_KEY = "e2e-admin-key-never-used-by-the-integrator"
INTERNAL_KEY = "e2e-internal-key"
EXTERNAL_ORG = "org_external_integrator_01"
AGENT_ID = "agent_external_integrator_01"

OPENAPI_SPEC_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "specs" / "external-integration-openapi.yaml"
)


def _load_contract_spec() -> dict:
    with OPENAPI_SPEC_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _spec_path_regex(template: str) -> re.Pattern[str]:
    """Turns an OpenAPI path template (e.g.
    ``/organizations/{organization_id}/relationship-status/{counterparty_organization_id}``)
    into a regex that matches a real request path with those segments
    substituted for concrete values."""
    escaped = re.escape(template)
    pattern = re.sub(r"\\\{[^}]+\\\}", r"[^/]+", escaped)
    return re.compile(f"^{pattern}$")


def _match_operation(spec: dict, *, method: str, path: str) -> tuple[str, dict] | None:
    for template, path_item in spec.get("paths", {}).items():
        if _spec_path_regex(template).match(path):
            operation = path_item.get(method.lower())
            if operation is not None:
                return template, operation
    return None


def _required_query_params(operation: dict) -> set[str]:
    return {
        p["name"]
        for p in operation.get("parameters", [])
        if p.get("in") == "query" and p.get("required")
    }


def _assert_call_conforms_to_contract(
    spec: dict, *, method: str, url: httpx.URL, status_code: int
) -> None:
    """Fails if this exact HTTP call is not something the published
    contract actually documents: an undocumented method/path, a missing
    required query parameter, or a response status the spec never lists
    for that operation."""
    match = _match_operation(spec, method=method, path=url.path)
    if match is None:
        raise AssertionError(
            f"{method} {url.path} is not a documented operation in the published "
            f"contract ({OPENAPI_SPEC_PATH.name})."
        )
    template, operation = match

    missing_params = _required_query_params(operation) - set(url.params.keys())
    if missing_params:
        raise AssertionError(
            f"{method} {template}: this call is missing required query "
            f"parameter(s) {sorted(missing_params)} that the published contract "
            f"requires (called as {url})."
        )

    documented_statuses = set(operation.get("responses", {}).keys())
    if str(status_code) not in documented_statuses and "default" not in documented_statuses:
        raise AssertionError(
            f"{method} {template} returned HTTP {status_code}, which the published "
            f"contract does not document for this operation (documented: "
            f"{sorted(documented_statuses)})."
        )


def _assert_response_conforms_to_contract(spec: dict, response: httpx.Response) -> None:
    _assert_call_conforms_to_contract(
        spec,
        method=response.request.method,
        url=response.request.url,
        status_code=response.status_code,
    )


def _fresh_sqlite_url(label: str) -> str:
    path = os.path.join(tempfile.gettempdir(), f"aimo_slice4_{label}_{uuid.uuid4().hex}.sqlite3")
    return f"sqlite+aiosqlite:///{path}"


def _build_network_app(*, database_url: str, monkeypatch):
    import network_api.app as network_app_module
    import network_api.settings as network_settings_module

    # monkeypatch.setenv, never a bare os.environ assignment: this test
    # exercises external_onboarding_enabled=true, and that override must
    # revert automatically at test teardown regardless of outcome -- a
    # bare os.environ write here would leak "true" into every test that
    # runs afterward in the same process (this is exactly the kind of
    # configuration leakage ADR-019 section 7 and its own test suite
    # guard against; a prior draft of this test got this wrong and broke
    # test_external_onboarding.py's flag-default tests when run together).
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("NETWORK_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("NETWORK_OPERATOR_KEY", OPERATOR_KEY)
    monkeypatch.setenv("NETWORK_INTERNAL_SERVICE_KEY", INTERNAL_KEY)
    monkeypatch.setenv("SERVICE_CREDENTIAL_SIGNING_SECRET", "e2e-signing-secret")
    monkeypatch.setenv("EXTERNAL_ONBOARDING_ENABLED", "true")
    network_settings_module.get_settings.cache_clear()
    return network_app_module.create_app()


def _build_registry_app(*, database_url: str, network_api_url: str, monkeypatch):
    import registry_api.app as registry_app_module
    import registry_api.settings as registry_settings_module

    monkeypatch.setenv("REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("SERVICE_CREDENTIAL_SIGNING_SECRET", "e2e-signing-secret")
    monkeypatch.setenv("NETWORK_API_URL", network_api_url)
    monkeypatch.setenv("NETWORK_INTERNAL_SERVICE_KEY", INTERNAL_KEY)
    monkeypatch.setenv("REGISTRY_ALLOW_PRIVATE_NETWORK_CARD_TARGETS", "true")
    registry_settings_module.get_settings.cache_clear()
    return registry_app_module.create_app()


def _ed25519_public_jwk(private_key: Ed25519PrivateKey, *, kid: str) -> dict[str, str]:
    """Hand-built RFC 8037 OKP JWK, per docs/specs/jws-artifact-signatures.md
    -- no AIMO package used, matching exactly what an independent
    implementer reading the published spec alone would produce."""
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return {"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": x, "use": "sig", "alg": "EdDSA"}


def _build_delegation_jws(
    *,
    root_private_key: Ed25519PrivateKey,
    issuing_kid: str,
    organization_id: str,
    agent_id: str,
    delegated_kid: str,
    delegated_public_jwk: dict[str, str],
    audience: str,
) -> str:
    """A delegation token built with only ``pyjwt``/``cryptography``, per
    docs/specs/jws-artifact-signatures.md's documented claim shape and
    ``typ: aimo-delegation+jws`` domain separator -- no
    ``message_integrity.delegations.build_delegation`` import."""
    import time

    now = int(time.time())
    claims = {
        "iss": organization_id,
        "sub": agent_id,
        "aud": audience,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "delegated_kid": delegated_kid,
        "delegated_pubkey_jwk": delegated_public_jwk,
        "scope": {"artifact_types": ["request_for_quote", "quote"], "protocol_version": "1"},
    }
    return jwt.encode(
        claims,
        root_private_key,
        algorithm="EdDSA",
        headers={"kid": issuing_kid, "typ": "aimo-delegation+jws"},
    )


@pytest.mark.asyncio
async def test_external_integrator_completes_the_published_flow_using_only_the_public_contract(
    monkeypatch,
):
    # --- Boot network-api and registry-api as real HTTP servers, each its
    # own SQLite database, full schema -- not a hand-picked table subset
    # (sidesteps the class of leak ADR-016/017/018/019 each already
    # caught; see issue #6). ------------------------------------------
    network_db = _fresh_sqlite_url("network")
    network_app = _build_network_app(database_url=network_db, monkeypatch=monkeypatch)
    network_engine = new_engine(network_db)
    async with network_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await network_engine.dispose()

    network_port = find_free_port()
    with running_server(network_app, port=network_port) as network_server:
        registry_db = _fresh_sqlite_url("registry")
        registry_app = _build_registry_app(
            database_url=registry_db,
            network_api_url=network_server.base_url,
            monkeypatch=monkeypatch,
        )
        registry_engine = new_engine(registry_db)
        async with registry_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await registry_engine.dispose()

        registry_port = find_free_port()
        with running_server(registry_app, port=registry_port) as registry_server:
            # --- A tiny standalone server representing the EXTERNAL
            # INTEGRATOR'S OWN endpoint -- plain FastAPI, no AIMO package
            # beyond what any independent agent operator would run
            # themselves to serve the documented well-known challenge
            # path. ------------------------------------------------------
            agent_state: dict[str, str] = {}
            agent_app = FastAPI()

            @agent_app.get("/health")
            async def health() -> dict[str, str]:
                return {"status": "ok"}

            @agent_app.get("/.well-known/aimo-brain-challenge")
            async def serve_challenge() -> Response:
                return Response(
                    content=agent_state.get("endpoint_challenge", ""), media_type="text/plain"
                )

            agent_port = find_free_port()
            agent_base_url = f"http://127.0.0.1:{agent_port}"

            @agent_app.get("/.well-known/agent-card.json")
            async def serve_card() -> JSONResponse:
                card = AgentCard(
                    name="Independent External Integrator Agent",
                    description=(
                        "A standalone, non-AIMO-hosted agent built only against the "
                        "published public contract."
                    ),
                    version="1.0.0",
                    default_input_modes=["application/json"],
                    default_output_modes=["application/json"],
                    capabilities=AgentCapabilities(streaming=False),
                    supported_interfaces=[
                        AgentInterface(
                            protocol_binding="JSONRPC", url=agent_base_url, protocol_version="1.0"
                        )
                    ],
                    skills=[],
                )
                return JSONResponse(content=MessageToDict(card))

            with running_server(agent_app, port=agent_port) as agent_server:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    # === Step 0: operator-only bootstrap. This is the ONE
                    # step that legitimately uses AIMO-operator authority,
                    # never the integrator's own -- onboarding is not
                    # self-service (ADR-019 section 3). ==================
                    create = await client.post(
                        f"{network_server.base_url}/organizations",
                        json={
                            "organization_id": EXTERNAL_ORG,
                            "display_name": "External Integrator Co",
                        },
                        headers={"X-Operator-Key": OPERATOR_KEY},
                    )
                    assert create.status_code in (200, 201), create.text

                    bootstrap = await client.post(
                        f"{network_server.base_url}/organizations/{EXTERNAL_ORG}/bootstrap-external",
                        headers={"X-Operator-Key": OPERATOR_KEY},
                    )
                    assert bootstrap.status_code == 200, bootstrap.text
                    org_credential = bootstrap.json()["admin_credential"]

                    # === From here on: ONLY the integrator's own
                    # org-scoped credential and documented public routes.
                    # Never X-Admin-Key, never the shared HMAC signing
                    # secret, never a database, never an AIMO package. ===
                    auth_header = {"Authorization": f"Bearer {org_credential}"}

                    # Step 1: generate the organization's own root keypair
                    # and register its public half (Layer 3, root half).
                    root_private_key = Ed25519PrivateKey.generate()
                    root_kid = f"{EXTERNAL_ORG}-root-1"
                    root_jwk = _ed25519_public_jwk(root_private_key, kid=root_kid)
                    register_root = await client.post(
                        f"{network_server.base_url}/organizations/{EXTERNAL_ORG}/signing-keys",
                        json={"kid": root_kid, "public_key_jwk": root_jwk, "validity_days": 365},
                        headers=auth_header,
                    )
                    assert register_root.status_code == 200, register_root.text

                    # Step 2: mint a manage_agents-scoped credential for
                    # registry-api -- the opaque, introspection-only
                    # format (ADR-019 section 4), never evaluated locally.
                    mint = await client.post(
                        f"{network_server.base_url}/organizations/{EXTERNAL_ORG}/service-credentials",
                        json={"scope": "manage_agents", "ttl_seconds": 3600},
                        headers=auth_header,
                    )
                    assert mint.status_code == 200, mint.text
                    manage_agents_token = mint.json()["token"]
                    assert manage_agents_token.startswith("svc_")
                    registry_auth_header = {"Authorization": f"Bearer {manage_agents_token}"}

                    # Step 3: generate the agent's own keypair and
                    # register it with registry-api (Layer 2, identity).
                    agent_private_key = Ed25519PrivateKey.generate()
                    agent_kid = f"{AGENT_ID}-key-1"
                    agent_jwk = _ed25519_public_jwk(agent_private_key, kid=agent_kid)
                    register_agent = await client.post(
                        f"{registry_server.base_url}/agents",
                        json={
                            "agent_id": AGENT_ID,
                            "organization_id": EXTERNAL_ORG,
                            "a2a_card_url": agent_server.base_url,
                            "signing_public_key_jwk": agent_jwk,
                        },
                        headers=registry_auth_header,
                    )
                    assert register_agent.status_code == 200, register_agent.text
                    assert register_agent.json()["status"] == "pending"

                    # Step 4: start verification -- two purpose-bound
                    # challenges (ADR-016).
                    start = await client.post(
                        f"{registry_server.base_url}/agents/{AGENT_ID}/verification/start",
                        params={"organization_id": EXTERNAL_ORG},
                        headers=registry_auth_header,
                    )
                    assert start.status_code == 200, start.text
                    endpoint_challenge = start.json()["endpoint_challenge"]
                    key_challenge = start.json()["key_challenge"]
                    assert key_challenge is not None

                    # Step 5: the integrator's own server serves the
                    # endpoint-control challenge at the documented
                    # well-known path.
                    agent_state["endpoint_challenge"] = endpoint_challenge

                    # Step 6: prove private-key control by signing the key
                    # challenge with the agent's own private key -- raw
                    # Ed25519, exactly as docs/specs describes.
                    signature = agent_private_key.sign(key_challenge.encode("utf-8"))
                    key_proof_signature = base64.b64encode(signature).decode("ascii")

                    confirm = await client.post(
                        f"{registry_server.base_url}/agents/{AGENT_ID}/verification/confirm",
                        params={"organization_id": EXTERNAL_ORG},
                        json={"key_proof_signature": key_proof_signature},
                        headers=registry_auth_header,
                    )
                    assert confirm.status_code == 200, confirm.text
                    assert confirm.json()["status"] == "active"

                    # Step 7: publish a per-agent signing delegation,
                    # constructed and signed entirely within the
                    # integrator's own trust boundary (Layer 3, delegated
                    # half) -- network-api never sees a private key.
                    delegated_kid = f"{AGENT_ID}-delegated-1"
                    delegated_jwk = _ed25519_public_jwk(agent_private_key, kid=delegated_kid)
                    delegation_jws = _build_delegation_jws(
                        root_private_key=root_private_key,
                        issuing_kid=root_kid,
                        organization_id=EXTERNAL_ORG,
                        agent_id=AGENT_ID,
                        delegated_kid=delegated_kid,
                        delegated_public_jwk=delegated_jwk,
                        audience="aimo-network:test",
                    )
                    publish = await client.post(
                        f"{network_server.base_url}/organizations/{EXTERNAL_ORG}/delegations",
                        json={"jws": delegation_jws},
                        headers=auth_header,
                    )
                    assert publish.status_code == 200, publish.text

                    # Step 8: confirm the published material is visible
                    # through the PUBLIC, unauthenticated discovery
                    # endpoints -- a counterparty verifying this
                    # organization needs no credential at all for these.
                    jwks = await client.get(
                        f"{network_server.base_url}/organizations/{EXTERNAL_ORG}/jwks.json"
                    )
                    assert jwks.status_code == 200
                    assert any(k["kid"] == root_kid for k in jwks.json()["keys"])

                    delegations = await client.get(
                        f"{network_server.base_url}/organizations/{EXTERNAL_ORG}/delegations.json"
                    )
                    assert delegations.status_code == 200
                    # Each entry is the raw delegation JWS itself (not
                    # pre-parsed metadata) -- a verifier decodes it to read
                    # delegated_kid/scope, exactly per docs/specs.
                    published_claims = [
                        jwt.decode(token, options={"verify_signature": False})
                        for token in delegations.json()["delegations"]
                    ]
                    assert any(c["delegated_kid"] == delegated_kid for c in published_claims)

                    # Step 9: query relationship status (Layer 4,
                    # eligibility only) using the integrator's own
                    # credential -- no relationship exists yet (creating
                    # one is a cross-organization admin action outside
                    # this integrator's own authority), so this correctly
                    # reports exists=False rather than ever disclosing
                    # anyone else's relationships.
                    relationship = await client.get(
                        f"{network_server.base_url}/organizations/{EXTERNAL_ORG}"
                        "/relationship-status/org_some_counterparty",
                        headers=auth_header,
                    )
                    assert relationship.status_code == 200, relationship.text
                    assert relationship.json() == {"exists": False, "status": None}

                    # Step 10: discovery (Layer 1) is itself public and
                    # unauthenticated -- confirm the registered agent is
                    # now discoverable by its own organization.
                    discover = await client.get(
                        f"{registry_server.base_url}/agents",
                        params={"requesting_organization_id": EXTERNAL_ORG},
                    )
                    assert discover.status_code == 200
                    assert any(a["agent_id"] == AGENT_ID for a in discover.json())

                    # === Contract conformance: every post-bootstrap call
                    # above (steps 1-10, never the operator-only bootstrap
                    # in step 0, which is intentionally absent from the
                    # public contract) must match a real, documented
                    # operation in external-integration-openapi.yaml --
                    # method, path, required parameters, and response
                    # status. This is what makes this an automated check
                    # of the published contract's completeness, not just
                    # a hard-coded list of routes this test happens to
                    # know about. ===================================
                    spec = _load_contract_spec()
                    for response in (
                        register_root,
                        mint,
                        register_agent,
                        start,
                        confirm,
                        publish,
                        jwks,
                        delegations,
                        relationship,
                        discover,
                    ):
                        _assert_response_conforms_to_contract(spec, response)

                    # --- Negative control: the integrator's org-scoped
                    # credential must still never work as the global key,
                    # and the global key must never work for this now-
                    # externally-onboarded organization (ADR-019 section
                    # 1) -- the public contract's own access boundary
                    # holds even from the integrator's own vantage point.
                    rejected = await client.post(
                        f"{network_server.base_url}/organizations/{EXTERNAL_ORG}/signing-keys",
                        json={
                            "kid": "should-not-work",
                            "public_key_jwk": root_jwk,
                            "validity_days": 1,
                        },
                        headers={"X-Admin-Key": ADMIN_KEY},
                    )
                    assert rejected.status_code == 401


def test_contract_conformance_check_catches_an_undocumented_route() -> None:
    """Proves ``_assert_call_conforms_to_contract`` genuinely has teeth,
    rather than being a check that would pass against any spec. Exactly
    reproduces the real PR #7 defect: a ``POST /agents`` call made during
    the walkthrough above, checked against a copy of the real spec with
    that one operation removed. Also proves the inverse: the SAME call
    passes against the real, current spec -- so this isn't just an
    always-fails or always-passes stub."""
    real_spec = _load_contract_spec()
    agents_post_call_url = httpx.URL("http://registry-api.example/agents")

    # Against the real, current spec: POST /agents is documented, so this
    # must pass.
    _assert_call_conforms_to_contract(
        real_spec, method="POST", url=agents_post_call_url, status_code=200
    )

    # Against a copy with POST /agents removed (reproducing the exact
    # PR #7 gap the independent review found): the same call must fail.
    spec_missing_agents_post = copy.deepcopy(real_spec)
    del spec_missing_agents_post["paths"]["/agents"]["post"]
    with pytest.raises(AssertionError, match="not a documented operation"):
        _assert_call_conforms_to_contract(
            spec_missing_agents_post, method="POST", url=agents_post_call_url, status_code=200
        )


def test_contract_conformance_check_catches_a_missing_required_query_parameter() -> None:
    """Same proof-of-teeth, for the other defect class PR #7's review
    found: ``verification/start`` requiring ``organization_id`` as a
    query parameter without the spec declaring it."""
    real_spec = _load_contract_spec()
    call_with_org_id = httpx.URL(
        "http://registry-api.example/agents/agent_x/verification/start",
        params={"organization_id": "org_x"},
    )
    call_without_org_id = httpx.URL("http://registry-api.example/agents/agent_x/verification/start")

    # With the required query parameter present: passes against the real spec.
    _assert_call_conforms_to_contract(
        real_spec, method="POST", url=call_with_org_id, status_code=200
    )

    # Without it: fails, because the spec declares organization_id required.
    with pytest.raises(AssertionError, match="missing required query parameter"):
        _assert_call_conforms_to_contract(
            real_spec, method="POST", url=call_without_org_id, status_code=200
        )

    # And if the spec itself regressed to not declaring organization_id as
    # required at all, the same omitted-parameter call would wrongly pass
    # -- proving the check's bite comes from the spec's own declaration,
    # not from this test's own hard-coded expectations.
    spec_without_required_param = copy.deepcopy(real_spec)
    for param in spec_without_required_param["paths"]["/agents/{agent_id}/verification/start"][
        "post"
    ]["parameters"]:
        if param["name"] == "organization_id":
            param["required"] = False
    _assert_call_conforms_to_contract(
        spec_without_required_param, method="POST", url=call_without_org_id, status_code=200
    )
