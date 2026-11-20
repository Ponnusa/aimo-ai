import { NextRequest, NextResponse } from "next/server";
import { config, isDevAuthAllowed } from "../../../../lib/config";
import { createSession, setSessionCookie, touchReauthentication } from "../../../../lib/session";

/** The clearly-labeled development login adapter (M6): sets a session
 * directly from form fields, with no identity-provider round trip at
 * all. Refused outright outside local/staging/test, exactly like
 * identity.dev_oidc on the Python side — never silently available in
 * production. */
export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isDevAuthAllowed()) {
    return NextResponse.json(
      { error: "The development login adapter is disabled outside local/staging/test." },
      { status: 403 },
    );
  }
  const form = await request.formData();
  const organizationId = String(form.get("organization_id") ?? "");
  const oidcSubject = String(form.get("oidc_subject") ?? "");
  const email = String(form.get("email") ?? "");
  const displayName = String(form.get("display_name") ?? oidcSubject);

  if (!organizationId || !oidcSubject || !email) {
    return NextResponse.json({ error: "Missing required fields." }, { status: 400 });
  }

  const sessionId = createSession({ organizationId, oidcSubject, email, displayName });
  touchReauthentication(sessionId);
  await setSessionCookie(sessionId);

  return NextResponse.redirect(new URL("/dashboard", config.portalPublicUrl));
}
