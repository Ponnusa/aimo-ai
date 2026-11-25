"""Root-cutover tests (M8 slice 3, ADR-018 invariant D).

A delegation well within its own ``not_after`` must stop authorizing
new actions the moment its issuing root key is revoked/expired -- not
because the delegation itself expired, but because the two-hop chain's
first hop (verifying the delegation against its issuing root's live
key) can no longer succeed. ``HttpDelegationResolver._fetch`` already
treats a delegation that fails to re-verify as absent (see its own
docstring) -- these tests prove that behavior actually fires for a
root-revocation specifically, for both the cache-boundary case and the
commitment-adjacent forced-fresh case, rather than assuming it works.
"""

from __future__ import annotations

import httpx
import pytest
from message_integrity import DelegationScope, InMemoryJwksResolver, build_delegation
from message_integrity.delegation_resolver import DelegationNotFoundError, HttpDelegationResolver
from message_integrity.keys import generate_signing_keypair, public_key_to_jwk

ORG = "org_fictional_bistro_01"
NETWORK_AUDIENCE = "aimo-network:test"


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value


class _AlwaysServesTransport(httpx.AsyncBaseTransport):
    def __init__(self, token: str) -> None:
        self._token = token

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"delegations": [self._token]}, request=request)


def _setup():
    root_private, root_public = generate_signing_keypair()
    jwks_resolver = InMemoryJwksResolver()
    jwks_resolver.register(organization_id=ORG, kid="org-root1", public_key=root_public)

    _, delegated_public = generate_signing_keypair()
    delegated_jwk = public_key_to_jwk(delegated_public, kid="agent-k1")
    token = build_delegation(
        root_private_key=root_private,
        issuing_kid="org-root1",
        organization_id=ORG,
        agent_id="agent_01",
        audience=NETWORK_AUDIENCE,
        delegated_kid="agent-k1",
        delegated_public_key_jwk=delegated_jwk,
        # not_after is far in the future -- the delegation itself is
        # nowhere near expiry; only its issuing root is about to be gone.
        validity_seconds=365 * 24 * 3600,
        scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
    )
    transport = _AlwaysServesTransport(token)
    http_client = httpx.AsyncClient(transport=transport)
    clock = _FakeClock(start=1_000.0)
    resolver = HttpDelegationResolver(
        network_api_url="http://network-api.test",
        jwks_resolver=jwks_resolver,
        cache_ttl_seconds=300.0,
        http_client=http_client,
        clock=clock,
    )
    return jwks_resolver, resolver, clock


@pytest.mark.asyncio
async def test_delegation_stops_authorizing_once_cache_expires_past_root_revocation():
    """Cache-boundary case: the delegation was cached as valid; the
    issuing root is then revoked; once the cache TTL elapses, the very
    next check must fail -- not because of the delegation's own
    not_after, but because the root it was issued under is gone."""
    jwks_resolver, resolver, clock = _setup()

    # Still valid and cached while the root key is live.
    delegation = await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
    )
    assert delegation.agent_id == "agent_01"

    jwks_resolver.revoke(organization_id=ORG, kid="org-root1")

    # Still within TTL -- the cached entry is honored (ADR-018's
    # corrected revocation bound: staleness is tolerated up to, not
    # beyond, cache_ttl_seconds).
    clock.value += 100.0
    still_cached = await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
    )
    assert still_cached.agent_id == "agent_01"

    # Past TTL -- the next check forces a refetch, re-verifies the
    # delegation against the (now revoked) root key, and must fail.
    clock.value += 300.0
    with pytest.raises(DelegationNotFoundError):
        await resolver.get_delegation(
            organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
        )


@pytest.mark.asyncio
async def test_commitment_adjacent_force_fresh_fails_immediately_no_ttl_grace():
    """Commitment-adjacent artifact types bypass the cache entirely
    (ADR-017) -- a root revocation must be visible on the very next
    check, with no TTL grace period at all."""
    jwks_resolver, resolver, clock = _setup()

    await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE, force_fresh=True
    )
    jwks_resolver.revoke(organization_id=ORG, kid="org-root1")

    # No clock advance at all -- force_fresh must still see the
    # revocation immediately, proving there is no TTL grace for
    # commitment-adjacent verification.
    with pytest.raises(DelegationNotFoundError):
        await resolver.get_delegation(
            organization_id=ORG,
            kid="agent-k1",
            expected_audience=NETWORK_AUDIENCE,
            force_fresh=True,
        )
