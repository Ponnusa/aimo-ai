# Security and Authorization Requirements

## Threat model highlights

- Spoofed agents, forged Agent Cards, impersonated organizations.
- Malicious tool endpoints and SSRF through discovery/MCP URLs.
- Prompt injection from supplier descriptions, catalogue entries, A2A messages and external documents.
- Cross-tenant data leakage, overbroad service credentials, unauthorized order creation.
- Replay, duplicate commit, altered quote, expired offer, approval substitution and race conditions.
- Sensitive price/customer information in traces, logs or shared registry.
- Revoked relationship or permission continuing through stale cached credentials.

## Controls

- OIDC for human login, MFA for privileged roles, RBAC/ABAC on portal APIs.
- Agent/service identity with short-lived scoped tokens; validate issuer, audience, expiry and organization binding; support revocation/rotation.
- Verify ownership of endpoints and maintain a reviewed trust status; Agent Cards are discovery documents, not identity proof by themselves.
- Treat A2A and MCP content as untrusted; tool adapters enforce independent access control and strict input schemas.
- Separate buyer/supplier secrets, storage roles, policy scopes and logs; test cross-tenant denial.
- OPA policy decision on exact immutable terms immediately before each binding operation. No model-generated approval.
- Human approval binds to exact proposal hash, organization, action, max amount and expiry.
- Idempotency key on all external order writes; persist intent before network call, reconcile uncertain outcomes.
- Encrypt in transit and at rest; restrict outbound network destinations and redirect chains; apply request size and rate limits.
- Retain minimal audit metadata, redact PII/credentials and apply retention rules.
- Production integration credentials only after written partner permission and security review.

## Required security test cases

1. Buyer agent cannot query supplier private tables or call supplier privileged tools.
2. Agent cannot use a revoked relationship or expired credential.
3. Edited quote after approval cannot be committed.
4. Replayed commit cannot create duplicate order.
5. Supplier-supplied prompt injection cannot bypass buyer budget or call unauthorized tools.
6. Forged Agent Card or unverified endpoint is rejected.
7. Malicious private-IP/metadata-service URL is blocked by discovery and MCP connection rules.
8. Failed/retried order submission resolves via reconciliation without silent duplication.
9. Logs and traces do not contain secrets or unnecessary customer-specific pricing.

## M5.1: per-artifact signature trust model and residual limitations

**What M5.1 actually verifies — every negotiation/authorization/
commitment artifact, not just M5's three.** `request_for_quote`,
`quote`, `counterproposal`, `accept_proposal`, `reject_proposal`,
`proposed_agreement`, `authorization_evidence`, `order_commit_request`
and `order_confirmation` all carry a `proof` object: a JWS whose signed
payload binds `iss`/`sub` (sender organization/agent), `aud` (intended
recipient organization), `neg`/`typ`/`ver`/`hash` (negotiation id,
artifact type, version, content_hash — redundant with, but checked
separately from, the digest below), `artifact_digest` (sha256 of the
canonicalized artifact content, so *any* field changed after signing is
detected, not just the ones named in explicit claims), and a `jti`
(unique per signed message, checked against a durable per-organization
table so the exact same signed bytes can never be replayed). The
receiver verifies, in order: signature validity against the issuer's
published JWKS -> digest match (tamper detection) -> explicit claim
match (scope/participant sanity) -> expiry -> replay -> the claimed
sender is a registered, `active` agent of the claimed organization with
an active trading relationship to the verifier
(`GET /agents/{id}/verify-sender`, registry-api) -> (once the
negotiation already has a recorded counterparty) the verified sender
matches it exactly. Only after all of this does any business handler
run. This closes the gap M4's ADR-010 left (payload-only sender checks)
for *every* artifact type, not only M5's authorization/commitment ones —
unlike M5, which signed only its own three new types.

**The cross-organization trust root is now asymmetric, not a shared
secret.** Each organization holds its own Ed25519 private key, generated
out of band (`scripts/generate_signing_key.py`) and never transmitted
anywhere. `network-api` publishes only the matching *public* JWKS
(`GET /organizations/{id}/jwks.json`) — it never receives or stores a
private key, so compromising `network-api`'s own data lets an attacker
read public keys, never forge a signature for an organization whose
private key they do not hold. This replaces M5's shared
`SERVICE_CREDENTIAL_SIGNING_SECRET` HMAC trust root for cross-organization
authentication entirely (the HMAC mechanism itself remains in use for
the *intra*-organization boundaries it was always appropriate for:
`use_buyer_tools`/`use_supplier_tools`/`execute_order`/`manage_agents`,
none of which ever cross an organization boundary).

