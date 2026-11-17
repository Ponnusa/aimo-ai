import pytest

from identity import verify_human_token, verify_service_credential


def test_service_credential_verification_not_yet_implemented() -> None:
    with pytest.raises(NotImplementedError):
        verify_service_credential("some-token")


def test_human_token_verification_not_yet_implemented() -> None:
    with pytest.raises(NotImplementedError):
        verify_human_token("some-token")
