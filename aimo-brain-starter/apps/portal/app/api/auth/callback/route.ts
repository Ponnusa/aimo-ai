import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { config } from "../../../../lib/config";
import { getOrganizationByOidcTenant } from "../../../../lib/networkApi";
import { exchangeCodeForIdToken, verifyIdToken } from "../../../../lib/oidc";
import { createSession, setSessionCookie, touchReauthentication } from "../../../../lib/session";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  const store = await cookies();
  const expectedState = store.get("oidc_state")?.value;
  const expectedNonce = store.get("oidc_nonce")?.value;
  const returnTo = store.get("oidc_return_to")?.value ?? "/dashboard";
  store.delete("oidc_state");
  store.delete("oidc_nonce");
  store.delete("oidc_return_to");

  if (!code || !state || !expectedState || !expectedNonce || state !== expectedState) {
    return NextResponse.redirect(new URL("/login?error=invalid_state", config.portalPublicUrl));
  }

  const idToken = await exchangeCodeForIdToken(code);
  const verified = await verifyIdToken(idToken, expectedNonce);

  // The actual anti-spoofing check (M6 requirement 1): an identity-
  // provider tenant with no mapping to an AIMO BRAIN organization
  // cannot resolve to a session at all, regardless of the token's
  // email domain.
  const organization = await getOrganizationByOidcTenant(verified.issuer, verified.tenantId);
  if (!organization) {
    return NextResponse.redirect(new URL("/login?error=unmapped_tenant", config.portalPublicUrl));
  }

  const sessionId = createSession({
    organizationId: organization.organization_id,
    oidcSubject: verified.subject,
    email: verified.email,
    displayName: verified.displayName,
  });
  touchReauthentication(sessionId);
  await setSessionCookie(sessionId);

  return NextResponse.redirect(new URL(returnTo, config.portalPublicUrl));
}
