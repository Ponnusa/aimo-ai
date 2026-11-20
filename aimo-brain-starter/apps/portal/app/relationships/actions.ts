"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { recordPortalAction } from "../../lib/audit";
import {
  createRelationship,
  isActiveMemberWithRole,
  transitionRelationship,
} from "../../lib/networkApi";
import { getCurrentSession } from "../../lib/session";

async function requireManageRole(session: { organizationId: string; oidcSubject: string }) {
  const [isAdmin, isOperator] = await Promise.all([
    isActiveMemberWithRole(session.organizationId, session.oidcSubject, "admin"),
    isActiveMemberWithRole(session.organizationId, session.oidcSubject, "operator"),
  ]);
  if (!isAdmin && !isOperator) {
    throw new Error("You do not hold the admin or operator role for this organization.");
  }
}

export async function requestRelationshipAction(formData: FormData): Promise<void> {
  const session = await getCurrentSession();
  if (!session) redirect("/login");
  await requireManageRole(session);

  const counterpartyOrganizationId = String(formData.get("counterparty_organization_id"));
  const asBuyer = formData.get("as_buyer") === "on";
  const [buyer, supplier] = asBuyer
    ? [session.organizationId, counterpartyOrganizationId]
    : [counterpartyOrganizationId, session.organizationId];

  await createRelationship(buyer, supplier);
  recordPortalAction({
    organizationId: session.organizationId,
    oidcSubject: session.oidcSubject,
    action: "request_relationship",
    detail: { buyer, supplier },
  });
  revalidatePath("/relationships");
}

export async function transitionRelationshipAction(formData: FormData): Promise<void> {
  const session = await getCurrentSession();
  if (!session) redirect("/login");
  await requireManageRole(session);

  const relationshipId = Number(formData.get("relationship_id"));
  const action = String(formData.get("action")) as "suspend" | "revoke" | "reactivate";

  await transitionRelationship(relationshipId, action);
  recordPortalAction({
    organizationId: session.organizationId,
    oidcSubject: session.oidcSubject,
    action: `relationship_${action}`,
    detail: { relationshipId },
  });
  revalidatePath("/relationships");
}
