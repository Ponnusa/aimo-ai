# The AIMO Trust Extension (v1)

ADR-019 section 6. This document describes the AIMO Trust Extension: a declarative, A2A-standard `AgentExtension` an agent's own Agent Card may carry, whose `uri` is:

```
https://aimo-brain.dev/extensions/trust/v1
```

and whose `params` conform to [`trust-extension.schema.json`](trust-extension.schema.json) in this directory.

## The one rule this whole document exists to state

**This descriptor is a declaration, never evidence.** It may say "I support X verification, and here is the endpoint that authoritatively answers it." It must never contain, and a verifier must never be built to accept, a field that says "X is currently true." There is no field in the schema named `verified`, `active`, or `authorized` — deliberately, so that implementing this extension correctly is not a matter of self-discipline but of the schema structurally not offering the shortcut.

A verifying party that reads this descriptor and then calls the authoritative source it names has used the extension correctly. A verifying party that reads this descriptor and treats its mere presence, or any field within it, as proof of anything has misused it — and per ADR-019, this is explicitly tested (acceptance test 12): the verification code path must always call the authoritative source and never short-circuit on a card's self-reported status.

## The four layers, and where each is actually answered (not asserted)

| Layer | What it actually proves | Where a verifier checks it (never: what this descriptor says) |
|---|---|---|
| **Discovery** | The endpoint exists and speaks A2A | Fetch the agent's own Agent Card directly |
| **Verified identity** | This agent controls this endpoint and this key | `registry-api`'s agent-status endpoint |
| **Signing delegation** | This key may sign this scope of artifact types for this organization | `network-api`'s delegations/introspection endpoints |
| **Permission to trade** | Counterparty *eligibility* only — never a specific transaction's authorization | `network-api`'s party/operator-only relationship-status endpoint |

Each row's right-hand column is the only place a verifier may get an actual answer. `verification_sources` in the descriptor names *which* endpoint to call for each layer — it is a directory entry, not a result.

## Two limits the descriptor must never paper over

- **Endpoint and key control establish control, not legal identity.** Verifying someone holds a given private key and controls a given URL is not a KYC or legal-identity determination. Nothing in this extension, or anywhere else in AIMO BRAIN, claims otherwise.
- **A trading relationship establishes eligibility, not per-transaction authorization.** A specific commitment additionally requires: the artifact's type is within the presented delegation's scope, a live (not stale-cached, for commitment-adjacent types) identity/delegation check, an OPA policy decision, any required human approval, and the commitment protocol's own two-round-trip semantics. The relationship check never substitutes for any of these.

## The `endpoint` field names an operation, never a trusted host

`verification_sources.*.endpoint` is a hint about *which operation to call for which layer* — it is never, by itself, a host a verifier may trust merely because a card named it. A malicious or compromised card can set any `endpoint` value, including one pointing at an attacker-controlled server that mimics the real response shape (e.g. echoing back `{"status": "active"}` for an identity check).

To use this extension correctly, a verifier MUST:

- Resolve each `verification_sources.*.endpoint` against its own independently configured, trusted AIMO base URL for that layer (e.g. an operator-configured `registry-api`/`network-api` origin held by the verifier itself) — **never** the literal host embedded in the descriptor's `endpoint` value. The `endpoint` field identifies the path/operation to invoke (discovery, identity, delegation, trading-relationship); the host is always the verifier's own trusted configuration, never the remote party's say-so.
- **Never forward any credential** (bearer token, admin credential, API key, etc.) to a host the descriptor itself supplied. A credential belongs only to the verifier's own pre-configured, trusted AIMO endpoint.
- **Never follow an HTTP redirect** from a trusted AIMO endpoint to an untrusted host and continue treating the response as authoritative. If a redirect target is not itself the verifier's own trusted host, the verifier must stop and treat the result as unverifiable — never silently follow it.

A verifier that dereferences `endpoint` as a literal host, or that forwards credentials to whatever host a card names, has misused this extension exactly as badly as one that trusts a self-reported `verified`/`active` field — the schema's refusal to offer a status flag does not, by itself, prevent this different mistake; only the verifier's own implementation can.

## The relationship-status endpoint's own access control is not bypassed by this descriptor

`verification_sources.trading_relationship` names an endpoint — it does not grant access to it. That endpoint (`GET /organizations/{organization_id}/relationship-status/{counterparty_organization_id}`) is itself access-controlled (ADR-019 section 6/8): answerable only to one of the two actual parties to the relationship, or an AIMO operator, authenticated exactly as every other organization-management action in this system (server-owned `auth_mode`, resolved per ADR-019 section 1). Publishing this descriptor does not, and cannot, open that endpoint to a third party — the descriptor's reader still has to clear the endpoint's own authorization before it answers anything.

## Freshness, restated not redefined

`freshness_policy` in the descriptor states the same bounded-cache and fail-closed rules that already govern every other verification path in this system (ADR-017/018/019) — a bounded TTL for ordinary checks, and a mandatory fresh, uncached check for commitment-adjacent artifact types. This extension does not invent a new freshness rule and does not let a publisher relax the real one by stating something more permissive here; a verifier's own configured policy, not the remote party's self-published descriptor, governs what it actually tolerates.

## Versioning

`schema_version` is this descriptor's own version, independent of `trade_protocol`'s artifact schema version (`docs/PROTOCOLS.md`) and independent of the JWS artifact-signature specification's version (`docs/specs/jws-artifact-signatures.md`). An unrecognized `schema_version` is a parse failure for a verifier, never a best-effort partial read.
