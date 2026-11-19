"""supplier-mcp's MCP tool server (M3).

Exposes the supplier organization's own business tools over the official
MCP SDK's Streamable HTTP transport: ``search_catalogue``,
``check_inventory``, ``get_customer_price`` and ``get_delivery_options``.
The underlying fictional data lives in
``food_wholesale.adapters.mock.supplier_tools`` — this module only wires
it up as typed MCP tools with per-call scoped-credential authorization
and audit logging.

This is also the fix for the M1-era shortcut in
``supplier_agent.a2a_executor``: the fictional catalogue import now lives
*only* here. ``supplier_agent`` no longer imports
``food_wholesale.adapters.mock.catalogue`` directly — it calls these
tools over MCP, like any other agent would.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from food_wholesale.adapters.mock import order_api, supplier_tools
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp_integration import require_tool_scope
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

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
    unit_price: str
    stock_today: str
    stock_tomorrow: str


class InventoryOut(BaseModel):
    found: bool
    product_ref: str
    unit: str | None = None
    stock_today: str | None = None
    stock_tomorrow: str | None = None


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


class OrderExecutionOut(BaseModel):
    idempotency_key: str
    external_order_id: str
    status: str
    duplicate: bool


class OrderStatusOut(BaseModel):
    found: bool
    idempotency_key: str
    external_order_id: str | None = None
    status: str | None = None


def create_mcp_server(
    *, settings: SupplierMcpSettings, session_factory: async_sessionmaker
) -> FastMCP:
    mcp = FastMCP(name="aimo-brain-supplier-mcp", streamable_http_path="/")
    #: The "approved mock order adapter" (M5) — one in-process instance
    #: per server, shared by every execute_order call on this process.
    #: Durable idempotency/reconciliation authority is the
    #: ``supplier_mcp_orders`` DB table below, not this object's own
    #: in-memory dict, so a restart never loses the idempotency guarantee.
    mock_order_api = order_api.MockOrderApi()

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
        """Search the fictional catalogue by free-text product query."""
        organization_id = await _authorize(
            ctx, tool_name="search_catalogue", required_scope=SUPPLIER_TOOLS_SCOPE
        )
        matches = supplier_tools.search_catalogue(query)
        await _audit(
            tool_name="search_catalogue",
            organization_id=organization_id,
            success=True,
            detail=f"query={query!r} matched={len(matches)}",
        )
        return [
            CatalogueProductOut(
                product_ref=p.product_ref,
                name=p.name,
                unit=p.unit,
                unit_price=str(p.unit_price),
                stock_today=str(p.stock_today),
                stock_tomorrow=str(p.stock_tomorrow),
            )
            for p in matches
        ]

    @mcp.tool()
    async def check_inventory(ctx: Context, product_ref: str) -> InventoryOut:
        """Return the current fictional stock snapshot for ``product_ref``."""
        organization_id = await _authorize(
            ctx, tool_name="check_inventory", required_scope=SUPPLIER_TOOLS_SCOPE
        )
        product = supplier_tools.check_inventory(product_ref)
        await _audit(
            tool_name="check_inventory",
            organization_id=organization_id,
            success=True,
            detail=f"product_ref={product_ref!r} found={product is not None}",
        )
        if product is None:
            return InventoryOut(found=False, product_ref=product_ref)
        return InventoryOut(
            found=True,
            product_ref=product.product_ref,
            unit=product.unit,
            stock_today=str(product.stock_today),
            stock_tomorrow=str(product.stock_tomorrow),
        )

    @mcp.tool()
    async def get_customer_price(
        ctx: Context, product_ref: str, buyer_organization_id: str
    ) -> CustomerPriceOut:
        """Return the customer-specific fictional unit price for ``product_ref``."""
        organization_id = await _authorize(
            ctx, tool_name="get_customer_price", required_scope=SUPPLIER_TOOLS_SCOPE
        )
        price = supplier_tools.get_customer_price(
            product_ref=product_ref, buyer_organization_id=buyer_organization_id
        )
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
        options = supplier_tools.get_delivery_options(product_ref=product_ref, quantity=quantity)
        await _audit(
            tool_name="get_delivery_options",
            organization_id=organization_id,
            success=True,
            detail=f"product_ref={product_ref!r} options={len(options)}",
        )
        return [
            DeliveryOptionOut(
                label=o.label, earliest_date=o.earliest_date, fee=str(o.fee), currency=o.currency
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
    ) -> OrderExecutionOut:
        """The approved mock order-execution tool (M5). Requires the
        narrow ``execute_order`` scope — a read-only
        ``use_supplier_tools`` credential cannot call this. Idempotent:
        the same ``idempotency_key`` always returns the exact same
        ``external_order_id`` rather than creating a second order,
        enforced by this table's own UNIQUE constraint, not merely by
        checking first (a concurrent duplicate call can race the check,
        never the constraint)."""
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

            confirmation = mock_order_api.submit_order(idempotency_key=idempotency_key)
            try:
                session.add(
                    SupplierMcpOrderRecord(
                        idempotency_key=idempotency_key,
                        agreement_id=agreement_id,
                        buyer_organization_id=buyer_organization_id,
                        content_hash=content_hash,
                        external_order_id=confirmation.external_order_id,
                        status=confirmation.status,
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
            detail=f"idempotency_key={idempotency_key!r} order={confirmation.external_order_id!r}",
        )
        return OrderExecutionOut(
            idempotency_key=idempotency_key,
            external_order_id=confirmation.external_order_id,
            status=confirmation.status,
            duplicate=False,
        )

    @mcp.tool()
    async def get_order_status(ctx: Context, idempotency_key: str) -> OrderStatusOut:
        """Order-status lookup for reconciliation (M5): the same narrow
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
