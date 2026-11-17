import pytest

from identity import verify_human_token


def test_human_token_verification_not_yet_implemented() -> None:
    with pytest.raises(NotImplementedError):
        verify_human_token("some-token")
