"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { recordPortalAction } from "../../lib/audit";
import { isActiveMemberWithRole } from "../../lib/networkApi";
import { transitionAgent } from "../../lib/registryApi";
import { getCurrentSession } from "../../lib/session";

export async function transitionAgentAction(formData: FormData): Promise<void> {
  const session = await getCurrentSession();
  if (!session) redirect("/login");

  const agentId = String(formData.get("agent_id"));
  const action = String(formData.get("action")) as "suspend" | "reactivate" | "revoke";

  const [isAdmin, isOperator] = await Promise.all([
    isActiveMemberWithRole(session.organizationId, session.oidcSubject, "admin"),
    isActiveMemberWithRole(session.organizationId, session.oidcSubject, "operator"),
  ]);
  if (!isAdmin && !isOperator) {
    throw new Error("You do not hold the admin or operator role for this organization.");
  }

  await transitionAgent(session.organizationId, agentId, action);
  recordPortalAction({
    organizationId: session.organizationId,
    oidcSubject: session.oidcSubject,
    action: `agent_${action}`,
    detail: { agentId },
  });
  revalidatePath("/agents");
}
