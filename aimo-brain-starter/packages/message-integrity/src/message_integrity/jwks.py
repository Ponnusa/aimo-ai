"""JWKS resolution for verifying cross-organization signatures.

``network-api`` is the one party both organizations already trust for
discovery (``PROJECT.md``'s "network operator" role) — but as of M5.1 it
only ever distributes *public* key material. Losing network-api's own
data never lets anyone forge a signature for an organization that
doesn't already hold that organization's private key, unlike M5's
shared HMAC secret.

Revocation is enforced by absence: network-api's own ``/jwks.json``
response excludes revoked/expired keys, so a resolver that always
re-fetches (or re-fetches after a bounded cache TTL expires) naturally
stops trusting a revoked key within that bound — the same "bounded
cache lifetime" principle ``docs/ARCHITECTURE.md`` already states for
M2 agent revocation.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from message_integrity.keys import public_key_from_jwk


class KeyNotFoundError(Exception):
    """Raised when the claimed (organization_id, kid) does not resolve
    to a currently valid public key — covers both "never existed" and
    "revoked/expired," which a verifier must treat identically: reject."""


class JwksResolver(ABC):
    @abstractmethod
    async def get_key(
        self, *, organization_id: str, kid: str, force_fresh: bool = False
    ) -> Ed25519PublicKey: ...

    @abstractmethod
    def invalidate(self, organization_id: str) -> None: ...


@dataclass
class _CacheEntry:
    keys: dict[str, Ed25519PublicKey]
    fetched_at: float


class HttpJwksResolver(JwksResolver):
    """Fetches ``GET {network_api_url}/organizations/{id}/jwks.json``,
    caching the parsed key set per organization for ``cache_ttl_seconds``
    (default 5 minutes — bounded, so a revocation is never trusted
    beyond this window even without an explicit invalidation)."""

    def __init__(
        self,
        *,
        network_api_url: str,
        cache_ttl_seconds: float = 300.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._network_api_url = network_api_url.rstrip("/")
        self._cache_ttl_seconds = cache_ttl_seconds
        self._http_client = http_client
        self._cache: dict[str, _CacheEntry] = {}

    def invalidate(self, organization_id: str) -> None:
        self._cache.pop(organization_id, None)

    async def _fetch(self, organization_id: str) -> dict[str, Ed25519PublicKey]:
        client = self._http_client or httpx.AsyncClient(timeout=5.0)
        try:
            response = await client.get(
                f"{self._network_api_url}/organizations/{organization_id}/jwks.json"
            )
            response.raise_for_status()
            document = response.json()
        finally:
            if self._http_client is None:
                await client.aclose()

        keys: dict[str, Ed25519PublicKey] = {}
        for jwk in document.get("keys", []):
            try:
                keys[jwk["kid"]] = public_key_from_jwk(jwk)
            except (KeyError, ValueError):
                continue
        return keys

    async def get_key(
        self, *, organization_id: str, kid: str, force_fresh: bool = False
    ) -> Ed25519PublicKey:
        """Fail-closed, always (M8 slice 2, ADR-017): a cache entry is
        never served past its TTL, and a refresh failure on an expired
        entry propagates as a failure — it never falls back to
        last-known-good data. ``force_fresh`` (required for
        commitment-adjacent artifact types) bypasses the cache
        entirely, even when the cached entry is still within its TTL."""
        entry = self._cache.get(organization_id)
        now = time.monotonic()
        if force_fresh or entry is None or (now - entry.fetched_at) > self._cache_ttl_seconds:
            keys = await self._fetch(organization_id)
            entry = _CacheEntry(keys=keys, fetched_at=now)
            self._cache[organization_id] = entry

        key = entry.keys.get(kid)
        if key is not None:
            return key

        # One forced refresh: a key published moments ago should not
        # require waiting out the full cache TTL in tests/CI.
        keys = await self._fetch(organization_id)
        entry = _CacheEntry(keys=keys, fetched_at=now)
        self._cache[organization_id] = entry
        key = entry.keys.get(kid)
        if key is None:
            raise KeyNotFoundError(
                f"No valid signing key {kid!r} for organization {organization_id!r}."
            )
        return key


class InMemoryJwksResolver(JwksResolver):
    """Test double: a plain ``{(organization_id, kid): public_key}`` map,
    with no network call and no caching semantics to simulate."""

    def __init__(self) -> None:
        self._keys: dict[tuple[str, str], Ed25519PublicKey] = {}

    def register(self, *, organization_id: str, kid: str, public_key: Ed25519PublicKey) -> None:
        self._keys[(organization_id, kid)] = public_key

    def revoke(self, *, organization_id: str, kid: str) -> None:
        self._keys.pop((organization_id, kid), None)

    def invalidate(self, organization_id: str) -> None:
        return None

    async def get_key(
        self, *, organization_id: str, kid: str, force_fresh: bool = False
    ) -> Ed25519PublicKey:
        key = self._keys.get((organization_id, kid))
        if key is None:
            raise KeyNotFoundError(
                f"No valid signing key {kid!r} for organization {organization_id!r}."
            )
        return key


__all__ = [
    "HttpJwksResolver",
    "InMemoryJwksResolver",
    "JwksResolver",
    "KeyNotFoundError",
]
