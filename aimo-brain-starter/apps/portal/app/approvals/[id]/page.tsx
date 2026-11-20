import { Shell } from "../../../components/Shell";
import { getApproval } from "../../../lib/managementApi";
import { canDecideApprovals } from "../../../lib/rbac";
import { requireSession } from "../../../lib/requireSession";
import { decideApprovalAction } from "./actions";

export default async function ApprovalDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const { session, roles } = await requireSession();
  const approval = await getApproval(session.organizationId, Number(id));
  const canDecide = canDecideApprovals(roles) && approval.status === "PENDING";

  return (
    <Shell session={session} roles={roles}>
      <h1 className="mb-6 text-2xl font-semibold">Approval #{approval.id}</h1>
      <dl className="grid grid-cols-1 gap-3 rounded border border-slate-200 bg-white p-5 text-sm sm:grid-cols-2">
        <Field label="Status" value={approval.status} />
        <Field label="Negotiation" value={approval.negotiation_id} />
        <Field label="Content hash" value={approval.content_hash} mono />
        <Field label="Policy version" value={approval.policy_version} />
        <Field label="Requested" value={new Date(approval.requested_at).toLocaleString()} />
        <Field label="Expires" value={new Date(approval.expires_at).toLocaleString()} />
      </dl>

      {!canDecide && approval.status === "PENDING" && (
        <p className="mt-6 text-sm text-slate-500">
          You do not hold the approver role for this organization — you can view this approval
          but not decide it.
        </p>
      )}

      {canDecide && (
        <form action={decideApprovalAction} className="mt-6 flex gap-3">
          <input type="hidden" name="approval_id" value={approval.id} />
          <button
            type="submit"
            name="decision"
            value="APPROVED"
            className="rounded bg-emerald-600 px-4 py-2 text-white hover:bg-emerald-700"
          >
            Approve
          </button>
          <button
            type="submit"
            name="decision"
            value="REJECTED"
            className="rounded bg-red-600 px-4 py-2 text-white hover:bg-red-700"
          >
            Reject
          </button>
        </form>
      )}
    </Shell>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-slate-500">{label}</dt>
      <dd className={mono ? "font-mono text-xs" : ""}>{value}</dd>
    </div>
  );
}
