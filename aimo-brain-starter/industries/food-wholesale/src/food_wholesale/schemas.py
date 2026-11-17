"""JSON Schema loading/validation for food-wholesale artifacts.

Schemas live as plain ``.schema.json`` files under ``../../schemas`` (one
directory above this installed package, i.e. the ``industries/
food-wholesale/schemas/`` folder in the repo) so they can be published and
consumed independently of the Python package, per ``docs/PROTOCOLS.md``
rule 1: "All artifact types have stable versioned JSON Schemas."

M0 scope: loading and structural validation only. Semantic validation
(expiry, hash, negotiation rules) lands in M4.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


@lru_cache
def load_schema(name: str) -> dict[str, Any]:
    """Load a schema by file stem, e.g. ``load_schema("request_for_quote")``."""
    path = _SCHEMAS_DIR / f"{name}.schema.json"
    if not path.exists():
        raise FileNotFoundError(f"No schema file found for '{name}' at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_against_schema(name: str, instance: dict[str, Any]) -> None:
    """Raise ``jsonschema.ValidationError`` if ``instance`` does not conform."""
    schema = load_schema(name)
    jsonschema.validate(instance=instance, schema=schema)
