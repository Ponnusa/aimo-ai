"""Resolves this industry's own versioned OPA policy bundle directories
(M5). The bundle *contents* (budget limits, approved suppliers/customers,
margin/quantity thresholds) are food-wholesale's own business data —
``packages/policy_engine``/``packages/authorization`` never see this
path construction logic, only the directory it points at.
"""

from __future__ import annotations

from pathlib import Path

#: Rooted at the installed package location (editable install in dev),
#: matching the pattern ``food_wholesale.schemas`` already uses for
#: ``../../schemas`` — independent of the caller's current working
#: directory.
_POLICIES_DIR = Path(__file__).resolve().parents[2] / "policies"


def policy_bundle_dir(role: str, version: str) -> Path:
    """Return the bundle directory for ``role`` (``"buyer"``/``"supplier"``)
    at ``version`` (e.g. ``"v1"``), e.g.
    ``industries/food-wholesale/policies/buyer/v1``."""
    return _POLICIES_DIR / role / version


def policy_query(role: str, version: str) -> str:
    """Return the OPA data query path for ``role``/``version``, e.g.
    ``data.aimo.buyer.v1.decision``."""
    return f"data.aimo.{role}.{version}.decision"


def list_policy_versions(role: str) -> list[str]:
    """List every bundle version directory shipped on disk for
    ``role`` (M6 policy catalog view) — never a live-editable list, just
    what the current deployment actually has."""
    role_dir = _POLICIES_DIR / role
    if not role_dir.is_dir():
        return []
    return sorted(p.name for p in role_dir.iterdir() if p.is_dir())


__all__ = ["list_policy_versions", "policy_bundle_dir", "policy_query"]
