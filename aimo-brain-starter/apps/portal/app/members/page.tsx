import { Shell } from "../../components/Shell";
import { listMembers } from "../../lib/networkApi";
import { canManageMembers } from "../../lib/rbac";
import { requireSession } from "../../lib/requireSession";
import { addMemberAction, revokeMemberAction } from "./actions";

export default async function MembersPage() {
  const { session, roles } = await requireSession();
  const canManage = canManageMembers(roles);
  const members = canManage ? await listMembers(session.organizationId) : [];

  return (
    <Shell session={session} roles={roles}>
      <h1 className="mb-6 text-2xl font-semibold">Organization members</h1>
      {!canManage ? (
        <p className="text-slate-500">You do not hold the admin role for this organization.</p>
      ) : (
        <>
          <table className="mb-10 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-300 text-left text-slate-500">
                <th className="py-2">Name</th>
                <th className="py-2">Email</th>
                <th className="py-2">Role</th>
                <th className="py-2">Status</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr key={member.id} className="border-b border-slate-100">
                  <td className="py-2">{member.display_name}</td>
                  <td className="py-2">{member.email}</td>
                  <td className="py-2">{member.role}</td>
                  <td className="py-2">{member.status}</td>
                  <td className="py-2">
                    {member.status === "active" && (
                      <form action={revokeMemberAction}>
                        <input type="hidden" name="member_id" value={member.id} />
                        <button type="submit" className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-100">
                          Revoke
                        </button>
                      </form>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <form action={addMemberAction} className="flex max-w-md flex-col gap-3 rounded border border-slate-200 bg-white p-5">
            <h2 className="font-semibold">Add a member</h2>
            <label className="text-sm text-slate-700">
              Subject id (from the identity provider)
              <input name="oidc_subject" required className="mt-1 w-full rounded border border-slate-300 px-2 py-1" />
            </label>
            <label className="text-sm text-slate-700">
              Email
              <input name="email" type="email" required className="mt-1 w-full rounded border border-slate-300 px-2 py-1" />
            </label>
            <label className="text-sm text-slate-700">
              Display name
              <input name="display_name" required className="mt-1 w-full rounded border border-slate-300 px-2 py-1" />
            </label>
            <label className="text-sm text-slate-700">
              Role
              <select name="role" className="mt-1 w-full rounded border border-slate-300 px-2 py-1">
                <option value="admin">admin</option>
                <option value="operator">operator</option>
                <option value="approver">approver</option>
                <option value="auditor">auditor</option>
              </select>
            </label>
            <button type="submit" className="rounded bg-slate-900 px-4 py-2 text-white hover:bg-slate-700">
              Add member
            </button>
          </form>
        </>
      )}
    </Shell>
  );
}
