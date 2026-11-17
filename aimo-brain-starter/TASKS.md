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

## M2 — COMPLETE (2026-11-18, see docs/DECISIONS.md ADR-008)

- [x] Organization, agent and capability registry
  - `apps/registry-api` (new, independently deployable service, own database `aimo_registry`, own Alembic chain, own Dockerfile/Railway config) owns `registry_agents`/`registry_agent_capabilities`/`registry_audit_events`. `network-api` gained `trading_relationships` (migration `0002`) per the user-directed responsibility split: network-api owns organization identity and relationships; registry-api owns agent registration/discovery. They never share a database or table; registry-api's one cross-service call (`registry_api.network_client.has_active_relationship`) is HTTP, internal-service-key authenticated, read-only.
  - Agent Card fetch uses the official A2A SDK (`A2ACardResolver`) over a custom SSRF-safe `httpx` transport (`registry_api.card_fetch`), never a hand-rolled HTTP client; capabilities are indexed from the card's real `skills` list.
  - Verified: `apps/registry-api/tests/test_app.py` (22 tests: registration, duplicate-agent-id-different-org conflict, re-registration resets to pending, capability indexing) and `apps/registry-api/tests/test_card_fetch.py` (10 tests).
- [x] Agent endpoint ownership verification
  - `POST /agents/{id}/verification/start` issues a random challenge; the agent's own operator (in this repo, the agent's own process) serves it at `/.well-known/aimo-brain-challenge` (new endpoint added to both `buyer_agent.app` and `supplier_agent.app`); `POST .../verification/confirm` re-fetches it through the same SSRF-safe transport and only then flips the agent to `active`. A mismatched or missing challenge stays `pending`, never silently "verified."
  - Verified: `test_full_verification_lifecycle`, `test_verification_confirm_rejects_mismatched_challenge` (unit, mocked fetch) and `tests/integration/test_registry_discovery_e2e.py::test_registry_discovery_then_direct_a2a_exchange` (real supplier-agent process serving a real challenge over real HTTP).
- [x] Scoped agent authentication and trading relationships
  - `packages/identity` (previously a stub raising `NotImplementedError` for this exact milestone) now implements short-lived, HMAC-signed, scope-bound service credentials (`issue_service_credential`/`verify_service_credential`). `network-api` mints one scoped to one organization and the `manage_agents` action; `registry-api` verifies it locally with a shared signing secret — no live call back to network-api for every write, no shared database.
  - `trading_relationships` (network-api) gates discovery: `registry-api` calls network-api's internal, key-authenticated `GET /relationships` before returning a cross-organization discovery match; same-organization self-discovery never needs a relationship.
  - Verified: `packages/identity/tests/test_tokens.py` (6 tests: valid round-trip, tampered payload, wrong secret, expired, wrong scope, malformed), `apps/network-api/tests/test_relationships_and_credentials.py` (5 tests).
- [x] Discovery filters and revocation tests
  - `GET /agents` filters by `requesting_organization_id` (relationship-gated) and optional `skill_id`; only `active` agents are returned, never `pending`/`suspended`/`revoked`. `revoked` is terminal — cannot be reactivated.
  - Verified end-to-end, over real HTTP, across four independently booted servers (network-api, registry-api, supplier-agent, buyer-agent) in `tests/integration/test_registry_discovery_e2e.py`: discovery without a relationship returns nothing; a suspended relationship immediately removes discoverability and reactivating restores it; a revoked agent is never discoverable; a malicious (`file://`) card URL is rejected before any record is stored, even with the local-dev SSRF bypass enabled.

**SSRF defense (explicit, tested requirement):** `registry_api.card_fetch` rejects non-`http(s)` schemes unconditionally and, by default, any hostname resolving to a private/loopback/link-local (covers `169.254.169.254`)/multicast/reserved/unspecified address; redirects are never followed. A documented, explicit, isolated local-dev/test override (`REGISTRY_ALLOW_PRIVATE_NETWORK_CARD_TARGETS`, default `false`) skips only the IP-range check, never the scheme check — proven by `test_malicious_scheme_card_url_is_rejected_before_any_registration` passing with that override enabled. Verified: `apps/registry-api/tests/test_card_fetch.py` (10 tests covering disallowed schemes, loopback, cloud-metadata address, private ranges, mixed-safe/unsafe resolution, the dev bypass, unresolvable hosts).

