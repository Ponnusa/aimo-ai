#!/usr/bin/env python3
"""Deployment provenance gate: fails the release if the Railway service
now running is not the exact commit the release claims to be, or is not
healthy.

Found the hard way (2026-11-27): a deploy was triggered without Railway's
``latestCommit: true`` flag and silently redeployed a stale commit instead
of the one just merged — caught only because an operator happened to
check the deployment's own recorded commit hash by hand afterward. This
script makes that check mandatory and automatic rather than something an
operator has to remember.

Reads ``RAILWAY_TOKEN`` from the environment (never as a CLI argument,
never printed). Talks only to Railway's GraphQL API and the service's own
public health endpoint — no AIMO-internal secret is read or needed.

Usage:
    RAILWAY_TOKEN=... uv run python scripts/verify_deployment.py \
        --service-id <railway-service-id> \
        --environment-id <railway-environment-id> \
        --expected-sha <full-40-char-git-sha> \
        --health-url <https://.../health> \
        [--timeout-seconds 300] [--poll-interval-seconds 10]

Exit code 0 only if: a deployment reaches a terminal state within the
timeout, its recorded commit hash exactly matches --expected-sha, its
status is SUCCESS, and --health-url responds 200. Any other outcome is a
distinct, clearly labeled failure on stderr and a nonzero exit code.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import httpx

TERMINAL_STATUSES = {"SUCCESS", "FAILED", "CRASHED", "REMOVED"}

# Shells out to the `railway` CLI rather than calling Railway's GraphQL
# endpoint directly: the CLI's own token handling is the proven-working
# path (a raw HTTP call with the same project-scoped token was rejected
# with "Not Authorized" when this script was first written — the CLI
# does something with the token this script does not need to reproduce).
_RAILWAY_BIN = shutil.which("railway") or "railway"


class VerificationFailure(Exception):
    pass


def _railway_api(query: str) -> dict:
    env = os.environ.copy()
    result = subprocess.run(
        [_RAILWAY_BIN, "api", query],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise VerificationFailure(f"`railway api` failed: {result.stderr.strip()[:500]}")
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationFailure(
            f"`railway api` returned non-JSON output: {result.stdout[:500]!r}"
        ) from exc
    if "errors" in body:
        raise VerificationFailure(f"Railway API returned errors: {body['errors']}")
    return body["data"]


def _latest_deployment(service_id: str) -> dict | None:
    data = _railway_api(
        'query { service(id: "'
        + service_id
        + '") { serviceInstances { edges { node { latestDeployment { id status } } } } } }'
    )
    edges = data["service"]["serviceInstances"]["edges"]
    if not edges:
        return None
    return edges[0]["node"]["latestDeployment"]


def _deployment_commit_hash(deployment_id: str) -> str | None:
    data = _railway_api('query { deployment(id: "' + deployment_id + '") { meta } }')
    meta = data["deployment"]["meta"] or {}
    return meta.get("commitHash")


def wait_for_terminal_deployment(
    service_id: str, *, timeout_seconds: float, poll_interval_seconds: float
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        deployment = _latest_deployment(service_id)
        if deployment is None:
            raise VerificationFailure(f"Service {service_id!r} has no deployments at all.")
        last_status = deployment["status"]
        if last_status in TERMINAL_STATUSES:
            return deployment
        time.sleep(poll_interval_seconds)
    raise VerificationFailure(
        f"Deployment did not reach a terminal state within {timeout_seconds}s "
        f"(last seen: {last_status!r})."
    )


def verify(
    *,
    service_id: str,
    expected_sha: str,
    health_url: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> None:
    deployment = wait_for_terminal_deployment(
        service_id, timeout_seconds=timeout_seconds, poll_interval_seconds=poll_interval_seconds
    )
    if deployment["status"] != "SUCCESS":
        raise VerificationFailure(
            f"Deployment {deployment['id']} reached terminal status "
            f"{deployment['status']!r}, not SUCCESS."
        )

    actual_sha = _deployment_commit_hash(deployment["id"])
    if actual_sha != expected_sha:
        raise VerificationFailure(
            f"Deployed commit {actual_sha!r} does not match expected {expected_sha!r} "
            f"(deployment {deployment['id']}). The release claims a SHA this service "
            f"is not actually running."
        )

    # Railway's own "SUCCESS" can briefly precede its edge actually
    # routing to the new container (seen directly: a 502 for a few
    # seconds immediately after SUCCESS, then 200) -- a short, bounded
    # retry absorbs that propagation gap without masking a genuinely
    # unhealthy service (this still fails if it never recovers).
    last_error: str | None = None
    for attempt in range(6):
        try:
            response = httpx.get(health_url, timeout=15.0)
        except httpx.HTTPError as exc:
            last_error = f"failed to connect: {exc}"
        else:
            if response.status_code == 200:
                last_error = None
                break
            last_error = f"returned {response.status_code}, expected 200"
        if attempt < 5:
            time.sleep(5.0)
    if last_error is not None:
        raise VerificationFailure(f"Health check at {health_url} {last_error} (after retries).")

    print(
        f"VERIFIED: deployment {deployment['id']} is commit {actual_sha}, "
        f"status SUCCESS, health check 200 at {health_url}."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-id", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=10.0)
    args = parser.parse_args()

    if not os.environ.get("RAILWAY_TOKEN"):
        print("VERIFICATION FAILED: RAILWAY_TOKEN is not set.", file=sys.stderr)
        return 1

    try:
        verify(
            service_id=args.service_id,
            expected_sha=args.expected_sha,
            health_url=args.health_url,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    except VerificationFailure as exc:
        print(f"VERIFICATION FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
