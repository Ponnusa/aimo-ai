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

## M5: authorization/commitment trust model and residual limitations

**What M5 actually verifies.** `authorization_evidence`/`order_commit_request`/
`order_confirmation` artifacts carry the sender's agent-scoped `commit`
service credential (network-api issued, bound to one `organization_id`
and one `agent_id`) inside the payload itself. The receiver always
verifies this credential's signature, expiry, scope, organization and
agent binding *before* trusting the artifact's own `organization_id`/
`sender_agent_id` fields at all — closing the gap M4's ADR-010 left,
where only the unauthenticated payload fields were checked. A commit can
only proceed once an organization independently holds `allow=True`
evidence from *both* sides for the exact same `content_hash`
(`authorization.is_mutually_authorized`), each produced by that
organization's own, separately invoked OPA decision.

**Explicitly not covered by M5 — do not claim otherwise.** M4's
negotiation artifacts themselves (`request_for_quote`/`quote`/
`counterproposal`/`accept_proposal`/`reject_proposal`) still rely on the
unauthenticated payload fields the same way M4 left them; only the new
M5 authorization/commitment artifacts carry a verified credential. An
attacker can still waste a supplier's compute with a spoofed RFQ, but
cannot reach `AUTHORIZED` or `COMMITTED` without presenting a real,
agent-scoped credential. Retrofitting credential verification onto the
M4 negotiation messages too is a documented follow-up, not done here.

**The cross-organization credential trust root is a shared secret, not
independent PKI.** Every service in this deployment — network-api,
registry-api, both MCP servers, and now both agents' `commit`/`approve`
credentials — verifies HMAC signatures against one shared
`SERVICE_CREDENTIAL_SIGNING_SECRET`. This is deliberate for a
managed/single-deployment reference implementation (network-api is the
one network-wide identity authority both organizations already trust
per `PROJECT.md`'s "network operator" role), but it is **not** real
multi-organization federation: any party who learns the shared secret
can forge a credential for *any* organization. A production,
independently-hosted multi-organization deployment needs each
organization to hold its own cryptographic identity — per-organization
signed JWTs with a published JWKS, or mutual TLS client certificates —
with `network-api`/the registry acting as a trust anchor/directory,
never as the one party whose secret everyone shares. This is not yet
built and must not be described as production-grade verified business
identity.

**Human approval is simulated, not real OIDC/MFA.** An `approve`-scoped
credential binds to one pre-registered `approver_id`, issued the same
way as any other service credential (an operator mints it out of band
via network-api's admin-key-protected endpoint). There is no human
login, session, SSO or MFA behind it — that remains M6/M7 scope. An
approval is bound to one exact `(negotiation_id, content_hash)` pair; a
revised proposal (different content_hash) always requires a new
approval, and an approval can never be reused across hashes.

**Policy fail-closed.** `packages/policy_engine.evaluate_policy` turns
every OPA failure mode (binary missing, timeout, non-zero exit,
unparseable or malformed output) into an explicit `allow=False`
decision — there is no code path where a policy outage is silently
treated as approval.

10. Buyer and supplier each independently deny on their own policy,
    reaching no shared conclusion.
11. A proposal with an outstanding human approval cannot reach
    `AUTHORIZED`; approving a different content_hash than the current
    proposal has no effect on it.
12. Cross-organization/cross-agent credential spoofing on
    `authorization_evidence` or `order_commit_request` is rejected
    before the artifact's claimed sender is trusted at all.
13. A non-`execute_order`-scoped credential (including the read-only
    `use_supplier_tools` one) cannot invoke `execute_order` directly.
14. A replayed `order_commit_request` with the same idempotency key
    never creates a second mock order — enforced by a database UNIQUE
    constraint in `supplier-mcp`, not just application logic.