**Residual limitation: organization-level, not per-agent, keys.** One
keypair signs for an entire organization; the JWT's `sub` claim
(org-vouched, not independently cryptographically bound) names which
agent used it. A compromised agent process can therefore sign on behalf
of its whole organization, not just itself. Acceptable while each
organization runs exactly one agent per role in this reference
deployment; per-agent keys are a natural extension, not yet built.

**Residual limitation: key distribution still depends on network-api's
availability and honesty for discovery, not forgery.** A verifier that
cannot reach `network-api` to fetch a JWKS fails closed (rejects the
message) rather than guessing — but `network-api` *could*, if it were
compromised, serve a wrong (not forged — it holds no private keys) JWKS
to misdirect verification toward an attacker's own public key paired
with an attacker-controlled `kid`. A real federated deployment would
have each organization publish its own JWKS from infrastructure it
controls (e.g. its own domain), with `network-api`/the registry acting
only as a directory pointing at it, not serving the key material
directly. Not yet built; `network-api` is currently both the directory
and the host.

**Human approval is simulated via a labeled development identity
adapter, not real OIDC/MFA.** `identity.dev_oidc` issues an ID-token-shaped
JWT (`iss = "dev-oidc:<organization_id>"` — structurally unmistakable
for a real provider) signed with the organization's own existing
Ed25519 key, carrying `sub = approver_id` and `roles: ["approver"]`.
`decide_approval` refuses every approval outright outside
`environment in {"local", "staging", "test"}` — there is no real
identity provider configured for production, and this is never silently
bypassed. An approval is bound to one exact `(negotiation_id,
content_hash)` pair; a revised proposal (different content_hash) always
requires a new approval, and an approval can never be reused across
hashes. There is still no human login, session, SSO or MFA behind any
of this — that remains M6/M7 scope.

**Policy fail-closed (unchanged from M5).** `packages/policy_engine.evaluate_policy`
turns every OPA failure mode (binary missing, timeout, non-zero exit,
unparseable or malformed output) into an explicit `allow=False`
decision — there is no code path where a policy outage is silently
treated as approval.

**Idempotent crash/retry recovery (M5.1, `apps/worker`).** A buyer-side
commit that crashes, times out, or returns an ambiguous
`RECONCILIATION_REQUIRED` outcome is retried by a role-parameterized
worker process, bounded by a durable per-row attempt counter, always
reusing the same idempotency key and re-invoking the exact same
idempotent entry point a live request uses. The actual duplicate-order
guard remains `supplier-mcp`'s own database UNIQUE constraint on
`idempotency_key`, regardless of how many times — or by which
process — a commit is retried.

10. Buyer and supplier each independently deny on their own policy,
    reaching no shared conclusion.
11. A proposal with an outstanding human approval cannot reach
    `AUTHORIZED`; approving a different content_hash than the current
    proposal has no effect on it.
12. Cross-organization/cross-agent impersonation on any negotiation or
    commitment artifact is rejected before the artifact's claimed
    sender is trusted at all — proven both by a key that was never
    registered for the claimed organization and by a real key signing
    while claiming a different organization.
13. A non-`execute_order`-scoped credential (including the read-only
    `use_supplier_tools` one) cannot invoke `execute_order` directly.
14. A replayed `order_commit_request` with the same idempotency key
    never creates a second mock order — enforced by a database UNIQUE
    constraint in `supplier-mcp`, not just application logic.
15. A literal resend of the exact same signed message (same `jti`) is
    rejected as a replay even within the signature's validity window;
    a legitimate retry (fresh signature, same artifact content) is not.
16. A message signed with a revoked key is rejected regardless of its
    stated expiry; a message signed with a newly rotated key is
    accepted once published, and the prior key remains valid only until
    explicitly revoked.
17. A tampered artifact (any field changed after signing, including
    price/quantity/recipient/agreement id) fails digest verification.
