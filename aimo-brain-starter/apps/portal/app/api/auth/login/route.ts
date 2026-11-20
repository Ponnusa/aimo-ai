import { randomUUID } from "node:crypto";
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { buildAuthorizationUrl } from "../../../../lib/oidc";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const state = randomUUID();
  const nonce = randomUUID();
  const forceFreshLogin = request.nextUrl.searchParams.get("force") === "1";
  const returnTo = request.nextUrl.searchParams.get("returnTo") ?? "/dashboard";

  const authorizationUrl = await buildAuthorizationUrl({ state, nonce, forceFreshLogin });

  const store = await cookies();
  store.set("oidc_state", state, { httpOnly: true, sameSite: "lax", maxAge: 600, path: "/" });
  store.set("oidc_nonce", nonce, { httpOnly: true, sameSite: "lax", maxAge: 600, path: "/" });
  store.set("oidc_return_to", returnTo, { httpOnly: true, sameSite: "lax", maxAge: 600, path: "/" });

  return NextResponse.redirect(authorizationUrl);
}
