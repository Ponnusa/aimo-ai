"""Supplier-side registry-api client (M5.1).

Unlike ``buyer_agent.registry_client``, this agent never discovers a
counterparty (the buyer always initiates). It only needs to confirm
that a cryptographically-verified inbound message's claimed sender is
actually a registered, active agent of its claimed organization, with
an active trading relationship — see
``buyer_agent.registry_client.verify_agent_is_registered_and_related``
for the identical logic and rationale.
"""

from __future__ import annotations

import httpx


class RegistryVerificationError(Exception):
    """Raised when registry-api cannot be reached, or the claimed
    sender is not a registered+active+related agent."""


async def verify_agent_is_registered_and_related(
    *,
    registry_url: str,
    requesting_organization_id: str,
    agent_id: str,
    organization_id: str,
    fetch_timeout_seconds: float = 5.0,
) -> None:
    """Uses ``GET /agents/{id}/verify-sender`` (direction-agnostic),
    not the M2 ``/agents`` discovery endpoint, which always treats the
    requester as the buyer side — message authentication has no such
    fixed role."""
    try:
        async with httpx.AsyncClient(timeout=fetch_timeout_seconds) as client:
            response = await client.get(
                f"{registry_url}/agents/{agent_id}/verify-sender",
                params={
                    "claimed_organization_id": organization_id,
                    "counterparty_organization_id": requesting_organization_id,
                },
            )
    except httpx.HTTPError as exc:
        raise RegistryVerificationError(
            f"Could not reach registry-api at {registry_url}: {exc}"
        ) from exc

    if response.status_code != 200:
        raise RegistryVerificationError(
            f"Agent {agent_id!r} of organization {organization_id!r} is not an active, "
            f"related sender for {requesting_organization_id!r} (status {response.status_code})."
        )
