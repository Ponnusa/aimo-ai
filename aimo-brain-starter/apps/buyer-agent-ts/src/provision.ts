// M8 slice 5, ADR-020 section 5: a ONE-SHOT provisioning component,
// run as its own process, entirely separate from the running buyer
// agent. It is the only thing in this package that ever holds the
// organization's ROOT private key -- generated here, used here to sign
// exactly one delegation, and then this process exits. The root private
// key is never written to disk, never logged, and never passed to the
// buyer agent process: only the resulting delegation JWS (public,
// independently verifiable by anyone) and the AGENT keypair are handed
// over, via stdout JSON, for the caller to feed to buyerAgent.ts.
import { ExternalContractClient } from "./contractClient.js";
import { buildDelegation, generateEd25519Keypair, publicKeyToJwk } from "./jws.js";
import { randomUUID } from "node:crypto";

const ARTIFACT_TYPES = [
  "request_for_quote",
  "quote",
  "accept_proposal",
  "authorization_evidence",
  "order_commit_request",
  "order_confirmation",
];

async function main(): Promise<void> {
  const networkApiUrl = requireEnv("NETWORK_API_URL");
  const organizationId = requireEnv("BUYER_ORGANIZATION_ID");
  const orgScopedCredential = requireEnv("ORG_SCOPED_CREDENTIAL");
  const agentId = requireEnv("BUYER_AGENT_ID");
  const audience = process.env.DELEGATION_AUDIENCE ?? "aimo-brain-reference-network";

  const client = new ExternalContractClient({ networkApiUrl, registryApiUrl: "" });

  // Root key: generated, used once, held only in this process's memory.
  const rootKid = `root-${randomUUID()}`;
  const root = generateEd25519Keypair();
  const rootPublicJwk = publicKeyToJwk(root.publicKey, rootKid);
  await client.registerSigningKey(organizationId, orgScopedCredential, rootKid, rootPublicJwk as any);

  // Agent (delegated) key: this is what the running buyer process keeps.
  const delegatedKid = `agent-${randomUUID()}`;
  const agent = generateEd25519Keypair();
  const agentPublicJwk = publicKeyToJwk(agent.publicKey, delegatedKid);
  const agentPrivateJwk = agent.privateKey.export({ format: "jwk" }) as Record<string, unknown>;

  const delegationJws = buildDelegation({
    rootPrivateKey: root.privateKey,
    issuingKid: rootKid,
    organizationId,
    agentId,
    audience,
    delegatedKid,
    delegatedPublicKeyJwk: agentPublicJwk as any,
    scope: { artifact_types: ARTIFACT_TYPES, protocol_version: "v1" },
  });

  const published = await client.publishDelegation(organizationId, orgScopedCredential, delegationJws);

  // root.privateKey goes out of scope here and this process exits --
  // never serialized, logged, or returned to the caller.
  const output = {
    rootKid,
    rootPublicJwk,
    delegatedKid,
    delegationJws,
    delegationJti: published.jti,
    agentPublicJwk,
    agentPrivateJwk,
  };
  process.stdout.write(JSON.stringify(output));
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set.`);
  return value;
}

main().catch((exc) => {
  console.error(exc);
  process.exit(1);
});
