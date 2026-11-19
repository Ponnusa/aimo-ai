from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from trade_protocol import Evidence

from audit import record_evidence


def test_record_evidence_emits_structured_log_line(caplog) -> None:
    evidence = Evidence(
        evidence_id="ev_1",
        task_id="task_1",
        kind="policy_decision",
        reference="sha256:fake",
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with caplog.at_level(logging.INFO, logger="aimo_brain.audit"):
        record_evidence(evidence, correlation_id="corr_1", detail={"policy_version": "v1"})

    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].message)
    assert payload["evidence_id"] == "ev_1"
    assert payload["correlation_id"] == "corr_1"
    assert payload["detail"]["policy_version"] == "v1"


def test_record_evidence_redacts_secrets(caplog) -> None:
    evidence = Evidence(
        evidence_id="ev_2",
        task_id="task_1",
        kind="authorization_evidence",
        reference="sha256:fake",
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with caplog.at_level(logging.INFO, logger="aimo_brain.audit"):
        record_evidence(
            evidence,
            detail={"credential": "super-secret-token", "nested": {"signing_secret": "abc"}},
        )

    payload = json.loads(caplog.records[0].message)
    assert payload["detail"]["credential"] == "[REDACTED]"
    assert payload["detail"]["nested"]["signing_secret"] == "[REDACTED]"
