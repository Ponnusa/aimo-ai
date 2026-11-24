# Milestones and Definition of Done

Milestones are **ordered by dependencies**, not promised calendar dates. Each milestone ends with a repeatable demo, passing automated tests and updated documentation.

## M0 — Clean foundation — COMPLETE (2026-11-17)

- Create Python workspace (`uv`), Next.js portal shell, Docker Compose dev dependencies and CI.
- Establish app/package boundaries, environment templates, migrations and test conventions.
- Provide mock organizations and fake LLM/tool adapters.
- **Done when:** fresh clone can run checks and local services using documented commands; no production credentials required.
- **Done:** see `TASKS.md` M0 section and `docs/DECISIONS.md` ADR-004/005/006 for exact verification evidence.

## M1 — Independent A2A agents — COMPLETE (2026-11-18)

- Deploy Buyer and Supplier Agent as separate processes and Docker images.
- Publish valid Agent Cards; use official A2A SDK for task request/reply.
- Persist private task state and demonstrate restart recovery.
- **Done when:** an A2A request/response succeeds across independent services, including restart tests, with no shared private database access.
- **Done:** both agents run as genuinely separate processes with their own Agent Cards (`a2a-sdk` 1.3.0), exchange a real `product_availability_request`/`product_availability_response` over HTTP (official SDK client/server, no simulated A2A), persist tasks via their own `DatabaseTaskStore` against their own database with proven restart recovery, and pass extended database-isolation contract/security tests. Each agent has its own `Dockerfile` and Railway service config (`infrastructure/railway/*.toml`); the image **build itself was not verified** with `docker build` (Docker is not installed in this environment) — exercise it in CI or locally with Docker before relying on it for a real deployment. See `TASKS.md` M1 section and `docs/DECISIONS.md` ADR-007 for exact verification evidence.

## M2 — Identity, registry and discovery — COMPLETE (2026-11-18)

- Implement organization/agent records, endpoint ownership verification, capability search and relationship permissions.
- Authenticate services and reject untrusted/unauthorized agents.
- **Done when:** buyer discovers eligible supplier; invalid or revoked relationships fail closed.
- **Done:** `registry-api` (new, independently deployable service, own database, own Dockerfile/Railway config) owns agent registration, Agent Card fetch/validation over an SSRF-safe transport, capability indexing, endpoint-ownership verification (challenge/response), and lifecycle (pending/active/suspended/revoked). `network-api` owns organization identity, trading relationships, and short-lived scoped service-credential issuance (`packages/identity`, no longer a stub). Buyer discovers the supplier through registry-api (never a hardcoded URL) and then talks to it directly over the official A2A SDK — registry-api never proxies the conversation. Verified end-to-end, over real HTTP across four independently booted servers: discovery without a relationship, a suspended relationship, and a revoked agent each fail closed (return nothing); a malicious `file://` card URL is rejected before any record is stored. See `TASKS.md` M2 section and `docs/DECISIONS.md` ADR-008 for exact verification evidence.

## M3 — MCP and mock business systems — COMPLETE (2026-11-18)

- Supplier MCP tools: catalogue search, stock, customer pricing, draft order and submit mock order.
- Buyer MCP tools: requirements, budget and approvals.
- Tool permissions, timeouts, typed errors and trace propagation.
- **Done when:** both agents use MCP tools through separate authenticated boundaries; malicious tool calls are denied.
- **Done:** `buyer-mcp`/`supplier-mcp` (new, independently deployable services, own databases, own Dockerfiles/Railway configs) expose each organization's business tools over the official MCP SDK's Streamable HTTP transport. Authorization reuses M2's network-api-issued scoped service credentials (new `use_buyer_tools`/`use_supplier_tools` scopes); a buyer-scoped credential cannot call supplier-mcp and vice versa. Neither agent imports a tool implementation directly anymore — `supplier_agent.a2a_executor` now sources catalogue/inventory/price data over MCP, and `buyer_agent.client` fetches purchasing context over MCP before every registry-mediated request. All required security cases (unauthorized, cross-tenant, invalid parameters, expired credential, server unavailable, malicious/malformed response) verified over real HTTP. See `TASKS.md` M3 section and `docs/DECISIONS.md` ADR-009 for exact verification evidence.

## M4 — Negotiation and durable trade protocol — COMPLETE (2026-11-18)

- Implement versioned food RFQ, quote, counterproposal and order artifacts with JSON Schemas.
- Persist lifecycle state, inbox/outbox, deduplication, expiry and retries.
- **Done when:** agents negotiate a partial-stock scenario, recover from interruption and reject stale/altered quotes.
- **Done:** reused M0's `trade_protocol.TradeState` lifecycle rather than inventing a parallel one; added `trade_protocol.negotiation` (content hashing/canonicalization, transition guard) and `food_wholesale.negotiation` (RFQ/quote/counterproposal/accept/reject/proposed-agreement builders plus a deterministic, product-agnostic counteroffer-evaluation rule). `buyer-agent`/`supplier-agent` each gained their own negotiation/proposal tables (own database, own migration) and, on the supplier side, an inbound-artifact dedup cache for replay safety. Every state-mutating call enforces optimistic concurrency (exact proposal-version reference), expiry, and — found and fixed during this milestone — sender/organization binding (a negotiation_id is not a secret, so cross-tenant hijacking had to be checked explicitly, not assumed away). Verified end-to-end over six real HTTP servers: RFQ → quote → a genuine budget-driven counteroffer → supplier's deterministic evaluation → convergence on an identical, explicitly `NONBINDING` proposed agreement, independently persisted by both organizations, recoverable after a simulated restart. See `TASKS.md` M4 section and `docs/DECISIONS.md` ADR-010 for exact verification evidence.

