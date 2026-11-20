// registry-api client (M6). Agent registration/lifecycle already
// existed (M2); this just gives the portal a typed way to call it with
// a freshly-minted "manage_agents" credential, never a long-lived one.
import { config } from "./config";
import { mintServiceCredential } from "./networkApi";

export interface AgentManagementOut {
  agent_id: string;
  organization_id: string;
  display_name: string;
  a2a_card_url: string;
  status: string;
  card_fingerprint: string;
  capabilities: string[];
  registered_at: string;
  updated_at: string;
  last_verified_at: string | null;
}

export async function listAgentsForOrganization(organizationId: string): Promise<AgentManagementOut[]> {
  const token = await mintServiceCredential(organizationId, "manage_agents");
  const response = await fetch(`${config.registryApiUrl}/organizations/${organizationId}/agents`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`registry-api list agents failed: ${response.status}`);
  return response.json();
}

export async function transitionAgent(
  organizationId: string,
  agentId: string,
  action: "suspend" | "reactivate" | "revoke",
): Promise<AgentManagementOut> {
  const token = await mintServiceCredential(organizationId, "manage_agents");
  const response = await fetch(
    `${config.registryApiUrl}/agents/${agentId}/${action}?organization_id=${encodeURIComponent(organizationId)}`,
    { method: "POST", headers: { Authorization: `Bearer ${token}` } },
  );
  if (!response.ok) throw new Error(`registry-api ${action} agent failed: ${response.status}`);
  return response.json();
}
