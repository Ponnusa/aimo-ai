"""Audit/evidence recording package boundary.

**M5 status: real.** ``record_evidence`` writes one structured, redacted
JSON log line per piece of evidence (proposal/agreement hashes, policy
decisions, approvals, commitment transitions, execution attempts,
idempotency keys, external order references, recovery/reconciliation
events — docs/SECURITY.md's "structured logs... redact secrets").

This package deliberately does not own a database: each service
persists its own durable evidence rows (approvals, authorization
evidence, orders) in its own database, per the "no new shared
infrastructure" constraint — this module is the shared, consistent
*logging* shape callers use alongside that durable write, not a
replacement for it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from trade_protocol import Evidence

logger = logging.getLogger("aimo_brain.audit")

#: Any detail dict key (case-insensitive) matching one of these is
#: replaced with a fixed redaction marker before logging — never a
#: bearer credential, signing secret or raw Authorization header value.
_REDACTED_KEYS = frozenset(
    {"token", "credential", "authorization", "signing_secret", "secret", "password", "bearer"}
)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if key.lower() in _REDACTED_KEYS else _redact(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def record_evidence(
    evidence: Evidence,
    *,
    correlation_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Emit one structured, redacted audit log line for ``evidence``.

    Callers (buyer-agent/supplier-agent/supplier-mcp) still persist their
    own durable row (approvals table, authorization-evidence table,
    orders table) — this call is the accompanying observable log entry,
    correlatable by ``correlation_id`` across both organizations'
    independent logs.
    """
    payload = {
        "evidence_id": evidence.evidence_id,
        "task_id": evidence.task_id,
        "kind": evidence.kind,
        "reference": evidence.reference,
        "recorded_at": evidence.recorded_at.isoformat(),
        "correlation_id": correlation_id,
        "detail": _redact(detail or {}),
    }
    logger.info(json.dumps(payload, sort_keys=True, default=str))


__all__ = ["record_evidence"]
