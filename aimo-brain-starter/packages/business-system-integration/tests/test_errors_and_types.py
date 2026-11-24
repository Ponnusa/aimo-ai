from __future__ import annotations

from business_system_integration import (
    AdapterAuthenticationError,
    AdapterError,
    AdapterInvalidRequestError,
    AdapterMalformedResponseError,
    AdapterUnavailableError,
    ProductSummary,
)


def test_error_hierarchy() -> None:
    for error_cls in (
        AdapterUnavailableError,
        AdapterAuthenticationError,
        AdapterInvalidRequestError,
        AdapterMalformedResponseError,
    ):
        assert issubclass(error_cls, AdapterError)
        assert issubclass(error_cls, Exception)


def test_product_summary_is_frozen() -> None:
    summary = ProductSummary(product_ref="p1", name="Widget", unit="ea")
    assert summary.product_ref == "p1"
    try:
        summary.name = "Changed"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("ProductSummary must be immutable")
