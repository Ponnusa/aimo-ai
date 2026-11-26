// M8 slice 5 (ADR-020): the running TypeScript reference buyer agent.
// Holds ONLY: its own agent private key (never the organization root
// key -- see provision.ts), a minted ServiceCredential, and whatever
// OrgScopedCredential/config it was handed at setup. No AIMO Python
// package is imported, no database is touched, and no AIMO-internal
// secret (network_admin_api_key / network_operator_key /
// network_internal_service_key) is ever read.
import { createHash, randomUUID, sign as cryptoSign } from "node:crypto";
import { ExternalContractClient } from "./contractClient.js";
import { startChallengeServer } from "./challengeServer.js";
import { buildBuyerAgentCard } from "./agentCard.js";
import { privateKeyFromJwk, signArtifact } from "./jws.js";
import { connectToAgent, sendDataMessage } from "./a2aClient.js";
import { buildEnvelope } from "./envelope.js";
import { contentHash } from "./contentHash.js";
import { evaluateBuyerPolicy, type QuoteLine } from "./policy.js";
import { verifyInboundArtifact } from "./verify.js";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set.`);
  return value;
}

async function main(): Promise<void> {
  const registryApiUrl = requireEnv("REGISTRY_API_URL");
  const networkApiUrl = requireEnv("NETWORK_API_URL");
  const buyerOrganizationId = requireEnv("BUYER_ORGANIZATION_ID");
  const supplierOrganizationId = requireEnv("SUPPLIER_ORGANIZATION_ID");
  const orgScopedCredential = requireEnv("ORG_SCOPED_CREDENTIAL");
  const buyerAgentId = requireEnv("BUYER_AGENT_ID");
  const supplierA2aUrl = requireEnv("SUPPLIER_A2A_URL");
  const selfPort = Number.parseInt(requireEnv("BUYER_SELF_PORT"), 10);
  const agentPrivateJwk = JSON.parse(requireEnv("AGENT_PRIVATE_KEY_JWK"));
  const delegatedKid = requireEnv("DELEGATED_KID");

  const agentPrivateKey = privateKeyFromJwk(agentPrivateJwk);
  const client = new ExternalContractClient({ registryApiUrl, networkApiUrl });

  // --- Layer 2: mint own ServiceCredential, register, verify ----------
  const serviceCredential = await client.issueServiceCredential(buyerOrganizationId, orgScopedCredential);

  const challenge = startChallengeServer(selfPort, buildBuyerAgentCard(`http://127.0.0.1:${selfPort}`));
  try {
    await client.registerAgent(serviceCredential.token, {
      agentId: buyerAgentId,
      organizationId: buyerOrganizationId,
      a2aCardUrl: challenge.baseUrl,
      signingPublicKeyJwk: JSON.parse(requireEnv("AGENT_PUBLIC_KEY_JWK")),
    });

    const started = await client.startVerification(serviceCredential.token, buyerAgentId, buyerOrganizationId);
    challenge.setChallenge(started.endpoint_challenge);
    let keyProofBase64: string | undefined;
    if (started.key_challenge) {
      const signature = cryptoSign(null, Buffer.from(started.key_challenge, "utf8"), agentPrivateKey);
      keyProofBase64 = signature.toString("base64");
    }
    const confirmed = await client.confirmVerification(
      serviceCredential.token,
      buyerAgentId,
      buyerOrganizationId,
      keyProofBase64,
    );
    if (confirmed.status !== "active") {
      throw new Error(`Agent did not reach active status: ${JSON.stringify(confirmed)}`);
    }

    // --- Layer 4: eligibility check before attempting to negotiate ----
    const relationship = await client.getRelationshipStatus(
      buyerOrganizationId,
      orgScopedCredential,
      supplierOrganizationId,
    );
    if (!relationship.exists || relationship.status !== "active") {
      throw new Error(`No active trading relationship with ${supplierOrganizationId}: ${JSON.stringify(relationship)}`);
    }

    // --- Negotiation: signed request_for_quote -> verified quote ------
    const supplierAgent = await connectToAgent(supplierA2aUrl);
    const supplierAgentId = requireEnv("SUPPLIER_AGENT_ID");
    const transactionId = randomUUID();

    const rfq = buildEnvelope({
      transactionId,
      senderAgentId: buyerAgentId,
      recipientAgentId: supplierAgentId,
      organizationId: buyerOrganizationId,
      artifactType: "request_for_quote",
      version: 1,
      proposalId: randomUUID(),
      payload: {
        lines: [{ product_query: "lactose-free milk", quantity: "100", unit: "L" }],
        delivery_location_ref: "loc-external-1",
      },
      validitySeconds: 86400,
    });
    const signedRfq = signArtifact(rfq as any, {
      privateKey: agentPrivateKey,
      kid: delegatedKid,
      organizationId: buyerOrganizationId,
      agentId: buyerAgentId,
      recipientOrganizationId: supplierOrganizationId,
    });

    const { responsePayload: quote } = await sendDataMessage(supplierAgent, signedRfq, buyerAgentId);
    const quoteArtifact = quote as Record<string, unknown> & {
      proof: { kid: string; jws: string };
      payload: { lines: QuoteLine[]; currency: string; delivery_terms?: string };
      content_hash: string;
      proposal_id: string;
      version: number;
    };

    await verifyInboundArtifact(client, quoteArtifact, {
      issuerOrganizationId: supplierOrganizationId,
      expectedRecipientOrganizationId: buyerOrganizationId,
      protocolVersion: "v1",
    });

    const recomputedHash = contentHash(quoteArtifact.payload as unknown as Record<string, unknown>);
    if (recomputedHash !== quoteArtifact.content_hash) {
      throw new Error(
        `Recomputed content_hash ${recomputedHash} does not match the quote's claimed content_hash ${quoteArtifact.content_hash} -- never trusting the supplier's claimed binding.`,
      );
    }

    // --- Own policy decision, bound to the exact recomputed hash ------
    const decision = evaluateBuyerPolicy(quoteArtifact.payload.lines, quoteArtifact.payload.currency);
    if (!decision.allow) {
      console.log(JSON.stringify({ outcome: "denied_by_own_policy", reason: decision.denyReason }));
      return;
    }

    const acceptProposal = buildEnvelope({
      transactionId,
      senderAgentId: buyerAgentId,
      recipientAgentId: supplierAgentId,
      organizationId: buyerOrganizationId,
      artifactType: "accept_proposal",
      version: quoteArtifact.version + 1,
      proposalId: randomUUID(),
      payload: { accepted_proposal_id: quoteArtifact.proposal_id, accepted_content_hash: quoteArtifact.content_hash },
      validitySeconds: 3600,
    });
    const signedAccept = signArtifact(acceptProposal as any, {
      privateKey: agentPrivateKey,
      kid: delegatedKid,
      organizationId: buyerOrganizationId,
      agentId: buyerAgentId,
      recipientOrganizationId: supplierOrganizationId,
    });
    const { responsePayload: proposedAgreement } = await sendDataMessage(supplierAgent, signedAccept, buyerAgentId);
    await verifyInboundArtifact(client, proposedAgreement as any, {
      issuerOrganizationId: supplierOrganizationId,
      expectedRecipientOrganizationId: buyerOrganizationId,
      protocolVersion: "v1",
    });

    // --- Own authorization_evidence, bound to the agreed content_hash -
    const ownEvidence = buildEnvelope({
      transactionId,
      senderAgentId: buyerAgentId,
      recipientAgentId: supplierAgentId,
      organizationId: buyerOrganizationId,
      artifactType: "authorization_evidence",
      version: 1,
      proposalId: randomUUID(),
      payload: {
        agreement_id: transactionId,
        content_hash: quoteArtifact.content_hash,
        allow: true,
        human_approval_required: false,
        deny_reason: null,
        policy_version: decision.policyVersion,
        policy_input_digest: decision.policyInputDigest,
        approval_id: null,
        approver_id: null,
      },
      validitySeconds: 7200,
    });
    const signedOwnEvidence = signArtifact(ownEvidence as any, {
      privateKey: agentPrivateKey,
      kid: delegatedKid,
      organizationId: buyerOrganizationId,
      agentId: buyerAgentId,
      recipientOrganizationId: supplierOrganizationId,
    });
    const { responsePayload: counterpartyEvidence } = await sendDataMessage(supplierAgent, signedOwnEvidence, buyerAgentId);
    await verifyInboundArtifact(client, counterpartyEvidence as any, {
      issuerOrganizationId: supplierOrganizationId,
      expectedRecipientOrganizationId: buyerOrganizationId,
      protocolVersion: "v1",
    });
    const counterpartyPayload = (counterpartyEvidence as any).payload;
    if (!counterpartyPayload.allow) {
      console.log(JSON.stringify({ outcome: "denied_by_counterparty_policy", reason: counterpartyPayload.deny_reason }));
      return;
    }

    // --- Commit ---------------------------------------------------------
    const idempotencyKey = createHash("sha256")
      .update(`${quoteArtifact.content_hash}:${buyerOrganizationId}:${supplierOrganizationId}`)
      .digest("hex");

    const commitRequest = buildEnvelope({
      transactionId,
      senderAgentId: buyerAgentId,
      recipientAgentId: supplierAgentId,
      organizationId: buyerOrganizationId,
      artifactType: "order_commit_request",
      version: 1,
      proposalId: randomUUID(),
      payload: { agreement_id: transactionId, content_hash: quoteArtifact.content_hash, idempotency_key: idempotencyKey },
      validitySeconds: 3600,
    });
    const signedCommit = signArtifact(commitRequest as any, {
      privateKey: agentPrivateKey,
      kid: delegatedKid,
      organizationId: buyerOrganizationId,
      agentId: buyerAgentId,
      recipientOrganizationId: supplierOrganizationId,
    });
    const { responsePayload: confirmation } = await sendDataMessage(supplierAgent, signedCommit, buyerAgentId);
    await verifyInboundArtifact(client, confirmation as any, {
      issuerOrganizationId: supplierOrganizationId,
      expectedRecipientOrganizationId: buyerOrganizationId,
      protocolVersion: "v1",
    });

    console.log(
      JSON.stringify({
        outcome: "committed",
        confirmation: (confirmation as any).payload,
        idempotencyKey,
        transactionId,
      }),
    );
  } finally {
    await challenge.close();
  }
}

main().catch((exc) => {
  console.error(exc);
  process.exit(1);
});
