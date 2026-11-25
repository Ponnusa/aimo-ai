// Generates a delegation token AND the artifact it covers ENTIRELY in
// Node.js (no AIMO Python package involved), proving the Node -> Python
// direction of delegation interoperability — the companion to
// verify-delegation.mjs, which proves Python -> Node. See
// packages/message-integrity/tests/test_jws_vectors.py's
// test_node_generated_delegation_verifies_in_python.

import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { generateKeyPairSync } from "node:crypto";
import { ARTIFACT_TYP, DELEGATION_TYP, digestHex, publicKeyToJwk, signJws } from "./jws.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));

const { privateKey: rootPrivateKey, publicKey: rootPublicKey } = generateKeyPairSync("ed25519");
const { privateKey: delegatedPrivateKey, publicKey: delegatedPublicKey } =
  generateKeyPairSync("ed25519");

const organizationId = "org_node_generated_delegation_01";
const agentId = "agent_node_generated_delegation_01";
const rootKid = "org_node_generated_delegation_01-root-k1";
const delegatedKid = "org_node_generated_delegation_01-agent01-k1";
const audience = "aimo-network:node-generated-test";
const recipientOrganizationId = "org_vector_supplier_01";

const ISSUED_AT = 1_700_000_000;
const EXPIRES_AT = 4_000_000_000;

const delegationClaims = {
  iss: organizationId,
  sub: agentId,
  aud: audience,
  jti: "jti_node_generated_delegation_01",
  iat: ISSUED_AT,
  nbf: ISSUED_AT,
  exp: EXPIRES_AT,
  delegated_kid: delegatedKid,
  delegated_pubkey_jwk: publicKeyToJwk(delegatedPublicKey, delegatedKid),
  scope: { artifact_types: ["request_for_quote", "quote"], protocol_version: "v1" },
};
const delegationJws = signJws(delegationClaims, {
  privateKey: rootPrivateKey,
  kid: rootKid,
  typ: DELEGATION_TYP,
});

const artifact = {
  artifact_id: "art_node_generated_delegation_01",
  transaction_id: "txn_node_generated_delegation_01",
  correlation_id: "corr_node_generated_delegation_01",
  sender_agent_id: agentId,
  recipient_agent_id: "agent_vector_supplier_01",
  organization_id: organizationId,
  type: "quote",
  version: 1,
  payload: { lines: [{ product_ref: "sku_0004", quantity: "3" }] },
  content_hash: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85",
};
const { hex: artifactDigest } = digestHex(artifact);
const artifactClaims = {
  iss: organizationId,
  sub: agentId,
  aud: recipientOrganizationId,
  neg: artifact.transaction_id,
  typ: artifact.type,
  ver: artifact.version,
  hash: artifact.content_hash,
  artifact_digest: artifactDigest,
  jti: "jti_node_generated_delegation_artifact_01",
  iat: ISSUED_AT,
  exp: EXPIRES_AT,
};
const artifactJws = signJws(artifactClaims, {
  privateKey: delegatedPrivateKey,
  kid: delegatedKid,
  typ: ARTIFACT_TYP,
});
const signedArtifact = { ...artifact, proof: { alg: "EdDSA", kid: delegatedKid, jws: artifactJws } };

const output = {
  description:
    "A delegation AND the artifact it covers, both signed ENTIRELY by the Node.js cross-language harness (generate-delegation.mjs), for the Python reference implementation to verify — proving the Node.js -> Python direction of delegation interoperability.",
  organization_id: organizationId,
  network_audience: audience,
  root_public_key_jwk: publicKeyToJwk(rootPublicKey, rootKid),
  delegation_jws: delegationJws,
  expected_recipient_organization_id: recipientOrganizationId,
  signed_artifact: signedArtifact,
};

const outPath = join(HERE, "node-generated-delegation.json");
writeFileSync(outPath, JSON.stringify(output, null, 2) + "\n", "utf8");
console.log(`Wrote ${outPath}`);
