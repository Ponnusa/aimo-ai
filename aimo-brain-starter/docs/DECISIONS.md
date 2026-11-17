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

## ADR-007 — A2A SDK integration specifics (2026-11-18, M1)

**Context:** `a2a-sdk` 1.3.0 (latest on PyPI, released 2026-10-27) was installed and its real API introspected directly (constructors, field names, enum values) rather than taken on faith from third-party tutorials, several of which describe older pre-1.0 or stale APIs. Key, non-obvious findings:

- The SDK's core types (`AgentCard`, `AgentSkill`, `Message`, `Task`, …) are **protobuf messages** (`a2a_pb2.*`), not Pydantic models. `Message.metadata` is a protobuf `Struct`, which supports `__getitem__`/`__contains__`/`__setitem__` but **not** `.get()` — calling `.metadata.get(...)` raises `AttributeError: get` only inside the running task, as an opaque JSON-RPC `InternalError`. Both executors use an explicit `"key" in message.metadata` check instead.
- `TaskState`/`Role` are plain `int`-backed `EnumTypeWrapper`s: a `Task.status.state` field value is a bare `int`, not an object with `.name`; converting to a readable string requires `TaskState.Name(value)`.
- `a2a.server.tasks.DatabaseTaskStore(engine: AsyncEngine, ...)` takes a plain SQLAlchemy async engine directly — it fits this project's own `database.new_engine()` factory with zero adapter code, and is used as the real task-persistence backend for both agents, bound to each agent's own database.
- `DatabaseTaskStore`'s non-default `table_name` dynamically declares a new mapped class (`create_task_model`) against the SDK's own shared `DeclarativeBase`, every time it is constructed with a non-`"tasks"` name. Since `create_app()` can run more than once per process (every test that boots a fresh app does this), a non-default `table_name` causes `sqlalchemy.exc.InvalidRequestError: Table '...' is already defined`. Both agents use the **default** `table_name="tasks"` for this reason; it never collides with this project's own `buyer_tasks`/`supplier_tasks` business task-log tables.
- The Agent Card's `AgentInterface.url` **must exactly match** the address the server is actually reachable on — the A2A client builds its transport from the resolved card's interface URL, not from the base URL used for discovery. `supplier_a2a_url`/`buyer_a2a_url` settings are therefore load-bearing, not cosmetic, and must be set correctly in every deployment (Railway services, CI, local dev) to the address the service is actually bound to.
- JSON-RPC routes (`create_jsonrpc_routes`) are mounted at `/` (root), matching the Agent Card's interface URL being the bare base URL — not at a `/a2a` subpath, which would mismatch the card and cause every send to 404.

## ADR-008 — Registry-api as a separate service; service credentials; SSRF defense (2026-11-18, M2)

