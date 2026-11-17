import pytest

from worker import run_dispatch_cycle


def test_dispatch_cycle_not_yet_implemented() -> None:
    with pytest.raises(NotImplementedError):
        run_dispatch_cycle()
