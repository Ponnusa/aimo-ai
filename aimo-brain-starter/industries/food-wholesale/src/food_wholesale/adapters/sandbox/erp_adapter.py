"""Sandbox ERP adapter (M7).

Implements :class:`business_system_integration.SupplierBusinessSystemAdapter`
by calling ``apps/supplier-sandbox-erp`` over real HTTP — the realistic
stand-in for a supplier's own business system described in
``docs/integrations/valio-aimo-pilot-readiness.md``. This is the only
food-wholesale module that talks to that service; nothing else (not
``supplier_agent``, not any core package) may reach it directly.
"""

from __future__ import annotations

from typing import Any

import httpx
from business_system_integration import (
    AdapterAuthenticationError,
    AdapterInvalidRequestError,
    AdapterMalformedResponseError,
    AdapterUnavailableError,
    AvailabilityStatus,
    CustomerPrice,
    DeliveryOption,
    OrderStatusResult,
    OrderSubmissionResult,
    ProductDetails,
    ProductSummary,
)


class SandboxErpAdapter:
    adapter_name = "sandbox"

    def __init__(self, *, base_url: str, token: str, request_timeout_seconds: float = 3.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = request_timeout_seconds

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            ) as client:
                response = await client.request(method, path, **kwargs)
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            raise AdapterUnavailableError(
                f"Could not reach sandbox ERP at {self._base_url}{path!r}: {exc}"
            ) from exc

        if response.status_code in (401, 403):
            raise AdapterAuthenticationError(
                f"Sandbox ERP rejected this adapter's credential: {response.text}"
            )
        if response.status_code == 503:
            raise AdapterUnavailableError(
                f"Sandbox ERP reported a transient error: {response.text}"
            )
        if response.status_code == 422 or response.status_code == 400:
            raise AdapterInvalidRequestError(response.text)
        if response.status_code >= 400:
            raise AdapterUnavailableError(
                f"Sandbox ERP returned unexpected status {response.status_code}: {response.text}"
            )
        return response

    def _json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise AdapterMalformedResponseError(
                f"Sandbox ERP response was not valid JSON: {exc}"
            ) from exc

    async def search_catalogue(self, *, query: str) -> list[ProductSummary]:
        response = await self._request("GET", "/catalogue/search", params={"query": query})
        try:
            return [
                ProductSummary(product_ref=p["product_ref"], name=p["name"], unit=p["unit"])
                for p in self._json(response)
            ]
        except (KeyError, TypeError) as exc:
            raise AdapterMalformedResponseError(str(exc)) from exc

    async def get_product_details(self, *, product_ref: str) -> ProductDetails | None:
        response = await self._request("GET", f"/products/{product_ref}")
        body = self._json(response)
        if body is None:
            return None
        try:
            return ProductDetails(
                product_ref=body["product_ref"],
                name=body["name"],
                unit=body["unit"],
                packaging=body.get("packaging"),
            )
        except (KeyError, TypeError) as exc:
            raise AdapterMalformedResponseError(str(exc)) from exc

    async def get_customer_price(
        self, *, product_ref: str, buyer_organization_id: str
    ) -> CustomerPrice | None:
        response = await self._request(
            "GET", f"/customers/{buyer_organization_id}/products/{product_ref}/price"
        )
        body = self._json(response)
        if not body.get("found"):
            return None
        try:
            return CustomerPrice(
                product_ref=product_ref,
                buyer_organization_id=buyer_organization_id,
                unit_price=body["unit_price"],
                currency=body["currency"],
            )
        except KeyError as exc:
            raise AdapterMalformedResponseError(str(exc)) from exc

    async def check_availability(
        self, *, product_ref: str, quantity: str
    ) -> AvailabilityStatus | None:
        response = await self._request(
            "GET", f"/products/{product_ref}/availability", params={"quantity": quantity}
        )
        body = self._json(response)
        if not body.get("found"):
            return None
        try:
            return AvailabilityStatus(
                product_ref=product_ref,
                quantity_available=body["quantity_available"],
                moq=body.get("moq"),
                cutoff_passed=bool(body.get("cutoff_passed", False)),
                substituted_for_product_ref=body.get("substituted_for_product_ref"),
            )
        except KeyError as exc:
            raise AdapterMalformedResponseError(str(exc)) from exc

    async def get_delivery_options(
        self, *, product_ref: str, quantity: str, delivery_location_ref: str | None = None
    ) -> list[DeliveryOption]:
        response = await self._request(
            "GET",
            f"/products/{product_ref}/delivery-options",
            params={"quantity": quantity},
        )
        try:
            return [
                DeliveryOption(
                    label=o["label"],
                    earliest_date=o["earliest_date"],
                    fee=o["fee"],
                    currency=o["currency"],
                    restrictions=tuple(o.get("restrictions", [])),
                )
                for o in self._json(response)
            ]
        except (KeyError, TypeError) as exc:
            raise AdapterMalformedResponseError(str(exc)) from exc

    async def submit_order(
        self,
        *,
        idempotency_key: str,
        buyer_organization_id: str,
        lines: list[dict[str, str]],
    ) -> OrderSubmissionResult:
        response = await self._request(
            "POST",
            "/orders",
            json={
                "idempotency_key": idempotency_key,
                "buyer_organization_id": buyer_organization_id,
                "lines": lines,
            },
        )
        body = self._json(response)
        try:
            return OrderSubmissionResult(
                idempotency_key=body["idempotency_key"],
                status=body["status"],
                external_order_id=body.get("external_order_id"),
                duplicate=bool(body.get("duplicate", False)),
            )
        except KeyError as exc:
            raise AdapterMalformedResponseError(str(exc)) from exc

    async def get_order_status(self, *, idempotency_key: str) -> OrderStatusResult:
        response = await self._request("GET", f"/orders/{idempotency_key}")
        body = self._json(response)
        try:
            return OrderStatusResult(
                found=bool(body["found"]),
                idempotency_key=idempotency_key,
                status=body.get("status"),
                external_order_id=body.get("external_order_id"),
            )
        except KeyError as exc:
            raise AdapterMalformedResponseError(str(exc)) from exc