**Context:** `docs/ARCHITECTURE.md` originally described a single "Network API / Registry" box owning organizations, agents, capabilities, relationships and trust status together. For M2, the user explicitly directed splitting this into two independently deployable services — `network-api` (organization identity, trading relationships, authorization to manage an org's own agents) and `registry-api` (agent registration, Agent Card validation, capability discovery, endpoint-ownership verification, lifecycle) — sharing a Neon Postgres *instance* at most, never a database, schema, role or direct table access.

**Decision — service split:** `registry-api` is a new, independently deployable FastAPI service (`apps/registry-api`) with its own database (`aimo_registry` locally/in CI), its own Alembic chain, its own Dockerfile, and its own Railway config. It owns exactly three tables: `registry_agents`, `registry_agent_capabilities`, `registry_audit_events`. It does **not** store organization records or trading relationships — those stay in `network-api`, which registry-api reaches only over HTTP (`registry_api.network_client.has_active_relationship`), authenticated with a shared internal-service key, never a database connection. This is the one cross-service call in the whole system; it is read-only and it never proxies the A2A conversation itself — discovery returns the supplier's real A2A endpoint URL, and the buyer talks to it directly with the official SDK.

**Decision — service credentials, not a new identity provider:** "Authorization to manage organization-owned agents" is network-api's responsibility per the split above, but registry-api must verify that authorization on every write without calling network-api synchronously (availability coupling) or sharing a database. `packages/identity` (previously a stub raising `NotImplementedError` for exactly this milestone) now implements short-lived, HMAC-signed, scope-bound service credentials: `network-api` mints one (`POST /organizations/{id}/service-credentials`, protected by a bootstrap admin key — there is still no human OIDC/approval flow; that is M6/M7) scoped to one organization and the `manage_agents` action; `registry-api` verifies it locally with a shared signing secret (`SERVICE_CREDENTIAL_SIGNING_SECRET`, must match exactly across both services). Rotating that secret immediately invalidates every outstanding credential. This is explicitly not OIDC and not a long-lived API key — `identity.verify_human_token` still correctly raises `NotImplementedError` for human authentication, which is unrelated and still out of scope.

**Decision — SSRF defense for Agent Card fetch:** registry-api is the one service that fetches an operator-supplied URL (`a2a_card_url`, and later the same host's `/.well-known/aimo-brain-challenge` for ownership verification). `registry_api.card_fetch` enforces, on every request: only `http`/`https` schemes (unconditionally — never bypassed); all resolved IP addresses checked against private/loopback/link-local (covers the `169.254.169.254` cloud metadata endpoint)/multicast/reserved/unspecified ranges; no redirects followed at all (a 3xx response is simply not a valid card response, so a redirect to a disallowed target is never reached by a second request); a response-size cap. A **documented residual limitation**: the IP check happens at request time but the subsequent TCP connection is not pinned to that exact address, so a DNS answer that changes between the check and httpcore's own resolution (DNS rebinding) is not fully closed — acceptable for M2 given there is no real external registration target yet, and stated here rather than silently assumed away.

**Decision — explicit, isolated dev/test bypass, not a disabled check:** buyer-agent/supplier-agent run on `127.0.0.1` in every local/CI environment this repository has, which the IP-range check would otherwise reject outright. `RegistryApiSettings.registry_allow_private_network_card_targets` (env `REGISTRY_ALLOW_PRIVATE_NETWORK_CARD_TARGETS`) skips *only* the IP-range check, never the scheme check, and defaults to `false`. Local dev (`.env.example`) and CI set it `true` explicitly; it must never be set in a real deployment. `tests/integration/test_registry_discovery_e2e.py::test_malicious_scheme_card_url_is_rejected_before_any_registration` proves the scheme check still rejects `file://` even with this bypass enabled, over real HTTP.

**Decision — discovery semantics:** `GET /agents` returns only agents that are `active` (not `pending`/`suspended`/`revoked`) and only those whose organization has an active trading relationship with the requesting organization — except an organization may always discover its own agents regardless of relationship state (self-discovery never calls network-api at all). The discovery response (`DiscoveredAgentOut`) is a minimal projection — `agent_id`, `organization_id`, `display_name`, `status`, `capabilities`, `a2a_endpoint_url` — deliberately excluding the raw Agent Card payload, verification challenge, and any credential material, per `docs/SECURITY.md`'s "no private Agent Card metadata or credentials through discovery."

**Decision — Agent Card is not identity:** registering a card only proves "something answers at this URL with this skill shape." Registry-identity (which organization owns this `agent_id`, and whether it is `active`) is a separate, registry-asserted fact, recorded only after the challenge-based endpoint-ownership check (`POST .../verification/start` then `.../confirm`, re-fetching `/.well-known/aimo-brain-challenge` through the same SSRF-safe transport) passes. `revoked` is a terminal state — a revoked agent cannot be reactivated, only re-registered as a new pending record.

**Decision:** Pin `a2a-sdk[http-server,fastapi]==1.3.0` on `buyer-agent`/`supplier-agent` only (never on shared `packages/*`). Each agent's own `DatabaseTaskStore` (default table name) against its own database is the real restart-recovery mechanism; each agent additionally keeps a lightweight `buyer_tasks`/`supplier_tasks` business task-log table (own Alembic migration) recording skill id, role and counterparty for queryability without depending on the SDK's internal schema.

**Tradeoff:** Tying the correctness of discovery to an exact advertised URL match is more operationally fragile than a path-relative scheme would be, but it is the SDK's actual contract, not a choice this project made.
