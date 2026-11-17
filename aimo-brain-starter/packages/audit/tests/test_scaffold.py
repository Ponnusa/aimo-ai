from datetime import UTC, datetime

import pytest
from trade_protocol import Evidence

from audit import record_evidence


def test_evidence_recording_not_yet_implemented() -> None:
    evidence = Evidence(
        evidence_id="ev_1",
        task_id="task_1",
        kind="policy_decision",
        reference="sha256:fake",
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(NotImplementedError):
        record_evidence(evidence)
