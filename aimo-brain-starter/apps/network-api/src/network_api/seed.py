"""Seed fictional organizations for local development and M0 demos.

All names, IDs and data below are fictional, per CLAUDE.md ("seed data is
fictional") and README.md ("Start with simulated data"). Nothing here
represents a real Valio Aimo customer or supplier.
"""

from __future__ import annotations

import asyncio

from database import new_engine, new_session_factory
from network_api.models import OrganizationRecord
from network_api.settings import get_settings

FICTIONAL_ORGANIZATIONS = [
    OrganizationRecord(
        organization_id="org_fictional_bistro_01",
        display_name="Fictional Lakeside Bistro",
        verified=True,
    ),
    OrganizationRecord(
        organization_id="org_fictional_wholesale_01",
        display_name="Fictional Nordic Wholesale Foods",
        verified=True,
    ),
    # M6: a non-trading pseudo-organization that exists solely so the
    # portal's own Ed25519 signing key can be registered and published
    # through the same /organizations/{id}/signing-keys and /jwks.json
    # endpoints every real organization already uses — no new key-
    # hosting mechanism was built for the portal. Never a party to a
    # trading relationship; never returned to buyer/supplier discovery.
    OrganizationRecord(
        organization_id="org_aimo_portal",
        display_name="AIMO BRAIN Portal (service identity)",
        verified=False,
    ),
]


async def seed() -> None:
    settings = get_settings()
    engine = new_engine(settings.network_database_url)
    session_factory = new_session_factory(engine)
    async with session_factory() as session:
        for org in FICTIONAL_ORGANIZATIONS:
            await session.merge(org)
        await session.commit()
    await engine.dispose()


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
