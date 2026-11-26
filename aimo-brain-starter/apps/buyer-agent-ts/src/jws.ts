// Ed25519 JWS compact-serialization sign/verify (RFC 7515, EdDSA only),
// per docs/specs/jws-artifact-signatures.md and
// packages/message-integrity/src/message_integrity/proofs.py's exact
// claim shape -- ported, not imported (this package never imports an
// AIMO Python package or its generated artifacts). Node's built-in
// crypto only, matching the existing cross-lang-harness/jws.mjs
// approach, extended here with artifact_digest / full VerifiedProof
// claims for independent verification (ADR-020 section 7).
import { createHash, createPrivateKey, createPublicKey, sign as cryptoSign, verify as cryptoVerify, generateKeyPairSync } from "node:crypto";
import type { KeyObject } from "node:crypto";
import { randomUUID } from "node:crypto";
import { canonicalize } from "./canonicalize.js";

export const ARTIFACT_TYP = "aimo-artifact+jws";
export const DELEGATION_TYP = "aimo-delegation+jws";

export interface Jwk {
  kty: string;
  crv: string;
  kid?: string;
  x: string;
  use?: string;
  alg?: string;
}

export class JwsVerificationError extends Error {}

function b64url(buf: Buffer): string {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(str: string): Buffer {
  const padded = str.replace(/-/g, "+").replace(/_/g, "/");
  const pad = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
  return Buffer.from(padded + pad, "base64");
}

export function generateEd25519Keypair(): { privateKey: KeyObject; publicKey: KeyObject } {
  const { privateKey, publicKey } = generateKeyPairSync("ed25519");
  return { privateKey, publicKey };
}

export function publicKeyFromJwk(jwk: Jwk): KeyObject {
  if (jwk.kty !== "OKP" || jwk.crv !== "Ed25519") {
    throw new Error(`Unsupported JWK type: ${JSON.stringify(jwk)}`);
  }
  return createPublicKey({ key: { kty: "OKP", crv: "Ed25519", x: jwk.x }, format: "jwk" });
}

export function privateKeyFromJwk(jwk: Record<string, unknown>): KeyObject {
  return createPrivateKey({ key: jwk as any, format: "jwk" });
}

export function publicKeyToJwk(publicKey: KeyObject, kid: string): Jwk {
  const jwk = publicKey.export({ format: "jwk" }) as { x: string };
  return { kty: "OKP", crv: "Ed25519", kid, x: jwk.x, use: "sig", alg: "EdDSA" };
}

function signCompact(header: Record<string, unknown>, claims: Record<string, unknown>, privateKey: KeyObject): string {
  const encodedHeader = b64url(Buffer.from(JSON.stringify(header), "utf8"));
  const encodedPayload = b64url(Buffer.from(JSON.stringify(claims), "utf8"));
  const signingInput = Buffer.from(`${encodedHeader}.${encodedPayload}`, "ascii");
  const signature = cryptoSign(null, signingInput, privateKey);
  return `${encodedHeader}.${encodedPayload}.${b64url(signature)}`;
}

export function decodeUnverified(token: string): { header: Record<string, any>; claims: Record<string, any> } {
  const parts = token.split(".");
  if (parts.length !== 3) throw new JwsVerificationError("Malformed JWS: expected 3 segments.");
  const header = JSON.parse(b64urlDecode(parts[0]).toString("utf8"));
  const claims = JSON.parse(b64urlDecode(parts[1]).toString("utf8"));
  return { header, claims };
}

function verifyCompact(
  token: string,
  publicKey: KeyObject,
): { header: Record<string, any>; claims: Record<string, any> } {
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
  const signingInput = Buffer.from(`${encodedHeader}.${encodedPayload}`, "ascii");
  const signature = b64urlDecode(encodedSignature);
  if (!cryptoVerify(null, signingInput, publicKey, signature)) {
    throw new JwsVerificationError("Signature verification failed.");
  }
  const now = Math.floor(Date.now() / 1000);
  if (typeof claims.exp === "number" && now >= claims.exp) {
    throw new JwsVerificationError("Proof has expired.");
  }
  return { header, claims };
}

// ---------------------------------------------------------------------
// Business-artifact proofs (message_integrity.proofs.sign_artifact /
// verify_artifact_proof_with_known_key, mirrored exactly)
// ---------------------------------------------------------------------

export interface Artifact {
  transaction_id: string;
  type: string;
  version: number;
  content_hash?: string | null;
  [key: string]: unknown;
}

export function artifactDigest(artifactWithoutProof: Record<string, unknown>): string {
  return createHash("sha256").update(canonicalize(artifactWithoutProof), "utf8").digest("hex");
}

export function signArtifact(
  artifact: Artifact,
  opts: { privateKey: KeyObject; kid: string; organizationId: string; agentId: string; recipientOrganizationId: string; validitySeconds?: number },
): Artifact {
  if ("proof" in artifact) {
    throw new Error("Artifact already carries a proof; sign before attaching one.");
  }
  const now = Math.floor(Date.now() / 1000);
  const claims = {
    iss: opts.organizationId,
    sub: opts.agentId,
    aud: opts.recipientOrganizationId,
    neg: artifact.transaction_id,
    typ: artifact.type,
    ver: artifact.version,
    hash: artifact.content_hash ?? null,
    artifact_digest: artifactDigest(artifact as Record<string, unknown>),
    jti: randomUUID(),
    iat: now,
    exp: now + (opts.validitySeconds ?? 600),
  };
  const token = signCompact({ kid: opts.kid, typ: ARTIFACT_TYP, alg: "EdDSA" }, claims, opts.privateKey);
  return { ...artifact, proof: { alg: "EdDSA", kid: opts.kid, jws: token } };
}

export interface VerifiedProof {
  organizationId: string;
  agentId: string;
  recipientOrganizationId: string;
  negotiationId: string;
  artifactType: string;
  version: number;
  contentHash: string | null;
  jti: string;
  issuedAt: number;
  expiresAt: number;
}

/** Full independent verification (ADR-020 section 7): recomputes
 * artifact_digest against the artifact actually received, checks
 * neg/typ/hash/ver scope binding, verifies the signature against the
 * caller-resolved public key (the caller must have already resolved
 * this key from a live, independently-verified JWKS/delegation --
 * never a key embedded in or alongside the artifact itself). */
export function verifyArtifactProofWithKnownKey(
  artifact: Record<string, unknown> & { proof?: { kid: string; jws: string } },
  publicKey: KeyObject,
  expectedRecipientOrganizationId: string,
  issuerOrganizationId: string,
): VerifiedProof {
  const proof = artifact.proof;
  if (!proof?.jws) throw new JwsVerificationError("Artifact carries no proof.");
  const { header, claims } = verifyCompact(proof.jws, publicKey);
  if (header.typ !== ARTIFACT_TYP && header.typ !== undefined && header.typ !== "JWT") {
    throw new JwsVerificationError(`Unexpected typ ${header.typ}.`);
  }
  if (claims.aud !== expectedRecipientOrganizationId) {
    throw new JwsVerificationError(`aud mismatch: expected ${expectedRecipientOrganizationId}, got ${claims.aud}.`);
  }
  if (claims.iss !== issuerOrganizationId) {
    throw new JwsVerificationError(`iss mismatch: expected ${issuerOrganizationId}, got ${claims.iss}.`);
  }

  const { proof: _drop, ...artifactWithoutProof } = artifact;
  const expectedDigest = artifactDigest(artifactWithoutProof);
  if (claims.artifact_digest !== expectedDigest) {
    throw new JwsVerificationError("Signed digest does not match the artifact's actual content.");
  }
  if (
    claims.neg !== artifact.transaction_id ||
    claims.typ !== artifact.type ||
    claims.ver !== artifact.version ||
    (claims.hash ?? null) !== ((artifact.content_hash as string | undefined) ?? null)
  ) {
    throw new JwsVerificationError(
      "Signed claims do not match this artifact's negotiation id, type, version or content_hash.",
    );
  }

  return {
    organizationId: issuerOrganizationId,
    agentId: claims.sub,
    recipientOrganizationId: claims.aud,
    negotiationId: claims.neg,
    artifactType: claims.typ,
    version: claims.ver,
    contentHash: claims.hash ?? null,
    jti: claims.jti,
    issuedAt: claims.iat,
    expiresAt: claims.exp,
  };
}

// ---------------------------------------------------------------------
// Delegation tokens (message_integrity.delegations, mirrored exactly)
// ---------------------------------------------------------------------

export interface DelegationScope {
  artifact_types: string[];
  protocol_version: string;
}

export function scopeAllows(scope: DelegationScope, artifactType: string, protocolVersion: string): boolean {
  return scope.artifact_types.includes(artifactType) && scope.protocol_version === protocolVersion;
}

export function buildDelegation(opts: {
  rootPrivateKey: KeyObject;
  issuingKid: string;
  organizationId: string;
  agentId: string;
  audience: string;
  delegatedKid: string;
  delegatedPublicKeyJwk: Jwk;
  scope: DelegationScope;
  validitySeconds?: number;
}): string {
  const now = Math.floor(Date.now() / 1000);
  const claims = {
    iss: opts.organizationId,
    sub: opts.agentId,
    aud: opts.audience,
    jti: randomUUID(),
    iat: now,
    nbf: now,
    exp: now + (opts.validitySeconds ?? 30 * 24 * 3600),
    delegated_kid: opts.delegatedKid,
    delegated_pubkey_jwk: opts.delegatedPublicKeyJwk,
    scope: opts.scope,
  };
  return signCompact({ kid: opts.issuingKid, typ: DELEGATION_TYP, alg: "EdDSA" }, claims, opts.rootPrivateKey);
}

export interface VerifiedDelegation {
  organizationId: string;
  agentId: string;
  issuingKid: string;
  delegatedKid: string;
  delegatedPublicKey: KeyObject;
  delegatedPubkeyJwk: Jwk;
  audience: string;
  scope: DelegationScope;
  jti: string;
  issuedAt: number;
  expiresAt: number;
}

/** Verifies a delegation token against the issuing organization's
 * LIVE root public key (caller-resolved, e.g. from a fresh jwks.json
 * fetch -- never a key embedded in the token itself). */
export function verifyDelegationToken(
  token: string,
  rootPublicKey: KeyObject,
  opts: { expectedAudience?: string; expectedOrganizationId: string },
): VerifiedDelegation {
  const unverifiedHeader = decodeUnverified(token).header;
  if (unverifiedHeader.typ !== DELEGATION_TYP) {
    throw new JwsVerificationError("A business-artifact proof cannot be verified as a delegation token.");
  }
  const { claims } = verifyCompact(token, rootPublicKey);
  if (claims.iss !== opts.expectedOrganizationId) {
    throw new JwsVerificationError(`iss mismatch: expected ${opts.expectedOrganizationId}, got ${claims.iss}.`);
  }
  if (opts.expectedAudience !== undefined && claims.aud !== opts.expectedAudience) {
    throw new JwsVerificationError(`aud mismatch: expected ${opts.expectedAudience}, got ${claims.aud}.`);
  }
  return {
    organizationId: claims.iss,
    agentId: claims.sub,
    issuingKid: unverifiedHeader.kid,
    delegatedKid: claims.delegated_kid,
    delegatedPublicKey: publicKeyFromJwk(claims.delegated_pubkey_jwk),
    delegatedPubkeyJwk: claims.delegated_pubkey_jwk,
    audience: claims.aud,
    scope: claims.scope,
    jti: claims.jti,
    issuedAt: claims.iat,
    expiresAt: claims.exp,
  };
}
