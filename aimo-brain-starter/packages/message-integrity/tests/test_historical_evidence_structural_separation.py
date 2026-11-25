from __future__ import annotations

import ast
from pathlib import Path

LIVE_VERIFICATION_MODULES = [
    "proofs.py",
    "delegated_verification.py",
    "jwks.py",
    "delegation_resolver.py",
]
FORBIDDEN_IMPORTS = {"message_integrity.historical_evidence", "historical_evidence"}


def _imported_names(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_live_verification_modules_never_import_historical_evidence():
    """ADR-018 test 12: Q1 (live authorization) must be structurally
    unreachable from, and never consult, Q2 (historical evidence) --
    proven here at the import level, not just by behavior."""
    package_dir = Path(__file__).resolve().parents[1] / "src" / "message_integrity"
    for module_name in LIVE_VERIFICATION_MODULES:
        imported = _imported_names(package_dir / module_name)
        assert not (imported & FORBIDDEN_IMPORTS), (
            f"{module_name} must never import historical_evidence -- "
            f"live authorization (Q1) must stay structurally separate from "
            f"historical evidence (Q2)."
        )
