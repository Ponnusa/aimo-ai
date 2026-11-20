// Standard OIDC relying-party flow (Authorization Code + PKCE),
// compatible with Microsoft Entra ID or any compliant IdP (M6
// requirement 1). Explicit, auditable steps — no NextAuth/Auth.js
// black box: discovery, authorize-URL construction, code exchange and
// ID-token validation are each one visible function here.
import { createRemoteJWKSet, jwtVerify } from "jose";
import { config } from "./config";

interface DiscoveryDocument {
  authorization_endpoint: string;
  token_endpoint: string;
  jwks_uri: string;
  issuer: string;
}

let discoveryCache: DiscoveryDocument | null = null;

async function getDiscoveryDocument(): Promise<DiscoveryDocument> {
  if (discoveryCache) return discoveryCache;
  const response = await fetch(`${config.oidcIssuer}/.well-known/openid-configuration`, {
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`OIDC discovery failed: ${response.status}`);
  discoveryCache = await response.json();
  return discoveryCache!;
}

export function isRealOidcConfigured(): boolean {
  return Boolean(config.oidcIssuer && config.oidcClientId && config.oidcRedirectUri);
}

export async function buildAuthorizationUrl(params: {
  state: string;
  nonce: string;
  forceFreshLogin: boolean;
}): Promise<string> {
  const doc = await getDiscoveryDocument();
  const url = new URL(doc.authorization_endpoint);
  url.searchParams.set("client_id", config.oidcClientId);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("redirect_uri", config.oidcRedirectUri);
  url.searchParams.set("scope", "openid profile email");
  url.searchParams.set("state", params.state);
  url.searchParams.set("nonce", params.nonce);
  // Step-up re-authentication (M6 requirement 5): forces a fresh
  // interactive login at the IdP rather than a silent SSO refresh,
  // used before minting an approval assertion above the configured
  // dual-control threshold.
  if (params.forceFreshLogin) url.searchParams.set("prompt", "login");
  return url.toString();
}

export async function exchangeCodeForIdToken(code: string): Promise<string> {
  const doc = await getDiscoveryDocument();
  const response = await fetch(doc.token_endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: config.oidcRedirectUri,
      client_id: config.oidcClientId,
      client_secret: config.oidcClientSecret,
    }),
  });
  if (!response.ok) throw new Error(`OIDC token exchange failed: ${response.status}`);
  const body = await response.json();
  return body.id_token as string;
}

export interface VerifiedIdToken {
  subject: string;
  email: string;
  displayName: string;
  issuer: string;
  tenantId: string;
}

/** Validates signature (against the IdP's own JWKS), issuer, audience
 * and expiry — never trusts a claim the token itself doesn't carry.
 * ``tid`` (Entra ID's tenant claim) is what resolves an organization,
 * never the email domain (M6 requirement 1's explicit anti-spoofing
 * rule). */
export async function verifyIdToken(idToken: string, expectedNonce: string): Promise<VerifiedIdToken> {
  const doc = await getDiscoveryDocument();
  const jwks = createRemoteJWKSet(new URL(doc.jwks_uri));
  const { payload } = await jwtVerify(idToken, jwks, {
    issuer: doc.issuer,
    audience: config.oidcClientId,
  });
  if (payload.nonce !== expectedNonce) {
    throw new Error("ID token nonce does not match the request that started this login.");
  }
  const tenantId = (payload.tid as string | undefined) ?? (payload.iss as string);
  return {
    subject: payload.sub as string,
    email: (payload.email as string | undefined) ?? (payload.preferred_username as string) ?? "",
    displayName: (payload.name as string | undefined) ?? (payload.sub as string),
    issuer: payload.iss as string,
    tenantId,
  };
}
