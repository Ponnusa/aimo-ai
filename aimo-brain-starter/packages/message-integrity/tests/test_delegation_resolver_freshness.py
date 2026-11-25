"""Controllable-clock tests for ``HttpDelegationResolver``'s cache
freshness/fail-closed behavior (M8 slice 2, ADR-017 merge-readiness
review). The implementing slice's own report named this a real gap:
the fail-closed TTL behavior was only verified by code audit, never by
a test that actually crosses the TTL boundary. These tests close that
gap using an injectable clock and a mocked HTTP transport, so no real
network-api process or wall-clock sleep is required.
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


class _ScriptedTransport(httpx.AsyncBaseTransport):
    """Returns ``tokens`` on every request until ``fail_after`` requests
    have been served, after which every further request raises — used
    to simulate a refresh that fails once the cache has expired."""

    def __init__(self, tokens_by_call: list[list[str] | None]) -> None:
        self._tokens_by_call = tokens_by_call
        self.call_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        index = min(self.call_count, len(self._tokens_by_call) - 1)
        self.call_count += 1
        tokens = self._tokens_by_call[index]
        if tokens is None:
            return httpx.Response(500, request=request, text="network-api unavailable")
        return httpx.Response(200, json={"delegations": tokens}, request=request)


def _build_resolver(
    tokens_by_call: list[list[str] | None], *, clock: _FakeClock, ttl: float = 300.0
):
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
        scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
    )
    transport = _ScriptedTransport(
        [
            tokens if tokens is None else [token] if tokens == ["__TOKEN__"] else tokens
            for tokens in tokens_by_call
        ]
    )
    http_client = httpx.AsyncClient(transport=transport)
    resolver = HttpDelegationResolver(
        network_api_url="http://network-api.test",
        jwks_resolver=jwks_resolver,
        cache_ttl_seconds=ttl,
        http_client=http_client,
        clock=clock,
    )
    return resolver, transport, token


@pytest.mark.asyncio
async def test_cache_hit_just_before_expiry_does_not_refetch() -> None:
    clock = _FakeClock(start=0.0)
    resolver, transport, _token = _build_resolver([["__TOKEN__"]], clock=clock, ttl=300.0)

    first = await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
    )
    assert first.agent_id == "agent_01"
    assert transport.call_count == 1

    clock.value = 299.0  # one second before the 300s TTL elapses
    again = await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
    )
    assert again.agent_id == "agent_01"
    assert transport.call_count == 1, "a cache hit within TTL must not trigger a refetch"


@pytest.mark.asyncio
async def test_cache_refetches_once_ttl_has_elapsed() -> None:
    clock = _FakeClock(start=0.0)
    resolver, transport, _token = _build_resolver(
        [["__TOKEN__"], ["__TOKEN__"]], clock=clock, ttl=300.0
    )

    await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
    )
    assert transport.call_count == 1

    clock.value = 300.01  # just past the TTL boundary
    await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
    )
    assert transport.call_count == 2, "a cache entry past its TTL must trigger a fresh fetch"


@pytest.mark.asyncio
async def test_refresh_failure_after_ttl_expiry_fails_closed_not_stale() -> None:
    """ADR-017's explicit requirement: a refresh failure on an expired
    entry is a rejection, never a fall-back to the last-known-good
    (stale) value."""
    clock = _FakeClock(start=0.0)
    resolver, transport, _token = _build_resolver([["__TOKEN__"], None], clock=clock, ttl=300.0)

    good = await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
    )
    assert good.agent_id == "agent_01"

    clock.value = 301.0
    with pytest.raises(httpx.HTTPStatusError):
        await resolver.get_delegation(
            organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
        )
    # The failure must not have been masked into a successful (stale) return.


@pytest.mark.asyncio
async def test_revocation_within_ttl_still_served_from_cache_then_caught_after_expiry() -> None:
    """Revocation occurring while a receiver holds a cached, still-
    within-TTL entry is not visible until that TTL elapses — this is
    the documented freshness bound, not a bug; the second half of this
    test proves the revocation IS caught once the TTL boundary passes."""
    clock = _FakeClock(start=0.0)
    resolver, transport, _token = _build_resolver([["__TOKEN__"], []], clock=clock, ttl=300.0)

    first = await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
    )
    assert first.agent_id == "agent_01"

    # "Revoked" at the server (next fetch returns no delegations), but
    # still within the cache TTL — the stale, now-invalid cache entry
    # is what gets served, by design (the documented freshness bound).
    clock.value = 100.0
    still_cached = await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
    )
    assert still_cached.agent_id == "agent_01"
    assert transport.call_count == 1

    # Past the TTL: the next lookup must refetch and observe the
    # revocation.
    clock.value = 301.0
    with pytest.raises(DelegationNotFoundError):
        await resolver.get_delegation(
            organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
        )
    # The expired-cache lookup triggers the normal TTL refetch, then
    # (finding no matching kid) one additional forced refresh — the
    # same "don't wait out the TTL for a just-published change" logic
    # jwks.py already has. Either way, the server was consulted again
    # after the TTL boundary — never served from the stale,
    # pre-revocation cache entry.
    assert transport.call_count > 1


@pytest.mark.asyncio
async def test_force_fresh_bypasses_cache_even_within_ttl() -> None:
    """Commitment-adjacent artifact types pass ``force_fresh=True`` —
    confirm this actually bypasses a still-valid cache entry, not just
    a logically-expired one."""
    clock = _FakeClock(start=0.0)
    resolver, transport, _token = _build_resolver(
        [["__TOKEN__"], ["__TOKEN__"]], clock=clock, ttl=300.0
    )

    await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
    )
    assert transport.call_count == 1

    clock.value = 1.0  # well within TTL
    await resolver.get_delegation(
        organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE, force_fresh=True
    )
    assert transport.call_count == 2, "force_fresh must bypass a still-valid cache entry"


class _RootKeyInfrastructureFailureResolver:
    """A JwksResolver whose ``get_key`` fails for a reason that is NOT
    ``KeyNotFoundError`` — simulating a genuine infrastructure failure
    of the root-key JWKS fetch itself (network-api unreachable, a
    timeout, a malformed response), as opposed to a legitimate
    "this kid is not currently valid" answer (revoked/expired/never
    existed, which real resolvers signal with ``KeyNotFoundError``
    specifically)."""

    def invalidate(self, organization_id: str) -> None:
        return None

    async def get_key(self, *, organization_id: str, kid: str, force_fresh: bool = False):
        raise RuntimeError("simulated network-api outage resolving the root JWKS")


@pytest.mark.asyncio
async def test_root_key_infra_failure_propagates_not_empty_list() -> None:
    """Merge-review addition, closing a real gap: before this fix,
    ``verify_delegation_token``'s root-key resolution step caught
    *any* exception from the JWKS resolver (not just ``KeyNotFoundError``)
    and converted it into ``InvalidSignatureError`` — a ``ProofError``
    that ``HttpDelegationResolver._fetch``'s per-token
    ``except ProofError: continue`` then silently swallowed. A root-key
    JWKS endpoint that is simply unreachable (not "this key is revoked")
    would therefore make every delegation signed under it vanish from
    the resolved list, indistinguishable from "this organization
    genuinely has no delegations" — a dangerous fail-open-by-omission
    for exactly the infrastructure-failure case the merge review asked
    about. This test proves the fix: the lookup now raises instead of
    silently returning an empty/not-found result."""
    root_private, root_public = generate_signing_keypair()
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
        scope=DelegationScope(artifact_types=("quote",), protocol_version="v1"),
    )
    transport = _ScriptedTransport([[token]])
    http_client = httpx.AsyncClient(transport=transport)
    resolver = HttpDelegationResolver(
        network_api_url="http://network-api.test",
        jwks_resolver=_RootKeyInfrastructureFailureResolver(),
        http_client=http_client,
        clock=_FakeClock(start=0.0),
    )

    # Must NOT resolve to "not found" -- that would be indistinguishable
    # from a legitimate "no delegations exist" answer. It must raise,
    # surfacing the real, recoverable infrastructure cause.
    with pytest.raises(Exception) as excinfo:
        await resolver.get_delegation(
            organization_id=ORG, kid="agent-k1", expected_audience=NETWORK_AUDIENCE
        )
    assert not isinstance(excinfo.value, DelegationNotFoundError)
    assert "simulated network-api outage" in str(excinfo.value)
