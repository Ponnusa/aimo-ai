"""Seed fictional organizations for local development and M0 demos.

All names, IDs and data below are fictional, per CLAUDE.md ("seed data is
fictional") and README.md ("Start with simulated data"). Nothing here
represents a real Valio Aimo customer or supplier.
"""

from __future__ import annotations

import asyncio

from network_api.models import OrganizationRecord
from network_api.settings import get_settings

from database import new_engine, new_session_factory

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
