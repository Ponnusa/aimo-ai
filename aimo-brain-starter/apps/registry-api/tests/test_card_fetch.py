from __future__ import annotations

import pytest
from registry_api.card_fetch import (
    UnsafeCardUrlError,
    validate_card_url,
)


def _resolve_to(*addresses: str):
    def _resolve(hostname: str) -> list[str]:
        return list(addresses)

    return _resolve


def test_rejects_non_http_scheme_even_with_bypass() -> None:
    with pytest.raises(UnsafeCardUrlError):
        validate_card_url(
            "file:///etc/passwd", allow_private_network_targets=True, resolve=_resolve_to()
        )


def test_rejects_ftp_scheme() -> None:
    with pytest.raises(UnsafeCardUrlError):
        validate_card_url(
            "ftp://example.invalid/card",
            allow_private_network_targets=False,
            resolve=_resolve_to("93.184.216.34"),
        )


def test_rejects_loopback_target_by_default() -> None:
    with pytest.raises(UnsafeCardUrlError):
        validate_card_url(
            "http://supplier.example/card",
            allow_private_network_targets=False,
            resolve=_resolve_to("127.0.0.1"),
        )


def test_rejects_cloud_metadata_endpoint() -> None:
    with pytest.raises(UnsafeCardUrlError):
        validate_card_url(
            "http://supplier.example/card",
            allow_private_network_targets=False,
            resolve=_resolve_to("169.254.169.254"),
        )


def test_rejects_private_network_ranges() -> None:
    for address in ("10.0.0.5", "172.16.0.5", "192.168.1.5", "0.0.0.0", "224.0.0.1"):
        with pytest.raises(UnsafeCardUrlError):
            validate_card_url(
                "http://supplier.example/card",
                allow_private_network_targets=False,
                resolve=_resolve_to(address),
            )


def test_rejects_if_any_resolved_address_is_unsafe() -> None:
    """A hostname that resolves to one public and one private address must
    still be rejected — not accepted because one address looked fine."""
    with pytest.raises(UnsafeCardUrlError):
        validate_card_url(
            "http://supplier.example/card",
            allow_private_network_targets=False,
            resolve=_resolve_to("93.184.216.34", "10.0.0.5"),
        )


def test_allows_public_looking_address_by_default() -> None:
    validate_card_url(
        "https://supplier.example/card",
        allow_private_network_targets=False,
        resolve=_resolve_to("93.184.216.34"),
    )


def test_bypass_allows_loopback_but_still_rejects_bad_scheme() -> None:
    validate_card_url(
        "http://127.0.0.1:8002/card", allow_private_network_targets=True, resolve=_resolve_to()
    )
    with pytest.raises(UnsafeCardUrlError):
        validate_card_url(
            "file:///etc/passwd", allow_private_network_targets=True, resolve=_resolve_to()
        )


def test_rejects_url_with_no_hostname() -> None:
    with pytest.raises(UnsafeCardUrlError):
        validate_card_url(
            "http:///no-host-path", allow_private_network_targets=True, resolve=_resolve_to()
        )


def test_unresolvable_host_is_rejected() -> None:
    def _raise(_hostname: str) -> list[str]:
        raise OSError("name resolution failed")

    with pytest.raises(UnsafeCardUrlError):
        validate_card_url(
            "http://does-not-resolve.invalid/card",
            allow_private_network_targets=False,
            resolve=_raise,
        )
