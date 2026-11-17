import pytest

from authorization import evaluate_binding_action


def test_binding_policy_evaluation_not_yet_implemented() -> None:
    """Guards against future code accidentally treating an unimplemented
    policy check as a pass. See CLAUDE.md: "deny by default"."""
    with pytest.raises(NotImplementedError):
        evaluate_binding_action(action="submit_order", proposal_hash="sha256:fake")
