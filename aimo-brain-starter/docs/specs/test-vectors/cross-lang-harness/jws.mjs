// Minimal JWS (RFC 7515) compact-serialization sign/verify for the
// EdDSA/Ed25519 case only, per docs/specs/jws-artifact-signatures.md
// section 3/3a. Node.js built-in crypto only -- no JOSE library.

import {
  createHash,
  createPrivateKey,
  createPublicKey,
  sign as cryptoSign,
  verify as cryptoVerify,
} from "node:crypto";
import { canonicalize } from "./canonicalize.mjs";

// M8 slice 2 (ADR-017) domain-separation header values -- see
// docs/specs/jws-artifact-signatures.md section 3b.
export const ARTIFACT_TYP = "aimo-artifact+jws";
export const DELEGATION_TYP = "aimo-delegation+jws";

export function digestHex(artifactWithoutProof) {
  const canonical = canonicalize(artifactWithoutProof);
  const hex = createHash("sha256").update(canonical, "utf8").digest("hex");
  return { canonical, hex };
}

function b64url(buf) {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(str) {
  const padded = str.replace(/-/g, "+").replace(/_/g, "/");
  const pad = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
  return Buffer.from(padded + pad, "base64");
}

export function publicKeyFromJwk(jwk) {
  if (jwk.kty !== "OKP" || jwk.crv !== "Ed25519") {
    throw new Error(`Unsupported JWK type: ${JSON.stringify(jwk)}`);
  }
  return createPublicKey({ key: { kty: "OKP", crv: "Ed25519", x: jwk.x }, format: "jwk" });
}

export function privateKeyFromJwk(jwk) {
  return createPrivateKey({ key: jwk, format: "jwk" });
}

export function publicKeyToJwk(publicKey, kid) {
  const jwk = publicKey.export({ format: "jwk" });
  return { kty: "OKP", crv: "Ed25519", kid, x: jwk.x, use: "sig", alg: "EdDSA" };
}

/** Section 3a, steps 1-8. `typ` defaults to "JWT" (the legacy header
 * value every pre-M8-slice-2 artifact carries) for backward
 * compatibility with this harness's existing vectors; M8 slice 2
 * callers pass the explicit discriminator (ARTIFACT_TYP/DELEGATION_TYP
 * below) instead. */
export function signJws(claims, { privateKey, kid, typ = "JWT" }) {
  const header = { typ, alg: "EdDSA", kid };
  const encodedHeader = b64url(Buffer.from(JSON.stringify(header), "utf8"));
  const encodedPayload = b64url(Buffer.from(JSON.stringify(claims), "utf8"));
  const signingInput = Buffer.from(`${encodedHeader}.${encodedPayload}`, "ascii");
  const signature = cryptoSign(null, signingInput, privateKey);
  const encodedSignature = b64url(signature);
  return `${encodedHeader}.${encodedPayload}.${encodedSignature}`;
}

export class JwsVerificationError extends Error {}

/** Decode (without verifying) a token's header and payload -- used to
 * inspect `typ` before deciding which verification path applies (M8
 * slice 2 domain separation), mirroring
 * message_integrity.proofs.parse_unverified_proof. */
export function decodeUnverified(token) {
  const parts = token.split(".");
  if (parts.length !== 3) throw new JwsVerificationError("Malformed JWS: expected 3 segments.");
  const [encodedHeader, encodedPayload] = parts;
  const header = JSON.parse(b64urlDecode(encodedHeader).toString("utf8"));
  const claims = JSON.parse(b64urlDecode(encodedPayload).toString("utf8"));
  return { header, claims };
}

/** Section 4, steps 1-7 (minus key resolution, which the caller does --
 * this harness uses a plain object map, not a live JWKS fetch).
 * `expectedTyp` (M8 slice 2): when provided, enforces domain
 * separation -- "legacy" accepts an absent typ or "JWT" (pre-slice-2
 * artifacts), "v1" requires exactly ARTIFACT_TYP, "delegation"
 * requires exactly DELEGATION_TYP; any mismatch (in particular,
 * presenting a delegation token where an artifact is expected, or
 * vice versa) is rejected before the signature is even checked. */
export function verifyJws(token, { resolvePublicKey, expectedAudience, expectedTyp }) {
  const parts = token.split(".");
  if (parts.length !== 3) throw new JwsVerificationError("Malformed JWS: expected 3 segments.");
  const [encodedHeader, encodedPayload, encodedSignature] = parts;

  let header, claims;
  try {
    header = JSON.parse(b64urlDecode(encodedHeader).toString("utf8"));
    claims = JSON.parse(b64urlDecode(encodedPayload).toString("utf8"));
  } catch (exc) {
    throw new JwsVerificationError(`Malformed proof: ${exc}`);
  }

  if (header.alg !== "EdDSA") {
    throw new JwsVerificationError(`Unsupported alg ${header.alg}; only EdDSA is accepted.`);
  }
  if (expectedTyp === "legacy" && header.typ !== undefined && header.typ !== "JWT") {
    throw new JwsVerificationError(`Unexpected typ ${header.typ}; expected legacy (absent or "JWT").`);
  }
  if (expectedTyp === "v1" && header.typ !== ARTIFACT_TYP) {
    throw new JwsVerificationError(`Unexpected typ ${header.typ}; expected ${ARTIFACT_TYP}.`);
  }
  if (expectedTyp === "delegation" && header.typ !== DELEGATION_TYP) {
    throw new JwsVerificationError(`Unexpected typ ${header.typ}; expected ${DELEGATION_TYP}.`);
  }
  const kid = header.kid;
  const issuer = claims.iss;
  if (!kid || !issuer) throw new JwsVerificationError("Proof is missing a kid or issuer.");

  const publicKey = resolvePublicKey(issuer, kid);
  if (!publicKey) throw new JwsVerificationError(`Could not resolve signing key for (${issuer}, ${kid}).`);

  const signingInput = Buffer.from(`${encodedHeader}.${encodedPayload}`, "ascii");
  const signature = b64urlDecode(encodedSignature);
  const ok = cryptoVerify(null, signingInput, publicKey, signature);
  if (!ok) throw new JwsVerificationError("Signature verification failed.");

  if (expectedAudience !== undefined && claims.aud !== expectedAudience) {
    throw new JwsVerificationError(`aud mismatch: expected ${expectedAudience}, got ${claims.aud}.`);
  }
  const now = Math.floor(Date.now() / 1000);
  if (typeof claims.exp === "number" && now >= claims.exp) {
    throw new JwsVerificationError("Proof has expired.");
  }

  return claims;
}
