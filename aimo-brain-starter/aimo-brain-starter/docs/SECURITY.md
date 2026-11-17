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
