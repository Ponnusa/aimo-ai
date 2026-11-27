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

## Switching `supplier-mcp`'s business-system adapter (`mock` → `sandbox`)

`SUPPLIER_BUSINESS_SYSTEM_ADAPTER` selects which business-system backend
`supplier-mcp` calls for `execute_order`/`get_order_status`/catalogue reads.
`mock` is in-process and has no external dependency; `sandbox` calls the
real `supplier-sandbox-erp` service over Railway's private network with a
scoped `use_sandbox_erp` credential. Treat flipping this variable as a
deploy, not a configuration no-op — it changes which system durably records
orders.

**Before switching:**
1. Confirm `supplier-sandbox-erp` is deployed, migrated, and (if newly
   provisioned) seeded — see its own deployment/migration evidence.
2. Confirm `supplier-mcp` already has `SANDBOX_ERP_BASE_URL` (the private
   domain, `http://supplier-sandbox-erp.railway.internal:8007`) and
   `SANDBOX_ERP_SERVICE_CREDENTIAL` (scope `use_sandbox_erp`, organization
   matching `SUPPLIER_SANDBOX_ERP_ORGANIZATION_ID`) set correctly — setting
   these does not itself switch the adapter and is safe to do in advance.
3. Record the current live deployment ID before switching (`railway api`
   query on `service(id).serviceInstances.edges[].node.latestDeployment`)
   as the rollback point.

**Switch:** set `SUPPLIER_BUSINESS_SYSTEM_ADAPTER=sandbox`, trigger a new
deploy (env var changes require a fresh deploy to take effect — the running
container does not hot-reload settings).

**Verify through the real adapter path before trusting it** — not just
that `supplier-sandbox-erp` responds directly, but that `supplier-mcp`'s own
`execute_order`/`get_order_status`/`search_catalogue` tools work end to end
over the private connection: a catalogue read returns real seeded data, an
order call returns `confirmed` with a real `external_order_id`, replaying
the same `idempotency_key` returns the identical `external_order_id` rather
than creating a second order, and a delayed-ack order correctly reports
`pending` then reconciles to `confirmed` on its own the next time
`get_order_status` is called (no separate sync step) after the sandbox ERP
resolves it. Until this passes, treat ordinary order traffic through this
path as paused, not merely "proceed with caution."

**Rollback:** set `SUPPLIER_BUSINESS_SYSTEM_ADAPTER` back to `mock` (or
unset it — `mock` is the default) and trigger a new deploy. This is always
safe to do immediately and takes effect on the next deploy; it does **not**
retroactively undo anything the `sandbox` adapter already did against the
real ERP. If a check fails with orders already created or left pending
against the real ERP, reconcile or explicitly resolve those orders (via
`supplier-sandbox-erp`'s own order-status/fault-injection endpoints, or a
direct database read) **before** reverting the variable — reverting only
stops *new* calls from reaching the sandbox adapter; it has no effect on
rows already written.

**This switch has no bearing on `external_onboarding_enabled`** — they are
unrelated flags on different services. Confirm it separately if both are
being touched in the same session.

**Safety boundary, stated plainly:** nothing in `business_system_integration`'s
adapter code enforces that `SANDBOX_ERP_BASE_URL` points at a non-production
system — that boundary exists only because no production ERP URL or
credential has ever been configured in this environment, not because of a
technical safeguard. This switch procedure, and any authorization to perform
it, covers the sandbox ERP only and never extends to a real production
system or a real customer order.

## Deployment preflight: `supplier-agent`'s MCP execution credential

Found the hard way during a staging end-to-end trade: `supplier-agent`'s
`SUPPLIER_MCP_EXECUTION_CREDENTIAL` defaults to an empty string
(`apps/supplier-agent/src/supplier_agent/settings.py`). An empty credential
is correctly rejected by supplier-mcp, and `commitment.py`'s own fail-safe
design correctly treats that rejection as `RECONCILIATION_REQUIRED` rather
than a loud failure — right behavior for a genuinely unreachable business
system, wrong behavior for a service that was simply never given its
credential. The two look identical from the buyer's side and from ordinary
health checks; only the deploy operator can tell them apart, and only by
checking the configuration directly.

**Before shifting traffic to a new `supplier-agent` deploy**, run:

```
cd apps/supplier-agent
python scripts/preflight_check.py
```

It exits non-zero with a clear message on stderr if
`SUPPLIER_MCP_EXECUTION_CREDENTIAL` is empty, and never prints the
credential's value either way. This is a deliberate release-step check, in
the same spirit as the migration procedure above — not something
`create_app()` itself enforces, since tests legitimately build an app
without this credential set for paths that never reach `execute_order`.

