#!/usr/bin/env python3
"""Generate an Ed25519 signing keypair for one organization (M5.1).

Run this ONCE per organization (and again for each key rotation) on a
machine the organization's own operator controls — never on a shared
or network-api-hosted machine. Only the printed *public* JWK is ever
registered with network-api (``POST /organizations/{id}/signing-keys``).
The private key PEM is this organization's own secret: put it directly
into that organization's agent deployment's environment
(``BUYER_SIGNING_PRIVATE_KEY_PEM``/``SUPPLIER_SIGNING_PRIVATE_KEY_PEM``)
and never commit it, log it, or send it anywhere else.

Usage:
    uv run python scripts/generate_signing_key.py org_fictional_bistro_01

Prints the private key PEM, the kid, and the public JWK to register.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from message_integrity import generate_signing_keypair, private_key_to_pem, public_key_to_jwk


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <organization_id>", file=sys.stderr)
        raise SystemExit(1)

    organization_id = sys.argv[1]
    kid = f"{organization_id}-{datetime.now(UTC).strftime('%Y%m')}"

    private_key, public_key = generate_signing_keypair()

    print("=== PRIVATE KEY (keep secret; set as this org's agent's env var) ===")
    print(private_key_to_pem(private_key))
    print(f"kid = {kid}")
    print()
    print("=== PUBLIC JWK (register with network-api) ===")
    print(json.dumps(public_key_to_jwk(public_key, kid=kid), indent=2))
    print()
    print("Register with:")
    print(
        f'  curl -X POST "$NETWORK_API_URL/organizations/{organization_id}/signing-keys" '
        f'-H "X-Admin-Key: $NETWORK_ADMIN_API_KEY" -H "Content-Type: application/json" '
        f'-d \'{{"kid": "{kid}", "public_key_jwk": <paste JWK above>}}\''
    )


if __name__ == "__main__":
    main()
