"""M0 security smoke test.

The full adversarial security suite from ``docs/SECURITY.md`` (spoofed
agents, replay, prompt injection, SSRF, etc.) requires A2A/MCP/policy
infrastructure that does not exist until M1/M3/M5. M0's scope is proving
the one precondition everything else depends on: each service is
configured against a genuinely different database, so cross-tenant access
is structurally impossible, not just policy-denied.
"""

from buyer_agent.settings import get_settings as get_buyer_settings
from network_api.settings import get_settings as get_network_settings
from supplier_agent.settings import get_settings as get_supplier_settings


def test_buyer_supplier_and_network_use_three_distinct_database_urls() -> None:
    get_buyer_settings.cache_clear()
    get_supplier_settings.cache_clear()
    get_network_settings.cache_clear()

    buyer_url = get_buyer_settings().buyer_database_url
    supplier_url = get_supplier_settings().supplier_database_url
    network_url = get_network_settings().network_database_url

    assert buyer_url != supplier_url
    assert buyer_url != network_url
    assert supplier_url != network_url
