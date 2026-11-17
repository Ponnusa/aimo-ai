# Execution Checklist

Claude Code: check an item only after its implementation and verification are complete. Record commit/test evidence in PRs or milestone notes.

## M0 — COMPLETE (2026-11-17, see docs/DECISIONS.md ADR-004/005/006)

- [x] Scaffold Python uv workspace and separate services
  - `uv sync --all-packages` succeeds; `network-api`, `buyer-agent`, `supplier-agent`, `worker`, all `packages/*` and `industries/food-wholesale` install as independent workspace members.
  - Verified: `uv sync --all-packages` → 63 packages installed, 0 errors.
- [x] Scaffold Next.js portal and local Docker Compose
  - Next.js 16.4.6 / React 19 App Router shell at `apps/portal`; `docker-compose.yml` provisions Postgres (3 isolated databases via `infrastructure/docker/init-databases.sql`) and Redis.
  - Verified: `npm run lint`, `npm run typecheck`, `npm run build` all pass with 0 errors; `npm audit` reports 0 vulnerabilities.
- [x] Add `.env.example`, secrets policy and developer commands
  - `.env.example` lists all service URLs with fictional placeholder credentials; `.gitignore` excludes `.env`/`.env.*`; `README.md` documents the full local dev command sequence.
- [x] Add lint, type checks, pytest and GitHub Actions
  - `.github/workflows/ci.yml`: Python job runs ruff check/format, mypy, Postgres-backed Alembic migrations for all three services, and the full pytest suite; portal job runs eslint, tsc, and `next build`.
  - Verified locally: `uv run ruff check .` → "All checks passed!"; `uv run ruff format --check .` → "72 files already formatted"; `uv run mypy apps packages industries` → "Success: no issues found in 33 source files"; `uv run pytest -q` → "43 passed".
- [x] Add migrations, fake LLM and seeded fictional test data
  - Reversible Alembic migrations for `network-api` (organizations), `buyer-agent` (buyer_tasks) and `supplier-agent` (supplier_tasks); verified `alembic upgrade head` then `alembic downgrade base` succeeds for all three.
  - `agent_runtime.FakeLLM` is deterministic and is the only LLM provider implemented (`LLM_PROVIDER=fake` default); verified by `packages/agent-runtime/tests/test_llm.py` and both agents' `/health` endpoints reporting it live.
  - `network_api.seed.FICTIONAL_ORGANIZATIONS` + `food_wholesale.adapters.mock` (fictional catalogue + idempotent in-memory mock order API) provide M0's "mock organizations and fake LLM/tool adapters"; verified by `tests/integration/test_network_api_seed.py` and `industries/food-wholesale/tests/test_mock_adapters.py`, and by booting `network-api` live and curling `/health` and `/organizations`.
  - Architectural isolation test proving service boundaries (`tests/contracts/test_database_isolation.py`) passes: buyer-agent/supplier-agent/network-api each import none of the others' internals, each declares a distinct database env var, and `a2a-integration` has no agent-to-agent call path (no simulated A2A).

**Not implemented in M0, by design:** real A2A communication between buyer-agent and supplier-agent (M1); registry discovery/trust/revocation (M2); real MCP tool calls (M3); negotiation/durable trade protocol (M4); OPA policy/human approval/idempotent commit (M5); portal onboarding/approval UI (M6); any Valio Aimo integration (M7+). `identity`, `authorization`, `audit`, `a2a-integration`, `mcp-integration` and `worker` are installed, importable, and their functions deliberately raise `NotImplementedError` naming the milestone that implements them.

## M1 — COMPLETE (2026-11-18, see docs/DECISIONS.md ADR-007)

- [x] Buyer and Supplier deploy as separate A2A servers
  - Both agents' FastAPI apps mount the official `a2a-sdk` 1.3.0 routes (`add_a2a_routes_to_fastapi`); each boots independently with its own settings/database/LLM adapter, plus new `/ready` endpoints (DB connectivity check) alongside the existing `/health`.
  - Verified: booted both as genuinely separate OS processes (`uvicorn` on distinct ports, distinct SQLite files), each independently responding to `/health`/`/ready`/`/.well-known/agent-card.json`.
