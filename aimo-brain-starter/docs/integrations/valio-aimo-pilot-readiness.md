# AIMO BRAIN — Valio Aimo Pilot Readiness Package (M7)

**Audience:** Valio Aimo's technical and business stakeholders evaluating a pilot integration with AIMO BRAIN.

**Status:** this is a readiness package for discussion, not a claim that integration is complete. AIMO BRAIN has no Valio Aimo API access, credentials or sandbox endpoint today. Every claim below about *what AIMO BRAIN can do* has been built and tested against a realistic sandbox stand-in (`apps/supplier-sandbox-erp`), not against Valio Aimo's real systems — see "Sandbox testing" for exactly what that proves and does not prove.

## 1. System architecture

AIMO BRAIN is a cross-organization agent network: a buyer-side agent and a supplier-side agent communicate over the official Agent2Agent (A2A) protocol to negotiate, authorize and commit purchase orders, with every binding step independently policy-checked and (optionally) human-approved on both sides before anything is submitted anywhere.

The part relevant to a supplier integration is narrow and deliberately isolated:

```text
supplier-agent  ->  supplier-mcp (AIMO's own service)  ->  [ configurable adapter ]  ->  Valio Aimo's systems
```

- `supplier-agent` never talks to Valio Aimo directly, and never holds Valio Aimo credentials.
- `supplier-mcp` is AIMO BRAIN's own service, operated by (or on behalf of) the supplier organization. It exposes catalogue/pricing/availability/order tools over the official MCP (Model Context Protocol) SDK, authenticated with AIMO's own short-lived, scoped service credentials.
- The **adapter** is the only component that would ever call a Valio Aimo API. It implements a fixed, documented interface (`SupplierBusinessSystemAdapter`) covering exactly seven capabilities (listed in §2) — swapping which business system is behind `supplier-mcp` is a configuration change, not a code change to any agent, policy, or protocol logic.

This means: Valio Aimo's technical team can review and approve exactly one integration surface (the adapter's API calls), independent of the rest of AIMO BRAIN's negotiation/authorization machinery, which does not change based on which supplier system is configured.

## 2. API capabilities required from the supplier

The adapter interface requires exactly these capabilities from Valio Aimo's systems:

| Capability | What it's used for |
|---|---|
| Product catalogue search | Resolving a buyer's free-text product request to real products |
| Product details and packaging | Confirming unit of measure, packaging, identifiers before quoting |
| Customer-specific pricing | Pricing a quote for the specific buyer organization, not a list price |
| Inventory and availability | Confirming stock before quoting and before committing |
| Delivery options and restrictions | Presenting delivery dates/fees, cutoffs, substitution constraints |
| Order submission | Placing the order once both organizations have authorized it |
| Order status and reconciliation | Resolving an order whose outcome was not immediately confirmed |

Nothing beyond these seven is required. AIMO BRAIN does not need write access to Valio Aimo's pricing, catalogue, or customer master data — read access to the first four, and a single order-submission/status pair, is the complete integration surface.

## 3. Authentication and security model

- Every call AIMO BRAIN's own services make to each other (buyer agent, supplier agent, each organization's own tool server) is authenticated with short-lived, narrowly-scoped credentials — never a long-lived shared secret, and never a credential with more access than the one call needs. The adapter boundary to Valio Aimo would follow the same discipline: whatever authentication scheme Valio Aimo requires, AIMO BRAIN holds it only in the adapter's own configuration, never in the agent's reasoning/negotiation code, and never logs it.
- Every binding action (an actual order submission) requires: an exact, hash-verified agreement between both organizations' independently evaluated policies; optional human approval on either side, enforced before any order call is made; and a deterministic idempotency key that is never regenerated on retry.
- **No Valio Aimo system is accessed, and no order is submitted, without Valio Aimo's explicit prior authorization and credentials.** This is a hard rule in AIMO BRAIN's own engineering constitution (`CLAUDE.md`), not a pilot-specific promise.

## 4. Data flow and ownership

