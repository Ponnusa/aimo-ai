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

## ADR-004 — M0 workspace layout and package naming (2026-11-17)

**Context:** Needed a concrete `uv` workspace and Next.js layout matching `docs/ARCHITECTURE.md`'s proposed repository layout.

**Decision:** Each app/package directory uses a hyphenated PyPI-style name (`buyer-agent`, `agent-runtime`, …) but installs an underscored importable module (`buyer_agent`, `agent_runtime`, …), per Python packaging convention. `industries/food-wholesale` installs as `aimo-food-wholesale` / `food_wholesale` to avoid colliding with a generic `food_wholesale` name on a shared index later. Packages not yet implemented in M0 (`identity`, `authorization`, `audit`, `a2a-integration`, `mcp-integration`, `worker`) exist as installable, importable boundaries whose functions raise `NotImplementedError` with the milestone that implements them, rather than any stub that could be mistaken for working code.

**Tradeoff:** Slightly more pyproject.toml boilerplate than a single flat package; in exchange, every service boundary from `docs/ARCHITECTURE.md` is real and independently installable from day one, and contract tests (`tests/contracts/test_database_isolation.py`) can statically verify no forbidden cross-service import exists.

## ADR-005 — Portal pinned to Next.js 16, ESLint flat config (2026-11-17)

**Context:** Next.js 14.2.15 (the version originally planned) has multiple disclosed CVEs with no 14.x patch resolving all of them per `npm audit`; only Next.js 16.x closes them. Next.js 16 also removed the `next lint` subcommand in favor of running ESLint directly, and ESLint 9's default flat config (`eslint.config.mjs`) replaces `.eslintrc.json`.

**Decision:** Portal uses Next.js 16.4.6, React 19.1.0, ESLint 9.39.5 with `eslint.config.mjs` spreading `eslint-config-next`'s flat config, and the portal's `lint` script runs `eslint .` directly. `npm audit` reports 0 vulnerabilities at this pin.

**Tradeoff:** Ahead of the `README.md`/`CLAUDE.md` "Next.js/TypeScript" stack entry's implied version; no API used by the M0 placeholder page is affected, but any portal code added in M6 should target the App Router conventions current to Next.js 16, not 14.

## ADR-006 — SQLite for fast local/unit tests, Postgres service containers in CI (2026-11-17)

**Context:** `docs/ARCHITECTURE.md` specifies Postgres (Neon in production, local Postgres via Docker Compose for dev) as the only persistence target; M0 needs tests that run without Docker/Postgres being available everywhere a contributor works.

**Decision:** Unit/contract/integration tests under each app's `tests/` and the top-level `tests/` directory point `DATABASE_URL`/`BUYER_DATABASE_URL`/`SUPPLIER_DATABASE_URL` at per-service temp-file SQLite databases (never `:memory:`, which does not share state across connections) by default. CI additionally provisions three real Postgres databases in a service container and runs every service's Alembic migrations against them, so the actual production dialect is verified on every push, not just structurally plausible SQL.

**Tradeoff:** A contributor without Docker running only `uv run pytest` does not get Postgres-dialect proof for free; they get it from CI. `docker compose up -d` plus the migration commands in `README.md` give the same proof locally if Docker is available.
