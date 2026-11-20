"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { recordPortalAction } from "../../lib/audit";
import { activatePolicy, policyDryRun } from "../../lib/managementApi";
import { isActiveMemberWithRole } from "../../lib/networkApi";
import { getCurrentSession } from "../../lib/session";

export interface DryRunResult {
  allow: boolean;
  human_approval_required: boolean;
  deny_reason: string | null;
}

export async function policyDryRunAction(
  _previous: DryRunResult | null,
  formData: FormData,
): Promise<DryRunResult> {
  const session = await getCurrentSession();
  if (!session) redirect("/login");

  const policyVersion = String(formData.get("policy_version"));
  const counterpartyOrganizationId = String(formData.get("counterparty_organization_id"));
  const totalAmount = String(formData.get("total_amount"));

  const result = await policyDryRun(session.organizationId, {
    policy_version: policyVersion,
    counterparty_organization_id: counterpartyOrganizationId,
    terms: { total_amount: totalAmount, currency: "EUR" },
  });
  recordPortalAction({
    organizationId: session.organizationId,
    oidcSubject: session.oidcSubject,
    action: "policy_dry_run",
    detail: { policyVersion, counterpartyOrganizationId, totalAmount, result },
  });
  return result;
}

export async function activatePolicyAction(formData: FormData): Promise<void> {
  const session = await getCurrentSession();
  if (!session) redirect("/login");

  const isAdmin = await isActiveMemberWithRole(session.organizationId, session.oidcSubject, "admin");
  if (!isAdmin) {
    throw new Error("You do not hold the admin role for this organization.");
  }

  const policyVersion = String(formData.get("policy_version"));
  await activatePolicy(session.organizationId, policyVersion, session.oidcSubject);
  recordPortalAction({
    organizationId: session.organizationId,
    oidcSubject: session.oidcSubject,
    action: "policy_activate",
    detail: { policyVersion },
  });
  revalidatePath("/policy");
}
