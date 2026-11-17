# AIMO BRAIN

**An open business agreement network for independently governed AI agents.**

AIMO BRAIN enables agents operated by different organizations to discover one another, exchange structured proposals, negotiate, obtain independent authorization, commit to business actions, and retain verifiable evidence. **Food wholesale is the first domain implementation, not a restriction of the core.**

## Current status

**Milestone M0 (clean foundation) is implemented and tested**: the `uv` workspace, independently-bootable `network-api`/`buyer-agent`/`supplier-agent`/`worker` services, shared packages, the `food-wholesale` domain module with mock catalogue/order adapters, a Next.js portal shell, Docker Compose dev dependencies, and CI all exist and pass automated checks — see `TASKS.md` for exact verification evidence. Everything from M1 onward (real A2A communication, registry/trust, MCP tools, negotiation, policy enforcement, portal UI, Valio Aimo integration) is **not yet implemented**; do not assume otherwise. No integration with Valio Aimo or its production systems is implied. Start with simulated data and obtain explicit permission before accessing any third-party systems.

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
5. Begin at **M0**: scaffold, local environment, CI, and an architectural test proving service isolation.

## Local development (M0)

M0 is implemented: a `uv` workspace with `network-api`, `buyer-agent`,
`supplier-agent`, `worker`, shared `packages/*`, the `industries/food-wholesale`
domain module, a Next.js portal shell, Docker Compose dev dependencies and
CI. Nothing beyond M0 is implemented — in particular, there is **no A2A
communication between agents yet** (that is M1).

```bash
# 1. Start local Postgres (3 isolated databases) and Redis
docker compose up -d

# 2. Copy env template and install Python dependencies
cp .env.example .env
uv sync --all-packages

# 3. Run migrations (one isolated database per service)
(cd apps/network-api && uv run --project ../.. alembic -c alembic.ini upgrade head)
(cd apps/buyer-agent && uv run --project ../.. alembic -c alembic.ini upgrade head)
(cd apps/supplier-agent && uv run --project ../.. alembic -c alembic.ini upgrade head)

# 4. Seed fictional organizations
uv run --project . python apps/network-api/src/network_api/seed.py

# 5. Run checks
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages industries
uv run pytest -v

# 6. Run a service locally, e.g.
uv run --project apps/network-api uvicorn network_api.app:app --reload --port 8000

# 7. Portal shell
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
