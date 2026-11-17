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
