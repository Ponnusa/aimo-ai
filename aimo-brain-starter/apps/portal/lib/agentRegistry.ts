import { config } from "./config";

/** Maps an organization id to its own agent's base URL (M6 reference
 * deployment: exactly the fixed buyer/supplier pair every other
 * milestone uses — see the docstring on config.buyerAgentUrl). */
export function agentUrlForOrganization(organizationId: string): string {
  if (organizationId === config.buyerOrganizationId) return config.buyerAgentUrl;
  if (organizationId === config.supplierOrganizationId) return config.supplierAgentUrl;
  throw new Error(`No known agent for organization ${organizationId}`);
}