## M5 — Independent policies and commitment — COMPLETE (2026-11-19)

- OPA buyer/supplier policies; optional human approvals tied to immutable quote hash.
- Idempotent mock order submission and external confirmation reconciliation.
- **Done when:** positive, denied, expired, changed-after-approval and duplicate-commit tests all pass.
- **Done:** `packages/policy-engine` (new) wraps the real `opa` CLI as a bounded subprocess (deny-by-default on any failure); `industries/food-wholesale/policies/{buyer,supplier}/v1/` hold the actual rego bundles/business data. `packages/identity` credentials gained `agent_id`/`approver_id` binding and two new scopes (`negotiate`/`commit`) plus `approve`/`execute_order`; `authorization_evidence`/`order_commit_request`/`order_confirmation` artifacts carry a verified agent-scoped `commit` credential inside their payload (never trusting the unauthenticated sender fields alone, closing the M4 ADR-010 gap for these new message types specifically). `buyer-agent`/`supplier-agent` each gained their own `approvals`/`authorization_evidence`/`orders` tables (own migration `0004`) and a durable, hash-bound human-approval workflow. `supplier-mcp` gained a separately-scoped `execute_order`/`get_order_status` tool pair backed by its own UNIQUE-constrained `supplier_mcp_orders` table — the real idempotency authority for commits. See `TASKS.md` M5 section and `docs/DECISIONS.md` ADR-011 for exact verification evidence and explicitly stated limitations (shared-secret cross-org trust root, simulated human approval, M4 negotiation messages not retrofitted with credential checks, `apps/worker`'s scheduled dispatcher still a scaffold).

## M5.1 — Cross-organization trust and security hardening — COMPLETE (2026-11-20)

- Authenticate and authorize every negotiation/authorization/commitment artifact; bind the sender to its registered agent, organization and trading relationship.
- Replace the shared cross-organization HMAC secret with independently verifiable, asymmetric per-organization identities.
- Remove raw bearer credentials from persistent A2A business artifacts.
- Authenticated human approval identity with a clearly labeled, environment-gated development adapter.
- Harden authorization evidence to exact hashes, participants, action, expiry and replay identifiers.
- Durable crash/retry recovery for pending commitments via a real worker.
- **Done when:** impersonation, tampering, replay, key rotation, revocation, cross-tenant authorization, stale-approval and worker-recovery tests all pass.
- **Done:** `packages/message-integrity` (new) signs all nine negotiation/authorization/commitment artifact types with a per-organization Ed25519 key and verifies them against that organization's published JWKS (`network-api`'s `GET /organizations/{id}/jwks.json`, public keys only, never a private one) before any business handler runs — replacing M5's shared `SERVICE_CREDENTIAL_SIGNING_SECRET` HMAC trust root for cross-organization authentication entirely. `registry-api` gained a direction-agnostic `GET /agents/{id}/verify-sender` so either side can confirm a verified sender is a registered, active agent of its claimed organization with an active trading relationship (M2's `/agents` discovery endpoint assumed the requester was always the buyer). Human approval now runs through `identity.dev_oidc`, a structurally unmistakable, environment-gated development identity adapter (`local`/`staging`/`test` only). `apps/worker` is real for the first time — a role-parameterized (`WORKER_ROLE=buyer|supplier`) process that retries a crashed/ambiguous buyer-side commit by re-invoking the same idempotent entry point a live request already uses, bounded by a durable per-row attempt count. CI's `actions/checkout`/`actions/setup-node` bumped to v5 to resolve Node.js 20 deprecation warnings. See `TASKS.md` M5.1 section and `docs/DECISIONS.md` ADR-012 for exact verification evidence and explicitly stated residual limitations (organization-level, not per-agent, keys; `network-api` still a single point for key-distribution honesty, not forgery; simulated human approval).

## M6 — Organization Control Center and Human Approval Portal — COMPLETE (2026-11-20)

