"use server";

import { redirect } from "next/navigation";
import { recordPortalAction } from "../../../lib/audit";
import { config } from "../../../lib/config";
import { decideApproval, getApproval } from "../../../lib/managementApi";
import { isActiveMemberWithRole } from "../../../lib/networkApi";
import { issuePortalApprovalToken } from "../../../lib/portalSigning";
import { getCurrentSession, getSessionById } from "../../../lib/session";

const STEP_UP_MAX_AGE_SECONDS = 5 * 60;

export async function decideApprovalAction(formData: FormData): Promise<void> {
  const session = await getCurrentSession();
  if (!session) redirect("/login");

  const approvalId = Number(formData.get("approval_id"));
  const decision = String(formData.get("decision"));
  if (decision !== "APPROVED" && decision !== "REJECTED") {
    throw new Error("Invalid decision.");
  }

  // Server-side re-check: the page only *hides* the button for a
  // non-approver, but the actual authorization decision is made here
  // and, independently again, by the agent itself in decide_approval.
  const [isApprover, approval] = await Promise.all([
    isActiveMemberWithRole(session.organizationId, session.oidcSubject, "approver"),
    getApproval(session.organizationId, approvalId),
  ]);
  if (!isApprover) {
    throw new Error("You do not hold the approver role for this organization.");
  }

  if (config.stepUpThresholdAmount !== null) {
    const fresh = await getSessionById(session.id);
    const reauthenticatedAt = fresh ? new Date(fresh.reauthenticatedAt).getTime() : 0;
    if (Date.now() - reauthenticatedAt > STEP_UP_MAX_AGE_SECONDS * 1000) {
      redirect(
        `/api/auth/login?force=1&returnTo=${encodeURIComponent(`/approvals/${approvalId}`)}`,
      );
    }
  }

  const approvalToken = await issuePortalApprovalToken({
    organizationId: session.organizationId,
    approverSubject: session.oidcSubject,
    negotiationId: approval.negotiation_id,
    contentHash: approval.content_hash,
  });

  const result = await decideApproval(session.organizationId, approvalId, decision, approvalToken);

  recordPortalAction({
    organizationId: session.organizationId,
    oidcSubject: session.oidcSubject,
    action: "decide_approval",
    detail: { approvalId, decision, result },
  });

  if (!result.ok) {
    throw new Error(result.error);
  }

  redirect(`/approvals/${approvalId}`);
}
