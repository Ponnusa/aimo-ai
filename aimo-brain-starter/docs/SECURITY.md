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
