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
BUYER_MCP_SRC = REPO_ROOT / "apps" / "buyer-mcp" / "src"
SUPPLIER_MCP_SRC = REPO_ROOT / "apps" / "supplier-mcp" / "src"


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


def _full_dotted_imports(package_dir: Path) -> set[str]:
    """Like :func:`_top_level_imports` but keeps the full dotted path
    (``food_wholesale.adapters.mock``, not just ``food_wholesale``) — used
    where the distinction between a package and one of its submodules
    matters, e.g. the M3 "agents use MCP, not direct tool imports" rule."""
    modules: set[str] = set()
    for path in package_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
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


def test_buyer_agent_never_imports_food_wholesale_adapters_directly() -> None:
    """M3: agents must use MCP clients, not tool implementations, for
    catalogue/budget/pricing data (see docs/ROADMAP.md M3). Only
    ``apps/buyer-mcp``/``apps/supplier-mcp`` may import
    ``food_wholesale.adapters``."""
    imports = _full_dotted_imports(BUYER_SRC)
    assert not any(name.startswith("food_wholesale.adapters") for name in imports)


def test_supplier_agent_never_imports_food_wholesale_adapters_directly() -> None:
    imports = _full_dotted_imports(SUPPLIER_SRC)
    assert not any(name.startswith("food_wholesale.adapters") for name in imports)


def test_buyer_mcp_and_supplier_mcp_never_import_agent_or_other_services_internals() -> None:
    """buyer-mcp/supplier-mcp are independently deployable: they never
    import an agent's internals, each other, network-api or registry-api
    as Python modules — only ``mcp_integration``/``identity`` (shared,
    DB-free packages) and their own ``food_wholesale`` tool adapters."""
    for src in (BUYER_MCP_SRC, SUPPLIER_MCP_SRC):
        imports = _top_level_imports(src)
        assert "buyer_agent" not in imports
        assert "supplier_agent" not in imports
        assert "network_api" not in imports
        assert "registry_api" not in imports
    assert "supplier_mcp" not in _top_level_imports(BUYER_MCP_SRC)
    assert "buyer_mcp" not in _top_level_imports(SUPPLIER_MCP_SRC)


def test_agents_never_import_each_others_mcp_server_internals() -> None:
    """buyer-agent/supplier-agent call their own org's MCP server only
    over MCP (``mcp_integration.call_tool``) — never by importing
    ``buyer_mcp``/``supplier_mcp`` as Python modules."""
    for src in (BUYER_SRC, SUPPLIER_SRC):
        imports = _top_level_imports(src)
        assert "buyer_mcp" not in imports
        assert "supplier_mcp" not in imports


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
    buyer_mcp_settings = (
        REPO_ROOT / "apps" / "buyer-mcp" / "src" / "buyer_mcp" / "settings.py"
    ).read_text(encoding="utf-8")
    supplier_mcp_settings = (
        REPO_ROOT / "apps" / "supplier-mcp" / "src" / "supplier_mcp" / "settings.py"
    ).read_text(encoding="utf-8")

    assert 'validation_alias="BUYER_DATABASE_URL"' in buyer_settings
    assert 'validation_alias="SUPPLIER_DATABASE_URL"' in supplier_settings
    assert 'validation_alias="DATABASE_URL"' in network_settings
    assert 'validation_alias="REGISTRY_DATABASE_URL"' in registry_settings
    assert 'validation_alias="BUYER_MCP_DATABASE_URL"' in buyer_mcp_settings
    assert 'validation_alias="SUPPLIER_MCP_DATABASE_URL"' in supplier_mcp_settings
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
    assert db_alias_pattern.findall(buyer_mcp_settings) == ["BUYER_MCP_DATABASE_URL"]
    assert db_alias_pattern.findall(supplier_mcp_settings) == ["SUPPLIER_MCP_DATABASE_URL"]
