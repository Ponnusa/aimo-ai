"""buyer-mcp's MCP tool server (M3).

Exposes the buyer organization's own business tools over the official
MCP SDK's Streamable HTTP transport: ``get_purchase_requirements``,
``get_budget``, ``list_approved_suppliers`` and
``request_purchase_approval``. The underlying fictional data lives in
``food_wholesale.adapters.mock.buyer_tools`` — this module only wires it
up as typed MCP tools with per-call scoped-credential authorization and
audit logging.

Every tool call is authorized with :func:`mcp_integration.require_tool_scope`
before touching any business data, and every attempt (successful or
rejected) is recorded to this service's own ``buyer_mcp_tool_invocations``
table. ``request_purchase_approval`` returns a deterministic mock
decision only — it is not OPA, not a binding authorization, and never
consulted by any commit path (M5 scope, see docs/ROADMAP.md).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from food_wholesale.adapters.mock import buyer_tools
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp_integration import require_tool_scope
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from buyer_mcp.models import BuyerMcpToolInvocationRecord
from buyer_mcp.settings import BuyerMcpSettings

BUYER_TOOLS_SCOPE = "use_buyer_tools"


class PurchaseRequirementOut(BaseModel):
    product_query: str
    quantity: str
    unit: str
    needed_by: str


class BudgetOut(BaseModel):
    organization_id: str
    currency: str
    remaining_amount: str


class ApprovedSupplierOut(BaseModel):
    organization_id: str
    display_name: str


class PurchaseApprovalOut(BaseModel):
    decision: str
    reason: str


def create_mcp_server(
    *, settings: BuyerMcpSettings, session_factory: async_sessionmaker
) -> FastMCP:
    mcp = FastMCP(name="aimo-brain-buyer-mcp", streamable_http_path="/")

    async def _authorize(ctx: Context, *, tool_name: str) -> str:
        try:
            claims = require_tool_scope(
                ctx,
                signing_secret=settings.service_credential_signing_secret,
                required_scope=BUYER_TOOLS_SCOPE,
                required_organization_id=settings.buyer_mcp_organization_id,
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
                BuyerMcpToolInvocationRecord(
                    tool_name=tool_name,
                    organization_id=organization_id,
                    success=success,
                    detail=json.dumps({"message": detail}),
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            await session.commit()

    @mcp.tool()
    async def get_purchase_requirements(ctx: Context) -> list[PurchaseRequirementOut]:
        """Return this organization's outstanding purchase requirements."""
        organization_id = await _authorize(ctx, tool_name="get_purchase_requirements")
        requirements = buyer_tools.get_purchase_requirements()
        await _audit(
            tool_name="get_purchase_requirements",
            organization_id=organization_id,
            success=True,
            detail=f"{len(requirements)} requirement(s) returned.",
        )
        return [
            PurchaseRequirementOut(
                product_query=r.product_query,
                quantity=r.quantity,
                unit=r.unit,
                needed_by=r.needed_by,
            )
            for r in requirements
        ]

    @mcp.tool()
    async def get_budget(ctx: Context, organization_id: str) -> BudgetOut:
        """Return the remaining purchasing budget for ``organization_id``."""
        caller_org = await _authorize(ctx, tool_name="get_budget")
        budget = buyer_tools.get_budget(organization_id)
        await _audit(
            tool_name="get_budget", organization_id=caller_org, success=True, detail=organization_id
        )
        return BudgetOut(
            organization_id=budget.organization_id,
            currency=budget.currency,
            remaining_amount=str(budget.remaining_amount),
        )

    @mcp.tool()
    async def list_approved_suppliers(
        ctx: Context, organization_id: str
    ) -> list[ApprovedSupplierOut]:
        """Return the suppliers this organization has pre-approved."""
        caller_org = await _authorize(ctx, tool_name="list_approved_suppliers")
        suppliers = buyer_tools.list_approved_suppliers(organization_id)
        await _audit(
            tool_name="list_approved_suppliers",
            organization_id=caller_org,
            success=True,
            detail=f"{len(suppliers)} supplier(s) returned.",
        )
        return [
            ApprovedSupplierOut(organization_id=s.organization_id, display_name=s.display_name)
            for s in suppliers
        ]

    @mcp.tool()
    async def request_purchase_approval(
        ctx: Context, organization_id: str, amount: str, currency: str
    ) -> PurchaseApprovalOut:
        """Return a mock approval decision for a proposed spend.

        Not a binding authorization and not OPA — see this module's
        docstring. ``amount`` is a decimal string, never a float, per
        docs/PROTOCOLS.md rule 2.
        """
        caller_org = await _authorize(ctx, tool_name="request_purchase_approval")
        try:
            parsed_amount = Decimal(amount)
        except InvalidOperation as exc:
            raise ToolError(f"Input validation error: {amount!r} is not a decimal amount.") from exc

        decision = buyer_tools.evaluate_purchase_approval(
            organization_id=organization_id, amount=parsed_amount, currency=currency
        )
        await _audit(
            tool_name="request_purchase_approval",
            organization_id=caller_org,
            success=True,
            detail=f"decision={decision.decision}",
        )
        return PurchaseApprovalOut(decision=decision.decision, reason=decision.reason)

    return mcp
