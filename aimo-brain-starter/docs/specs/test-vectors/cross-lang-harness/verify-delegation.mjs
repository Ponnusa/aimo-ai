// M8 slice 2 (ADR-017): independently verifies vector_07_delegation.json
// -- the Python-generated delegation + the business artifact it covers
// -- using ONLY this harness's own from-scratch implementation
// (jws.mjs, built from the published spec, no AIMO package involved).
//
// Proves two things Node-side, matching what the Python test
// (test_delegation_vector_07_verifies_end_to_end) proves Python-side:
//   1. The delegation token verifies against the root public key and
//      recovers the expected claims, including its scope.
//   2. The signed artifact verifies against the DELEGATED key (never
//      the root key), and is correctly rejected if presented with the
//      wrong typ discriminator (domain separation).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { decodeUnverified, publicKeyFromJwk, verifyJws, DELEGATION_TYP } from "./jws.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const vectorPath = join(HERE, "..", "vector_07_delegation.json");
const vector = JSON.parse(readFileSync(vectorPath, "utf8"));

let failures = 0;
function check(label, condition) {
  if (condition) {
    console.log(`OK   ${label}`);
  } else {
    console.error(`FAIL ${label}`);
    failures += 1;
  }
}

// --- Step 1: verify the delegation itself against the root key -----
const rootPublicKey = publicKeyFromJwk(vector.root_public_key_jwk);
const delegationClaims = verifyJws(vector.delegation_jws, {
  resolvePublicKey: () => rootPublicKey,
  expectedAudience: vector.network_audience,
  expectedTyp: "delegation",
});
check("delegation typ header is the delegation discriminator", (() => {
  const { header } = decodeUnverified(vector.delegation_jws);
  return header.typ === DELEGATION_TYP;
})());
check(
  "delegation claims match expected_delegation_claims",
  JSON.stringify(delegationClaims) === JSON.stringify(vector.expected_delegation_claims),
);
check(
  "delegation scope covers the artifact's own type",
  delegationClaims.scope.artifact_types.includes(vector.signed_artifact.type),
);

// --- Step 2: verify the business artifact against the DELEGATED key,
//     never the root key -- this is the two-hop chain itself. --------
const delegatedPublicKey = publicKeyFromJwk(delegationClaims.delegated_pubkey_jwk);
const artifactClaims = verifyJws(vector.signed_artifact.proof.jws, {
  resolvePublicKey: () => delegatedPublicKey,
  expectedAudience: vector.expected_recipient_organization_id,
  expectedTyp: "v1",
});
check("artifact sub matches the delegation's own agent_id", artifactClaims.sub === delegationClaims.sub);
check(
  "artifact negotiation id matches expected_verified_claims",
  artifactClaims.neg === vector.expected_verified_claims.negotiation_id,
);

// --- Step 3: domain separation -- a delegation token must be REJECTED
//     if presented where a business artifact (typ "v1") is expected,
//     and vice versa. -------------------------------------------------
try {
  verifyJws(vector.delegation_jws, {
    resolvePublicKey: () => rootPublicKey,
    expectedAudience: vector.network_audience,
    expectedTyp: "v1",
  });
  check("delegation token rejected when a business artifact was expected", false);
} catch {
  check("delegation token rejected when a business artifact was expected", true);
}

try {
  verifyJws(vector.signed_artifact.proof.jws, {
    resolvePublicKey: () => delegatedPublicKey,
    expectedAudience: vector.expected_recipient_organization_id,
    expectedTyp: "delegation",
  });
  check("business artifact rejected when a delegation token was expected", false);
} catch {
  check("business artifact rejected when a delegation token was expected", true);
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed.`);
  process.exit(1);
}
console.log("\nAll delegation checks passed.");