- [x] Valid Agent Cards and SDK-based request/response
  - Built from `a2a.types.AgentCard`/`AgentSkill` (never a hand-rolled JSON shape). Supplier exposes `food.check_product_availability`; buyer exposes `food.buyer_agent_status`. The buyer→supplier exchange (discovery → `product_availability_request` → mock-catalogue lookup → `product_availability_response`) uses the official A2A SDK client/server over **real HTTP**, never a direct Python function call — confirmed by running both agents as separate live processes and exercising the full exchange over sockets before writing the automated tests.
  - `industries/food-wholesale` owns the request/response JSON Schemas and the actual catalogue-lookup logic (`food_wholesale.availability`); both `AgentExecutor` subclasses only unwrap/rewrap A2A messages and never contain product/SKU logic themselves (requirement 8).
  - Verified: `tests/integration/test_a2a_exchange.py` — 5 tests, all against real uvicorn servers on real ports (`a2a_server_harness.py`): Agent Card discovery for both agents, full product-availability exchange, invalid-message handling (malformed payload → `TASK_STATE_FAILED`, not a crash), agent-unavailable (nothing listening → typed `AgentUnavailableError`), duplicate requests (two independent messages, same content, each succeeds — full business idempotency is explicit M5 scope, not claimed here).
- [x] Private task persistence and restart recovery
  - Each agent's own `a2a.server.tasks.DatabaseTaskStore` bound to its own engine/database is the real persistence mechanism; each agent also keeps its own `buyer_tasks`/`supplier_tasks` business task-log table (own Alembic migration, `0002_add_task_context_columns`) recording skill id, role and counterparty.
  - Verified: `tests/integration/test_task_persistence_restart.py` — persists a `Task` via one engine/store bound to a SQLite file, disposes it, opens a **completely fresh** engine/store against the same file, and recovers the task with its state intact; a never-created task id returns `None`, not an error.
- [x] Contract tests proving no shared private database access
  - `tests/contracts/test_database_isolation.py` extended: each service's settings file declares exactly one `*_DATABASE_URL` validation alias, and it is only ever its own (regex-verified, not just a `.count()` check — M1 legitimately added non-database settings like each agent's own id and the counterparty's public A2A URL for discovery).
  - `tests/security/test_buyer_supplier_data_isolation.py` (new): a task saved in the supplier's `DatabaseTaskStore` is provably absent from the buyer's — not denied, structurally not there, because the databases are different files/connections entirely. Same guarantee re-verified against the business-level `buyer_tasks`/`supplier_tasks` tables directly.

**Not verified:** each agent's `Dockerfile` and `infrastructure/railway/*.toml` exist (so each agent *can* run as a separate container/Railway service, per requirement 6) but the image build itself was never run with `docker build` — Docker is not installed in this environment. No Railway deployment was attempted (no account/credentials). Treat both as unverified scaffolding until exercised in CI or by a developer with Docker/Railway access.

**Verified test run:** `uv run pytest -q` → **57 passed** (43 M0 + 14 new M1 tests), run three times with no flakiness. `uv run ruff check .` → All checks passed. `uv run ruff format --check .` → 86 files already formatted. `uv run mypy apps packages industries` → Success, 39 source files. Alembic `upgrade head` → `downgrade base` reverified clean for buyer-agent and supplier-agent with the new `0002` revision.

**Not implemented in M1, by design:** real identity/credential verification (buyer/supplier agent ids are self-declared config, not cryptographically verified — M2); registry/discovery beyond direct Agent Card URL fetch (M2); MCP tool calls (M3 — the mock catalogue is called directly by the domain module, not yet exposed as an MCP tool); negotiation/quote/order lifecycle (M4); OPA policy enforcement and human approval (M5); any Valio Aimo integration (M7+).

## M2
- [ ] Organization, agent and capability registry
- [ ] Agent endpoint ownership verification
- [ ] Scoped agent authentication and trading relationships
- [ ] Discovery filters and revocation tests

## M3
- [ ] Buyer MCP tool server/client integration
- [ ] Supplier MCP tool server/client integration
- [ ] Mock catalogue, inventory, customer pricing and order API
- [ ] Authenticated tools, input validation and denial tests

## M4
- [ ] Versioned JSON Schemas and artifact validators
- [ ] RFQ, quote, counteroffer and negotiation state machine
- [ ] Durable inbox/outbox, retry, timeout and deduplication
- [ ] Expiry, schema mismatch and restart tests

## M5
- [ ] Independent OPA buyer and supplier policies
- [ ] Approval workflow bound to exact quote hash
- [ ] Idempotent order submission and reconciliation
- [ ] Adversarial security and negative-path tests

## M6
- [ ] Organization and agent onboarding UI
- [ ] Capability search and relationship UI
- [ ] Policy controls and human approval inbox
- [ ] Transaction timeline and audit viewer

## M7
- [ ] Confirm Valio Aimo authorization, access and pilot sponsor
- [ ] Build isolated Valio adapter against permitted sandbox
- [ ] Verify security, price/stock authority and failure handling
- [ ] Obtain partner sign-off before any production transaction

## M8
- [ ] External BYOA interoperability test
- [ ] Containerized self-hosted deployment guide
- [ ] Construction RFQ schema without core changes
- [ ] Load, disaster recovery and operational runbooks
