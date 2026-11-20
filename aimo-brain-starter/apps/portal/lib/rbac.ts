// Role matrix (M6, docs/ARCHITECTURE.md's M6 section table). Used only
// to decide what the portal's own UI shows/allows attempting — every
// privileged backend action is independently re-checked server-side by
// network-api/the owning agent, never trusted from this alone.
export type Role = "admin" | "operator" | "approver" | "auditor";

export function canManageMembers(roles: string[]): boolean {
  return roles.includes("admin");
}

export function canManageAgentsAndRelationships(roles: string[]): boolean {
  return roles.includes("admin") || roles.includes("operator");
}

export function canActivatePolicy(roles: string[]): boolean {
  return roles.includes("admin");
}

/** Mirrors the agent's own ``approver_role_required_strict`` default
 * (on): only ``approver``-role members may decide approvals in this
 * reference deployment, never ``admin`` alone. */
export function canDecideApprovals(roles: string[]): boolean {
  return roles.includes("approver");
}
