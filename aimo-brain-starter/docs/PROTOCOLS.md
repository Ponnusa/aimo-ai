# AIMO BRAIN Business Protocols v0.1

A2A defines task/message transport. The following are **AIMO application artifacts**, carried as typed A2A artifacts/messages; do not pretend they are native A2A message types.

## Generic artifact envelope (illustrative)

```json
{
  "schema": "https://schemas.aimobrain.example/core/artifact/v0.1",
  "artifact_id": "art_01",
  "transaction_id": "txn_01",
  "correlation_id": "corr_01",
  "sender_agent_id": "agent_buyer_01",
  "recipient_agent_id": "agent_supplier_01",
  "organization_id": "org_buyer_01",
  "domain": "food-wholesale",
  "type": "request_for_quote",
  "version": 1,
  "created_at": "2026-11-16T12:00:00Z",
  "expires_at": "2026-11-17T12:00:00Z",
  "payload": {
    "lines": [{"product_query": "lactose-free milk", "quantity": "100", "unit": "L"}],
    "delivery_location_ref": "site_01"
  }
}
```

This is a **design example**; the implemented food-wholesale schemas (M4, see `docs/DECISIONS.md` ADR-010) are in `industries/food-wholesale/schemas/`: `request_for_quote`, `quote`, `counterproposal`, `accept_proposal`, `reject_proposal`, `proposed_agreement`. Each carries `proposal_id`/`supersedes_proposal_id`/`content_hash` (canonicalization + sha256, via `trade_protocol.negotiation`); `transaction_id` doubles as the stable negotiation ID. Avoid embedding sensitive delivery addresses in public registry metadata.

M5 (see `docs/DECISIONS.md` ADR-011) adds three further artifact types, also in `industries/food-wholesale/schemas/`: `authorization_evidence` (one organization's independent OPA decision, and approval if one was required, for one exact `content_hash`), `order_commit_request` (the buyer's commitment intent, carrying an idempotency key), and `order_confirmation` (the supplier's reply from its mock order adapter — `status: "CONFIRMED"` is the exact point the simulated transaction becomes binding).

As of M5.1 (see `docs/DECISIONS.md` ADR-012), none of the nine artifact types above carries a bearer credential inside `payload` anymore. Instead, every one of them carries a top-level `proof` object (`packages/message-integrity`): a JWS signed with the sender organization's own Ed25519 key, binding sender/recipient organization, negotiation id, artifact type/version, a content hash, a sha256 digest of the full canonicalized artifact, a unique `jti`, and issuance/expiry times. The receiver verifies the signature against the sender's published JWKS, the digest (tamper detection), the explicit claims, expiry, replay (`jti`), and that the claimed sender is a registered, active, trading-related agent (`GET /agents/{id}/verify-sender`, registry-api) before `organization_id`/`sender_agent_id` are trusted at all — never message-level metadata alone, since a supplier's synchronous Task-artifact reply has no sender metadata channel of its own.

## Domain-independent lifecycle

`DISCOVERED → REQUESTED → PROPOSED ↔ NEGOTIATING → PENDING_AUTHORIZATION → AUTHORIZED → COMMITTING → COMMITTED → FULFILLING → VERIFIED`

Terminal/exceptional states include `REJECTED`, `EXPIRED`, `CANCELLED`, `FAILED`, and `RECONCILIATION_REQUIRED`. Every transition requires a documented actor and preconditions. A generic conversational acceptance is **not** a legal or technical commitment.

## Food wholesale artifacts

- `request_for_quote`: product queries or SKU refs, quantities, delivery requirements, buyer reference.
- `quote`: supplier identity, buyer eligibility, line items, units, prices/currency, tax treatment where relevant, delivery terms, stock assumptions, validity and version.
- `counterproposal`: references prior immutable quote version and specifies changed terms.
- `authorization_request` / `authorization_evidence`: exact artifact hash, scope, approver, policy version, expiry and decision.
- `order_commit_request`: exact accepted quote hash, approval refs, idempotency key, buyer/supplier identifiers.
- `order_confirmation`: external order ID, confirmed lines, status, supplier timestamp and provenance.
- `fulfilment_update`: shipment/delivery status and supporting references.

## Rules

1. All artifact types have stable versioned JSON Schemas and semantic validation.
2. Money uses decimal strings plus ISO currency codes; never binary float for binding prices.
3. Quantities use decimal strings and explicit units; define conversions in the domain module.
4. Every offer has a validity window and immutable content hash. Revised offers get a new version/hash.
5. All messages include correlation IDs; receiving agents maintain deduplication records.
6. Commit requires both parties' independent policy checks and configured human approvals against **the same exact terms**.
7. Commit APIs use idempotency keys and reconcile on ambiguous network failures.
8. Audit stores verified source identity and policy outcomes, not unsupported claims of legal enforceability.
9. For incompatible schema versions, reject explicitly or use a negotiated compatible version; never silently reinterpret fields.
10. Supplier stock/price checks are refreshed at the authorized commit boundary as required by supplier policy.

## Business-system adapter boundary (M7)

A fourth layer, distinct from A2A (transport), the business-artifact layer above, and MCP (tool-calling): the `SupplierBusinessSystemAdapter` Protocol (`packages/business-system-integration`) that `apps/supplier-mcp`'s tool implementations call to reach a real supplier ERP/API. This is never exposed over A2A or as a new artifact type — it is purely internal to the supplier side, behind the existing MCP tool surface. See `docs/ARCHITECTURE.md`'s M7 section and `docs/integrations/` for the Valio Aimo adapter contract and pilot-readiness package.

## Discovery

Agent Cards advertise skills and endpoint details. AIMO registry additionally tracks verified organization, capabilities, protocol/schema versions, trust state, relationship eligibility and endpoint verification. Registry listing is not consent to receive arbitrary transactions. Prefer capability discovery and signed/verified relationship grants over open anonymous order submission.

## Example skill IDs (application-level conventions)

`food.search_products`, `food.request_quote`, `food.counteroffer`, `food.submit_order`, `food.track_order`.

## Future domains

`construction.request_bid`, `construction.propose_milestones`, `automotive.check_fitment`, `logistics.request_freight_quote`. These are future schema modules. Financial execution requires separate legal, licensing and regulated-system controls.
