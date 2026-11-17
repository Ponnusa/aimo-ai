# Architecture Decision Log

Record each important decision with date, context, alternatives, decision and consequences. Do not silently replace protocol or infrastructure choices.

## ADR-001 — Independent agents, shared open protocols

**Decision:** Use separate buyer and supplier services with official A2A for inter-agent communication and official MCP for tools. Keep core role-neutral and domain schemas modular.

**Rationale:** Preserves organizational autonomy, external interoperability and future vertical expansion.

**Tradeoff:** More service, identity and workflow complexity than a single-process demo.

## ADR-002 — Managed first, portable by design

**Decision:** Railway for initial container services, Neon for Postgres, Vercel for portal; avoid provider-specific domain logic.

**Tradeoff:** Cloud management is simpler initially, but isolation and self-hosted compatibility require explicit testing.

## ADR-003 — Deterministic binding authority

**Decision:** LLM proposes; policy services and authorized business systems commit. Every commit references immutable terms and an idempotency key.

**Tradeoff:** More approvals and state transitions, substantially lower unauthorized-transaction risk.
