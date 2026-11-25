#!/usr/bin/env python3
"""Construct, sign, and publish a per-agent signing delegation (M8
slice 2, ADR-017).

Run this on a machine the DELEGATING ORGANIZATION'S OWN OPERATOR
controls — it needs that organization's root private key PEM (the same
one already set as BUYER_SIGNING_PRIVATE_KEY_PEM/
SUPPLIER_SIGNING_PRIVATE_KEY_PEM) to sign the delegation.
network-api never sees this private key; only the finished, signed
token is submitted to it.

This is deliberately a thin operator script, not a management API
endpoint — full operational tooling (a portal UI, automated rotation)
is explicitly deferred to a later slice; see docs/DECISIONS.md
ADR-017's "remaining slice 3 limitations" section.

Usage:
    uv run python scripts/issue_agent_delegation.py \\
        --root-private-key-pem-file /path/to/root_key.pem \\
        --issuing-kid org_fictional_bistro_01-202611 \\
        --organization-id org_fictional_bistro_01 \\
        --agent-id agent_fictional_buyer_01 \\
        --audience aimo-network:dev \\
        --artifact-types request_for_quote,quote,counterproposal,accept_proposal,reject_proposal \\
        --protocol-version v1 \\
        --network-api-url http://localhost:8000 \\
        --admin-key "$NETWORK_ADMIN_API_KEY" \\
        --publish

Without ``--publish``, prints the delegated agent's own private key
PEM (set it as this agent's own signing key — NOT the org root key)
and the signed delegation token, without submitting anything.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx
from message_integrity import (
    DelegationScope,
    build_delegation,
    generate_signing_keypair,
    private_key_from_pem,
    private_key_to_pem,
    public_key_to_jwk,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-private-key-pem-file", required=True)
    parser.add_argument("--issuing-kid", required=True)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--artifact-types", required=True, help="comma-separated")
    parser.add_argument("--protocol-version", default="v1")
    parser.add_argument("--delegated-kid", default=None, help="defaults to <agent-id>-k1")
    parser.add_argument("--network-api-url", default=None)
    parser.add_argument("--admin-key", default=None)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()

    if args.publish and (not args.network_api_url or not args.admin_key):
        print("--publish requires --network-api-url and --admin-key", file=sys.stderr)
        raise SystemExit(1)

    with open(args.root_private_key_pem_file, encoding="ascii") as handle:
        root_private_key = private_key_from_pem(handle.read())

    delegated_kid = args.delegated_kid or f"{args.agent_id}-k1"
    delegated_private_key, delegated_public_key = generate_signing_keypair()
    delegated_jwk = public_key_to_jwk(delegated_public_key, kid=delegated_kid)

    scope = DelegationScope(
        artifact_types=tuple(args.artifact_types.split(",")),
        protocol_version=args.protocol_version,
    )
    token = build_delegation(
        root_private_key=root_private_key,
        issuing_kid=args.issuing_kid,
        organization_id=args.organization_id,
        agent_id=args.agent_id,
        audience=args.audience,
        delegated_kid=delegated_kid,
        delegated_public_key_jwk=delegated_jwk,
        scope=scope,
    )

    print("=== DELEGATED AGENT PRIVATE KEY (set as this agent's own signing key) ===")
    print(private_key_to_pem(delegated_private_key))
    print(f"delegated_kid = {delegated_kid}")
    print()
    print("=== DELEGATION TOKEN ===")
    print(token)

    if args.publish:
        response = httpx.post(
            f"{args.network_api_url.rstrip('/')}/organizations/{args.organization_id}/delegations",
            json={"jws": token},
            headers={"X-Admin-Key": args.admin_key},
            timeout=10.0,
        )
        print()
        print(f"Publish response: {response.status_code}")
        print(json.dumps(response.json(), indent=2))
        response.raise_for_status()


if __name__ == "__main__":
    main()
