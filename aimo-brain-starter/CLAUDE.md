# Claude Code — project instructions

You are the principal engineer for **AIMO BRAIN**, a production-oriented, open, cross-organization agent business network. Read `README.md`, `PROJECT.md`, `docs/ARCHITECTURE.md`, `docs/PROTOCOLS.md`, `docs/SECURITY.md`, `docs/ROADMAP.md`, and `TASKS.md` before making changes.

## Required workflow

1. Inspect the repository and report what exists; never pretend unimplemented features work.
2. Identify the current milestone and the smallest coherent vertical slice that satisfies its acceptance tests.
3. Write a brief implementation plan and list any external secrets or access needed. Proceed with local, simulated alternatives when possible.
4. Implement code, tests, migrations and documentation together. Run relevant checks and report exact results.
5. Update `TASKS.md` checkboxes only when tests verify completion. Add decisions to `docs/DECISIONS.md`.
6. Never access Valio Aimo production systems, scrape protected data, or submit live orders without explicit authorization and credentials.

## Non-negotiable architecture

- Buyer and Supplier are **roles**, not hardcoded network primitives. Core uses Organization, Agent, Capability, Relationship, Task, Proposal, Agreement, Authorization and Evidence.
- Separate deployable processes/services for buyer and supplier; separate identities, secrets, policy contexts and durable state. No direct database reads across organizational boundaries.
- Use the **official A2A SDK** for communication and Agent Cards; do not invent a proprietary A2A replacement.
- Use the **official MCP SDK** for agent-to-tool integration; tool calls require server-side authorization.
- LLM outputs are untrusted proposals. Never let an LLM bypass policy checks, approve its own binding action or hold unrestricted ERP credentials.
- Binding actions must verify the exact immutable proposal/version/hash, current authority, approvals, expiry and idempotency key; deny by default.
- The network registry provides discovery and verification metadata, not blanket permission to transact.
- Keep domain-specific food schemas under `industries/food-wholesale`; no SKU assumptions in core.
- Use an outbox/inbox or equivalent transactional mechanism for reliable messaging and recovery; don't claim exactly-once delivery. Ensure **effectively-once business effects** through idempotency and reconciliation.
- Design for cloud-managed, customer-hosted and bring-your-own-agent modes; implement managed mode first.

## Stack decisions

- Python 3.12+, uv, FastAPI, Pydantic, SQLAlchemy, Alembic, pytest.
- Official A2A Python SDK and MCP Python SDK, version-pinned after compatibility checks.
- LangGraph for stateful agent workflows; LLM adapter with a deterministic fake for tests.
- Neon Postgres, Redis/Celery as needed, OPA for binding policy checks.
- Next.js/TypeScript portal on Vercel; Railway for APIs/agents/workers.
- OpenTelemetry; structured redacted logs; GitHub Actions.

## Engineering quality bar

- Type checking, linting, formatting, tests and migration checks in CI.
- Contract tests between agents and protocol schema versions.
- Security tests for tenant isolation, authn/authz, expired quotes, replay, duplicate orders, revoked permissions, malicious prompts and tool abuse.
- Secrets via environment variables or secret manager, never committed.
- Migrations are reversible where practical; seed data is fictional.
- OpenAPI for platform APIs; JSON Schema for business artifacts.
- No user-visible claims of completed features until tested.

## Order of implementation

M0 foundation → M1 independent A2A agents → M2 registry/trust → M3 MCP tools → M4 trade workflow → M5 independent policies → M6 portal → M7 real authorized integrations → M8 hardening and external interoperability.

## When blocked

Document the blocker, implement a mock adapter with the same contract, and continue without inventing credentials or production access. Ask only for the missing decision that materially blocks progress.
