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

## M1
- [ ] Buyer and Supplier deploy as separate A2A servers
- [ ] Valid Agent Cards and SDK-based request/response
- [ ] Private task persistence and restart recovery
- [ ] Contract tests proving no shared private database access

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
