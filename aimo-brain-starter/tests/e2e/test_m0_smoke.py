"""M0 end-to-end smoke test.

Boots all three M0 services in-process (each still with its own settings
and its own database engine — no shared app state) and checks each
reports healthy with its own identity. This is the M0 "fresh clone can run
checks and local services" acceptance test; it is not an A2A test (M1) and
makes no inter-agent call.
"""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_all_m0_services_report_healthy_independently() -> None:
    from buyer_agent.app import create_app as create_buyer_app
    from network_api.app import create_app as create_network_app
    from supplier_agent.app import create_app as create_supplier_app

    checks = {
        "network-api": create_network_app(),
        "buyer-agent": create_buyer_app(),
        "supplier-agent": create_supplier_app(),
    }

    for expected_service, app in checks.items():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == expected_service
