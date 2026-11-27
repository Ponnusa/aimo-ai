"""Runs the preflight script the same way a deploy step would: as its own
process, not an import -- this is also what proves it never prints the
credential, since that's checked against real process stdout/stderr."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preflight_check.py"


def _run(env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_fails_when_execution_credential_is_empty() -> None:
    env = os.environ.copy()
    env.pop("SUPPLIER_MCP_EXECUTION_CREDENTIAL", None)
    result = _run({"SUPPLIER_MCP_EXECUTION_CREDENTIAL": ""})
    assert result.returncode == 1
    assert "PREFLIGHT FAILED" in result.stderr


def test_passes_when_execution_credential_is_set() -> None:
    result = _run({"SUPPLIER_MCP_EXECUTION_CREDENTIAL": "svc_test.anything"})
    assert result.returncode == 0
    assert "PREFLIGHT OK" in result.stdout


def test_never_prints_the_credential_value() -> None:
    secret_marker = "svc_do-not-print-this-marker.the-secret-half"
    result = _run({"SUPPLIER_MCP_EXECUTION_CREDENTIAL": secret_marker})
    assert secret_marker not in result.stdout
    assert secret_marker not in result.stderr
