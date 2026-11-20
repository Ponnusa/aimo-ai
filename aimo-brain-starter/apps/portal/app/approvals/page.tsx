import { Shell } from "../../components/Shell";
import { listApprovals } from "../../lib/managementApi";
import { requireSession } from "../../lib/requireSession";

export default async function ApprovalsPage() {
  const { session, roles } = await requireSession();
  const approvals = await listApprovals(session.organizationId, "PENDING");

  return (
    <Shell session={session} roles={roles}>
      <h1 className="mb-6 text-2xl font-semibold">Pending approvals</h1>
      {approvals.length === 0 ? (
        <p className="text-slate-500">Nothing is pending approval right now.</p>
      ) : (
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-300 text-left text-slate-500">
              <th className="py-2">Negotiation</th>
              <th className="py-2">Content hash</th>
              <th className="py-2">Policy version</th>
              <th className="py-2">Expires</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {approvals.map((approval) => (
              <tr key={approval.id} className="border-b border-slate-100">
                <td className="py-2">{approval.negotiation_id}</td>
                <td className="max-w-xs truncate py-2 font-mono text-xs">{approval.content_hash}</td>
                <td className="py-2">{approval.policy_version}</td>
                <td className="py-2">{new Date(approval.expires_at).toLocaleString()}</td>
                <td className="py-2">
                  <a href={`/approvals/${approval.id}`} className="text-blue-700 hover:underline">
                    Review
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Shell>
  );
}
