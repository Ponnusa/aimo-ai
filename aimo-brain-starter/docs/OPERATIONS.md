# Operations: release-step database migrations

Every service that owns a schema (`network-api`, `registry-api`, `buyer-agent`,
`supplier-agent`, `buyer-mcp`, `supplier-mcp`, `supplier-sandbox-erp`) ships its
own Alembic migrations under `apps/<service>/migrations/`. This document states
the one way those migrations are applied to a real (non-CI) database, and why.

## The rule

**A schema migration is a single, deliberate release step — run once, by an
operator (human or automation acting with explicit authorization), using that
database's owner-privileged credential. It is never run automatically by an
application process at startup, and never run by more than one instance per
deploy.**

Two reasons, both load-bearing:

1. **Least-privilege runtime credentials can't run it anyway.** Each service's
   running application connects to its database with a role scoped to
   `read_write` DML only (`SELECT`/`INSERT`/`UPDATE`/`DELETE`) on that one
   database — it holds no `CREATE`/`ALTER`/`DROP` privilege. A migration that
   adds a column or table structurally cannot run under that role. Migrations
   require the database's owner role, which the running application process
   never holds and never needs to.
2. **Every replica racing its own migration at startup is a correctness
   hazard**, not just a performance one: concurrent `CREATE TABLE`/`ALTER
   TABLE` from multiple booting instances of the same service is exactly the
   kind of race Alembic's own locking doesn't fully protect against under
   every deployment platform, and it makes "which replica actually migrated,
   and when" unanswerable after the fact. A migration that runs as its own
   explicit step, once, before any replica of the new release takes traffic,
   has none of that ambiguity.

## What was tried and rejected, and why

Railway's `preDeployCommand` (a per-deploy, run-once-before-traffic hook) was
the first candidate for automating this, since it matches requirement 2 above
structurally. It was rejected after direct testing: the field accepts a single
command string, invoked with **no shell** — `echo hello` runs, but any shell
syntax (`cd x && ...`, env-var substitution, quoting) fails silently with no
build or deploy log output at all, and the array form that would let a shell
be invoked explicitly (`["sh", "-c", "..."]`) is rejected by Railway's own
GraphQL input validation (`BAD_USER_INPUT`) despite being the type the schema
introspection advertises. This is a real, reproducible platform limitation,
not a one-off mistake — do not re-attempt it without first confirming Railway
has changed this behavior.

## The actual procedure

1. Confirm the target database's owner-role connection string (never the
   least-privilege app role) is available to whoever is running the
   migration — in this deployment, a separate, owner-role connection string
   is kept alongside (never instead of) each database's app-role one, used
   for nothing else.
2. From a machine with this repository and `uv` available:
   ```
   cd apps/<service>
   DATABASE_URL="<owner-role connection string>" uv run --project ../.. alembic upgrade head
   ```
   (the exact env var name varies by service — `DATABASE_URL` for
   `network-api`, `REGISTRY_DATABASE_URL` for `registry-api`,
   `BUYER_DATABASE_URL` for `buyer-agent`, etc. — match `migrations/env.py`
   for that service.)
3. Confirm `alembic current` reports the expected head revision before
   deploying the release that depends on it.
4. Only then trigger the deploy of the new application image. The
   least-privilege app role is sufficient for it to run against the
   now-migrated schema.

On Windows specifically: psycopg3's async mode requires a Selector event loop,
not Windows' default Proactor loop. Running Alembic directly hits this; it
does not affect the deployed service itself, which runs on Linux. Work around
it locally with:
```python
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from alembic.config import main

main(argv=["upgrade", "head"])
```

## Rollback / recovery

- Alembic migrations in this repository are written to be reversible where
  practical (per `CLAUDE.md`'s engineering bar) — `alembic downgrade -1`
  reverts the most recent revision using the same owner-role credential and
  the same manual, deliberate invocation as step 2 above. This is never
  automatic.
- A migration is a schema change; it is deliberately decoupled from an
  application deploy (step 4 happens only after step 3 succeeds). If a new
  release's migration succeeds but the application image itself then fails
  to deploy, the schema is left at the new revision and the previous
  application image (which must remain compatible with the new schema, or be
  explicitly reverted together with it) keeps serving traffic — Railway does
  not cut traffic to a new deployment until it reports healthy.
- If a migration itself fails partway, Alembic's transactional DDL (enabled
  for every migration in this project — see each `env.py`) means a single
  revision's change is all-or-nothing; there is no partially-applied
  revision to clean up. Re-run step 2 after fixing the cause.
- There is no automated recovery from a bad migration that already committed
  and was then built upon by further writes — that is an application-level
  data-recovery problem, not something this procedure can generalize a fix
  for. Treat it the same as any other production incident: restore from a
  Neon point-in-time snapshot if the blast radius requires it.
