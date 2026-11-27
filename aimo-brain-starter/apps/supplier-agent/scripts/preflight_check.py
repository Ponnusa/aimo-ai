"""Deployment preflight: fails loudly, before traffic shifts, when this
service's configuration cannot actually commit an order.

``execute_order`` against supplier-mcp requires a real
``SUPPLIER_MCP_EXECUTION_CREDENTIAL`` regardless of which business-system
adapter supplier-mcp itself is configured with (mock or sandbox/real) --
an empty one is rejected by supplier-mcp, which this service's own
fail-safe design (commitment.py) turns into ``RECONCILIATION_REQUIRED``
rather than a loud failure. That is the right behavior for a genuinely
unreachable business system; it is the wrong behavior for a service that
was simply never given its credential, which this check catches instead,
as a deliberate release step -- see docs/OPERATIONS.md.

Never prints the credential's value, only whether it is present.
"""

from __future__ import annotations

import sys

from supplier_agent.settings import get_settings


def main() -> int:
    settings = get_settings()
    if not settings.supplier_mcp_execution_credential:
        print(
            "PREFLIGHT FAILED: SUPPLIER_MCP_EXECUTION_CREDENTIAL is empty. "
            "Every order commit will silently become RECONCILIATION_REQUIRED "
            "instead of failing loudly -- see docs/OPERATIONS.md.",
            file=sys.stderr,
        )
        return 1
    print("PREFLIGHT OK: SUPPLIER_MCP_EXECUTION_CREDENTIAL is set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