Wiring this into Railway's `preDeployCommand` was not attempted as part of
this check's addition: that field only accepts a single command with no
shell (see "What was tried and rejected" above), and `python
scripts/preflight_check.py` is a single command, so it may well work there
— left for a follow-up to actually verify live, rather than assumed.

## Deployment provenance: enforcing the preflight and the deployed SHA

Found the hard way, twice, in the same staging exercise: once an operator
is trusted to remember a manual step, they will eventually not. The
`SUPPLIER_MCP_EXECUTION_CREDENTIAL` gap above went unnoticed for an
unknown period because nothing enforced checking it; separately, a deploy
of `supplier-agent`/`buyer-agent` was triggered without Railway's
`latestCommit: true` flag and silently redeployed a stale commit instead
of the one just merged — caught only because an operator happened to
check the deployment's own recorded commit hash by hand afterward.
Neither failure is survivable by "remember to check" alone.

**`scripts/verify_deployment.py`** is the generic gate: given a Railway
service id, the exact git SHA the release is supposed to be, and a health
URL, it polls until the deployment reaches a terminal state, then fails
loudly (nonzero exit, clear message on stderr) unless *all three* hold:
the deployment's own recorded `commitHash` exactly matches the expected
SHA, its status is `SUCCESS`, and the health URL answers `200` (with a
short, bounded retry for the few seconds right after `SUCCESS` during
which Railway's own edge can still 502 before routing to the new
container — a real, directly observed timing gap, not assumed).

```
RAILWAY_TOKEN=... uv run python scripts/verify_deployment.py \
    --service-id <railway-service-id> \
    --expected-sha <full-40-char-git-sha> \
    --health-url <https://.../health>
```

**`scripts/release_supplier_agent.py`** composes this with the preflight
above into one enforced sequence for `supplier-agent` specifically — the
preflight and the SHA/health check are both steps the release command
itself runs, not two separate things an operator has to remember:

1. Run `apps/supplier-agent/scripts/preflight_check.py` against the
   service's own real environment (over `railway run`, which injects the
   real variables locally — the credential's value is never read by, or
   printed from, this orchestrating script). Stop here, before touching
   the deployment, if this fails.
2. Trigger a new deployment with `latestCommit: true` explicit (the flag
   omitted during the finding above).
3. Run `verify_deployment.py`'s check against the SHA this release
   claims to be shipping.

```
RAILWAY_TOKEN=... uv run python scripts/release_supplier_agent.py \
    --service-id <railway-service-id> \
    --environment-id <railway-environment-id> \
    --expected-sha <full-40-char-git-sha-you-intend-to-ship> \
    --health-url <https://.../health>
```

Any step failing stops the release with a nonzero exit; it never falls
through to a later step. Verified for real against the live staging
`supplier-agent` service (2026-11-27): a deliberately wrong `--expected-sha`
and a deliberately broken `--health-url` each failed with a distinct,
correct message, and the full chained sequence (preflight → deploy →
verify) succeeded end-to-end against the real deployment. `buyer-agent`
has no execution-credential preflight of its own (it never calls
`execute_order`), so only `verify_deployment.py` applies to it directly;
a `release_buyer_agent.py` wrapper with no preflight step is a
straightforward, not-yet-built follow-up if one becomes useful.

This still does not replace a human deciding to ship — it only makes the
two checks above impossible to accidentally skip once that decision is
made.

## Reconciling an uncertain prior order attempt

An order that reached `RECONCILIATION_REQUIRED` and was later resolved by a
*different*, independent commit attempt (a new transaction, not a retry of
the same one) is not automatically closed out just because the later
attempt succeeded — the earlier attempt's own row is a separate, still-open
question: did it ever reach the business system at all, and under what
idempotency key? Check `GET /management/orders?state=RECONCILIATION_REQUIRED`
(supplier-agent's own `manage_organization`-scoped credential) for any row
still in that state, and for each one, resolve it explicitly:

1. Look up its `idempotency_key` directly against the business system (the
   sandbox ERP's `GET /orders/{idempotency_key}`, over the private network
   or a deliberate, temporary, explicitly-authorized exposure — never
   assumed reachable from outside Railway's network).
2. If the business system has no record for that key, the attempt never
   reached it; nothing to reconcile against, but the row documents a real
   failed attempt and should not be deleted.
3. If the business system does have a record, confirm it matches what this
   row expected (content hash / lines), and that no *other* row in this
   service's own ledger claims the same `external_order_id` under a
   different idempotency key — that would indicate a real duplicate, not
   mere uncertainty.

A later, successful, independently-idempotency-keyed commit for the same
negotiation does not retroactively prove the earlier attempt was harmless
— it only proves the later one worked.
