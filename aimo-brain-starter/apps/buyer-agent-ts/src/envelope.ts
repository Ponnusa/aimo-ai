// Mirrors industries/food-wholesale/src/food_wholesale/negotiation.py's
// `_envelope` helper exactly (field names, schema identifier shape) --
// ported from the published manifest's documented identifiers
// (docs/specs/external-integration-artifacts/manifest.json), never from
// the internal Python module.
import { randomUUID } from "node:crypto";

const SCHEMA_BASE = "https://schemas.aimobrain.example/food-wholesale";

export function buildEnvelope(opts: {
  transactionId: string;
  correlationId?: string;
  senderAgentId: string;
  recipientAgentId: string;
  organizationId: string;
  artifactType: string;
  version: number;
  proposalId: string;
  payload: Record<string, unknown>;
  validitySeconds: number;
  extra?: Record<string, unknown>;
}): Record<string, unknown> {
  const now = new Date();
  const expires = new Date(now.getTime() + opts.validitySeconds * 1000);
  return {
    schema: `${SCHEMA_BASE}/${opts.artifactType}/v0.1`,
    artifact_id: randomUUID(),
    transaction_id: opts.transactionId,
    correlation_id: opts.correlationId ?? randomUUID(),
    sender_agent_id: opts.senderAgentId,
    recipient_agent_id: opts.recipientAgentId,
    organization_id: opts.organizationId,
    domain: "food-wholesale",
    type: opts.artifactType,
    version: opts.version,
    proposal_id: opts.proposalId,
    created_at: now.toISOString(),
    expires_at: expires.toISOString(),
    payload: opts.payload,
    ...(opts.extra ?? {}),
  };
}
