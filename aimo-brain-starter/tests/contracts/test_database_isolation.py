"""Architectural tests proving service isolation.

These tests are the M0 "Done when" criterion from ``docs/ROADMAP.md``
("an architectural test proving service isolation") and enforce
``docs/ARCHITECTURE.md``: "No cross-organization SQL joins to exchange
business data" plus ``docs/SECURITY.md`` test case 1: "Buyer agent cannot
query supplier private tables."

They work by static source inspection rather than by booting real
databases, so they run fast, in every CI run, with no infrastructure, and
they fail the moment someone adds a forbidden import — before any runtime
test would even get the chance to catch it.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

BUYER_SRC = REPO_ROOT / "apps" / "buyer-agent" / "src"
SUPPLIER_SRC = REPO_ROOT / "apps" / "supplier-agent" / "src"
NETWORK_SRC = REPO_ROOT / "apps" / "network-api" / "src"
REGISTRY_SRC = REPO_ROOT / "apps" / "registry-api" / "src"


def _top_level_imports(package_dir: Path) -> set[str]:
    modules: set[str] = set()
    for path in package_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".")[0])
    return modules


def test_buyer_agent_never_imports_supplier_agent() -> None:
    imports = _top_level_imports(BUYER_SRC)
    assert "supplier_agent" not in imports


def test_supplier_agent_never_imports_buyer_agent() -> None:
    imports = _top_level_imports(SUPPLIER_SRC)
    assert "buyer_agent" not in imports


def test_buyer_agent_never_imports_network_api_internals() -> None:
    imports = _top_level_imports(BUYER_SRC)
    assert "network_api" not in imports


def test_supplier_agent_never_imports_network_api_internals() -> None:
    imports = _top_level_imports(SUPPLIER_SRC)
    assert "network_api" not in imports


def test_network_api_never_imports_buyer_or_supplier_agent() -> None:
    imports = _top_level_imports(NETWORK_SRC)
    assert "buyer_agent" not in imports
    assert "supplier_agent" not in imports


def test_network_api_never_imports_registry_api_internals() -> None:
    imports = _top_level_imports(NETWORK_SRC)
    assert "registry_api" not in imports


def test_registry_api_never_imports_other_services_internals() -> None:
    """registry-api's only cross-service interaction is an HTTP call to
    network-api's own API (registry_api.network_client) — it must never
    import network_api, buyer_agent or supplier_agent as Python modules,
    which would imply sharing a database connection or process."""
    imports = _top_level_imports(REGISTRY_SRC)
    assert "network_api" not in imports
    assert "buyer_agent" not in imports
    assert "supplier_agent" not in imports


def test_buyer_and_supplier_agents_never_import_registry_api_internals() -> None:
    """buyer-agent talks to registry-api only over HTTP
    (buyer_agent.registry_client) — never by importing registry_api."""
    assert "registry_api" not in _top_level_imports(BUYER_SRC)
    assert "registry_api" not in _top_level_imports(SUPPLIER_SRC)


def test_a2a_integration_package_has_no_direct_agent_to_agent_call_path() -> None:
    """Guards docs/CLAUDE_START_PROMPT.md: "Do not simulate A2A by making
    direct Python function calls between agents." """
    a2a_src = REPO_ROOT / "packages" / "a2a-integration" / "src"
    imports = _top_level_imports(a2a_src)
    assert "buyer_agent" not in imports
    assert "supplier_agent" not in imports


def test_each_agent_settings_reads_a_distinct_database_env_var() -> None:
    """Buyer, supplier, network and registry each declare a different
    ``validation_alias`` for their database URL, so no two services can be
    pointed at the same connection string by a shared environment
    variable name."""
    buyer_settings = (
        REPO_ROOT / "apps" / "buyer-agent" / "src" / "buyer_agent" / "settings.py"
    ).read_text(encoding="utf-8")
    supplier_settings = (
        REPO_ROOT / "apps" / "supplier-agent" / "src" / "supplier_agent" / "settings.py"
    ).read_text(encoding="utf-8")
    network_settings = (
        REPO_ROOT / "apps" / "network-api" / "src" / "network_api" / "settings.py"
    ).read_text(encoding="utf-8")
    registry_settings = (
        REPO_ROOT / "apps" / "registry-api" / "src" / "registry_api" / "settings.py"
    ).read_text(encoding="utf-8")

    assert 'validation_alias="BUYER_DATABASE_URL"' in buyer_settings
    assert 'validation_alias="SUPPLIER_DATABASE_URL"' in supplier_settings
    assert 'validation_alias="DATABASE_URL"' in network_settings
    assert 'validation_alias="REGISTRY_DATABASE_URL"' in registry_settings
    # Each service declares exactly one *_DATABASE_URL validation_alias, and
    # it is its own — never another service's. Non-database settings (own
    # agent id, own A2A URL, the counterparty's public A2A URL for
    # discovery, shared signing/admin/internal-service secrets) are fine to
    # add; database connection strings are not.
    import re

    db_alias_pattern = re.compile(r'validation_alias="(\w*DATABASE_URL)"')
    assert db_alias_pattern.findall(buyer_settings) == ["BUYER_DATABASE_URL"]
    assert db_alias_pattern.findall(supplier_settings) == ["SUPPLIER_DATABASE_URL"]
    assert db_alias_pattern.findall(network_settings) == ["DATABASE_URL"]
    assert db_alias_pattern.findall(registry_settings) == ["REGISTRY_DATABASE_URL"]
