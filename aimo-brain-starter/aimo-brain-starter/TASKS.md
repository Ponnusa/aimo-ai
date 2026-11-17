# Execution Checklist

Claude Code: check an item only after its implementation and verification are complete. Record commit/test evidence in PRs or milestone notes.

## M0
- [ ] Scaffold Python uv workspace and separate services
- [ ] Scaffold Next.js portal and local Docker Compose
- [ ] Add `.env.example`, secrets policy and developer commands
- [ ] Add lint, type checks, pytest and GitHub Actions
- [ ] Add migrations, fake LLM and seeded fictional test data

## M1
- [ ] Buyer and Supplier deploy as separate A2A servers
- [ ] Valid Agent Cards and SDK-based request/response
- [ ] Private task persistence and restart recovery
- [ ] Contract tests proving no shared private database access

## M2
- [ ] Organization, agent and capability registry
- [ ] Agent endpoint ownership verification
- [ ] Scoped agent authentication and trading relationships
- [ ] Discovery filters and revocation tests

## M3
- [ ] Buyer MCP tool server/client integration
- [ ] Supplier MCP tool server/client integration
- [ ] Mock catalogue, inventory, customer pricing and order API
- [ ] Authenticated tools, input validation and denial tests

## M4
- [ ] Versioned JSON Schemas and artifact validators
- [ ] RFQ, quote, counteroffer and negotiation state machine
- [ ] Durable inbox/outbox, retry, timeout and deduplication
- [ ] Expiry, schema mismatch and restart tests

## M5
- [ ] Independent OPA buyer and supplier policies
- [ ] Approval workflow bound to exact quote hash
- [ ] Idempotent order submission and reconciliation
- [ ] Adversarial security and negative-path tests

## M6
- [ ] Organization and agent onboarding UI
- [ ] Capability search and relationship UI
- [ ] Policy controls and human approval inbox
- [ ] Transaction timeline and audit viewer

## M7
- [ ] Confirm Valio Aimo authorization, access and pilot sponsor
- [ ] Build isolated Valio adapter against permitted sandbox
- [ ] Verify security, price/stock authority and failure handling
- [ ] Obtain partner sign-off before any production transaction

## M8
- [ ] External BYOA interoperability test
- [ ] Containerized self-hosted deployment guide
- [ ] Construction RFQ schema without core changes
- [ ] Load, disaster recovery and operational runbooks
