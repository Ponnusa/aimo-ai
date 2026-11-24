# Valio Aimo adapter contract (M7)

**Status: not yet authorized.** AIMO BRAIN has no Valio Aimo API access, credentials, sandbox endpoint or written permission at the time this document was written. Nothing in this repository calls a real Valio Aimo system — `food_wholesale.adapters.valio_aimo.ValioAimoAdapter` exists only to prove the adapter *boundary* is real (it satisfies `business_system_integration.SupplierBusinessSystemAdapter`, the same Protocol `food_wholesale.adapters.sandbox.SandboxErpAdapter` implements against the realistic sandbox ERP), and every one of its methods raises `NotImplementedError` naming exactly what is missing.

This document is the checklist of what Valio Aimo would need to confirm or provide before any method below moves from `NotImplementedError` to a real HTTP call. It is deliberately specific about *what AIMO BRAIN needs to ask*, not about what Valio's API looks like — we do not know that yet, and we do not guess.

## Required API access and permissions

- A sandbox/test environment distinct from production, with its own base URL and its own credentials (see "Sandbox and production environment separation" below).
- Scoped, read-only access to: catalogue/product search, product detail, customer-specific pricing, stock/availability, delivery options.
- A separate, explicitly-granted scope for order submission and order status lookup — AIMO BRAIN's own internal convention (supplier-mcp's `use_supplier_tools` vs. `execute_order` scope split) already assumes the underlying system can and should separate "read" from "write" access; we would ask Valio Aimo for the same separation if their API supports distinct API keys/roles.
- Written confirmation of rate limits (requests/second or /minute, per endpoint) so `food_wholesale.adapters.valio_aimo`'s real implementation can apply a client-side limiter rather than discovering limits by triggering them.

## Product and customer identifiers

- Valio Aimo's own product identifier format (SKU, GTIN, internal code — unknown). AIMO BRAIN's generic `product_ref` (see `business_system_integration.types.ProductSummary`) is adapter-defined; the real adapter would need a mapping table or pass-through, whichever Valio's API expects.
- Valio Aimo's own customer identifier format. AIMO BRAIN's `buyer_organization_id` is an AIMO-internal organization id — almost certainly **not** the same value Valio's systems use for a customer account/contract number. A mapping (likely maintained by Valio or agreed during pilot onboarding) would be required before `get_customer_price` could be implemented; this is explicitly called out in the method's docstring rather than invented as a 1:1 mapping.

## Authentication requirements

- Unknown: API key, OAuth2 client credentials, mutual TLS, or something else. Whatever it is, the real adapter would hold it the same way `SandboxErpAdapter` holds its bearer token — injected via settings, never hardcoded, never logged (matching `docs/SECURITY.md`'s "logs and traces do not contain secrets" rule already enforced for every other credential in this system).
- Confirmation of token lifetime/refresh semantics, so the adapter can refresh proactively rather than discovering expiry via a failed call.

## Catalogue, pricing and stock mappings

- **Catalogue mapping**: which Valio Aimo endpoint returns a product list/search result, and which fields map to `ProductSummary.product_ref`/`name`/`unit` and `ProductDetails.packaging`/`attributes`.
- **Pricing mapping**: whether Valio Aimo's API exposes customer-specific pricing directly, or whether pricing requires a separate contract/agreement lookup. Confirmation of currency and rounding/precision rules (AIMO BRAIN represents money as decimal strings throughout, per `docs/PROTOCOLS.md` — never a binary float).
- **Stock mapping**: whether availability is real-time or batch-updated (and on what cadence), and whether Valio Aimo's own API models minimum order quantity, substitution or delivery cutoffs the way `AvailabilityStatus` does, or whether those would need to be layered on top by AIMO BRAIN instead.

## Ordering and status semantics

- Valio Aimo's own order submission endpoint and its idempotency/duplicate-detection behavior: does it accept a client-supplied idempotency key (AIMO BRAIN's `idempotency_key` is `sha256(content_hash + buyer_org_id + supplier_org_id)`, computed once and never regenerated — see `docs/DECISIONS.md` ADR-011), or does it require its own duplicate-detection scheme that AIMO BRAIN's adapter would need to bridge?
- The full vocabulary of order statuses Valio Aimo's API can return, and which of them map to AIMO BRAIN's `OrderSubmissionResult.status` values (`"confirmed"`, `"pending"`, `"rejected"`) — a `"pending"`/delayed-acknowledgement state is assumed possible (it is common in ERP integrations and is exactly what `SandboxErpAdapter` was built to model realistically) but not confirmed for Valio Aimo specifically.
- **No order is ever submitted to a real Valio Aimo endpoint without explicit written authorization and credentials**, per `CLAUDE.md`. This applies even to a confirmed sandbox: AIMO BRAIN would still require an affirmative go-ahead from Valio Aimo's technical contact before the first real `submit_order` call, sandbox or production.

## Delivery and substitution constraints

- Valio Aimo's own delivery-location identifier format and how routes/cutoffs are modeled in their API (if at all) — AIMO BRAIN's generic `DeliveryOption.restrictions` field is a free-text list precisely because we do not yet know Valio's own structured representation, if one exists.
- Whether Valio Aimo's system performs product substitution itself (and reports it) or expects AIMO BRAIN to decide and request a specific substitute product.

## Rate limits and error behavior

- Documented rate limits per endpoint (see above).
- The exact set of error responses Valio Aimo's API can return, and which represent a transient condition (mapped to `AdapterUnavailableError`, which AIMO BRAIN's `apps/worker` reconciliation loop treats as retryable, never as proof of failure) versus a definite business rejection (mapped to `AdapterInvalidRequestError`, never retried).

## Data ownership and retention

- Confirmation of which organization's data governs retention: AIMO BRAIN's own position (see `docs/ARCHITECTURE.md`'s persistence section) is that the supplier's ERP remains authoritative for stock, prices and orders — AIMO BRAIN never treats a cached snapshot as authoritative at commit time, and would ask Valio Aimo to confirm the same expectation holds on their side (i.e. AIMO BRAIN does not become a second system of record for Valio Aimo's own order data).
- Confirmation of any data residency or retention-period requirements Valio Aimo needs AIMO BRAIN to honor for data read from or written to their system (e.g. how long AIMO BRAIN's own audit copies of order confirmations may be retained).

## Sandbox and production environment separation

- A distinct sandbox base URL and distinct sandbox credentials, confirmed in writing to be fully separate from production (no shared database, no shared order queue) — mirroring AIMO BRAIN's own `apps/supplier-sandbox-erp` vs. a hypothetical production Valio adapter configuration (`SUPPLIER_BUSINESS_SYSTEM_ADAPTER=sandbox` vs. `valio_aimo`, selected per-deployment, never by a runtime flag an agent process could flip on its own).
- Agreement on how/when AIMO BRAIN graduates from sandbox testing to a production pilot — see `docs/integrations/valio-aimo-pilot-readiness.md`'s "Pilot acceptance criteria."

## Implementation note

Once the above is confirmed, implementing the real adapter is expected to be a contained change: a new `food_wholesale/adapters/valio_aimo/http_adapter.py` implementing the same `SupplierBusinessSystemAdapter` Protocol (following `SandboxErpAdapter`'s exact shape — an `httpx.AsyncClient`, the same error-mapping discipline), registered in `apps/supplier-mcp/src/supplier_mcp/adapter_factory.py` as a real `"valio_aimo"` case. No change would be required to `apps/supplier-mcp`'s tool surface, schemas, database, or to `supplier_agent`/`apps/worker` — the entire point of the generic adapter interface introduced in M7.
