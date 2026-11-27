#!/usr/bin/env python3
"""Enforced release procedure for `supplier-agent`: the preflight and the
deployment-provenance check (see `scripts/verify_deployment.py`) are both
mandatory steps a single command runs, not two separate things an
operator has to remember to do by hand.

Found the hard way (2026-11-27, same staging exercise): a deploy was
triggered without Railway's `latestCommit: true` and silently ran a
stale commit; separately, `SUPPLIER_MCP_EXECUTION_CREDENTIAL` had been
missing from this exact service for an unknown period before anyone
noticed, because nothing enforced checking it. Neither failure mode is
survivable by "remember to check" alone, so this script makes both
checks the actual gate for releasing this service, in this order:

1. Preflight: run `scripts/preflight_check.py` against this service's
   OWN live environment, over `railway run` (injects the real variables
   locally; nothing is read from the Railway API or printed). Stops
   here, before touching the deployment, if this fails.
2. Deploy: trigger a new deployment from the latest commit on the given
   branch (`latestCommit: true`, explicitly — the flag omitted during
   the finding above).
3. Verify: delegate to `scripts/verify_deployment.py`'s `verify()` to
   confirm the deployment that actually runs is the commit this release
   claims, reached SUCCESS, and answers its health check.

Any step failing stops the release with a nonzero exit and a clear
message; it never falls through to a later step on failure.

Reads `RAILWAY_TOKEN` from the environment (never as an argument, never
printed). No AIMO-internal secret is read, needed, or exposed.

Usage:
    RAILWAY_TOKEN=... uv run python scripts/release_supplier_agent.py \
        --service-id <railway-service-id> \
        --environment-id <railway-environment-id> \
        --expected-sha <full-40-char-git-sha-you-intend-to-ship> \
        --health-url <https://.../health> \
        [--skip-preflight-for-local-dev]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import verify_deployment

SCRIPTS_DIR = Path(__file__).resolve().parent
SUPPLIER_AGENT_DIR = SCRIPTS_DIR.parent / "apps" / "supplier-agent"


class ReleaseFailure(Exception):
    pass


def run_preflight() -> None:
    """Runs `apps/supplier-agent/scripts/preflight_check.py` against this
    service's own real environment via `railway run`, from the service's
    own project directory (so `uv run` resolves the right workspace
    package). Never reads or prints the credential's value itself --
    only the script's own PASS/FAIL line, which is already safe."""
    uv_bin = shutil.which("uv") or "uv"
    result = subprocess.run(
        [
            verify_deployment._RAILWAY_BIN,
            "run",
            "--service",
            "supplier-agent",
            "--",
            uv_bin,
            "run",
            "--project",
            "../..",
            "python",
            "scripts/preflight_check.py",
        ],
        cwd=SUPPLIER_AGENT_DIR,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        raise ReleaseFailure(
            f"Preflight failed (exit {result.returncode}); release stopped before "
            f"touching the deployment: {result.stderr.strip()[:500]}"
        )


def trigger_deploy(service_id: str, environment_id: str) -> None:
    query = (
        'mutation { serviceInstanceDeploy(serviceId: "'
        + service_id
        + '", environmentId: "'
        + environment_id
        + '", latestCommit: true) }'
    )
    result = subprocess.run(
        [verify_deployment._RAILWAY_BIN, "api", query],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise ReleaseFailure(f"Triggering deploy failed: {result.stderr.strip()[:500]}")
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseFailure(
            f"Deploy trigger returned non-JSON output: {result.stdout[:500]!r}"
        ) from exc
    if "errors" in body or not body.get("data", {}).get("serviceInstanceDeploy"):
        raise ReleaseFailure(f"Deploy trigger did not report success: {body}")
    print(f"Triggered deploy of service {service_id} from latest commit on its tracked branch.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-id", required=True)
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--health-url", required=True)
    parser.add_argument(
        "--skip-preflight-for-local-dev",
        action="store_true",
        help=(
            "Only for exercising this script's deploy/verify steps outside "
            "Railway; never use for a real release."
        ),
    )
    args = parser.parse_args()

    if not os.environ.get("RAILWAY_TOKEN"):
        print("RELEASE FAILED: RAILWAY_TOKEN is not set.", file=sys.stderr)
        return 1

    try:
        if args.skip_preflight_for_local_dev:
            print("Preflight SKIPPED (--skip-preflight-for-local-dev) -- not a real release.")
        else:
            run_preflight()
        trigger_deploy(args.service_id, args.environment_id)
        verify_deployment.verify(
            service_id=args.service_id,
            expected_sha=args.expected_sha,
            health_url=args.health_url,
            timeout_seconds=300.0,
            poll_interval_seconds=10.0,
        )
    except (ReleaseFailure, verify_deployment.VerificationFailure) as exc:
        print(f"RELEASE FAILED: {exc}", file=sys.stderr)
        return 1

    print("RELEASE COMPLETE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
