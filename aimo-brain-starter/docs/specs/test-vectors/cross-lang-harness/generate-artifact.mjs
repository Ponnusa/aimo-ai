// Generates a fresh Ed25519 keypair and a signed artifact ENTIRELY in
// Node.js (no AIMO Python package involved), and writes it to a JSON
// file that the Python reference implementation then verifies --
// proving the round trip works in both directions, not just
// Python-signs/Node-verifies. See verify-node-generated-artifact.py.

import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { generateKeyPairSync } from "node:crypto";
import { digestHex, publicKeyToJwk, signJws } from "./jws.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));

const { privateKey, publicKey } = generateKeyPairSync("ed25519");
const kid = "org_node_generated_01-k1";
const organizationId = "org_node_generated_01";
const agentId = "agent_node_generated_01";
const recipientOrganizationId = "org_vector_supplier_01";

const artifact = {
  artifact_id: "art_node_generated_01",
  transaction_id: "txn_node_generated_01",
  correlation_id: "corr_node_generated_01",
  sender_agent_id: agentId,
  recipient_agent_id: "agent_vector_supplier_01",
  organization_id: organizationId,
  type: "request_for_quote",
  version: 1,
  payload: { lines: [{ product_ref: "sku_0002", quantity: "5", note: "généré par Node.js" }] },
  content_hash: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85",
};

// Fixed, far-future timestamps -- matching every other committed vector's
// convention (see vector_01's description) -- so this committed fixture
// never expires. A relative `now + N` expiry would make this file go
// stale the moment more than N seconds pass between generation and
// whoever next verifies it (CI, a later local run, a reviewer), which is
// exactly the failure this harness exists to avoid, not reproduce.
const ISSUED_AT = 1_700_000_000; // 2023-11-14T22:13:20Z -- matches vector_01's convention
const EXPIRES_AT = 4_000_000_000; // 2096-10-02T11:33:20Z -- matches vector_01's convention
const { hex: artifactDigest } = digestHex(artifact);
const claims = {
  iss: organizationId,
  sub: agentId,
  aud: recipientOrganizationId,
  neg: artifact.transaction_id,
  typ: artifact.type,
  ver: artifact.version,
  hash: artifact.content_hash,
  artifact_digest: artifactDigest,
  jti: "jti_node_generated_01",
  iat: ISSUED_AT,
  exp: EXPIRES_AT,
};

const jws = signJws(claims, { privateKey, kid });
const signedArtifact = { ...artifact, proof: { alg: "EdDSA", kid, jws } };

const output = {
  description:
    "An artifact signed ENTIRELY by the Node.js cross-language harness (generate-artifact.mjs), for the Python reference implementation to verify -- proving the Node.js -> Python direction of interoperability, not just Python -> Node.js.",
  kid,
  public_key_jwk: publicKeyToJwk(publicKey, kid),
  signer_organization_id: organizationId,
  expected_recipient_organization_id: recipientOrganizationId,
  signed_artifact: signedArtifact,
};

const outPath = join(HERE, "node-generated-artifact.json");
writeFileSync(outPath, JSON.stringify(output, null, 2) + "\n", "utf8");
console.log(`Wrote ${outPath}`);
