"""Resolves per-agent delegations for verification (M8 slice 2, ADR-017).

Mirrors ``message_integrity.jwks.JwksResolver``'s shape and its
fail-closed caching discipline deliberately: a cache entry is never
served past its TTL, and a refresh failure on an expired entry is a
failure, never a fall-back to stale data. ``force_fresh`` (required by
callers verifying commitment-adjacent artifact types) bypasses the
cache entirely.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from message_integrity.delegations import (
    DelegationError,
    VerifiedDelegation,
    verify_delegation_token,
)
from message_integrity.jwks import JwksResolver


class DelegationNotFoundError(Exception):
    """No currently-live delegation exists for this (organization_id,
    kid) — covers "never existed," "expired," and "revoked" identically,
    which a verifier must treat the same way: this ``kid`` is not a
    delegated-agent key at all."""


class DelegationResolver(ABC):
    @abstractmethod
    async def get_delegation(
        self, *, organization_id: str, kid: str, expected_audience: str, force_fresh: bool = False
    ) -> VerifiedDelegation: ...

    @abstractmethod
    def invalidate(self, organization_id: str) -> None: ...


@dataclass
class _CacheEntry:
    delegations: dict[str, VerifiedDelegation]
    fetched_at: float


class HttpDelegationResolver(DelegationResolver):
    """Fetches ``GET {network_api_url}/organizations/{id}/delegations.json``
    — a list of currently-live delegation JWS tokens — cryptographically
    re-verifies every one of them against ``jwks_resolver`` (never
    trusting network-api's storage alone), and caches the result per
    organization for ``cache_ttl_seconds``."""

    def __init__(
        self,
        *,
        network_api_url: str,
        jwks_resolver: JwksResolver,
        cache_ttl_seconds: float = 300.0,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._network_api_url = network_api_url.rstrip("/")
        self._jwks_resolver = jwks_resolver
        self._cache_ttl_seconds = cache_ttl_seconds
        self._http_client = http_client
        self._clock = clock
        self._cache: dict[str, _CacheEntry] = {}

    def invalidate(self, organization_id: str) -> None:
        self._cache.pop(organization_id, None)

    async def _fetch(
        self, organization_id: str, *, expected_audience: str, force_fresh: bool
    ) -> dict[str, VerifiedDelegation]:
        client = self._http_client or httpx.AsyncClient(timeout=5.0)
        try:
            response = await client.get(
                f"{self._network_api_url}/organizations/{organization_id}/delegations.json"
            )
            response.raise_for_status()
            document = response.json()
        finally:
            if self._http_client is None:
                await client.aclose()

        delegations: dict[str, VerifiedDelegation] = {}
        for token in document.get("delegations", []):
            try:
                verified = await verify_delegation_token(
                    token,
                    jwks_resolver=self._jwks_resolver,
                    expected_audience=expected_audience,
                    force_fresh=force_fresh,
                )
            except DelegationError:
                # A delegation that no longer verifies (e.g. its issuing
                # root key was itself revoked after publication) is
                # treated as absent, never as a resolver-level failure —
                # other, still-valid delegations for this organization
                # must remain usable.
                continue
            delegations[verified.delegated_kid] = verified
        return delegations

    async def get_delegation(
        self, *, organization_id: str, kid: str, expected_audience: str, force_fresh: bool = False
    ) -> VerifiedDelegation:
        entry = self._cache.get(organization_id)
        now = self._clock()
        if force_fresh or entry is None or (now - entry.fetched_at) > self._cache_ttl_seconds:
            delegations = await self._fetch(
                organization_id, expected_audience=expected_audience, force_fresh=force_fresh
            )
            entry = _CacheEntry(delegations=delegations, fetched_at=now)
            self._cache[organization_id] = entry

        delegation = entry.delegations.get(kid)
        if delegation is not None:
            return delegation

        # One forced refresh, mirroring HttpJwksResolver: a delegation
        # published moments ago should not require waiting out the full
        # cache TTL in tests/CI.
        delegations = await self._fetch(
            organization_id, expected_audience=expected_audience, force_fresh=True
        )
        entry = _CacheEntry(delegations=delegations, fetched_at=now)
        self._cache[organization_id] = entry
        delegation = entry.delegations.get(kid)
        if delegation is None:
            raise DelegationNotFoundError(
                f"No currently-live delegation for kid {kid!r} in organization {organization_id!r}."
            )
        return delegation


class InMemoryDelegationResolver(DelegationResolver):
    """Test double: a plain ``{(organization_id, kid): VerifiedDelegation}``
    map, no network call, no caching semantics to simulate."""

    def __init__(self) -> None:
        self._delegations: dict[tuple[str, str], VerifiedDelegation] = {}

    def register(self, delegation: VerifiedDelegation) -> None:
        self._delegations[(delegation.organization_id, delegation.delegated_kid)] = delegation

    def revoke(self, *, organization_id: str, kid: str) -> None:
        self._delegations.pop((organization_id, kid), None)

    def invalidate(self, organization_id: str) -> None:
        return None

    async def get_delegation(
        self, *, organization_id: str, kid: str, expected_audience: str, force_fresh: bool = False
    ) -> VerifiedDelegation:
        delegation = self._delegations.get((organization_id, kid))
        if delegation is None:
            raise DelegationNotFoundError(
                f"No currently-live delegation for kid {kid!r} in organization {organization_id!r}."
            )
        if delegation.audience != expected_audience:
            from message_integrity.delegations import DelegationAudienceError

            raise DelegationAudienceError(
                f"Delegation audience {delegation.audience!r} does not match "
                f"expected {expected_audience!r}."
            )
        return delegation


__all__ = [
    "DelegationNotFoundError",
    "DelegationResolver",
    "HttpDelegationResolver",
    "InMemoryDelegationResolver",
]
