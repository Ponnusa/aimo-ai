# AIMO BRAIN

**An open business agreement network for independently governed AI agents.**

AIMO BRAIN enables agents operated by different organizations to discover one another, exchange structured proposals, negotiate, obtain independent authorization, commit to business actions, and retain verifiable evidence. **Food wholesale is the first domain implementation, not a restriction of the core.**

## Current status

**Milestones M0 (clean foundation), M1 (independent A2A agents) and M2 (identity, registry and discovery) are implemented and tested.** M0: the `uv` workspace, independently-bootable `network-api`/`buyer-agent`/`supplier-agent`/`worker` services, shared packages, the `food-wholesale` domain module with mock catalogue/order adapters, a Next.js portal shell, Docker Compose dev dependencies, and CI. M1: buyer-agent and supplier-agent each run as a genuinely separate process with their own official-SDK Agent Card, exchange a real `product_availability_request`/`product_availability_response` over HTTP (the official A2A SDK, never a simulated/direct-call exchange), and persist tasks via their own database-backed task store with proven restart recovery. M2: a new, independently deployable `registry-api` service handles agent registration, SSRF-safe Agent Card fetch/validation, capability-indexed discovery and endpoint-ownership verification/lifecycle; `network-api` gained trading relationships and short-lived scoped service-credential issuance (`packages/identity`, no longer a stub). The buyer now discovers the supplier through registry-api (never a hardcoded URL) and then talks to it directly over the official A2A SDK. See `TASKS.md` for exact verification evidence. Everything from M3 onward (MCP tools, negotiation, policy enforcement, portal UI, Valio Aimo integration) is **not yet implemented**; do not assume otherwise. No integration with Valio Aimo or its production systems is implied. Start with simulated data and obtain explicit permission before accessing any third-party systems.

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
5. **M0**, **M1** and **M2** are complete: scaffold, local environment, CI, service-isolation tests, a real A2A exchange between independent buyer/supplier agents, and registry-mediated discovery with endpoint-ownership verification. Continue at **M3** (MCP and mock business systems).

## Local development (M0 + M1 + M2)

A `uv` workspace with `network-api`, `registry-api`, `buyer-agent`,
`supplier-agent`, `worker`, shared `packages/*`, the `industries/food-wholesale`
domain module, a Next.js portal shell, Docker Compose dev dependencies and
CI, a real official-A2A-SDK exchange between buyer-agent and
supplier-agent, and registry-mediated discovery (registry-api) gated by a
trading relationship (network-api) and endpoint-ownership verification.
Nothing beyond M2 is implemented — in particular, there is **no MCP tool
integration and no negotiation/policy enforcement yet** (M3+), and
organization identity itself is still self-declared config, not
OIDC-verified.

```bash
# 1. Start local Postgres (4 isolated databases) and Redis
docker compose up -d

# 2. Copy env template and install Python dependencies
cp .env.example .env
uv sync --all-packages

# 3. Run migrations (one isolated database per service)
(cd apps/network-api && uv run --project ../.. alembic -c alembic.ini upgrade head)
(cd apps/registry-api && uv run --project ../.. alembic -c alembic.ini upgrade head)
(cd apps/buyer-agent && uv run --project ../.. alembic -c alembic.ini upgrade head)
(cd apps/supplier-agent && uv run --project ../.. alembic -c alembic.ini upgrade head)

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

# 7. Run both agents and exercise the real A2A exchange
#    (two separate terminals; env vars shown inline for clarity)
BUYER_A2A_URL=http://127.0.0.1:8001 SUPPLIER_A2A_URL=http://127.0.0.1:8002 \
  uv run --project apps/buyer-agent uvicorn buyer_agent.app:app --port 8001
SUPPLIER_A2A_URL=http://127.0.0.1:8002 \
  uv run --project apps/supplier-agent uvicorn supplier_agent.app:app --port 8002
# then, from a third terminal, discover the supplier and send a
# product_availability_request — see buyer_agent.client.request_product_availability

# 8. Register and verify the supplier with registry-api, then discover it
#    through registry-api instead of a hardcoded URL — see
#    tests/integration/test_registry_discovery_e2e.py for the full
#    sequence (mint a service credential from network-api, register,
#    verify ownership, then buyer_agent.client.request_product_availability_via_registry)

# 9. Portal shell
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