- Valio Aimo's own systems remain the single source of truth for stock, pricing and order status. AIMO BRAIN never treats a cached value as authoritative at the moment of commit — every commit re-derives its idempotency key from the exact agreed terms and calls back to the business system's own order-status endpoint when an outcome is ambiguous, rather than guessing.
- AIMO BRAIN keeps its own durable audit copy of what it asked Valio Aimo's systems to do and what it was told back (for its own audit trail and for the human-approval record), but this is explicitly a copy, not a second system of record.
- No data flows from Valio Aimo's systems to any other organization on the network except as a direct, authorized response to that organization's own request (e.g. a quote for a specific buyer, priced using that buyer's own customer-specific price).

## 5. Integration options

Two realistic options, in order of integration effort:

1. **Direct API integration**: Valio Aimo grants AIMO BRAIN sandbox, then production, API access (REST or whatever Valio Aimo's platform already exposes). AIMO BRAIN implements one adapter module against that API. This is the default assumption in this document.
2. **File/batch integration**: if Valio Aimo's systems do not expose a suitable real-time API, a batch/file-based adapter (e.g. polling an export, writing orders to a drop location) is also representable within the same adapter interface — the interface's methods do not assume synchronous HTTP, only that each one eventually returns the documented result shape.

AIMO BRAIN has no fixed preference; whichever option matches what Valio Aimo can realistically support with the least new engineering on Valio's side is preferred.

## 6. Sandbox testing

What has been built and tested: a standalone sandbox service (`apps/supplier-sandbox-erp`) that behaves like a realistic external ERP — customer-specific prices, stock that changes as orders are placed, a delivery-cutoff flag, minimum-order-quantity enforcement with product substitution, and deliberately injectable transient errors, timeouts and delayed order acknowledgement. AIMO BRAIN's full commit-and-reconciliation path (including the "never treat a timeout as proof of failure" and worker-driven retry logic) has been proven against this sandbox end to end, including the exact failure modes a real integration is likely to hit.

What this does **not** prove: anything about Valio Aimo's actual API shape, actual failure modes, actual rate limits, or actual identifier formats. Once Valio Aimo grants sandbox access, the next step is a short, scoped exercise: point the same adapter interface at Valio Aimo's real sandbox, confirm each of the seven capabilities against real responses, and re-run AIMO BRAIN's existing adapter-contract test suite against it (the same test suite the mock and sandbox-ERP adapters both already pass) before any pilot order is placed.

## 7. Deployment model

- The adapter and AIMO BRAIN's supplier-side services (`supplier-agent`, `supplier-mcp`) are operated by (or on behalf of) the supplier organization — Valio Aimo or Valio Aimo's chosen operator, not by AIMO BRAIN's own operator, unless otherwise agreed. Each service is independently deployable (its own container image, its own database).
- Credentials for Valio Aimo's API are configured only where the adapter runs, never distributed elsewhere in the network.
- Sandbox and production are fully separate deployments (separate adapter configuration, separate credentials) — never a runtime flag that could be flipped accidentally.

## 8. Operational responsibilities

| Responsibility | Owner |
|---|---|
| Valio Aimo API availability, correctness, rate limits | Valio Aimo |
| Granting/revoking AIMO BRAIN's API credentials | Valio Aimo |
| Adapter implementation, deployment, monitoring | Supplier organization's AIMO BRAIN operator |
| Policy configuration (approved customers, thresholds, approval requirements) | Supplier organization |
| Buyer-side negotiation/authorization behavior | Each buyer organization independently |
| Incident response for a stuck/ambiguous order | Supplier organization's operator, using AIMO BRAIN's own reconciliation tooling and portal visibility (integration health, pending/failed orders, reconciliation history) |

## 9. Pilot acceptance criteria

Before any pilot order is placed against Valio Aimo's production systems, all of the following must hold:

1. Valio Aimo has granted explicit written authorization and sandbox credentials.
2. The real Valio Aimo adapter implementation passes the same adapter-contract test suite the mock and sandbox-ERP adapters already pass.
3. A full negotiation-through-commitment-through-reconciliation cycle has been exercised against Valio Aimo's own sandbox, including at least one deliberately-forced failure/timeout case, with the correct outcome (no duplicate order, correct reconciliation) verified.
4. Both organizations' policy configuration for the pilot (approved counterparties, order limits, human-approval thresholds) is explicitly reviewed and agreed, not left at development defaults.
5. A named technical contact on both sides is identified for incident response during the pilot window.
6. Valio Aimo has separately and explicitly approved the transition from sandbox to production — this is a distinct decision from granting sandbox access, never implied by it.

Production access is always a separate approval decision from everything in this document, consistent with `docs/ROADMAP.md`'s stated M7 "Done when": sandbox tests and partner acceptance criteria pass; production remains a separate approval decision.
