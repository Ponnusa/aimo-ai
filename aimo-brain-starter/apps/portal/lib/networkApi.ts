// network-api client (M6). Every call here uses the portal's own
// server-held secrets (admin key or internal-service key) — never
// exposed to the browser. See docs/ARCHITECTURE.md's M6 section.
import { config } from "./config";

export interface MemberOut {
  id: number;
  organization_id: string;
  oidc_subject: string;
  email: string;
  display_name: string;
  role: string;
  status: string;
}

export interface RelationshipOut {
  id: number;
  buyer_organization_id: string;
  supplier_organization_id: string;
  status: string;
}

export interface OrganizationOut {
  organization_id: string;
  display_name: string;
  verified: boolean;
}

async function adminFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${config.networkApiUrl}${path}`, {
    ...init,
    headers: { ...init.headers, "X-Admin-Key": config.networkAdminApiKey },
    cache: "no-store",
  });
}

async function internalFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${config.networkApiUrl}${path}`, {
    ...init,
    headers: { ...init.headers, "X-Internal-Service-Key": config.networkInternalServiceKey },
    cache: "no-store",
  });
}

export async function getOrganizationByOidcTenant(
  oidcIssuer: string,
  oidcTenantId: string,
): Promise<{ organization_id: string; display_name: string } | null> {
  const response = await internalFetch(
    `/organizations/by-oidc-tenant?${new URLSearchParams({ oidc_issuer: oidcIssuer, oidc_tenant_id: oidcTenantId })}`,
  );
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`network-api by-oidc-tenant failed: ${response.status}`);
  return response.json();
}

export async function listOrganizations(): Promise<OrganizationOut[]> {
  const response = await fetch(`${config.networkApiUrl}/organizations`, { cache: "no-store" });
  if (!response.ok) throw new Error(`network-api list organizations failed: ${response.status}`);
  return response.json();
}

export async function isActiveMemberWithRole(
  organizationId: string,
  oidcSubject: string,
  role: string,
): Promise<boolean> {
  const response = await internalFetch(
    `/members/lookup?${new URLSearchParams({ organization_id: organizationId, oidc_subject: oidcSubject, role })}`,
  );
  if (!response.ok) throw new Error(`network-api member lookup failed: ${response.status}`);
  const body = await response.json();
  return Boolean(body.active);
}

const MEMBER_ROLES = ["admin", "operator", "approver", "auditor"] as const;

/** Resolves every role the signed-in subject currently, actively holds
 * for ``organizationId`` — always a fresh lookup, never cached on the
 * session, so a revoked role takes effect on the very next call. */
export async function getMyRoles(organizationId: string, oidcSubject: string): Promise<string[]> {
  const results = await Promise.all(
    MEMBER_ROLES.map(async (role) => ({
      role,
      active: await isActiveMemberWithRole(organizationId, oidcSubject, role),
    })),
  );
  return results.filter((r) => r.active).map((r) => r.role);
}

export async function listMembers(organizationId: string): Promise<MemberOut[]> {
  const response = await adminFetch(`/organizations/${organizationId}/members`);
  if (!response.ok) throw new Error(`network-api list members failed: ${response.status}`);
  return response.json();
}

export async function addMember(
  organizationId: string,
  body: { oidc_subject: string; email: string; display_name: string; role: string },
): Promise<MemberOut> {
  const response = await adminFetch(`/organizations/${organizationId}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`network-api add member failed: ${response.status}`);
  return response.json();
}

export async function revokeMember(organizationId: string, memberId: number): Promise<MemberOut> {
  const response = await adminFetch(
    `/organizations/${organizationId}/members/${memberId}/revoke`,
    { method: "POST" },
  );
  if (!response.ok) throw new Error(`network-api revoke member failed: ${response.status}`);
  return response.json();
}

export async function listRelationshipsForOrganization(
  organizationId: string,
): Promise<RelationshipOut[]> {
  const response = await internalFetch(`/organizations/${organizationId}/relationships`);
  if (!response.ok) throw new Error(`network-api list relationships failed: ${response.status}`);
  return response.json();
}

export async function createRelationship(
  buyerOrganizationId: string,
  supplierOrganizationId: string,
): Promise<RelationshipOut> {
  const response = await adminFetch(`/relationships`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      buyer_organization_id: buyerOrganizationId,
      supplier_organization_id: supplierOrganizationId,
    }),
  });
  if (!response.ok) throw new Error(`network-api create relationship failed: ${response.status}`);
  return response.json();
}

export async function transitionRelationship(
  relationshipId: number,
  action: "suspend" | "revoke" | "reactivate",
): Promise<RelationshipOut> {
  const response = await adminFetch(`/relationships/${relationshipId}/${action}`, {
    method: "POST",
  });
  if (!response.ok) throw new Error(`network-api ${action} relationship failed: ${response.status}`);
  return response.json();
}

export async function getJwks(organizationId: string): Promise<{ keys: Array<Record<string, string>> }> {
  const response = await fetch(`${config.networkApiUrl}/organizations/${organizationId}/jwks.json`, {
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`network-api jwks failed: ${response.status}`);
  return response.json();
}

/** Mints a short-lived scoped service credential the portal uses to
 * call registry-api (``manage_agents``) or an agent's own management
 * API (``manage_organization``) on behalf of the signed-in human's own
 * organization. ``asAdminAction`` sets the sentinel ``agent_id`` an
 * agent's management API requires for admin-only actions (policy
 * activation) — the portal sets it only after confirming the caller
 * holds the ``admin`` role itself. */
export async function mintServiceCredential(
  organizationId: string,
  scope: string,
  options: { asAdminAction?: boolean; ttlSeconds?: number } = {},
): Promise<string> {
  const response = await adminFetch(`/organizations/${organizationId}/service-credentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      scope,
      ttl_seconds: options.ttlSeconds ?? 60,
      agent_id: options.asAdminAction ? "portal-admin-action" : null,
    }),
  });
  if (!response.ok) throw new Error(`network-api mint credential failed: ${response.status}`);
  const body = await response.json();
  return body.token as string;
}
