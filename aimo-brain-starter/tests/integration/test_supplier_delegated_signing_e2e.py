"""Acceptance test for supplier-agent signing with a dedicated,
organisation-root-delegated key instead of holding the organization
root private key directly (ADR-017's delegation model, already generic
and already used end-to-end by the external TypeScript buyer during the
staging interoperability exercise; this is the first time it is proven
for supplier-agent specifically).

Reuses ``test_authorization_commitment_e2e``'s network/registry/buyer
harness verbatim; only ``supplier_server`` is replaced here with a
variant whose ``SUPPLIER_SIGNING_PRIVATE_KEY_PEM``/``SUPPLIER_SIGNING_KID``
are a delegated key, never the organization's root private key. Covers
every item in the acceptance criteria: a full synthetic trade succeeding
with delegated supplier signing, and verification correctly enforcing
scope, audience, expiry, and revocation -- plus rotation (issuing a
fresh delegation under the same root without ever needing the old
delegated key again).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil

import httpx
import pytest
from message_integrity import DelegationScope, build_delegation, private_key_from_pem
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import new_engine

from .a2a_server_harness import (
    find_free_port,
    generate_and_register_delegated_signing_key,
    revoke_delegation,
    running_server,
)
from .test_authorization_commitment_e2e import (  # noqa: F401 -- fixtures
    ADMIN_KEY,
    BUYER_ORG,
    SUPPLIER_AGENT_ID,
    SUPPLIER_ORG,
    _build_supplier_app,
    _build_supplier_mcp_app,
    _create_relationship,
    _create_tables,
    _fresh_sqlite_url,
    _mint_credential,
    _register_and_verify_buyer,
    _register_and_verify_supplier,
    buyer_server,
    network_server,
    registry_server,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed"),
]

#: Every artifact type supplier-agent ever signs outbound -- see
#: supplier_agent.negotiation/commitment's build_quote/
#: build_proposed_agreement/build_reject_proposal/
#: build_authorization_evidence/build_order_confirmation call sites.
SUPPLIER_DELEGATION_SCOPE = (
    "quote",
    "proposed_agreement",
    "reject_proposal",
    "authorization_evidence",
    "order_confirmation",
)
NETWORK_AUDIENCE = "aimo-network:dev"


async def _build_delegated_supplier_server(
    *,
    registry_server,  # noqa: F811 -- pytest fixture, imported from the sibling e2e module
    network_server,  # noqa: F811 -- pytest fixture, imported from the sibling e2e module
    delegated_key,
    stack: contextlib.ExitStack,
    database_url: str | None = None,
):
    """Boots a real supplier-agent server signing with ``delegated_key``
    (never the organization root key) and a fresh supplier-mcp alongside
    it, mirroring ``test_authorization_commitment_e2e.supplier_server``
    exactly except for which key is set as
    ``SUPPLIER_SIGNING_PRIVATE_KEY_PEM``/``SUPPLIER_SIGNING_KID``. Both
    servers are entered onto ``stack`` so the caller's ``with
    contextlib.ExitStack() as stack`` closes them, instead of this
    helper managing their lifetime itself."""
    mcp_database_url = _fresh_sqlite_url("supplier_mcp")
    mcp_app, mcp_models = _build_supplier_mcp_app(database_url=mcp_database_url)
    mcp_engine = new_engine(mcp_database_url)
    await _create_tables(mcp_engine, *mcp_models)
    await mcp_engine.dispose()

    mcp_port = find_free_port()
    mcp_server = stack.enter_context(running_server(mcp_app, port=mcp_port))
    port = find_free_port()
    a2a_url = f"http://127.0.0.1:{port}"
    database_url = database_url or _fresh_sqlite_url("supplier")
    _, models = _build_supplier_app(
        a2a_url=a2a_url,
        database_url=database_url,
        supplier_mcp_url=f"{mcp_server.base_url}/mcp",
        network_api_url=network_server.base_url,
        registry_api_url=registry_server.base_url,
        signing_private_key_pem=delegated_key.private_key_pem,
    )
    # _build_supplier_app always sets SUPPLIER_SIGNING_KID right after
    # SUPPLIER_SIGNING_PRIVATE_KEY_PEM to the SUPPLIER_KID constant --
    # override it to the actual delegated kid before the app (which
    # reads settings at import/construction time) is built.
    os.environ["SUPPLIER_SIGNING_KID"] = delegated_key.kid
    import supplier_agent.app as supplier_app_module
    import supplier_agent.settings as supplier_settings_module

    supplier_settings_module.get_settings.cache_clear()
    app = supplier_app_module.create_app()

    engine = new_engine(database_url)
    await _create_tables(engine, *models)
    await engine.dispose()

    server = stack.enter_context(running_server(app, port=port))
    server.database_url = database_url  # type: ignore[attr-defined]

    # _build_supplier_app sets SUPPLIER_A2A_URL as a side effect; the
    # buyer reads it at its own settings-cache-clear time
    # (buyer_agent.commitment reads settings.supplier_a2a_url directly,
    # not through registry discovery). When buyer_server was already
    # built before this call (every test here except the one using the
    # shared ``supplier_server``/``buyer_server`` fixture pair, which
    # are ordered supplier-before-buyer), its cached settings would
    # otherwise still point at a stale or absent port.
    import buyer_agent.settings as buyer_settings_module

    buyer_settings_module.get_settings.cache_clear()
    return server


@pytest.fixture
async def delegated_supplier_key(network_server):  # noqa: F811 -- pytest fixture
    return await generate_and_register_delegated_signing_key(
        network_api_base_url=network_server.base_url,
        organization_id=SUPPLIER_ORG,
        admin_key=ADMIN_KEY,
        agent_id=SUPPLIER_AGENT_ID,
        audience=NETWORK_AUDIENCE,
        artifact_types=SUPPLIER_DELEGATION_SCOPE,
    )


@pytest.fixture
async def supplier_server(registry_server, network_server, delegated_supplier_key):  # noqa: F811
    with contextlib.ExitStack() as stack:
        server = await _build_delegated_supplier_server(
            registry_server=registry_server,
            network_server=network_server,
            delegated_key=delegated_supplier_key,
            stack=stack,
        )
        yield server


async def _run_trade(
    *,
    network_server,  # noqa: F811 -- pytest fixture, imported from the sibling e2e module
    registry_server,  # noqa: F811
    supplier_server,
    buyer_server,  # noqa: F811
) -> dict:
    token = await _mint_credential(network_server, SUPPLIER_ORG)
    await _create_relationship(network_server)
    await _register_and_verify_supplier(
        registry_server=registry_server,
        supplier_server=supplier_server,
        network_server=network_server,
        token=token,
    )
    buyer_token = await _mint_credential(network_server, BUYER_ORG)
    await _register_and_verify_buyer(
        registry_server=registry_server,
        buyer_server=buyer_server,
        network_server=network_server,
        token=buyer_token,
    )

    from buyer_agent.client import negotiate_purchase_via_registry
    from buyer_agent.commitment import run_authorization_and_commitment
    from buyer_agent.settings import get_settings

    settings = get_settings()
    buyer_engine = new_engine(os.environ["BUYER_DATABASE_URL"])
    buyer_session_factory = async_sessionmaker(buyer_engine, expire_on_commit=False)

    agreement = await negotiate_purchase_via_registry(
        settings=settings,
        session_factory=buyer_session_factory,
        lines=[{"product_query": "lactose-free milk", "quantity": "50", "unit": "L"}],
        delivery_location_ref="site_01",
    )
    negotiation_id = agreement["transaction_id"]
    confirmation = await run_authorization_and_commitment(
        negotiation_id, settings=settings, session_factory=buyer_session_factory
    )
    await buyer_engine.dispose()
    return confirmation


async def test_synthetic_trade_succeeds_with_delegated_supplier_signing(
    network_server,  # noqa: F811 -- pytest fixtures, imported from the sibling e2e module
    registry_server,  # noqa: F811
    supplier_server,
    buyer_server,  # noqa: F811
    delegated_supplier_key,
) -> None:
    """Acceptance criterion: a deployed (here: real-HTTP, sandbox)
    synthetic trade succeeds end to end with supplier signing every
    outbound artifact under its dedicated delegated key -- never the
    organization root key, which this test never even sets as
    SUPPLIER_SIGNING_PRIVATE_KEY_PEM."""
    confirmation = await _run_trade(
        network_server=network_server,
        registry_server=registry_server,
        supplier_server=supplier_server,
        buyer_server=buyer_server,
    )
    assert confirmation["type"] == "order_confirmation"
    assert confirmation["payload"]["status"] == "CONFIRMED"

    # The artifact's own proof header names the delegated kid -- direct
    # proof it was signed by the dedicated key, not the root key (whose
    # kid is a different string, generated by the helper as
    # f"{organization_id}-root-k1").
    from message_integrity.proofs import parse_unverified_proof

    header, _ = parse_unverified_proof(confirmation["proof"]["jws"])
    assert header["kid"] == delegated_supplier_key.kid
    assert header["kid"] != delegated_supplier_key.root_kid


async def test_verification_rejects_delegation_missing_the_needed_scope(
    network_server,  # noqa: F811 -- pytest fixtures, imported from the sibling e2e module
    registry_server,  # noqa: F811
    buyer_server,  # noqa: F811
) -> None:
    """A delegation that never covers order_confirmation must be
    rejected at commit time -- the buyer's own verification of the
    supplier's order_confirmation must fail with a scope error, not
    silently accept an under-scoped signature."""
    narrow_key = await generate_and_register_delegated_signing_key(
        network_api_base_url=network_server.base_url,
        organization_id=SUPPLIER_ORG,
        admin_key=ADMIN_KEY,
        agent_id=SUPPLIER_AGENT_ID,
        audience=NETWORK_AUDIENCE,
        artifact_types=("quote", "proposed_agreement", "reject_proposal", "authorization_evidence"),
    )
    with contextlib.ExitStack() as stack:
        server = await _build_delegated_supplier_server(
            registry_server=registry_server,
            network_server=network_server,
            delegated_key=narrow_key,
            stack=stack,
        )
        with pytest.raises(Exception, match=r"[Ss]cope"):
            await _run_trade(
                network_server=network_server,
                registry_server=registry_server,
                supplier_server=server,
                buyer_server=buyer_server,
            )


async def test_verification_rejects_delegation_with_wrong_audience(
    network_server,  # noqa: F811 -- pytest fixtures, imported from the sibling e2e module
    registry_server,  # noqa: F811
    buyer_server,  # noqa: F811
) -> None:
    """A delegation signed for a different network audience than the
    one the verifying buyer is configured with must be rejected. By
    design (HttpDelegationResolver's per-token catch), an audience
    mismatch surfaces the same way as a revoked or never-published
    delegation -- "no currently-live delegation for this kid" -- rather
    than a distinct audience-specific message; both are a correct
    rejection, so this only asserts rejection, not the exact wording."""
    wrong_audience_key = await generate_and_register_delegated_signing_key(
        network_api_base_url=network_server.base_url,
        organization_id=SUPPLIER_ORG,
        admin_key=ADMIN_KEY,
        agent_id=SUPPLIER_AGENT_ID,
        audience="aimo-network:some-other-environment",
        artifact_types=SUPPLIER_DELEGATION_SCOPE,
    )
    with contextlib.ExitStack() as stack:
        server = await _build_delegated_supplier_server(
            registry_server=registry_server,
            network_server=network_server,
            delegated_key=wrong_audience_key,
            stack=stack,
        )
        with pytest.raises(Exception, match=r"[Nn]o currently-live delegation|[Aa]udience"):
            await _run_trade(
                network_server=network_server,
                registry_server=registry_server,
                supplier_server=server,
                buyer_server=buyer_server,
            )


async def test_publish_rejects_an_already_expired_delegation(
    network_server,  # noqa: F811 -- pytest fixtures, imported from the sibling e2e module
    registry_server,  # noqa: F811
    buyer_server,  # noqa: F811
) -> None:
    """A delegation whose own exp has already passed at issuance must
    never even be published -- network-api's own publish-time
    verification rejects it outright, the earliest point this check can
    fire. This is a distinct scenario from
    ``test_verification_rejects_a_delegation_that_expires_before_use``
    below: a delegation that was valid when published but has since
    expired must also be rejected, specifically at verify time, not
    only at publish time."""
    from message_integrity import generate_signing_keypair, public_key_to_jwk

    from .a2a_server_harness import generate_and_register_signing_key

    root_kid = f"{SUPPLIER_ORG}-root-k1"
    root_private_key_pem = await generate_and_register_signing_key(
        network_api_base_url=network_server.base_url,
        organization_id=SUPPLIER_ORG,
        admin_key=ADMIN_KEY,
        kid=root_kid,
    )
    delegated_private_key, delegated_public_key = generate_signing_keypair()
    delegated_kid = f"{SUPPLIER_AGENT_ID}-k1"
    token = build_delegation(
        root_private_key=private_key_from_pem(root_private_key_pem),
        issuing_kid=root_kid,
        organization_id=SUPPLIER_ORG,
        agent_id=SUPPLIER_AGENT_ID,
        audience=NETWORK_AUDIENCE,
        delegated_kid=delegated_kid,
        delegated_public_key_jwk=public_key_to_jwk(delegated_public_key, kid=delegated_kid),
        scope=DelegationScope(artifact_types=SUPPLIER_DELEGATION_SCOPE, protocol_version="v1"),
        validity_seconds=-3600,  # already expired at issuance
    )
    async with httpx.AsyncClient() as client:
        publish = await client.post(
            f"{network_server.base_url}/organizations/{SUPPLIER_ORG}/delegations",
            json={"jws": token},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
    # network-api's own publish-time verification already rejects an
    # already-expired delegation outright -- this is the earliest point
    # the expiry check can fire, and is itself the acceptance evidence.
    assert publish.status_code == 400, publish.text
    assert "expired" in publish.text.lower() or "exp" in publish.text.lower()


async def test_verification_rejects_a_delegation_that_expires_before_use(
    network_server,  # noqa: F811 -- pytest fixtures, imported from the sibling e2e module
    registry_server,  # noqa: F811
    buyer_server,  # noqa: F811
) -> None:
    """Publish-time rejection of an already-expired token (the test
    above) only proves the earliest possible check. It does not prove
    that a delegation genuinely *valid when published* is still
    rejected once it expires before a later verification -- which is
    the actual commit-time property this PR claims: commitment-adjacent
    artifact types always force a fresh (uncached) delegation fetch, so
    this must fail even though nothing here is served from a stale
    cache. Publishes a delegation valid for only 2 seconds, waits past
    that, then attempts a real trade and confirms it is rejected."""
    with contextlib.ExitStack() as stack:
        short_lived_key = await generate_and_register_delegated_signing_key(
            network_api_base_url=network_server.base_url,
            organization_id=SUPPLIER_ORG,
            admin_key=ADMIN_KEY,
            agent_id=SUPPLIER_AGENT_ID,
            audience=NETWORK_AUDIENCE,
            artifact_types=SUPPLIER_DELEGATION_SCOPE,
            validity_seconds=2,
        )
        server = await _build_delegated_supplier_server(
            registry_server=registry_server,
            network_server=network_server,
            delegated_key=short_lived_key,
            stack=stack,
        )
        await asyncio.sleep(3.0)
        # Collapses to the same "no currently-live delegation" message
        # as a revoked or wrong-audience delegation -- HttpDelegationResolver's
        # per-token catch treats an expired delegation's own
        # verify_delegation_token failure as "this delegation is no
        # longer valid," by design, not as a distinct error string.
        with pytest.raises(Exception, match=r"[Nn]o currently-live delegation|[Ee]xpired"):
            await _run_trade(
                network_server=network_server,
                registry_server=registry_server,
                supplier_server=server,
                buyer_server=buyer_server,
            )


async def test_rotation_old_key_revoked_new_key_continues_without_root_redeploy(
    network_server,  # noqa: F811 -- pytest fixtures, imported from the sibling e2e module
    registry_server,  # noqa: F811
    buyer_server,  # noqa: F811
) -> None:
    """Rotation: issue a second delegation under the same root for a
    fresh dedicated key, revoke the first delegation, and confirm a
    trade succeeds under the new key -- all without ever touching the
    root private key again after the first delegation was issued."""
    key_a = await generate_and_register_delegated_signing_key(
        network_api_base_url=network_server.base_url,
        organization_id=SUPPLIER_ORG,
        admin_key=ADMIN_KEY,
        agent_id=SUPPLIER_AGENT_ID,
        audience=NETWORK_AUDIENCE,
        artifact_types=SUPPLIER_DELEGATION_SCOPE,
    )
    # Same organization and same agent_id as key_a -- a rotation issues
    # a new delegated KEY for the same agent, never a new agent. Only
    # the delegated_kid (and thus the key material) differs; it must be
    # given explicitly since network-api enforces delegated_kid
    # uniqueness per organization and the default would collide with
    # key_a's.
    key_b = await generate_and_register_delegated_signing_key(
        network_api_base_url=network_server.base_url,
        organization_id=SUPPLIER_ORG,
        admin_key=ADMIN_KEY,
        agent_id=SUPPLIER_AGENT_ID,
        audience=NETWORK_AUDIENCE,
        artifact_types=SUPPLIER_DELEGATION_SCOPE,
        root_kid=key_a.root_kid,
        root_private_key_pem=key_a.root_private_key_pem,
        delegated_kid=f"{SUPPLIER_AGENT_ID}-k2",
    )
    assert key_a.kid != key_b.kid

    await revoke_delegation(
        network_api_base_url=network_server.base_url,
        organization_id=SUPPLIER_ORG,
        admin_key=ADMIN_KEY,
        jti=key_a.jti,
    )

    database_url = _fresh_sqlite_url("supplier")
    with contextlib.ExitStack() as stack:
        server_b = await _build_delegated_supplier_server(
            registry_server=registry_server,
            network_server=network_server,
            delegated_key=key_b,
            stack=stack,
            database_url=database_url,
        )
        confirmation = await _run_trade(
            network_server=network_server,
            registry_server=registry_server,
            supplier_server=server_b,
            buyer_server=buyer_server,
        )
        assert confirmation["payload"]["status"] == "CONFIRMED"

        from message_integrity.proofs import parse_unverified_proof

        header, _ = parse_unverified_proof(confirmation["proof"]["jws"])
        assert header["kid"] == key_b.kid


async def test_revoked_delegation_is_rejected_after_revocation(
    network_server,  # noqa: F811 -- pytest fixtures, imported from the sibling e2e module
    registry_server,  # noqa: F811
    buyer_server,  # noqa: F811
) -> None:
    """A delegation that verified fine before revocation must stop
    verifying immediately after -- revocation is by absence from the
    live ``/delegations.json`` list, not a flag on the token itself."""
    key = await generate_and_register_delegated_signing_key(
        network_api_base_url=network_server.base_url,
        organization_id=SUPPLIER_ORG,
        admin_key=ADMIN_KEY,
        agent_id=SUPPLIER_AGENT_ID,
        audience=NETWORK_AUDIENCE,
        artifact_types=SUPPLIER_DELEGATION_SCOPE,
    )
    await revoke_delegation(
        network_api_base_url=network_server.base_url,
        organization_id=SUPPLIER_ORG,
        admin_key=ADMIN_KEY,
        jti=key.jti,
    )
    with contextlib.ExitStack() as stack:
        server = await _build_delegated_supplier_server(
            registry_server=registry_server,
            network_server=network_server,
            delegated_key=key,
            stack=stack,
        )
        with pytest.raises(Exception, match=r"[Dd]elegation"):
            await _run_trade(
                network_server=network_server,
                registry_server=registry_server,
                supplier_server=server,
                buyer_server=buyer_server,
            )
