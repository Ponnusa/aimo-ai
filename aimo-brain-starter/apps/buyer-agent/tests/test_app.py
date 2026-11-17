import pytest
from buyer_agent.app import create_app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint_reports_ok_and_fake_llm() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "buyer-agent"
    assert body["llm_provider"] == "fake-deterministic-v1"
