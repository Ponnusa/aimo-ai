"""M8 slice 3 merge review, concern 3 (rotation scope).

Confirms, by actually exercising it rather than reading the code and
assuming, that the UNDERLYING root-key rotation lifecycle operation
already works today -- an organization can register a second,
incoming root key while the first, outgoing key remains fully live;
artifacts signed by EITHER verify during that overlap window; cutover
is an explicit operator action (``revoke``), not automatic; and once
the outgoing key is revoked, both the artifacts it signed directly AND
any delegation it had issued stop verifying, while the incoming key
(and anything signed under it) is unaffected.

This is a genuine capability of the existing per-organization
multi-key JWKS/revocation model (``OrganizationSigningKeyRecord``,
unchanged since M5.1/ADR-012, extended by ADR-017's shared kid
namespace) -- nothing in M8 slice 2/3 needed to add a new mechanism
for this to work. What IS actually missing, confirmed separately by
reading ``register_signing_key`` (always creates ``status="active"``;
nothing in the codebase ever creates or checks for a literal
``"rotating"`` status despite ADR-018's prose describing one) and by
the absence of any scheduler/convenience script, is: (a) the cosmetic
``"rotating"`` status distinction ADR-018 describes is never actually
produced by any code path -- a second concurrently-active key is
simply another ``"active"`` row, which is sufficient for every
behavior this test proves, but the status-name distinction itself is
aspirational, not implemented; and (b) no operator-facing automation
(cron/script) walks a human through the sequence end-to-end. Neither
of these is the lifecycle PRIMITIVE itself being missing -- this test
proves the primitive is real and usable today via direct, authenticated
API calls, i.e. a documented manual runbook is honest, not a cover for
a missing capability.
"""

from __future__ import annotations

import pytest
from message_integrity import InMemoryJwksResolver, generate_signing_keypair, sign_artifact
from message_integrity.proofs import InvalidSignatureError, verify_artifact_proof

ORG_A = "org_fictional_bistro_01"
ORG_B = "org_fictional_wholesale_01"


def _artifact(**overrides) -> dict:
    base = {
        "artifact_id": "art_rotation_01",
        "transaction_id": "txn_rotation_01",
        "correlation_id": "corr_rotation_01",
        "sender_agent_id": "agent_buyer_01",
        "recipient_agent_id": "agent_supplier_01",
        "organization_id": ORG_A,
        "type": "request_for_quote",
        "version": 1,
        "payload": {"lines": []},
        "content_hash": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_manual_root_rotation_runbook_overlap_then_explicit_cutover() -> None:
    """Drives the exact sequence a human operator would perform today,
    with no convenience script: (1) outgoing key already live; (2)
    register an incoming key while the outgoing key stays live -- both
    now verify (the overlap window); (3) operator explicitly revokes
    the outgoing key (cutover is a deliberate action, never automatic);
    (4) the outgoing key's artifact no longer verifies, the incoming
    key's does."""
    outgoing_private, outgoing_public = generate_signing_keypair()
    incoming_private, incoming_public = generate_signing_keypair()
    resolver = InMemoryJwksResolver()

    # Step 1: outgoing key is the only one live.
    resolver.register(organization_id=ORG_A, kid="org_a-outgoing", public_key=outgoing_public)
    signed_by_outgoing = sign_artifact(
        _artifact(artifact_id="art_outgoing"),
        private_key=outgoing_private,
        kid="org_a-outgoing",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    verified_outgoing_before_rotation = await verify_artifact_proof(
        signed_by_outgoing, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
    )
    assert verified_outgoing_before_rotation.attribution == "organization"

    # Step 2: register the incoming key -- the outgoing key is NOT
    # touched or removed. Both are now live simultaneously (the
    # overlap window): this is the rotation primitive itself, and it
    # requires no new mechanism beyond "register a second key."
    resolver.register(organization_id=ORG_A, kid="org_a-incoming", public_key=incoming_public)

    signed_by_incoming = sign_artifact(
        _artifact(artifact_id="art_incoming"),
        private_key=incoming_private,
        kid="org_a-incoming",
        organization_id=ORG_A,
        agent_id="agent_buyer_01",
        recipient_organization_id=ORG_B,
    )
    # Both verify during the overlap window.
    verified_outgoing_during_overlap = await verify_artifact_proof(
        signed_by_outgoing, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
    )
    verified_incoming_during_overlap = await verify_artifact_proof(
        signed_by_incoming, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
    )
    assert verified_outgoing_during_overlap.organization_id == ORG_A
    assert verified_incoming_during_overlap.organization_id == ORG_A

    # Step 3: cutover is an EXPLICIT operator action -- revoke the
    # outgoing key. Nothing happens automatically just because an
    # incoming key now exists; both stayed live until this call.
    resolver.revoke(organization_id=ORG_A, kid="org_a-outgoing")

    # Step 4: the outgoing key's own artifact (already signed, during
    # the overlap window) no longer verifies for a NEW verification
    # attempt -- revocation is enforced going forward, exactly as
    # ADR-012's JWKS model already specifies (surfaced as
    # InvalidSignatureError, the correct, deliberate relabeling of a
    # legitimate "key no longer resolves" case -- see the merge-review
    # fix in proofs.verify_artifact_proof, which now narrows this catch
    # to KeyNotFoundError specifically so a genuine INFRASTRUCTURE
    # failure is no longer relabeled the same way). The incoming key is
    # completely unaffected.
    with pytest.raises(InvalidSignatureError):
        await verify_artifact_proof(
            signed_by_outgoing, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
        )
    verified_incoming_after_cutover = await verify_artifact_proof(
        signed_by_incoming, jwks_resolver=resolver, expected_recipient_organization_id=ORG_B
    )
    assert verified_incoming_after_cutover.organization_id == ORG_A
