"""network-api organization-membership client (M6).

Used by :func:`buyer_agent.authorization.decide_approval` to
independently revalidate a portal approval assertion's claimed approver
before honoring any decision — the assertion's own ``roles`` claim is
never treated as sufficient on its own (docs/SECURITY.md's M6 section).
"""

from __future__ import annotations

import httpx


class MemberLookupError(Exception):
    """Raised when network-api cannot be reached. Fails closed: callers
    must treat this the same as "not an active member", never as
    "assume active"."""


async def is_active_member_with_role(
    *,
    network_api_url: str,
    internal_service_key: str,
    organization_id: str,
    oidc_subject: str,
    role: str,
    fetch_timeout_seconds: float = 5.0,
) -> bool:
    try:
        async with httpx.AsyncClient(timeout=fetch_timeout_seconds) as client:
            response = await client.get(
                f"{network_api_url}/members/lookup",
                params={
                    "organization_id": organization_id,
                    "oidc_subject": oidc_subject,
                    "role": role,
                },
                headers={"X-Internal-Service-Key": internal_service_key},
            )
    except httpx.HTTPError as exc:
        raise MemberLookupError(f"Could not reach network-api: {exc}") from exc

    if response.status_code != 200:
        raise MemberLookupError(f"network-api member lookup returned {response.status_code}.")
    return bool(response.json()["active"])
