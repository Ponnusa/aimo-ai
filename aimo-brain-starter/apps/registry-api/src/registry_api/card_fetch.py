"""SSRF-safe Agent Card fetching.

Registry-api is the one AIMO BRAIN service that fetches arbitrary,
operator-supplied URLs (the ``a2a_card_url`` an agent registers with, and
later the same host's ``/.well-known/aimo-brain-challenge`` path for
ownership verification). Per docs/SECURITY.md test case 7 ("Malicious
private-IP/metadata-service URL is blocked by discovery") every such fetch
goes through :func:`validate_card_url` first and through an httpx
transport that re-checks the target on every single request — never
trusting a URL just because it looked safe once.

Defenses implemented:
- Only ``http``/``https`` schemes are accepted — always, even in the
  explicit local-dev bypass below.
- The hostname's resolved IP addresses are checked against private,
  loopback, link-local (this also covers the 169.254.169.254 cloud
  metadata endpoint), multicast, reserved and unspecified ranges.
- Redirects are never followed (``follow_redirects=False``); a 3xx
  response is simply not a valid Agent Card response, so a redirect to a
  disallowed target is never reached by a second request.
- Responses are capped in size to avoid resource exhaustion.

Known residual limitation, documented rather than silently assumed away:
this validates the resolved IP at request time but does not pin the
subsequent TCP connection to that exact address, so a DNS answer that
changes between the check and httpcore's own connection (a "DNS
rebinding" attack) is not fully closed. That is an acceptable residual
risk for M2 given there is no real external registration target yet;
closing it fully (IP-pinned connections with correct TLS SNI) is a
documented follow-up, not something this module claims to already do.

Local development/test override: set ``allow_private_network_targets`` so
buyer-agent/supplier-agent, which run on ``127.0.0.1`` in this repo's own
tests and local dev, can register with registry-api. This must remain
unset/false for any real deployment — see
``RegistryApiSettings.registry_allow_private_network_card_targets``.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

import httpx
from a2a.client import A2ACardResolver
from a2a.client.errors import AgentCardResolutionError
from a2a.types import AgentCard

ALLOWED_SCHEMES = {"http", "https"}
MAX_RESPONSE_BYTES = 256 * 1024

#: hostname -> list of resolved IP address strings. Swappable in tests so
#: SSRF behavior is deterministic without relying on real DNS/network.
Resolver = Callable[[str], list[str]]


class UnsafeCardUrlError(Exception):
    """Raised when a card URL (or a challenge-path host) fails an SSRF
    safety check. Never caught and silently ignored by callers."""


class CardFetchError(Exception):
    """Raised when a URL passes safety checks but the card cannot be
    fetched or does not parse as a valid Agent Card."""


def default_resolve(hostname: str) -> list[str]:
    infos = socket.getaddrinfo(hostname, None)
    return sorted({str(info[4][0]) for info in infos})


def _is_disallowed_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_card_url(
    url: str,
    *,
    allow_private_network_targets: bool,
    resolve: Resolver = default_resolve,
) -> None:
    """Raise :class:`UnsafeCardUrlError` unless ``url`` is a safe target.

    Scheme validation always applies. IP-range validation is skipped only
    when ``allow_private_network_targets`` is explicitly set — the local
    dev/test escape hatch.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeCardUrlError(f"URL scheme {parsed.scheme!r} is not permitted.")
    if not parsed.hostname:
        raise UnsafeCardUrlError("URL has no hostname.")

    if allow_private_network_targets:
        return

    try:
        addresses = resolve(parsed.hostname)
    except OSError as exc:
        raise UnsafeCardUrlError(f"Could not resolve host {parsed.hostname!r}: {exc}") from exc

    if not addresses:
        raise UnsafeCardUrlError(f"Host {parsed.hostname!r} did not resolve to any address.")

    for address in addresses:
        if _is_disallowed_ip(address):
            raise UnsafeCardUrlError(
                f"Host {parsed.hostname!r} resolves to disallowed address {address}."
            )


class _SsrfSafeAsyncTransport(httpx.AsyncHTTPTransport):
    """Re-validates the target of every single request this transport
    carries — including what would otherwise be a redirect hop — so a
    URL that passed an earlier check cannot be reused to reach a
    disallowed target later."""

    def __init__(
        self,
        *,
        allow_private_network_targets: bool,
        resolve: Resolver,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._allow_private_network_targets = allow_private_network_targets
        self._resolve = resolve

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        validate_card_url(
            str(request.url),
            allow_private_network_targets=self._allow_private_network_targets,
            resolve=self._resolve,
        )
        return await super().handle_async_request(request)


def build_card_fetch_client(
    *,
    allow_private_network_targets: bool,
    timeout: float = 5.0,
    resolve: Resolver = default_resolve,
) -> httpx.AsyncClient:
    transport = _SsrfSafeAsyncTransport(
        allow_private_network_targets=allow_private_network_targets, resolve=resolve
    )
    return httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False)


async def fetch_and_validate_agent_card(
    url: str,
    *,
    allow_private_network_targets: bool,
    fetch_timeout_seconds: float = 5.0,
    resolve: Resolver = default_resolve,
) -> AgentCard:
    """Fetch ``url``'s Agent Card through the official A2A SDK resolver,
    over an SSRF-safe transport. Raises :class:`UnsafeCardUrlError` for an
    unsafe target or :class:`CardFetchError` for any other failure."""
    validate_card_url(
        url, allow_private_network_targets=allow_private_network_targets, resolve=resolve
    )
    async with build_card_fetch_client(
        allow_private_network_targets=allow_private_network_targets,
        timeout=fetch_timeout_seconds,
        resolve=resolve,
    ) as client:
        try:
            resolver = A2ACardResolver(httpx_client=client, base_url=url)
            return await resolver.get_agent_card()
        except (httpx.HTTPError, AgentCardResolutionError, UnsafeCardUrlError) as exc:
            if isinstance(exc, UnsafeCardUrlError):
                raise
            raise CardFetchError(f"Could not fetch or parse Agent Card at {url}: {exc}") from exc


async def fetch_text_safely(
    url: str,
    *,
    allow_private_network_targets: bool,
    fetch_timeout_seconds: float = 5.0,
    resolve: Resolver = default_resolve,
) -> str:
    """Fetch plain text from ``url`` (used for ownership-verification
    challenge re-fetch) through the same SSRF-safe transport."""
    validate_card_url(
        url, allow_private_network_targets=allow_private_network_targets, resolve=resolve
    )
    async with build_card_fetch_client(
        allow_private_network_targets=allow_private_network_targets,
        timeout=fetch_timeout_seconds,
        resolve=resolve,
    ) as client:
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            raise CardFetchError(f"Could not fetch {url}: {exc}") from exc
        if response.status_code != 200:
            raise CardFetchError(f"Fetching {url} returned status {response.status_code}.")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise CardFetchError(f"Response from {url} exceeded the allowed size.")
        return response.text
