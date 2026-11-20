# AIMO BRAIN

**An open business agreement network for independently governed AI agents.**

AIMO BRAIN enables agents operated by different organizations to discover one another, exchange structured proposals, negotiate, obtain independent authorization, commit to business actions, and retain verifiable evidence. **Food wholesale is the first domain implementation, not a restriction of the core.**

## Current status

**Milestones M0 through M6 are implemented and tested.** M0: the `uv` workspace, independently-bootable `network-api`/`buyer-agent`/`supplier-agent`/`worker` services, shared packages, the `food-wholesale` domain module with mock catalogue/order adapters, a Next.js portal shell, Docker Compose dev dependencies, and CI. M1: buyer-agent and supplier-agent each run as a genuinely separate process with their own official-SDK Agent Card, exchange a real `product_availability_request`/`product_availability_response` over HTTP (the official A2A SDK, never a simulated/direct-call exchange), and persist tasks via their own database-backed task store with proven restart recovery. M2: a new, independently deployable `registry-api` service handles agent registration, SSRF-safe Agent Card fetch/validation, capability-indexed discovery and endpoint-ownership verification/lifecycle; `network-api` gained trading relationships and short-lived scoped service-credential issuance (`packages/identity`, no longer a stub). The buyer now discovers the supplier through registry-api (never a hardcoded URL) and then talks to it directly over the official A2A SDK. M3: two new, independently deployable `buyer-mcp`/`supplier-mcp` services expose each organization's business tools (purchase requirements/budget/approvals; catalogue/inventory/pricing/delivery) over the official MCP SDK's Streamable HTTP transport, authenticated with network-api-issued scoped service credentials; neither agent imports a tool implementation directly anymore. M4: buyer-agent and supplier-agent negotiate a versioned, explicitly **NONBINDING** RFQ → quote → counteroffer → proposed-agreement sequence over the same direct A2A connection (no new service); each persists its own negotiation history, reusing M0's generic lifecycle state machine; a buyer whose MCP-sourced budget can't cover a quote sends a real counteroffer, which the supplier evaluates deterministically (never via an LLM) against its own margin floor. M5: both agents independently evaluate their own versioned OPA policy, exchange authorization evidence, create a durable human-approval request when required, and reach a real, idempotent `COMMITTED` state over the same A2A connection — `TradeState.AUTHORIZED` through `COMMITTED` are now reachable, gated on independent policy decisions, never a default. M5.1: every negotiation/authorization/commitment artifact (not just M5's three) carries a per-artifact JWS signature verified against the sending organization's own published Ed25519 key (`packages/message-integrity`), replacing the earlier shared-HMAC cross-organization trust root entirely; `apps/worker` became a real, role-parameterized crash/retry recovery process. M6: `apps/portal` is a real Next.js backend-for-frontend — standard OIDC enterprise login (Entra ID-compatible) with an environment-gated development adapter, organization membership/roles, agent management, a policy catalog/dry-run/portal-activatable version pointer, a human-approval queue whose decisions are independently re-verified by the deciding agent against both a portal-issued signed assertion and a fresh role check, negotiation monitoring, trading-relationship management and an audit/evidence view. See `TASKS.md` for exact verification evidence. M7 (authorized Valio Aimo integration pilot) and M8 (external interoperability/hardening) are **not yet implemented**; do not assume otherwise. No integration with Valio Aimo or its production systems is implied. Start with simulated data and obtain explicit permission before accessing any third-party systems.

## First demonstration

1. Run a Buyer Agent and Supplier Agent as separate processes/services with separate credentials and state.
2. Register the supplier's A2A Agent Card and discover it through a network registry.
3. Buyer sends a structured food-wholesale RFQ over A2A.
4. Supplier uses its own MCP catalogue/inventory tools and returns a versioned quote.
5. Buyer and supplier each enforce their own policies; human approval occurs if required.
6. An authorized order is submitted to a **mock** supplier order system, exactly once.
7. Both parties can recover after restart and inspect their own audit evidence.

## Preferred stack

| Concern | Choice |
|---|---|
| Portal | Next.js, TypeScript, Tailwind; Vercel |
| Backend and agents | Python 3.12+, FastAPI, Pydantic, uv; Railway |
| Agent reasoning | LangGraph, configurable LLM provider |
| Inter-agent protocol | Official A2A Python SDK; Agent Cards |
| Agent tools | Official MCP Python SDK; Streamable HTTP where remote |
| Persistence | Neon PostgreSQL, SQLAlchemy, Alembic |
| Async work | Redis + Celery (or an explicitly justified durable alternative) |
| Policy | Open Policy Agent (OPA), default-deny for binding actions |
| Identity | OIDC for humans; scoped service credentials and validated agent identities |
| Tracing | OpenTelemetry; optional Langfuse |
| Artifacts | S3-compatible storage, with signed access |
| Tests | pytest, pytest-asyncio, contract/e2e tests; Playwright for portal |
| CI | GitHub Actions |

**Avoid introducing Kubernetes, a vector database, or a blockchain without a demonstrated need.**

## Start here

1. Read [`CLAUDE.md`](CLAUDE.md) (instructions for Claude Code).
2. Read [`PROJECT.md`](PROJECT.md) (scope and acceptance criteria).
3. Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/PROTOCOLS.md`](docs/PROTOCOLS.md).
4. Follow [`docs/ROADMAP.md`](docs/ROADMAP.md) and update [`TASKS.md`](TASKS.md) as work progresses.
5. **M0** through **M4** are complete: scaffold, local environment, CI, service-isolation tests, a real A2A exchange between independent buyer/supplier agents, registry-mediated discovery with endpoint-ownership verification, MCP-based business tool integration for both agents, and nonbinding RFQ/quote/counteroffer negotiation to a proposed agreement. Continue at **M5** (independent policies and commitment).

## Local development (M0 + M1 + M2 + M3 + M4)

A `uv` workspace with `network-api`, `registry-api`, `buyer-agent`,
`supplier-agent`, `buyer-mcp`, `supplier-mcp`, `worker`, shared `packages/*`,
the `industries/food-wholesale` domain module, a Next.js portal shell,
Docker Compose dev dependencies and CI, a real official-A2A-SDK exchange
between buyer-agent and supplier-agent, registry-mediated discovery
(registry-api) gated by a trading relationship (network-api),
endpoint-ownership verification, MCP-based business tool integration
(buyer-mcp/supplier-mcp, official MCP SDK, Streamable HTTP, scoped
service credentials), and nonbinding RFQ/quote/counteroffer negotiation
to a proposed agreement (M4) over that same A2A connection. Nothing
beyond M4 is implemented — in particular, there is **no OPA policy
enforcement, human approval or binding commit yet** (M5+), and
organization identity itself is still self-declared config, not
OIDC-verified.

```bash
# 1. Start local Postgres (6 isolated databases) and Redis
docker compose up -d

# 2. Copy env template and install Python dependencies
cp .env.example .env
uv sync --all-packages

# 3. Run migrations (one isolated database per service)
(cd apps/network-api && uv run --project ../.. alembic -c alembic.ini upgrade head)
(cd apps/registry-api && uv run --project ../.. alembic -c alembic.ini upgrade head)
(cd apps/buyer-agent && uv run --project ../.. alembic -c alembic.ini upgrade head)
(cd apps/supplier-agent && uv run --project ../.. alembic -c alembic.ini upgrade head)
(cd apps/buyer-mcp && uv run --project ../.. alembic -c alembic.ini upgrade head)
(cd apps/supplier-mcp && uv run --project ../.. alembic -c alembic.ini upgrade head)

# 4. Seed fictional organizations
uv run --project . python apps/network-api/src/network_api/seed.py

# 5. Run checks
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages industries
uv run pytest -v

# 6. Run network-api and registry-api locally
uv run --project apps/network-api uvicorn network_api.app:app --reload --port 8000
uv run --project apps/registry-api uvicorn registry_api.app:app --reload --port 8003

# 7. Run buyer-mcp and supplier-mcp locally
uv run --project apps/buyer-mcp uvicorn buyer_mcp.app:app --reload --port 8004
uv run --project apps/supplier-mcp uvicorn supplier_mcp.app:app --reload --port 8005

# 8. Run both agents and exercise the real A2A exchange
#    (two separate terminals; env vars shown inline for clarity — a real
#    run also needs BUYER_MCP_SERVICE_CREDENTIAL/SUPPLIER_MCP_SERVICE_CREDENTIAL,
#    minted from network-api as shown in step 10)
BUYER_A2A_URL=http://127.0.0.1:8001 SUPPLIER_A2A_URL=http://127.0.0.1:8002 \
  uv run --project apps/buyer-agent uvicorn buyer_agent.app:app --port 8001
SUPPLIER_A2A_URL=http://127.0.0.1:8002 \
  uv run --project apps/supplier-agent uvicorn supplier_agent.app:app --port 8002
# then, from a third terminal, discover the supplier and send a
# product_availability_request — see buyer_agent.client.request_product_availability

# 9. Register and verify the supplier with registry-api, then discover it
#    through registry-api instead of a hardcoded URL — see
#    tests/integration/test_registry_discovery_e2e.py for the full
#    sequence (mint a service credential from network-api, register,
#    verify ownership, then buyer_agent.client.request_product_availability_via_registry)

# 10. Mint MCP-tool service credentials (scope "use_buyer_tools"/
#     "use_supplier_tools") the same way, then call buyer-mcp/supplier-mcp
#     directly over MCP — see tests/security/test_mcp_tool_security.py
#     and apps/{buyer,supplier}-mcp/tests/test_app.py for worked examples

# 11. Run the full nonbinding negotiation (RFQ -> quote -> counteroffer
#     -> proposed agreement, explicitly NONBINDING) — see
#     buyer_agent.client.negotiate_purchase_via_registry and
#     tests/integration/test_negotiation_e2e.py for the full sequence

# 12. Portal shell
cd apps/portal && npm install && npm run dev
```

## Deployment model

Initially, both agents may be hosted in separate Railway services managed by AIMO BRAIN. They must still be independently deployable and governed by different organizations. Later, either party may self-host or bring its own A2A-compatible agent. The portal is a management interface, not the agent runtime.

## Core principles

- Agent autonomy is **not** authority to bind an organization.
- A2A handles agent communication; MCP handles tool interoperability.
- AIMO BRAIN supplies registry, verification, business schemas, workflow evidence and authorization integration.
- No shared private agent database; no cross-organization access by default.
- External ERP, stock and pricing systems remain their own sources of truth.
- Business messages are typed, versioned, correlated, authenticated and replay-safe.
- Support direct agent-to-agent transport when possible; central routing is optional.

## Important external references

- [A2A specification](https://a2a-protocol.org/)
- [A2A Python SDK](https://github.com/a2aproject/a2a-python)
- [MCP specification](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [LangGraph](https://github.com/langchain-ai/langgraph)
- [OPA](https://github.com/open-policy-agent/opa)

Pin tested compatible versions during implementation; do not assume these upstream interfaces are immutable.
