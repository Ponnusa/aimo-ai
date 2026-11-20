import { NextResponse } from "next/server";
import { config } from "../../../../lib/config";
import { clearSessionCookie } from "../../../../lib/session";

export async function POST(): Promise<NextResponse> {
  await clearSessionCookie();
  return NextResponse.redirect(new URL("/login", config.portalPublicUrl));
}
