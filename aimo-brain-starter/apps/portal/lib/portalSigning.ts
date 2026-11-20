// Mints a one-decision portal approval assertion (M6, see
// identity.portal_approval on the Python side — this is the same JWS
// shape, verified there against this key's published JWKS). The
// portal's own Ed25519 private key never leaves this process; only the
// public half is ever registered with network-api
// (scripts/generate_signing_key.py, against the "org_aimo_portal"
// pseudo-organization).
import { importPKCS8, SignJWT } from "jose";
import { config } from "./config";

export async function issuePortalApprovalToken(params: {
  organizationId: string;
  approverSubject: string;
  negotiationId: string;
  contentHash: string;
  validitySeconds?: number;
}): Promise<string> {
  if (!config.portalSigningPrivateKeyPem || !config.portalSigningKid) {
    throw new Error(
      "PORTAL_SIGNING_PRIVATE_KEY_PEM/PORTAL_SIGNING_KID are not configured — generate a " +
        "keypair with scripts/generate_signing_key.py and register the public half for " +
        `${config.portalOrganizationId}.`,
    );
  }
  const privateKey = await importPKCS8(config.portalSigningPrivateKeyPem, "EdDSA");
  const now = Math.floor(Date.now() / 1000);
  return new SignJWT({
    organization_id: params.organizationId,
    negotiation_id: params.negotiationId,
    content_hash: params.contentHash,
    roles: ["approver"],
  })
    .setProtectedHeader({ alg: "EdDSA", kid: config.portalSigningKid })
    .setIssuer("aimo-portal")
    .setSubject(params.approverSubject)
    .setAudience(params.organizationId)
    .setIssuedAt(now)
    .setExpirationTime(now + (params.validitySeconds ?? 120))
    .sign(privateKey);
}
