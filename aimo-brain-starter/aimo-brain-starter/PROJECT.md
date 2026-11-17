# AIMO BRAIN — Project Specification

**Status:** Initial engineering specification  
**Product:** Open business agreement network for autonomous agents  
**First vertical:** Food wholesale buyer–supplier transactions  
**Deployment:** Managed multi-organization cloud first; customer-hosted and BYOA supported architecturally

## 1. Problem

Organizations increasingly operate AI agents that can interpret needs and use internal tools, but cross-company business interactions still lack common discovery, trust, negotiation semantics, independent authorization, reliable commitment and audit. Existing procurement and ordering products often require both parties to adopt one platform.

## 2. Product thesis

Enable independently governed agents to find compatible counterparties and complete **authorized, verifiable business agreements** over open interoperability standards. AIMO BRAIN does not own the parties' business decisions, models, policies or ERP systems.

## 3. Actors

- **Organization administrator:** configures company, agents, credentials, relationships and policies.
- **Agent operator:** manages an agent runtime and capabilities; may be the organization or a managed hosting provider.
- **Business Agent:** autonomous reasoning and tool-using process with organization-scoped identity.
- **Human approver:** approves exact binding terms when required.
- **Network operator:** operates registry, optional relay and managed services, not unilateral authority over a customer's purchases.
- **External business system:** ERP, catalogue, inventory, purchasing, delivery or invoicing system.

## 4. Functional requirements

### FR-01 Organizations and identity
Register organizations; verify control of agent endpoints; authenticate humans with OIDC and services with short-lived, scoped credentials; support key rotation, revocation, and audit. Distinguish verified identity from approved trading relationship.

### FR-02 Agent registration and discovery
Register Agent Cards and versioned capabilities; query by capability, industry, service region and organization; validate cards and endpoint ownership; return verified metadata. Discovery must not grant transaction permission.

### FR-03 Independently hosted agents
Buyer and supplier must work as separate deployables, with distinct secrets, database permissions and policy services. Support a non-AIMO-hosted compatible agent through standard A2A without requiring the portal.

### FR-04 Agent reasoning and task orchestration
Use LangGraph or equivalent durable stateful orchestration; agents interpret tasks, plan tool calls and handle clarification/approval pauses. Provide model adapters and deterministic tests. Business authorization remains outside model inference.

### FR-05 Tool interoperability
Expose scoped MCP tools for product search, stock, customer price, order draft and order submission. Provide mock adapters first; production ERP integration requires explicit authorization. Tool calls must be authenticated, logged and bounded.

### FR-06 Trade lifecycle
Support `discover → request → propose → negotiate → authorize → commit → fulfil → verify`. Each step has explicit state, actor, correlation ID, schema version, deadlines and permitted transitions. Food wholesale adds RFQ, quote, substitutions, delivery terms and order confirmation.

### FR-07 Independent policy enforcement
Buyer enforces budgets, preferred suppliers and approvals; supplier enforces customer eligibility, price, stock and fulfillment constraints. Neither can override the other. Binding execution requires fresh policy decisions on immutable terms, human approval when configured, and revocation/expiry checks.

### FR-08 Reliable execution
Persist task transitions and messages; handle retries, deduplication, idempotency, timeouts, cancellation, failure recovery and reconciliation. Do not claim network delivery is exactly once. External order systems are authoritative for final order acceptance.

### FR-09 Evidence and audit
Record proposal versions, policy decisions, approvals, identities, timestamps, request/response references and external order IDs. Retain only data permitted by parties; redact sensitive fields; use append-only event semantics and integrity controls.

### FR-10 Management portal
Organizations, agents, capabilities, trusted relationships, policy configuration, approval inbox, transaction timeline and operational status. The portal must not be required for unattended agent-to-agent execution.

### FR-11 Multi-domain extensibility
Core must not contain food-specific product assumptions. Define domain schemas as separately versioned modules; demonstrate extension design with a construction RFQ schema without implementing construction operations.

## 5. Non-functional requirements

- **Security:** zero implicit cross-tenant access; least privilege; validated audience and issuer; SSRF protections for agent endpoints and MCP URLs; secret rotation; rate limits.
- **Reliability:** resumable tasks, idempotent business effects, observable failures and replay-safe messages.
- **Interoperability:** use upstream A2A/MCP protocols and publish business JSON Schemas and compatibility tests.
- **Portability:** agents deploy via containers; avoid provider-specific business logic.
- **Privacy:** minimize stored external data; support retention policies and deletion requests consistent with legal recordkeeping.
- **Observability:** traces with correlation IDs, policy outcome metrics, latency/error dashboards; redact prompts and credentials.
- **Performance:** establish baseline in M1 and set production SLOs with pilot partner; do not invent unsupported targets.

## 6. First reference transaction

A restaurant Buyer Agent needs 100 litres of lactose-free milk. It discovers an eligible Supplier Agent, sends an RFQ, receives a quote for 80 litres today and 20 tomorrow, requests an alternative, receives a revised quote, verifies budget and delivery rules, obtains approvals as necessary, and submits the exact approved order to a mock supplier API. Both parties record independent evidence; duplicates and restarts do not create duplicate orders.

## 7. Out of scope for initial delivery

Live payments, autonomous financial trading, custody, legal enforceability guarantees, universal cross-domain ontology, open public marketplace, automatic supplier onboarding, blockchain, and replacing Valio Aimo's ERP/webshop. No production transactions without partner agreement.

## 8. Success criteria

- Two separate agent services exchange real A2A tasks with no shared private state.
- Registry discovers a verified agent, and refuses untrusted or unauthorized counterparties.
- Supplier tools run through MCP and return typed results from mock business systems.
- Negotiation produces versioned quotes and rejects stale or altered terms.
- Buyer and supplier policies independently approve/deny; required human approval blocks execution until granted.
- Order submission uses idempotency and reconciles external confirmation.
- Recovery tests pass after process termination at critical workflow stages.
- A third-party A2A agent can participate using published specs and credentials, without importing internal AIMO runtime code.
- Food-domain schemas can be swapped for a construction RFQ example without modifying core abstractions.

## 9. Business validation

Before committing to production integrations, interview 5–10 buyers/suppliers and seek a pilot sponsor. For Valio Aimo, validate permitted API access, data protection, ownership of agent deployment, commercial approval process and success metrics. Their interest is **unconfirmed** until they agree.

## 10. Delivery plan

See `docs/ROADMAP.md` for milestones and `TASKS.md` for the executable checklist. All phases require tests, documentation and an explicit demo before sign-off.
