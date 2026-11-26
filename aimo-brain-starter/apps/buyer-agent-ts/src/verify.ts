// Independent verification of an inbound artifact and the delegation
// chain backing it (ADR-020 section 7) -- fetches jwks.json and
// delegations.json FRESH from the published, unauthenticated contract
// endpoints (never a key embedded in or alongside the artifact itself),
// verifies the delegation's own signature against the issuer's live
// root key, checks scope/audience/expiry, confirms the delegation is
// still present in the live (revocation-by-absence) list, and only
// then verifies the artifact's own signature against the delegated key
// it names. This is "verifying", not "decoding".
import { ExternalContractClient } from "./contractClient.js";
import {
  decodeUnverified,
  publicKeyFromJwk,
  scopeAllows,
  verifyArtifactProofWithKnownKey,
  verifyDelegationToken,
  type Jwk,
  type VerifiedProof,
} from "./jws.js";

export class DelegationNotLiveError extends Error {}

/** Fetches jwks.json + delegations.json fresh and verifies ``artifact``
 * was signed by a key the issuing organization has LIVE delegated
 * authority over, for exactly this artifact's own type -- this is
 * what a "forced fresh, uncached check" means for commitment-adjacent
 * artifact types (authorization_evidence, order_commit_request,
 * order_confirmation); callers at those types must call this function
 * every time, never reuse a prior result. */
export async function verifyInboundArtifact(
  client: ExternalContractClient,
  artifact: Record<string, unknown> & { proof?: { kid: string; jws: string } },
  opts: { issuerOrganizationId: string; expectedRecipientOrganizationId: string; protocolVersion: string },
): Promise<VerifiedProof> {
  const proof = artifact.proof;
  if (!proof?.jws) throw new Error("Artifact carries no proof.");

  const [jwks, delegationsResp] = await Promise.all([
    client.getJwks(opts.issuerOrganizationId),
    client.getDelegationsJson(opts.issuerOrganizationId),
  ]);

  const { header: artifactHeader } = decodeUnverified(proof.jws);
  const delegatedKid = artifactHeader.kid;

  // Legacy/organization-level attribution (ADR-017): the artifact may be
  // signed directly by one of the organization's own LIVE root keys,
  // with no delegation at all -- still a valid, live key, just a
  // different attribution than a per-agent delegated key.
  const directRootJwk = (jwks.keys as Jwk[]).find((k) => k.kid === delegatedKid);
  if (directRootJwk) {
    return verifyArtifactProofWithKnownKey(
      artifact,
      publicKeyFromJwk(directRootJwk),
      opts.expectedRecipientOrganizationId,
      opts.issuerOrganizationId,
    );
  }

  let matchedDelegationToken: string | undefined;
  for (const token of delegationsResp.delegations) {
    const { header, claims } = decodeUnverified(token);
    if (claims.delegated_kid === delegatedKid) {
      matchedDelegationToken = token;
      break;
    }
  }
  if (!matchedDelegationToken) {
    throw new DelegationNotLiveError(
      `No live delegation found for kid ${delegatedKid} -- either never delegated or revoked (revocation-by-absence).`,
    );
  }

  const { header: delegationHeader } = decodeUnverified(matchedDelegationToken);
  const rootJwk = (jwks.keys as Jwk[]).find((k) => k.kid === delegationHeader.kid);
  if (!rootJwk) {
    throw new DelegationNotLiveError(`Issuing root key ${delegationHeader.kid} is not currently live.`);
  }
  const rootPublicKey = publicKeyFromJwk(rootJwk);

  const verifiedDelegation = verifyDelegationToken(matchedDelegationToken, rootPublicKey, {
    expectedOrganizationId: opts.issuerOrganizationId,
  });

  if (!scopeAllows(verifiedDelegation.scope, artifact.type as string, opts.protocolVersion)) {
    throw new DelegationNotLiveError(
      `Delegation scope does not cover artifact type ${artifact.type} at protocol version ${opts.protocolVersion}.`,
    );
  }

  return verifyArtifactProofWithKnownKey(
    artifact,
    verifiedDelegation.delegatedPublicKey,
    opts.expectedRecipientOrganizationId,
    opts.issuerOrganizationId,
  );
}