- Real OIDC enterprise authentication (Entra ID-compatible), organization membership/roles, agent management, policy catalog/dry-run/activation, human approval queue, negotiation monitoring, trading relationships, audit/evidence.
- **Done when:** user can configure and review a transaction without manually editing database records; agents still work with portal offline.
- **Done:** `apps/portal` is a real Next.js backend-for-frontend (server-held secrets only, no NextAuth/Auth.js black box): standard OIDC code+PKCE login with signature/issuer/audience/expiry validation and tenant-to-organization mapping (never an email domain), plus an environment-gated development login adapter mirroring `identity.dev_oidc`. `network-api` gained `organization_members` (admin/operator/approver/auditor roles, resolved fresh on every privileged request, never session-cached) and OIDC tenant mapping. `buyer-agent`/`supplier-agent` each gained an additive `/management` API (new `manage_organization` service-credential scope) exposing negotiations/approvals/evidence/orders and a `decide_approval` surface that now also accepts a portal-issued, Ed25519-signed approval assertion (`identity.portal_approval`) — independently revalidated by the agent against network-api's own membership records before any decision is honored, never trusted from the assertion's claims alone. Optional per-organization dual control (a second, distinct approver above a configured amount) and a portal-activatable, admin-gated policy-version pointer round out the authorization surface; agent suspension/revocation and trading-relationship management reuse M2's existing registry-api/network-api endpoints unchanged. See `TASKS.md` M6 section and `docs/DECISIONS.md` ADR-013 for exact verification evidence and residual limitations (portal is a trusted backend holding network-api's admin/internal keys, not a thin relay; organization-level not per-human signing keys remain M5.1's own stated limitation, unchanged).

## M7 — Supplier business-system integration and Valio Aimo pilot readiness — COMPLETE (2026-11-22)

- Generic, reusable supplier business-system adapter interface; realistic sandbox implementation; documented Valio Aimo boundary; portal integration visibility; partner-facing readiness package.
- Only after explicit access: map real catalogue, stock, customer-specific price and order APIs through supplier adapters.
- Agree security, data retention, pilot scope, manual approval and rollback with partner.
- **Done when:** sandbox tests and partner acceptance criteria pass; production is a separate approval decision.
- **Done:** `packages/business-system-integration` (new) defines `SupplierBusinessSystemAdapter`, an industry-independent `Protocol` (mirroring `agent_runtime.llm.LLMAdapter`'s pattern) covering catalogue search, product details, customer pricing, inventory/availability, delivery options, order submission and order status/reconciliation, with normalized decimal-string data shapes and a typed error hierarchy. `apps/supplier-mcp` resolves the configured adapter (`SUPPLIER_BUSINESS_SYSTEM_ADAPTER=mock|sandbox|valio_aimo`, default `mock` — unchanged M3-M6 behavior) via a small factory, never a second tool surface. A new standalone service, `apps/supplier-sandbox-erp` (own database, own Dockerfile/Railway config), behaves like a realistic external ERP — customer-specific prices, stock that decrements on confirmed orders, MOQ/substitution, a delivery-cutoff flag, delayed-acknowledgement orders, and a clearly-labeled, environment-gated fault-injection surface for deterministic transient-error/timeout testing — reached only over real HTTP by `food_wholesale.adapters.sandbox.SandboxErpAdapter`, with a network-api-minted `use_sandbox_erp`-scoped credential; a reusable adapter-contract test suite proves the mock and sandbox adapters are genuinely interchangeable. `food_wholesale.adapters.valio_aimo.ValioAimoAdapter` implements the same Protocol with every method raising `NotImplementedError` naming the exact missing access decision (`docs/integrations/valio-aimo-adapter-contract.md`) — a static test proves it makes no network calls at all. Supplier-side reconciliation is real for the first time (`apps/worker`'s `WORKER_ROLE=supplier`, previously a documented no-op per M5.1/ADR-012): it retries `RECONCILIATION_REQUIRED` orders by re-asking supplier-mcp's order-status tool, never re-submitting, recorded in new `supplier_order_reconciliation_events`/`buyer_order_reconciliation_events` history tables. The M6 portal gained an `/integration` page and three new `/management` endpoints (health, orders with attempts/last_error, reconciliation history) — no second portal. A partner-facing pilot-readiness package (`docs/integrations/valio-aimo-pilot-readiness.md`) covers architecture, required API capabilities, auth/security, data flow, integration options, sandbox testing, deployment, operational responsibilities and pilot acceptance criteria. See `TASKS.md` M7 section and `docs/DECISIONS.md` ADR-014 for exact verification evidence and explicitly stated limitations (no real Valio Aimo code anywhere; sandbox fault behavior is illustrative, not representative of any real supplier system).

## M8 — Interoperability and hardening

- Connect an independently authored A2A agent; validate published protocol compatibility.
- Add load tests, monitoring, disaster recovery, security review, schema migration compatibility and multi-domain example.
- **Done when:** external agent works without internal SDK, core unchanged for construction RFQ sample, and operational runbooks exist.

## Feature priorities

**P0:** A2A communication, agent identity, discovery, MCP tools, independent authorization, durable quote/order state, idempotency, audit.  
**P1:** portal, human approvals, enterprise integration adapters, observability, self-hosted packaging.  
**P2:** marketplace matching/ranking, analytics, advanced multi-party negotiation, additional verticals, monetization/billing.

## Explicit non-goals

Do not build a public marketplace, payment rails, financial trading, full ERP, blockchain identity, or generic vector-memory platform in early milestones.
