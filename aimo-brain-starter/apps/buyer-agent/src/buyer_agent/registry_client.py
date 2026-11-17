"""Buyer-side registry-api client: discovery only.

This is the M2 replacement for a hardcoded supplier A2A URL. It talks to
registry-api over plain HTTP — never to supplier-agent directly, and
never by reading registry-api's database. Once discovery returns a
supplier's real A2A endpoint URL, the buyer talks to that endpoint
directly with the official A2A SDK (see ``buyer_agent.client``) —
registry-api is never in the conversation after this point.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class RegistryDiscoveryError(Exception):
    """Raised when registry-api cannot be reached, or returns no agent
    matching the requested capability."""


@dataclass(frozen=True)
class DiscoveredSupplier:
    agent_id: str
    organization_id: str
    display_name: str
    a2a_endpoint_url: str


async def discover_supplier_via_registry(
    *,
    registry_url: str,
    requesting_organization_id: str,
    skill_id: str,
    fetch_timeout_seconds: float = 5.0,
) -> DiscoveredSupplier:
    """Ask registry-api for an active, discoverable agent offering
    ``skill_id``. Raises :class:`RegistryDiscoveryError` if registry-api
    is unreachable or if no matching, authorized agent is found — this
    never falls back to a hardcoded URL."""
    try:
        async with httpx.AsyncClient(timeout=fetch_timeout_seconds) as client:
            response = await client.get(
                f"{registry_url}/agents",
                params={
                    "requesting_organization_id": requesting_organization_id,
                    "skill_id": skill_id,
                },
            )
    except httpx.HTTPError as exc:
        raise RegistryDiscoveryError(
            f"Could not reach registry-api at {registry_url}: {exc}"
        ) from exc

    if response.status_code != 200:
        raise RegistryDiscoveryError(
            f"registry-api discovery returned status {response.status_code}."
        )

    matches = response.json()
    if not matches:
        raise RegistryDiscoveryError(
            f"No discoverable agent offering skill {skill_id!r} for organization "
            f"{requesting_organization_id!r}."
        )

    chosen = matches[0]
    return DiscoveredSupplier(
        agent_id=chosen["agent_id"],
        organization_id=chosen["organization_id"],
        display_name=chosen["display_name"],
        a2a_endpoint_url=chosen["a2a_endpoint_url"],
    )