18. A crashed/timed-out commit is recovered by the worker to a
    `CONFIRMED` state without creating a duplicate order; a second
    recovery cycle against an already-resolved order is a no-op.

## M6: portal trust model and residual limitations

**Enterprise authentication.** The portal is a standard OIDC relying
party (Entra ID-compatible): it validates an ID token's signature
(against the IdP's own published JWKS), issuer, audience and expiry,
then resolves which AIMO BRAIN organization the signed-in human belongs
to from the token's `tid`/issuer claim alone — via an explicit, admin-set
mapping (`organizations.oidc_issuer`/`oidc_tenant_id`, network-api) — the
email domain is never consulted for this decision. An identity-provider
tenant with no mapping authenticates against the IdP successfully but
resolves to no portal session at all. A parallel development login
adapter (`PORTAL_ENVIRONMENT=local/staging/test` only, refused outright
in production, mirroring `identity.dev_oidc`) sets a session directly
from form fields with no IdP round trip, for local development and the
test suite.

**Sessions and roles.** The portal issues its own opaque, server-side
session (random id, `HttpOnly`/`SameSite=Lax` cookie, backed by the
portal's own database) — the browser never holds a claim the portal
trusts as authority. Organization role (`admin`/`operator`/`approver`/
`auditor`) is **never** cached on the session: every privileged page load
and server action re-queries network-api's `organization_members` table
fresh, so a role revoked mid-session stops granting access on the very
next request, not merely the next login.

**The human-approval trust chain, end to end.** The portal holds its own
Ed25519 keypair, published through network-api's existing per-organization
JWKS endpoints against a non-trading pseudo-organization (`org_aimo_portal`)
— the same mechanism every real organization's signing key already uses,
not a second one. Deciding an approval through the portal: (1) the portal
independently verifies the signed-in human's OIDC session and (2) a fresh
network-api lookup confirms they currently hold the `approver` role for
that approval's organization; only then does it (3) mint a ≤120-second
assertion bound to the exact `organization_id`/`negotiation_id`/
`content_hash` being decided, signed with its own key; the portal then (4)
calls the owning agent's new `/management/approvals/{id}/decide` endpoint
with that assertion. The agent's `decide_approval` (5) verifies the
assertion's signature against the portal's own published JWKS, (6) checks
every bound claim matches the approval it was asked to decide, (7) makes
its *own* fresh network-api lookup confirming the claimed approver still
actively holds the `approver` role — never trusting the assertion's own
role claim — and only then (8) records the decision. A portal assertion is
proof that "the portal says a verified human asked for this," never by
itself proof that the action is authorized; step 7 is what actually
authorizes it, independent of whether the portal's own check in step 2 was
ever run at all. This is why a role revoked between step 2 and step 7 (or
a compromised portal skipping step 2 entirely) still cannot produce an
honored decision.

**Dual control.** An organization may configure
`BUYER_REQUIRE_SECOND_APPROVER_ABOVE_AMOUNT` (and the supplier
equivalent); when an agreement's total exceeds it, one approver's decision
alone leaves the approval `PENDING` — a second, *distinct* approver must
also decide before evidence is finalized. The same approver deciding the
same approval twice is rejected by a database UNIQUE constraint
(`buyer_approval_decisions`/`supplier_approval_decisions`), which is also
the literal single-approver self-approval guard: today's approvals are
policy-generated, never person-initiated, so there is no separate
"proposer" identity to compare against, and none is fabricated.

**Residual limitation: the portal is a trusted backend, not merely a
relay.** It holds the network-api admin key and internal-service key
needed to mint credentials and resolve roles — compromising the portal's
own server process is equivalent to compromising an operator's own
backend tooling, not a negotiation or commitment participant's private
key. This mirrors every other M2–M5.1 service-credential-holding service
in this codebase; it is not a new category of risk, but it is the reason
step 7 above re-derives authorization from network-api rather than
trusting the portal's own claim.

**Residual limitation: agent management/policy activation reuse a single
`manage_organization` scope with an admin-only sentinel, not distinct
scopes per capability.** `policy/activate` and other admin-only portal
actions are gated by network-api only minting the fixed
`agent_id="portal-admin-action"` binding after the portal has itself
confirmed the caller holds the `admin` role — a reasonable scope for this
reference deployment's single back-office consumer, not a general-purpose
fine-grained authorization system.

**Unchanged from M5.1:** per-artifact JWS signing, replay protection, key
rotation/revocation and the registry's sender verification are untouched
by M6 — the portal never bypasses them, never writes an evidence/order row
directly, and never calls OPA itself; every state change goes through the
same `authorization.py`/`commitment.py` entry points an A2A-driven request
already uses.

19. An OIDC session from a tenant with no organization mapping cannot
    resolve to a portal session, even with a valid, signature-verified ID
    token from a real IdP.
20. A role revoked through the portal's member-management page stops
    granting access on the very next request — not the next login,
    cross-tenant or otherwise.
21. A cross-tenant/cross-agent portal approval assertion (wrong
    organization, wrong negotiation, wrong content_hash, or signed by an
    unregistered key) is rejected before any approval decision is
    recorded — proven both for a key never published for the claimed
    organization and for the portal's own key used with mismatched
    claims.
22. A portal approval assertion whose claimed approver no longer actively
    holds the `approver` role (per a fresh network-api lookup made at
    decide time, not a cached one) is rejected even if the assertion's own
    signature and claims are otherwise entirely valid.
23. Above a configured dual-control threshold, a single approver's
    decision cannot finalize an approval; a second, distinct approver can;
    the same approver deciding twice is rejected.
24. A non-admin session cannot activate a policy version pointer or
    reach any endpoint gated on the portal's admin-confirmed credential.

## M7: supplier business-system adapter trust model and residual limitations

**The adapter boundary is internal, authenticated, and never a new
trust root.** `apps/supplier-mcp`'s tool implementations call a
configured `business_system_integration.SupplierBusinessSystemAdapter`
instead of importing fictional mock functions directly, but nothing
about *who* is allowed to call `supplier-mcp`'s own tools changes: the
existing `use_supplier_tools`/`execute_order` scope split (M5) is
unchanged, and callers (`supplier_agent`) never see which adapter is
configured. The adapter's own call to the business system it wraps
(e.g. `apps/supplier-sandbox-erp`) is authenticated the same way every
other intra-organization service call in this codebase already is: a
network-api-minted, narrowly-scoped (`use_sandbox_erp`) HMAC service
credential, never a new cross-organization trust mechanism and never a
credential an agent process holds directly.

**Adapter failures are explicitly typed and never silently trusted.**
`AdapterUnavailableError` (unreachable/timeout) is mapped to
`RECONCILIATION_REQUIRED`, never `FAILED` — the same "timeout is not
proof of failure" rule M5.1 established for the buyer's own commit path
now holds at the adapter boundary too. `AdapterAuthenticationError`
fails closed immediately: no order row is left ambiguous, no retry is
attempted, because a rejected credential is a configuration/authorization
problem, not a transient condition. `AdapterMalformedResponseError`
(a response that doesn't fit the expected shape) is raised rather than
guessing at missing fields — proven directly against a deliberately
rogue HTTP server returning non-JSON, not only against a hypothetical.

**Idempotency remains layered exactly as M5/ADR-011 established.**
`supplier-mcp`'s own `supplier_mcp_orders` table (UNIQUE
`idempotency_key`) is still the actual durable idempotency authority,
checked *before* the adapter is ever called — the adapter's own
behavior underneath (even a genuinely different idempotency scheme, or
none at all) cannot cause a duplicate order to be recorded on AIMO's
side. `apps/supplier-sandbox-erp` additionally enforces its own
UNIQUE-constrained idempotency, proving the pattern holds one layer
further out, against a genuinely separate system/database — but this is
a defense-in-depth property of the sandbox, not something AIMO BRAIN
depends on a real supplier system to provide.

## M8 slice 1: registration identity trust model and residual limitations

**Endpoint control and private-key control are two distinct, sequentially
enforced proofs, never conflated.** `apps/registry-api`'s existing
endpoint-ownership challenge (M2, test case 6/7 above) proves a registrant
controls the URL named by its Agent Card; it proves nothing about a
signing identity. The new `signing_public_key_jwk` path proves a
registrant controls the Ed25519 private key matching a specific public
key, via a signature over a challenge bound to that exact purpose
(`purpose: "key-control"`), organization, agent, and key thumbprint — a
signature produced for one purpose is rejected if presented for the
other (test case 32 below), and neither proof is ever treated as
satisfying the other.

**Mandatory key-control for an "external"-classified organization is a
server-held fact, never a caller-selectable one.** Classification lives
in a table only an operator-held, distinct secret (`registry_admin_token`
— never derived from, and never interchangeable with,
`service_credential_signing_secret`) can write. An organization's own
`manage_agents` credential cannot classify itself, and no field in the
registration request body expresses classification at all.

**This mandatory-key-proof mechanism is not, by itself, a safe external-
onboarding authentication boundary.** `require_org_bearer`'s shared-HMAC
scheme still authenticates every registration call, and registry-api
still performs no independent check that the organization named in a
verified credential is the organization that credential was actually
meant for — that trust is placed entirely in `network-api`'s own
credential-issuance path, outside this slice's scope. **Real external
onboarding — an untrusted, independently-operated organization placing
its own registration calls — remains blocked on a stronger registry-
authentication mechanism than the current shared HMAC secret, which must
never be distributed to any organization.** See `docs/DECISIONS.md`
ADR-016's "Registry authentication boundary" section and `TASKS.md`'s M8
section for this stated as a named release gate, not an optional
hardening task.

**Registration and verification are both closed against the two
concurrency races that mattered.** Two simultaneous first-time key
registrations for the same agent, and two simultaneous verification
confirmations for the same agent, are each resolved by a conditional
`UPDATE` whose `WHERE` clause re-checks the precondition at the moment of
the write (test cases 30/31) — never by an unlocked read followed by an
unconditional write, which the review that preceded this revision found
would otherwise let a race through.

**Lifecycle transitions are enforced, not merely documented.** A
`revoked` agent cannot be resurrected by re-registration; a `suspended`
agent cannot become `active` except through an explicit `/reactivate`
call; `/reactivate` itself only succeeds from `suspended`, never from
`pending` (test cases 33–35) — see `docs/DECISIONS.md` ADR-016 for the
full transition table.

**Known, explicitly accepted gaps carried forward by this slice:**
key-reuse-across-agents detection is a plain query, not a race-safe CAS
(accepted — a race here is an audit-hygiene annoyance, not an identity
hijack); non-integer floating-point numbers in a canonicalized artifact
remain formally undefined (`docs/specs/jws-artifact-signatures.md`
section 2e); cross-language JWS interoperability is proven for Python ↔
Node.js only, not for any other language; this slice's migration and
concurrency-sensitive code have not yet been verified against real
PostgreSQL (pending an approved CI run, not claimed as done — see
ADR-016).

**Supplier-side reconciliation is real, not a documented gap.**
`apps/worker`'s supplier role (previously a no-op, per M5.1/ADR-012)
now retries `supplier_orders` rows stuck in `RECONCILIATION_REQUIRED`
past backoff, bounded by the same durable per-row attempt count as the
buyer role, by re-invoking
`supplier_agent.commitment.reconcile_pending_order` — which only ever
re-asks the adapter's order-status lookup, never re-submits an order.
Every reconciliation attempt (not just the latest) is recorded in a new
`supplier_order_reconciliation_events`/`buyer_order_reconciliation_events`
table, surfaced through the portal's integration-health view.

**Residual limitation: the sandbox ERP's failure modes are illustrative,
not representative of any real supplier system, let alone Valio Aimo
specifically.** `apps/supplier-sandbox-erp`'s transient-error/timeout/
delayed-acknowledgement behavior is deterministic and test-controlled
(an environment-gated `/admin/fault-injection` surface, refused outside
local/staging/test, mirroring `identity.dev_oidc`'s own gating) — it
proves AIMO BRAIN's commit/reconciliation path handles *a* realistic
set of ERP failure shapes correctly, not that it has been validated
against Valio Aimo's actual behavior, which remains unknown until
sandbox access is granted (see
`docs/integrations/valio-aimo-adapter-contract.md`).

**Residual limitation: the Valio Aimo adapter is unimplemented by
design, not merely incomplete.** `food_wholesale.adapters.valio_aimo.ValioAimoAdapter`
raises `NotImplementedError` on every method; a static architectural
test (`tests/contracts/test_database_isolation.py`) asserts it imports
no HTTP client at all, so it cannot regress into a real network call
without that test failing first. No Valio Aimo credential, endpoint or
production access exists anywhere in this codebase, per `CLAUDE.md`.

25. An adapter authentication failure (wrong organization, wrong scope,
    or an unregistered credential entirely) is rejected before any
    catalogue/price/order call succeeds, and never leaves a supplier
    order row in an ambiguous state.
26. A malformed response from the configured business system (invalid
    JSON, a response missing required fields) is rejected as
    `AdapterMalformedResponseError`, never partially trusted into a
    half-populated result.
27. A business-system timeout or transient unavailability during
    `execute_order` resolves to `RECONCILIATION_REQUIRED`, never
    `FAILED` — proven against a deliberately armed one-shot transient
    fault on the real sandbox ERP server, not merely asserted.
28. A delayed-acknowledgement ("pending") order is never reported as
    `"confirmed"` until the business system actually resolves it, and a
    duplicate submission with the same idempotency key during the
    pending window returns the original result rather than creating a
    second order.
29. `WORKER_ROLE=supplier` recovers a `RECONCILIATION_REQUIRED` order to
    its true resolved state (`CONFIRMED`/`FAILED`) without re-submitting
    it, and a second reconciliation cycle against an already-resolved
    order is a no-op.
30. Two concurrent first-time signing-key registrations for the same
    agent cannot both succeed — exactly one wins, the other receives a
    deterministic conflict, and the key is never silently overwritten.
31. Two concurrent verification confirmations for the same agent cannot
    both activate it — a verification challenge cannot be consumed more
    than once.
32. A signature over the endpoint-control challenge cannot be presented
    as proof of key control, and vice versa — the two proofs are bound
    to distinct, purpose-tagged challenges that are never
    interchangeable.
33. A `revoked` agent cannot be reactivated, directly or indirectly,
    through ordinary re-registration.
34. A `suspended` agent cannot become `active` through ordinary
    re-registration, and `/reactivate` only succeeds when the agent is
    currently `suspended` (never from `pending`).
35. An organization not explicitly classified "external" by a registry
    operator can register an agent without a signing key (unchanged
    M0-M7 behavior); an organization explicitly classified "external"
    cannot register an agent without one, and cannot classify itself.

## Found during a staging end-to-end exercise: unauthenticated challenge write

`buyer-agent`/`supplier-agent`'s own `PUT /internal/ownership-challenge` —
the operator-facing endpoint that tells this process what value to serve
at `/.well-known/aimo-brain-challenge` for registry-api's endpoint-
ownership fetch (M2) — had no authentication at all. The `GET` side is
correctly, deliberately public (registry-api, an unauthenticated third
party from this service's point of view, must be able to fetch it); the
gap was specifically that the **write** side was equally open, so any
unauthenticated caller on the internet could overwrite or clear what gets
served there at any time, including mid-verification. This did not let an
attacker forge a *valid* challenge response — registry-api's own
nonce+signature-bound challenge format (test case 32 above) still
protects that — but it is a real unauthenticated-write/availability gap
on this one step, found live rather than by inspection. Fixed by requiring
the same `manage_organization`-scoped credential the rest of each
service's management API already uses; see
`test_ownership_challenge_auth.py` in each app's test suite for the
authorization matrix (no credential, wrong organization's credential,
wrong scope, correct credential — and that the `GET` side remains
unauthenticated throughout).

Residual, explicitly not fixed as part of this: the stored challenge
value has no TTL of its own and is never cleared after a successful
verification — it is served indefinitely until the next `PUT` or process
restart. This is a minor information-exposure concern (it discloses a
past, no-longer-useful nonce and timestamp to anyone who asks), not a
security boundary — registry-api enforces its own challenge expiry
independently and never accepts a stale one. Left undone rather than
risking a retry bug under time pressure (a legitimate operator may need
to re-confirm after an unrelated failure, which needs the same value
served again).

## Supplier-agent delegated signing is supported and enforced at verification (sandbox-proven; not yet deployed)

Until now, `supplier-agent` signed its outbound artifacts directly with
its organization's root private key (ADR-017's "organization-level
attribution" / legacy path, `verify.ts`/the Python verifier's
direct-root-JWK branch) — the same material that also signs every
other agent's delegations and ultimately roots the organization's whole
JWKS. Any process holding it is trusted identically, with no per-agent
scope, and the only revocation lever is rotating the entire root key
(which this project has had to do once already, for an unrelated key-
corruption incident). `buyer-agent-ts` (the external TypeScript buyer)
already self-bootstraps a delegated key via `EXTERNAL_ONBOARDING_ENABLED`;
this was the first time it was proven for `supplier-agent` specifically.

**What this proves, precisely:** delegated supplier signing works, and
delegation is enforced at verification — every one of scope, audience,
expiry, and revocation, specifically for commitment-adjacent artifact
types (`authorization_evidence`, `order_commit_request`,
`order_confirmation`), which always force a fresh, uncached fetch of
both the delegation list and the signing JWKS rather than trusting a
cached result. **What this does not prove, and must not be read as:**
that the code structurally prevents `supplier-agent` from holding or
using its organization's root key. `SUPPLIER_SIGNING_PRIVATE_KEY_PEM`
(`apps/supplier-agent/src/supplier_agent/settings.py`) is a single,
generic setting — there is no second "root key" field, and nothing in
`sign_outbound_artifact` (`message_auth.py`) or anywhere else in this
service can tell a delegated key from a root key by inspecting it. A
deployment is "using delegated signing" only because an operator
chooses to put a delegated key's PEM there instead of the root key's —
an operator-enforced deployment requirement, not a code guarantee. This
distinction was raised directly by an independent review of this work
and is recorded here deliberately, not as a hedge.

The delegation model itself needed no new code — `message_integrity`'s
`build_delegation`/`verify_delegation_token`/`HttpDelegationResolver`,
network-api's `/organizations/{id}/delegations` publish/revoke
endpoints, and `scripts/issue_agent_delegation.py` are already fully
generic across buyer and supplier. What this work added:

- `tests/integration/a2a_server_harness.py`'s
  `generate_and_register_delegated_signing_key`/`revoke_delegation` —
  the delegated-signing equivalent of the existing root-key test helper,
  for building a real HTTP test harness around a delegated supplier.
- `tests/integration/test_supplier_delegated_signing_e2e.py` — an
  end-to-end acceptance suite proving: a full synthetic trade succeeds
  with supplier signing every outbound artifact under a dedicated
  delegated key (never the org root key, which the test never even
  sets as `SUPPLIER_SIGNING_PRIVATE_KEY_PEM`); verification correctly
  rejects an under-scoped delegation and a wrong-audience delegation;
  an already-expired delegation is rejected at publish time, and
  separately, a delegation that was valid when published but expires
  before a later verification is still rejected at verify time (proving
  the fresh-fetch property above actually matters, not only the
  earliest possible check); revocation takes effect immediately; and
  rotation (issuing a second delegation under the same root, revoking
  the first) continues signing successfully under the new key without
  ever touching the root key again.
- A genuine pre-existing bug found while building the expiry test case:
  `POST /organizations/{id}/delegations` caught only `DelegationError`
  around its token verification, but `verify_delegation_token` raises
  plain `InvalidSignatureError`/`ExpiredProofError` (siblings of
  `DelegationError` under the common `ProofError` base, not subclasses
  of it) for a malformed, already-expired, or issuing-root-key-
  unresolvable token. Those cases fell through as an unhandled 500
  instead of the same clean 400 every other rejection reason already
  got. Fixed by catching `ProofError` instead
  (`apps/network-api/src/network_api/app.py`).

**Residual, explicitly not done as part of this:** no supplier-agent
deployment uses this yet — `SUPPLIER_SIGNING_PRIVATE_KEY_PEM` in any
live environment still holds whatever key was deployed previously. This
was sandbox implementation and tests only, per the task's own scope; no
production access or deployment was authorized or attempted. Rolling it
out for real requires: issuing a delegation with
`scripts/issue_agent_delegation.py` scoped to supplier's actual outbound
artifact types (`quote`, `proposed_agreement`, `reject_proposal`,
`authorization_evidence`, `order_confirmation`), publishing it,
deploying the delegated key/kid as `SUPPLIER_SIGNING_PRIVATE_KEY_PEM`/
`SUPPLIER_SIGNING_KID`, and removing the root key from that
environment entirely rather than leaving it alongside — a separate,
explicitly authorized change.
