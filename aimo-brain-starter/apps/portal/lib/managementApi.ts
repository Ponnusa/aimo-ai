// Client for an organization's own buyer-agent/supplier-agent
// management API (M6, docs/ARCHITECTURE.md's M6 section). Every call
// mints a fresh, short-lived "manage_organization"-scoped service
// credential from network-api first — the portal never holds a
// long-lived credential for another service.
import { agentUrlForOrganization } from "./agentRegistry";
import { mintServiceCredential } from "./networkApi";

export interface NegotiationOut {
  negotiation_id: string;
  counterparty_organization_id: string;
  counterparty_agent_id: string;
  state: string;
  round_number: number;
  created_at: string;
  updated_at: string;
}

export interface ApprovalOut {
  id: number;
  negotiation_id: string;
  content_hash: string;
  status: string;
  policy_version: string;
  requested_at: string;
  expires_at: string;
  decided_at: string | null;
}

export interface EvidenceOut {
  negotiation_id: string;
  content_hash: string;
  source: string;
  allow: boolean;
  human_approval_required: boolean;
  deny_reason: string | null;
  policy_version: string;
  created_at: string;
}

export interface OrderOut {
  negotiation_id: string;
  content_hash: string;
  idempotency_key: string;
  state: string;
  external_order_id: string | null;
}

export interface PolicyCatalogOut {
  role: string;
  active_version: string;
  available_versions: string[];
}

async function managementFetch(
  organizationId: string,
  path: string,
  init: RequestInit = {},
  options: { asAdminAction?: boolean } = {},
): Promise<Response> {
  const token = await mintServiceCredential(organizationId, "manage_organization", {
    asAdminAction: options.asAdminAction,
  });
  const baseUrl = agentUrlForOrganization(organizationId);
  return fetch(`${baseUrl}/management${path}`, {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
}

export async function listNegotiations(
  organizationId: string,
  state?: string,
): Promise<NegotiationOut[]> {
  const query = state ? `?state=${encodeURIComponent(state)}` : "";
  const response = await managementFetch(organizationId, `/negotiations${query}`);
  if (!response.ok) throw new Error(`list negotiations failed: ${response.status}`);
  return response.json();
}

export async function listApprovals(
  organizationId: string,
  status: string | null = "PENDING",
): Promise<ApprovalOut[]> {
  const query = `?status=${encodeURIComponent(status ?? "ALL")}`;
  const response = await managementFetch(organizationId, `/approvals${query}`);
  if (!response.ok) throw new Error(`list approvals failed: ${response.status}`);
  return response.json();
}

export async function getApproval(organizationId: string, approvalId: number): Promise<ApprovalOut> {
  const response = await managementFetch(organizationId, `/approvals/${approvalId}`);
  if (!response.ok) throw new Error(`get approval failed: ${response.status}`);
  return response.json();
}

export async function decideApproval(
  organizationId: string,
  approvalId: number,
  decision: "APPROVED" | "REJECTED",
  approvalToken: string,
): Promise<{ ok: true; approval: ApprovalOut } | { ok: false; error: string }> {
  const response = await managementFetch(organizationId, `/approvals/${approvalId}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, approval_token: approvalToken }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    return { ok: false, error: body.detail ?? `status ${response.status}` };
  }
  return { ok: true, approval: await response.json() };
}

export async function listEvidence(organizationId: string): Promise<EvidenceOut[]> {
  const response = await managementFetch(organizationId, `/evidence`);
  if (!response.ok) throw new Error(`list evidence failed: ${response.status}`);
  return response.json();
}

export async function listOrders(organizationId: string): Promise<OrderOut[]> {
  const response = await managementFetch(organizationId, `/orders`);
  if (!response.ok) throw new Error(`list orders failed: ${response.status}`);
  return response.json();
}

export async function getPolicyCatalog(organizationId: string): Promise<PolicyCatalogOut> {
  const response = await managementFetch(organizationId, `/policy/catalog`);
  if (!response.ok) throw new Error(`policy catalog failed: ${response.status}`);
  return response.json();
}

export async function policyDryRun(
  organizationId: string,
  body: { policy_version: string; counterparty_organization_id: string; terms: Record<string, unknown> },
): Promise<{ allow: boolean; human_approval_required: boolean; deny_reason: string | null }> {
  const response = await managementFetch(organizationId, `/policy/dry-run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`policy dry-run failed: ${response.status}`);
  return response.json();
}

export async function activatePolicy(
  organizationId: string,
  policyVersion: string,
  activatedBy: string,
): Promise<PolicyCatalogOut> {
  const response = await managementFetch(
    organizationId,
    `/policy/activate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ policy_version: policyVersion, activated_by: activatedBy }),
    },
    { asAdminAction: true },
  );
  if (!response.ok) throw new Error(`policy activate failed: ${response.status}`);
  return response.json();
}
