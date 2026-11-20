"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { recordPortalAction } from "../../lib/audit";
import { addMember, isActiveMemberWithRole, revokeMember } from "../../lib/networkApi";
import { getCurrentSession } from "../../lib/session";

async function requireAdmin(session: { organizationId: string; oidcSubject: string }) {
  const isAdmin = await isActiveMemberWithRole(session.organizationId, session.oidcSubject, "admin");
  if (!isAdmin) throw new Error("You do not hold the admin role for this organization.");
}

export async function addMemberAction(formData: FormData): Promise<void> {
  const session = await getCurrentSession();
  if (!session) redirect("/login");
  await requireAdmin(session);

  const oidcSubject = String(formData.get("oidc_subject"));
  const email = String(formData.get("email"));
  const displayName = String(formData.get("display_name"));
  const role = String(formData.get("role"));

  await addMember(session.organizationId, {
    oidc_subject: oidcSubject,
    email,
    display_name: displayName,
    role,
  });
  recordPortalAction({
    organizationId: session.organizationId,
    oidcSubject: session.oidcSubject,
    action: "add_member",
    detail: { oidcSubject, role },
  });
  revalidatePath("/members");
}

export async function revokeMemberAction(formData: FormData): Promise<void> {
  const session = await getCurrentSession();
  if (!session) redirect("/login");
  await requireAdmin(session);

  const memberId = Number(formData.get("member_id"));
  await revokeMember(session.organizationId, memberId);
  recordPortalAction({
    organizationId: session.organizationId,
    oidcSubject: session.oidcSubject,
    action: "revoke_member",
    detail: { memberId },
  });
  revalidatePath("/members");
}
