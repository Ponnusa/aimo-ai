"""supplier-mcp's MCP tool server (M3; adapter-based as of M7).

Exposes the supplier organization's own business tools over the official
MCP SDK's Streamable HTTP transport: ``search_catalogue``,
``check_inventory``, ``get_customer_price``, ``get_delivery_options``,
``execute_order`` and ``get_order_status``. As of M7, every tool except
``search_catalogue``/``check_inventory`` calls the configured
``SupplierBusinessSystemAdapter`` (see ``supplier_mcp.adapter_factory``)
rather than importing ``food_wholesale.adapters.mock`` directly, so
swapping ``SUPPLIER_BUSINESS_SYSTEM_ADAPTER`` (``mock``/``sandbox``/
``valio_aimo``) changes what's underneath without touching this module's
tool surface, schemas or database.

``search_catalogue``/``check_inventory`` keep their exact M3-M6 merged
shape (catalogue search embedding today/tomorrow stock) *only* for the
default ``mock`` adapter, to avoid changing ``supplier_agent``'s
existing consumer behavior; the sandbox/Valio adapters report the
generic, non-merged shape instead (price/forecast-stock fields are
``None`` — a real ERP's catalogue search would not embed customer-
specific pricing either).

This is also the fix for the M1-era shortcut in
``supplier_agent.a2a_executor``: the fictional catalogue import now lives
*only* here. ``supplier_agent`` no longer imports
``food_wholesale.adapters.mock.catalogue`` directly — it calls these
tools over MCP, like any other agent would.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from business_system_integration import (
    AdapterAuthenticationError,
    AdapterError,
    AdapterUnavailableError,
)
from food_wholesale.adapters.mock import supplier_tools
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp_integration import require_tool_scope
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from supplier_mcp.adapter_factory import get_adapter
from supplier_mcp.models import SupplierMcpOrderRecord, SupplierMcpToolInvocationRecord
from supplier_mcp.settings import SupplierMcpSettings

SUPPLIER_TOOLS_SCOPE = "use_supplier_tools"
#: Narrow, separate scope for the execution tool (M5) — a credential
#: minted for the read-only catalogue/inventory/pricing tools cannot
#: call execute_order, and vice versa. See docs/DECISIONS.md M5 ADR.
EXECUTE_ORDER_SCOPE = "execute_order"


class CatalogueProductOut(BaseModel):
    product_ref: str
    name: str
    unit: str
    #: Only populated for the ``mock`` adapter (M3-M6 merged shape,
    #: preserved for ``supplier_agent.mcp_client``'s existing fallback
    #: behavior). ``None`` for sandbox/Valio — a real catalogue search
    #: would not embed customer-specific price or a stock forecast.
    unit_price: str | None = None
    stock_today: str | None = None
    stock_tomorrow: str | None = None


class InventoryOut(BaseModel):
    found: bool
    product_ref: str
    unit: str | None = None
    stock_today: str | None = None
    stock_tomorrow: str | None = None
    #: M7: only populated by a non-mock adapter.
    moq: str | None = None
    cutoff_passed: bool = False
    substituted_for_product_ref: str | None = None


class CustomerPriceOut(BaseModel):
    found: bool
    product_ref: str
    buyer_organization_id: str
    unit_price: str | None = None
    currency: str | None = None


class DeliveryOptionOut(BaseModel):
    label: str
    earliest_date: str
    fee: str
    currency: str
    restrictions: list[str] = []


class OrderExecutionOut(BaseModel):
    idempotency_key: str
    #: ``None`` while ``status == "pending"`` (a delayed-ack business
    #: system that has not yet issued a reference) -- never fabricated.
    external_order_id: str | None
    status: str
    duplicate: bool


def _adapter_error_to_tool_error(exc: AdapterError) -> ToolError:
    """Map an adapter failure to the MCP tool-error vocabulary the
    client (``mcp_integration``) already understands, matching the
    ``server_auth``/``require_tool_scope`` convention of prefixing
    authentication failures with ``"Unauthorized:"``."""
    if isinstance(exc, AdapterAuthenticationError):
        return ToolError(f"Unauthorized: business system rejected adapter credentials: {exc}")
    if isinstance(exc, AdapterUnavailableError):
        return ToolError(f"Business system unavailable: {exc}")
    return ToolError(str(exc))


class OrderStatusOut(BaseModel):
    found: bool
    idempotency_key: str
    external_order_id: str | None = None
    status: str | None = None


def create_mcp_server(
    *, settings: SupplierMcpSettings, session_factory: async_sessionmaker
) -> FastMCP:
    # host must match the Dockerfile's real `uvicorn --host 0.0.0.0` bind
    # address. FastMCP defaults to assuming it's bound to loopback and
    # self-applies a restrictive Host-header allowlist when it thinks so
    # (DNS-rebinding protection) -- which then rejects every real caller,
    # including a same-project private-network one, since the request's
    # real Host header is never "127.0.0.1"/"localhost". Telling it the
    # real bind host avoids that mismatch.
    mcp = FastMCP(name="aimo-brain-supplier-mcp", streamable_http_path="/", host="0.0.0.0")
    #: The configured business-system adapter (M7) — one instance per
    #: server, shared by every tool call on this process. Durable
    #: idempotency/reconciliation authority for orders is always the
    #: ``supplier_mcp_orders`` DB table below, regardless of adapter, so
    #: a restart never loses the idempotency guarantee even against a
    #: stateful adapter.
    adapter = get_adapter(settings)

    async def _authorize(ctx: Context, *, tool_name: str, required_scope: str) -> str:
        try:
            claims = require_tool_scope(
                ctx,
                signing_secret=settings.service_credential_signing_secret,
                required_scope=required_scope,
                required_organization_id=settings.supplier_mcp_organization_id,
            )
        except ToolError as exc:
            await _audit(tool_name=tool_name, organization_id=None, success=False, detail=str(exc))
            raise
        return claims.organization_id

    async def _audit(
        *, tool_name: str, organization_id: str | None, success: bool, detail: str
    ) -> None:
        async with session_factory() as session:
            session.add(
                SupplierMcpToolInvocationRecord(
                    tool_name=tool_name,
                    organization_id=organization_id,
                    success=success,
                    detail=json.dumps({"message": detail}),
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            await session.commit()

    @mcp.tool()
    async def search_catalogue(ctx: Context, query: str) -> list[CatalogueProductOut]:
        """Search the configured business system's catalogue by
        free-text product query."""
        organization_id = await _authorize(
            ctx, tool_name="search_catalogue", required_scope=SUPPLIER_TOOLS_SCOPE
        )
        if settings.supplier_business_system_adapter == "mock":
            # Preserve the exact M3-M6 merged shape (embeds today/
            # tomorrow stock) for the default adapter -- see module
            # docstring.
            mock_matches = supplier_tools.search_catalogue(query)
            results = [
                CatalogueProductOut(
                    product_ref=p.product_ref,
                    name=p.name,
                    unit=p.unit,
                    unit_price=str(p.unit_price),
                    stock_today=str(p.stock_today),
                    stock_tomorrow=str(p.stock_tomorrow),
                )
                for p in mock_matches
            ]
        else:
            try:
                summaries = await adapter.search_catalogue(query=query)
            except AdapterError as exc:
                await _audit(
                    tool_name="search_catalogue",
                    organization_id=organization_id,
                    success=False,
                    detail=str(exc),
                )
                raise _adapter_error_to_tool_error(exc) from exc
            results = [
                CatalogueProductOut(product_ref=s.product_ref, name=s.name, unit=s.unit)
                for s in summaries
            ]
        await _audit(
            tool_name="search_catalogue",
            organization_id=organization_id,
            success=True,
            detail=f"query={query!r} matched={len(results)}",
        )
        return results

    @mcp.tool()
    async def check_inventory(ctx: Context, product_ref: str) -> InventoryOut:
        """Return the current stock/availability snapshot for
        ``product_ref`` from the configured business system."""
        organization_id = await _authorize(
            ctx, tool_name="check_inventory", required_scope=SUPPLIER_TOOLS_SCOPE
        )
        if settings.supplier_business_system_adapter == "mock":
            product = supplier_tools.check_inventory(product_ref)
            found = product is not None
            result = (
                InventoryOut(found=False, product_ref=product_ref)
                if product is None
                else InventoryOut(
                    found=True,
                    product_ref=product.product_ref,
                    unit=product.unit,
                    stock_today=str(product.stock_today),
                    stock_tomorrow=str(product.stock_tomorrow),
                )
            )
        else:
            try:
                availability = await adapter.check_availability(
                    product_ref=product_ref, quantity="0"
                )
            except AdapterError as exc:
                await _audit(
                    tool_name="check_inventory",
                    organization_id=organization_id,
                    success=False,
                    detail=str(exc),
                )
                raise _adapter_error_to_tool_error(exc) from exc
            found = availability is not None
            result = (
                InventoryOut(found=False, product_ref=product_ref)
                if availability is None
                else InventoryOut(
                    found=True,
                    product_ref=product_ref,
                    stock_today=availability.quantity_available,
                    stock_tomorrow=availability.quantity_available,
                    moq=availability.moq,
                    cutoff_passed=availability.cutoff_passed,
                    substituted_for_product_ref=availability.substituted_for_product_ref,
                )
            )
        await _audit(
            tool_name="check_inventory",
            organization_id=organization_id,
            success=True,
            detail=f"product_ref={product_ref!r} found={found}",
        )
        return result

    @mcp.tool()
    async def get_customer_price(
        ctx: Context, product_ref: str, buyer_organization_id: str
    ) -> CustomerPriceOut:
        """Return the customer-specific fictional unit price for ``product_ref``."""
        organization_id = await _authorize(
            ctx, tool_name="get_customer_price", required_scope=SUPPLIER_TOOLS_SCOPE
        )
        try:
            price = await adapter.get_customer_price(
                product_ref=product_ref, buyer_organization_id=buyer_organization_id
            )
        except AdapterError as exc:
            await _audit(
                tool_name="get_customer_price",
                organization_id=organization_id,
                success=False,
                detail=str(exc),
            )
            raise _adapter_error_to_tool_error(exc) from exc
        await _audit(
            tool_name="get_customer_price",
            organization_id=organization_id,
            success=True,
            detail=(
                f"product_ref={product_ref!r} buyer={buyer_organization_id!r} "
                f"found={price is not None}"
            ),
        )
        if price is None:
            return CustomerPriceOut(
                found=False, product_ref=product_ref, buyer_organization_id=buyer_organization_id
            )
        return CustomerPriceOut(
            found=True,
            product_ref=price.product_ref,
            buyer_organization_id=price.buyer_organization_id,
            unit_price=str(price.unit_price),
            currency=price.currency,
        )

    @mcp.tool()
    async def get_delivery_options(
        ctx: Context, product_ref: str, quantity: str
    ) -> list[DeliveryOptionOut]:
        """Return fictional delivery options for ``quantity`` units of ``product_ref``."""
        organization_id = await _authorize(
            ctx, tool_name="get_delivery_options", required_scope=SUPPLIER_TOOLS_SCOPE
        )
        try:
            options = await adapter.get_delivery_options(product_ref=product_ref, quantity=quantity)
        except AdapterError as exc:
            await _audit(
                tool_name="get_delivery_options",
                organization_id=organization_id,
                success=False,
                detail=str(exc),
            )
            raise _adapter_error_to_tool_error(exc) from exc
        await _audit(
            tool_name="get_delivery_options",
            organization_id=organization_id,
            success=True,
            detail=f"product_ref={product_ref!r} options={len(options)}",
        )
        return [
            DeliveryOptionOut(
                label=o.label,
                earliest_date=o.earliest_date,
                fee=o.fee,
                currency=o.currency,
                restrictions=list(o.restrictions),
            )
            for o in options
        ]

    @mcp.tool()
    async def execute_order(
        ctx: Context,
        idempotency_key: str,
        agreement_id: str,
        content_hash: str,
        buyer_organization_id: str,
        lines: list[dict[str, str]] | None = None,
    ) -> OrderExecutionOut:
        """The approved order-execution tool (M5; adapter-based as of
        M7). Requires the narrow ``execute_order`` scope — a read-only
        ``use_supplier_tools`` credential cannot call this. Idempotent:
        the same ``idempotency_key`` always returns the exact same
        result rather than creating a second order, enforced by this
        table's own UNIQUE constraint (the durable idempotency
        authority, independent of whatever the configured adapter does
        underneath) — not merely by checking first, since a concurrent
        duplicate call can race the check, never the constraint.
        ``status`` may be ``"pending"`` (a delayed-ack business system)
        -- never treated as failure by any caller."""
        organization_id = await _authorize(
            ctx, tool_name="execute_order", required_scope=EXECUTE_ORDER_SCOPE
        )
        async with session_factory() as session:
            existing = (
                await session.execute(
                    select(SupplierMcpOrderRecord).where(
                        SupplierMcpOrderRecord.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                await _audit(
                    tool_name="execute_order",
                    organization_id=organization_id,
                    success=True,
                    detail=f"idempotency_key={idempotency_key!r} duplicate=True",
                )
                return OrderExecutionOut(
                    idempotency_key=idempotency_key,
                    external_order_id=existing.external_order_id,
                    status=existing.status,
                    duplicate=True,
                )

            try:
                result = await adapter.submit_order(
                    idempotency_key=idempotency_key,
                    buyer_organization_id=buyer_organization_id,
                    lines=lines or [],
                )
            except AdapterAuthenticationError as exc:
                # Never left ambiguous: a credential rejection is a
                # configuration/authorization failure, not a transient
                # condition -- fail closed immediately, no order row.
                await _audit(
                    tool_name="execute_order",
                    organization_id=organization_id,
                    success=False,
                    detail=str(exc),
                )
                raise _adapter_error_to_tool_error(exc) from exc
            except AdapterError as exc:
                # Unavailable/malformed: genuinely unknown outcome --
                # surfaced as a tool error so supplier_agent.commitment
                # marks RECONCILIATION_REQUIRED, never FAILED, and the
                # worker retries later. No row is written here because
                # we never learned an external_order_id to record.
                await _audit(
                    tool_name="execute_order",
                    organization_id=organization_id,
                    success=False,
                    detail=str(exc),
                )
                raise _adapter_error_to_tool_error(exc) from exc

            try:
                session.add(
                    SupplierMcpOrderRecord(
                        idempotency_key=idempotency_key,
                        agreement_id=agreement_id,
                        buyer_organization_id=buyer_organization_id,
                        content_hash=content_hash,
                        external_order_id=result.external_order_id,
                        status=result.status,
                        created_at=datetime.now(UTC).replace(tzinfo=None),
                    )
                )
                await session.commit()
            except IntegrityError:
                # Lost a race against a concurrent identical commit: the
                # other request's row is now the durable record — read
                # it back rather than treating this as a failure.
                await session.rollback()
                existing = (
                    await session.execute(
                        select(SupplierMcpOrderRecord).where(
                            SupplierMcpOrderRecord.idempotency_key == idempotency_key
                        )
                    )
                ).scalar_one()
                await _audit(
                    tool_name="execute_order",
                    organization_id=organization_id,
                    success=True,
                    detail=f"idempotency_key={idempotency_key!r} duplicate=True (race)",
                )
                return OrderExecutionOut(
                    idempotency_key=idempotency_key,
                    external_order_id=existing.external_order_id,
                    status=existing.status,
                    duplicate=True,
                )

        await _audit(
            tool_name="execute_order",
            organization_id=organization_id,
            success=True,
            detail=f"idempotency_key={idempotency_key!r} order={result.external_order_id!r}",
        )
        return OrderExecutionOut(
            idempotency_key=idempotency_key,
            external_order_id=result.external_order_id,
            status=result.status,
            duplicate=False,
        )

    @mcp.tool()
    async def get_order_status(ctx: Context, idempotency_key: str) -> OrderStatusOut:
        """Order-status lookup for reconciliation (M5; M7: also
        reconciles a locally-pending row against the configured adapter
        before answering, so a resolved delayed-ack order is reflected
        here without a separate sync step): the same narrow
        ``execute_order`` scope as the execution tool itself."""
        organization_id = await _authorize(
            ctx, tool_name="get_order_status", required_scope=EXECUTE_ORDER_SCOPE
        )
        async with session_factory() as session:
            record = (
                await session.execute(
                    select(SupplierMcpOrderRecord).where(
                        SupplierMcpOrderRecord.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if record is not None and record.status == "pending":
                try:
                    remote = await adapter.get_order_status(idempotency_key=idempotency_key)
                except AdapterError:
                    remote = None
                if remote is not None and remote.found and remote.status != "pending":
                    record.status = "confirmed" if remote.status == "confirmed" else remote.status
                    if remote.external_order_id:
                        record.external_order_id = remote.external_order_id
                    await session.commit()
        await _audit(
            tool_name="get_order_status",
            organization_id=organization_id,
            success=True,
            detail=f"idempotency_key={idempotency_key!r} found={record is not None}",
        )
        if record is None:
            return OrderStatusOut(found=False, idempotency_key=idempotency_key)
        return OrderStatusOut(
            found=True,
            idempotency_key=idempotency_key,
            external_order_id=record.external_order_id,
            status=record.status,
        )

    return mcp
