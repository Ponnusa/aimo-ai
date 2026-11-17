"""Audit/evidence recording package boundary.

**M0 status: scaffold only.** Append-only evidence persistence (FR-09) is
implemented alongside the durable trade protocol starting M4/M5. The
:class:`trade_protocol.Evidence` model defines the payload shape already;
this package will own writing it durably and redacting sensitive fields.
"""

from __future__ import annotations

from trade_protocol import Evidence


def record_evidence(evidence: Evidence) -> None:
    raise NotImplementedError(
        "Durable, redacted evidence recording lands in M4/M5. Not implemented in M0."
    )