**Integration (buyer discovers supplier through registry-api, then talks directly):** `buyer_agent.registry_client.discover_supplier_via_registry` + `buyer_agent.client.request_product_availability_via_registry` replace the hardcoded `SUPPLIER_A2A_URL` lookup with a real HTTP call to registry-api; the A2A exchange itself (official SDK, real sockets) is unchanged from M1 and registry-api is not involved in it. The original hardcoded-URL path (`request_product_availability`) is preserved for agents outside AIMO BRAIN's own registry entirely (BYOA), per the explicit "preserve support for agents hosted outside AIMO BRAIN" requirement.

**Verified test run:** `uv run pytest -q` → **97 passed** (57 M0+M1 baseline plus this milestone's new/changed coverage: `packages/identity/tests` 7, `apps/network-api/tests` 7, `apps/registry-api/tests` 22 — 12 app-level + 10 SSRF/card-fetch, `tests/integration/test_registry_discovery_e2e.py` 5, `tests/contracts/test_database_isolation.py` 10), run twice with no flakiness. `uv run ruff check . --no-cache` → All checks passed (note: an earlier, stale `.ruff_cache` had silently hidden two real M0 import-sort violations during M1's final review — now fixed, and every `--no-cache` run since has been clean). `uv run ruff format --check .` → clean. `uv run mypy apps packages industries` → Success, 46 source files. Alembic `upgrade head` → `downgrade base` reverified clean for `registry-api` (new `0001`) and `network-api` (new `0002`) against SQLite; Postgres-dialect verification happens in CI (`.github/workflows/ci.yml`), which also now builds `registry-api`'s Docker image alongside buyer-agent/supplier-agent's.

**Not implemented in M2, by design:** MCP tool integrations (M3); negotiation/quote/order lifecycle (M4); OPA policy enforcement and human approval (M5); the management frontend (M6); any Valio Aimo integration (M7+). Organization identity itself (who a human represents) is still self-declared config, not OIDC-verified — that remains a portal/approval milestone (M6/M7); M2 only adds *service*-credential scoping for agent management, not human authentication.

## M3 — COMPLETE (2026-11-18, see docs/DECISIONS.md ADR-009)

- [x] Buyer MCP tool server/client integration
  - `apps/buyer-mcp` (new, independently deployable service, own database `aimo_buyer_mcp`, own Alembic chain, own Dockerfile/Railway config) exposes `get_purchase_requirements`/`get_budget`/`list_approved_suppliers`/`request_purchase_approval` as MCP tools over the official SDK's Streamable HTTP transport (`mcp==1.30.0`, introspected directly rather than taken from tutorials — see ADR-009).
  - `buyer_agent.mcp_client.fetch_purchasing_context` is the only place `buyer_agent` touches this data — it calls `buyer-mcp` via `mcp_integration.call_tool`, never a direct function import. `buyer_agent.client.request_product_availability_via_registry` calls it before discovery/send, matching the acceptance-test ordering.
  - Verified: `apps/buyer-mcp/tests/test_app.py` (7 tests, real HTTP against a real uvicorn server).
- [x] Supplier MCP tool server/client integration
  - `apps/supplier-mcp` (own database `aimo_supplier_mcp`) exposes `search_catalogue`/`check_inventory`/`get_customer_price`/`get_delivery_options`. `supplier_agent.mcp_client.fetch_availability_matches` is the only place `supplier_agent` touches catalogue/inventory/pricing data — it calls all three (catalogue, inventory, pricing) over MCP per requested line, enriching each match with live stock and the buyer's customer-specific price, then hands the result to `food_wholesale.availability.assemble_response_from_matches` to build/validate the existing `product_availability_response` envelope.
  - This replaces the M1-era shortcut where `supplier_agent.a2a_executor` called `food_wholesale.availability.handle_request`, which internally imported the mock catalogue directly. `tests/contracts/test_database_isolation.py` now statically asserts neither agent ever imports `food_wholesale.adapters` directly, and neither agent imports the other's MCP server's Python module.
  - Verified: `apps/supplier-mcp/tests/test_app.py` (8 tests, real HTTP), plus the existing M1 real-A2A tests (`tests/integration/test_a2a_exchange.py`, now booting a real supplier-mcp server too) continuing to pass end-to-end through the new MCP-sourced path.
