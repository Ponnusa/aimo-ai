import { randomUUID } from "node:crypto";
import { cookies } from "next/headers";
import { config } from "./config";
import { getDb } from "./db";

export interface PortalSession {
  id: string;
  organizationId: string;
  oidcSubject: string;
  email: string;
  displayName: string;
  createdAt: string;
  reauthenticatedAt: string;
}

export function createSession(params: {
  organizationId: string;
  oidcSubject: string;
  email: string;
  displayName: string;
}): string {
  const id = randomUUID();
  const now = new Date().toISOString();
  getDb()
    .prepare(
      `INSERT INTO sessions (id, organization_id, oidc_subject, email, display_name, created_at, reauthenticated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    )
    .run(id, params.organizationId, params.oidcSubject, params.email, params.displayName, now, now);
  return id;
}

export function getSessionById(id: string): PortalSession | null {
  const row = getDb().prepare(`SELECT * FROM sessions WHERE id = ?`).get(id) as
    | Record<string, string>
    | undefined;
  if (!row) return null;
  return {
    id: row.id,
    organizationId: row.organization_id,
    oidcSubject: row.oidc_subject,
    email: row.email,
    displayName: row.display_name,
    createdAt: row.created_at,
    reauthenticatedAt: row.reauthenticated_at,
  };
}

export function touchReauthentication(id: string): void {
  getDb()
    .prepare(`UPDATE sessions SET reauthenticated_at = ? WHERE id = ?`)
    .run(new Date().toISOString(), id);
}

export function destroySessionById(id: string): void {
  getDb().prepare(`DELETE FROM sessions WHERE id = ?`).run(id);
}

/** Server Components only: reads the session, never issues one — only
 * a Route Handler/Server Action may set the cookie. Every privileged
 * data fetch re-derives the caller's role from network-api (see
 * lib/networkApi.ts.getMyRoles), never from anything cached here. */
export async function getCurrentSession(): Promise<PortalSession | null> {
  const store = await cookies();
  const id = store.get(config.sessionCookieName)?.value;
  if (!id) return null;
  return getSessionById(id);
}

export async function setSessionCookie(id: string): Promise<void> {
  const store = await cookies();
  store.set(config.sessionCookieName, id, {
    httpOnly: true,
    secure: config.environment !== "local",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
}

export async function clearSessionCookie(): Promise<void> {
  const store = await cookies();
  const id = store.get(config.sessionCookieName)?.value;
  if (id) destroySessionById(id);
  store.delete(config.sessionCookieName);
}
