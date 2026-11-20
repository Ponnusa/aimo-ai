import { redirect } from "next/navigation";
import { getMyRoles } from "./networkApi";
import { getCurrentSession, PortalSession } from "./session";

/** Every protected Server Component calls this first. Roles are
 * resolved fresh from network-api on every call — never cached on the
 * session — so a role revoked mid-session stops granting access on the
 * very next page load (M6 requirement: "never rely on client-side
 * permission checks," extended here to "never rely on session-cached
 * permission checks" either). */
export async function requireSession(): Promise<{
  session: PortalSession;
  roles: string[];
}> {
  const session = await getCurrentSession();
  if (!session) redirect("/login");
  const roles = await getMyRoles(session.organizationId, session.oidcSubject);
  return { session, roles };
}
