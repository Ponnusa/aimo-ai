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

## M4 — Negotiation and durable trade protocol

- Implement versioned food RFQ, quote, counterproposal and order artifacts with JSON Schemas.
- Persist lifecycle state, inbox/outbox, deduplication, expiry and retries.
- **Done when:** agents negotiate a partial-stock scenario, recover from interruption and reject stale/altered quotes.

## M5 — Independent policies and commitment

- OPA buyer/supplier policies; optional human approvals tied to immutable quote hash.
- Idempotent mock order submission and external confirmation reconciliation.
- **Done when:** positive, denied, expired, changed-after-approval and duplicate-commit tests all pass.

## M6 — Usable portal

- Organization/agent onboarding, registry search, trusted relationships, policy editor/controls, approval inbox and transaction timeline.
- **Done when:** user can configure and review a transaction without manually editing database records; agents still work with portal offline.

## M7 — Authorized Valio Aimo integration pilot

- Only after explicit access: map real catalogue, stock, customer-specific price and order APIs through supplier adapters.
- Agree security, data retention, pilot scope, manual approval and rollback with partner.
- **Done when:** sandbox tests and partner acceptance criteria pass; production is a separate approval decision.

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
