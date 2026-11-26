"""registry-api's only call into network-api: a read-only, internal-
service-key authenticated relationship lookup. This is an HTTP call to
network-api's own API, never a direct read of network-api's database —
see docs/DECISIONS.md ADR-008 for why trading relationships are modeled
there, not here.
"""

from __future__ import annotations

import httpx


class RelationshipLookupError(Exception):
    """Raised when network-api cannot be reached or rejects the lookup."""


class IntrospectionError(Exception):
    """Raised for any reason an opaque ``manage_agents`` credential could
    not be authoritatively confirmed (ADR-019 section 4) -- timeout,
    malformed response, or network-api unreachable. Every one of these
    must be treated as a rejection, fail-closed, never a best-effort
    accept; never caught and silently ignored by a caller."""


async def introspect_credential(
    *,
    network_api_url: str,
    internal_service_key: str,
    token: str,
    expected_organization_id: str,
    expected_scope: str,
    fetch_timeout_seconds: float = 5.0,
) -> dict:
    """Calls network-api's ``/introspect`` -- the authoritative, live
    answer for an opaque credential. registry-api never evaluates one
    of these locally; see ADR-019 section 4. The raw token is sent only
    in this one request body, over TLS in any real deployment, and is
    never logged by this function or by the caller."""
    try:
        async with httpx.AsyncClient(timeout=fetch_timeout_seconds) as client:
            response = await client.post(
                f"{network_api_url}/introspect",
                json={
                    "token": token,
                    "expected_organization_id": expected_organization_id,
                    "expected_scope": expected_scope,
                },
                headers={"X-Internal-Service-Key": internal_service_key},
            )
    except httpx.HTTPError as exc:
        raise IntrospectionError(f"Could not reach network-api for introspection: {exc}") from exc

    if response.status_code != 200:
        raise IntrospectionError(
            f"network-api introspection returned status {response.status_code}."
        )
    try:
        return response.json()
    except ValueError as exc:
        raise IntrospectionError(
            f"network-api returned a malformed introspection body: {exc}"
        ) from exc


async def has_active_relationship(
    *,
    network_api_url: str,
    internal_service_key: str,
    buyer_organization_id: str,
    supplier_organization_id: str,
    fetch_timeout_seconds: float = 5.0,
) -> bool:
    try:
        async with httpx.AsyncClient(timeout=fetch_timeout_seconds) as client:
            response = await client.get(
                f"{network_api_url}/relationships",
                params={
                    "buyer_organization_id": buyer_organization_id,
                    "supplier_organization_id": supplier_organization_id,
                },
                headers={"X-Internal-Service-Key": internal_service_key},
            )
    except httpx.HTTPError as exc:
        raise RelationshipLookupError(f"Could not reach network-api: {exc}") from exc

    if response.status_code != 200:
        raise RelationshipLookupError(
            f"network-api relationship lookup returned status {response.status_code}."
        )

    relationships = response.json()
    return any(r["status"] == "active" for r in relationships)