- [x] Mock catalogue, inventory, customer pricing and order API
  - Fictional tool data lives in `industries/food-wholesale/adapters/mock/buyer_tools.py` (new) and `supplier_tools.py` (new, reuses the existing fictional catalogue); the MCP servers only wire these up as typed tools — per CLAUDE.md, no SKU/business assumptions in the generic `mcp_integration` package. `request_purchase_approval`/`evaluate_purchase_approval` return a deterministic mock decision only — not OPA, not a binding authorization (that is M5).
  - Verified: `industries/food-wholesale/tests/test_mock_tools.py` (16 tests).
- [x] Authenticated tools, input validation and denial tests
  - Authorization reuses M2's service-credential mechanism: `network-api`'s credential endpoint gained a `scope` field (`manage_agents`/`use_buyer_tools`/`use_supplier_tools`, allowlisted). Each MCP server deployment is configured with exactly one organization id and one required scope; `mcp_integration.server_auth.require_tool_scope` (shared by both servers) rejects a credential that doesn't match both — a buyer-scoped credential cannot call supplier-mcp even if it somehow had the right organization id, and vice versa.
  - Tool inputs are validated by the MCP SDK itself against each tool's auto-derived JSON Schema (plain typed parameters); tool outputs are explicit Pydantic models (never a raw dict), so a malformed/missing field fails schema validation rather than silently propagating.
  - Required security test cases — all verified over real HTTP against real servers in `tests/security/test_mcp_tool_security.py` (6 tests) and `apps/{buyer,supplier}-mcp/tests/test_app.py`: unauthorized tool call (missing/garbage credential), cross-tenant access (wrong org and/or wrong scope), invalid tool parameters (non-decimal amount), expired credential, MCP server unavailable (bounded retry, typed `McpServerUnavailableError`, never a hang), and a malicious/malformed tool response (a rogue MCP server returning a catalogue match missing required fields) — caught and re-raised as `McpMalformedResponseError`, never an opaque crash or a silently-wrong result.
  - Tool-invocation audit: both MCP servers record every call attempt (tool name, caller organization id if authorized, success/failure, non-secret detail) to their own `*_mcp_tool_invocations` table — the bearer credential itself is never logged.

**End-to-end acceptance test (`tests/integration/test_registry_discovery_e2e.py::test_registry_discovery_then_direct_a2a_exchange`, now extended):** buyer-agent discovers supplier-agent through registry-api, calls its own buyer-mcp for purchasing context, sends the A2A request; supplier-agent calls its own supplier-mcp (catalogue, inventory, pricing) and returns a structured A2A response; buyer-agent persists the result. Six independent real HTTP servers (network-api, registry-api, buyer-mcp, supplier-mcp, buyer-agent, supplier-agent), each its own uvicorn instance, own port, own SQLite file.

**Verified test run:** `uv run pytest -q` → **148 passed** (97 M0+M1+M2 baseline plus this milestone's new/changed coverage: `industries/food-wholesale/tests/test_mock_tools.py` 16, `apps/buyer-mcp/tests` 7, `apps/supplier-mcp/tests` 8, `packages/mcp-integration/tests` 9, `apps/network-api/tests` +2 (scope selection/rejection), `tests/security/test_mcp_tool_security.py` 6, `tests/contracts/test_database_isolation.py` +4), run twice with no flakiness. `uv run ruff check . --no-cache` → All checks passed. `uv run ruff format --check .` → clean. `uv run mypy apps packages industries` → Success, 62 source files. Alembic `upgrade head` → `downgrade base` reverified clean for `buyer-mcp`/`supplier-mcp` (new) against SQLite; Postgres-dialect verification happens in CI, which also builds `buyer-mcp`'s/`supplier-mcp`'s Docker images alongside the four existing services'.

**Not implemented in M3, by design:** negotiation/quote/counteroffer/order lifecycle (M4); OPA policy enforcement and human approval (M5) — `request_purchase_approval` is explicitly a mock decision, never consulted by any commit path; the management frontend (M6); any Valio Aimo integration (M7+). `mcp` 2.x (a recent, larger SDK restructure) was not adopted — `mcp==1.30.0` (the last 1.x release) was pinned instead, after introspecting both.

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
