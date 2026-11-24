"""supplier-sandbox-erp: a realistic sandbox external ERP/API system (M7).

This service plays the role of a real supplier business system over
real HTTP — the thing a ``SupplierBusinessSystemAdapter`` implementation
talks to. It is reached only by ``apps/supplier-mcp``'s sandbox adapter,
authenticated with a network-api-minted ``use_sandbox_erp``-scoped
service credential (the same intra-organization HMAC trust root every
other AIMO-internal call uses — see ``packages/identity``). Nothing
outside this service ever touches its database directly.

Deliberately realistic, not just a happy-path mock: customer-specific
prices, stock that decrements on confirmed orders, minimum-order-quantity
enforcement with a configured substitution, a delivery cutoff flag, and
a delayed-acknowledgement ("pending") order state — plus a clearly-
labeled, environment-gated fault-injection surface for deterministic
tests (``/admin/fault-injection/*``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import FastAPI, Header, HTTPException
from identity.tokens import ServiceCredentialError, verify_service_credential
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import new_engine
from supplier_sandbox_erp.fault_injection import FaultInjectionStore
from supplier_sandbox_erp.models import (
    SandboxCustomerPriceRecord,
    SandboxOrderRecord,
    SandboxProductRecord,
)
from supplier_sandbox_erp.seed import seed_if_empty
from supplier_sandbox_erp.settings import get_settings

SANDBOX_ERP_SCOPE = "use_sandbox_erp"
_DEV_ENVIRONMENTS = {"local", "staging", "test"}


class ProductOut(BaseModel):
    product_ref: str
    name: str
    unit: str
    packaging: str


class CustomerPriceOut(BaseModel):
    found: bool
    product_ref: str
    buyer_organization_id: str
    unit_price: str | None = None
    currency: str | None = None


class AvailabilityOut(BaseModel):
    found: bool
    product_ref: str
    quantity_available: str | None = None
    moq: str | None = None
    cutoff_passed: bool = False
    substituted_for_product_ref: str | None = None


class DeliveryOptionOut(BaseModel):
    label: str
    earliest_date: str
    fee: str
    currency: str
    restrictions: list[str] = []


class OrderLineIn(BaseModel):
    product_ref: str
    quantity: str
    unit: str
    unit_price: str


class SubmitOrderIn(BaseModel):
    idempotency_key: str
    buyer_organization_id: str
    lines: list[OrderLineIn]


class OrderOut(BaseModel):
    idempotency_key: str
    status: str
    external_order_id: str | None
    duplicate: bool


class OrderStatusOut(BaseModel):
    found: bool
    idempotency_key: str
    status: str | None = None
    external_order_id: str | None = None


class ArmFaultIn(BaseModel):
    scenario: str
    idempotency_key: str | None = None


def create_app() -> FastAPI:
    settings = get_settings()
    engine = new_engine(settings.supplier_sandbox_erp_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    faults = FaultInjectionStore()

    async def require_sandbox_credential(authorization: str | None) -> str:
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer service credential.")
        token = authorization.removeprefix("Bearer ")
        try:
            claims = verify_service_credential(
                token,
                signing_secret=settings.service_credential_signing_secret,
                required_scope=SANDBOX_ERP_SCOPE,
            )
        except ServiceCredentialError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if claims.organization_id != settings.supplier_sandbox_erp_organization_id:
            raise HTTPException(
                status_code=403, detail="Credential does not grant access to this sandbox."
            )
        return claims.organization_id

    def require_dev_environment() -> None:
        if settings.environment not in _DEV_ENVIRONMENTS:
            raise HTTPException(
                status_code=403,
                detail="Fault injection is refused outside local/staging/test environments.",
            )

    async def _consume_fault(idempotency_key: str | None) -> None:
        scenario = faults.consume(idempotency_key=idempotency_key)
        if scenario == "transient_error":
            raise HTTPException(status_code=503, detail="Sandbox ERP: simulated transient error.")
        if scenario == "timeout":
            await asyncio.sleep(settings.fault_injection_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with session_factory() as session:
            await seed_if_empty(session)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="AIMO BRAIN Supplier Sandbox ERP", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "supplier-sandbox-erp"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "service": "supplier-sandbox-erp", "database": "reachable"}

    @app.get("/catalogue/search", response_model=list[ProductOut])
    async def search_catalogue(
        query: str, authorization: str | None = Header(default=None)
    ) -> list[ProductOut]:
        await require_sandbox_credential(authorization)
        await _consume_fault(None)
        needle = query.strip().lower()
        async with session_factory() as session:
            rows = (await session.execute(select(SandboxProductRecord))).scalars().all()
        return [
            ProductOut(product_ref=r.product_ref, name=r.name, unit=r.unit, packaging=r.packaging)
            for r in rows
            if needle and needle in r.name.lower()
        ]

    @app.get("/products/{product_ref}", response_model=ProductOut | None)
    async def get_product(
        product_ref: str, authorization: str | None = Header(default=None)
    ) -> ProductOut | None:
        await require_sandbox_credential(authorization)
        await _consume_fault(None)
        async with session_factory() as session:
            row = await session.get(SandboxProductRecord, product_ref)
        if row is None:
            return None
        return ProductOut(
            product_ref=row.product_ref, name=row.name, unit=row.unit, packaging=row.packaging
        )

    @app.get(
        "/customers/{buyer_organization_id}/products/{product_ref}/price",
        response_model=CustomerPriceOut,
    )
    async def get_customer_price(
        buyer_organization_id: str,
        product_ref: str,
        authorization: str | None = Header(default=None),
    ) -> CustomerPriceOut:
        await require_sandbox_credential(authorization)
        await _consume_fault(None)
        async with session_factory() as session:
            product = await session.get(SandboxProductRecord, product_ref)
            if product is None:
                return CustomerPriceOut(
                    found=False,
                    product_ref=product_ref,
                    buyer_organization_id=buyer_organization_id,
                )
            custom = (
                await session.execute(
                    select(SandboxCustomerPriceRecord).where(
                        SandboxCustomerPriceRecord.product_ref == product_ref,
                        SandboxCustomerPriceRecord.buyer_organization_id == buyer_organization_id,
                    )
                )
            ).scalar_one_or_none()
        unit_price = str(custom.unit_price) if custom is not None else str(product.base_unit_price)
        currency = custom.currency if custom is not None else "EUR"
        return CustomerPriceOut(
            found=True,
            product_ref=product_ref,
            buyer_organization_id=buyer_organization_id,
            unit_price=unit_price,
            currency=currency,
        )

    @app.get("/products/{product_ref}/availability", response_model=AvailabilityOut)
    async def check_availability(
        product_ref: str, quantity: str, authorization: str | None = Header(default=None)
    ) -> AvailabilityOut:
        await require_sandbox_credential(authorization)
        await _consume_fault(None)
        async with session_factory() as session:
            product = await session.get(SandboxProductRecord, product_ref)
            if product is None:
                return AvailabilityOut(found=False, product_ref=product_ref)
            requested = Decimal(quantity)
            moq = Decimal(str(product.minimum_order_quantity))
            substituted_for: str | None = None
            stock = Decimal(str(product.stock))
            if requested < moq and product.substitute_product_ref:
                substituted_for = product.substitute_product_ref
                substitute = await session.get(SandboxProductRecord, product.substitute_product_ref)
                stock = Decimal(str(substitute.stock)) if substitute is not None else Decimal("0")
        return AvailabilityOut(
            found=True,
            product_ref=product_ref,
            quantity_available=str(stock),
            moq=str(moq),
            cutoff_passed=bool(product.delivery_cutoff_passed),
            substituted_for_product_ref=substituted_for,
        )

    @app.get("/products/{product_ref}/delivery-options", response_model=list[DeliveryOptionOut])
    async def get_delivery_options(
        product_ref: str, quantity: str, authorization: str | None = Header(default=None)
    ) -> list[DeliveryOptionOut]:
        await require_sandbox_credential(authorization)
        await _consume_fault(None)
        async with session_factory() as session:
            product = await session.get(SandboxProductRecord, product_ref)
        if product is None:
            return []
        if product.delivery_cutoff_passed:
            return [
                DeliveryOptionOut(
                    label="Standard delivery (next available slot)",
                    earliest_date="2026-11-24",
                    fee="0.00",
                    currency="EUR",
                    restrictions=["delivery cutoff already passed for the next scheduled slot"],
                )
            ]
        return [
            DeliveryOptionOut(
                label="Standard delivery", earliest_date="2026-11-21", fee="0.00", currency="EUR"
            ),
            DeliveryOptionOut(
                label="Next-day delivery", earliest_date="2026-11-20", fee="15.00", currency="EUR"
            ),
        ]

    @app.post("/orders", response_model=OrderOut)
    async def submit_order(
        body: SubmitOrderIn, authorization: str | None = Header(default=None)
    ) -> OrderOut:
        await require_sandbox_credential(authorization)
        async with session_factory() as session:
            existing = (
                await session.execute(
                    select(SandboxOrderRecord).where(
                        SandboxOrderRecord.idempotency_key == body.idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return OrderOut(
                    idempotency_key=existing.idempotency_key,
                    status=existing.status,
                    external_order_id=existing.external_order_id,
                    duplicate=True,
                )

            scenario = faults.consume(idempotency_key=body.idempotency_key)
            if scenario == "transient_error":
                raise HTTPException(
                    status_code=503, detail="Sandbox ERP: simulated transient error."
                )
            if scenario == "timeout":
                await asyncio.sleep(settings.fault_injection_timeout_seconds)
            if scenario == "delayed_ack":
                faults.mark_pending_ack(body.idempotency_key)
                status = "pending"
                external_order_id = None
            else:
                status = "confirmed"
                external_order_id = f"sandbox_erp_order_{uuid.uuid4().hex[:12]}"
                for line in body.lines:
                    product = await session.get(SandboxProductRecord, line.product_ref)
                    if product is not None:
                        remaining = Decimal(str(product.stock)) - Decimal(line.quantity)
                        product.stock = str(max(remaining, Decimal("0")))

            record = SandboxOrderRecord(
                idempotency_key=body.idempotency_key,
                buyer_organization_id=body.buyer_organization_id,
                lines=json.dumps([line.model_dump() for line in body.lines]),
                status=status,
                external_order_id=external_order_id,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
            session.add(record)
            await session.commit()
        return OrderOut(
            idempotency_key=body.idempotency_key,
            status=status,
            external_order_id=external_order_id,
            duplicate=False,
        )

    @app.get("/orders/{idempotency_key}", response_model=OrderStatusOut)
    async def get_order_status(
        idempotency_key: str, authorization: str | None = Header(default=None)
    ) -> OrderStatusOut:
        await require_sandbox_credential(authorization)
        async with session_factory() as session:
            record = (
                await session.execute(
                    select(SandboxOrderRecord).where(
                        SandboxOrderRecord.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if record is None:
                return OrderStatusOut(found=False, idempotency_key=idempotency_key)
            if record.status == "pending" and not faults.is_pending_ack(idempotency_key):
                # The admin resolved it since the order was submitted.
                record.status = "confirmed"
                record.external_order_id = f"sandbox_erp_order_{uuid.uuid4().hex[:12]}"
                await session.commit()
            return OrderStatusOut(
                found=True,
                idempotency_key=idempotency_key,
                status=record.status,
                external_order_id=record.external_order_id,
            )

    @app.post("/admin/fault-injection/arm")
    async def arm_fault(body: ArmFaultIn) -> dict[str, str]:
        require_dev_environment()
        if body.scenario not in {"transient_error", "timeout", "delayed_ack"}:
            raise HTTPException(status_code=400, detail=f"Unknown scenario {body.scenario!r}.")
        faults.arm(scenario=body.scenario, idempotency_key=body.idempotency_key)
        return {"status": "armed"}

    @app.post("/admin/fault-injection/resolve/{idempotency_key}")
    async def resolve_fault(idempotency_key: str) -> dict[str, str]:
        require_dev_environment()
        resolved = faults.resolve_ack(idempotency_key)
        return {"status": "resolved" if resolved else "not_pending"}

    @app.post("/admin/fault-injection/reset")
    async def reset_faults() -> dict[str, str]:
        require_dev_environment()
        faults.reset()
        return {"status": "reset"}

    return app


app = create_app()
