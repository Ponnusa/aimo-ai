import { Shell } from "../../components/Shell";
import { listPortalActions } from "../../lib/audit";
import { listEvidence, listOrders } from "../../lib/managementApi";
import { requireSession } from "../../lib/requireSession";

export default async function AuditPage() {
  const { session, roles } = await requireSession();
  const [evidence, orders, portalActions] = await Promise.all([
    listEvidence(session.organizationId),
    listOrders(session.organizationId),
    Promise.resolve(listPortalActions(session.organizationId)),
  ]);

  return (
    <Shell session={session} roles={roles}>
      <h1 className="mb-6 text-2xl font-semibold">Audit and evidence</h1>

      <h2 className="mb-2 text-lg font-semibold">Policy decisions and evidence</h2>
      <p className="mb-3 text-xs text-slate-500">
        Each row is a durable, independently-evaluated policy decision for one exact negotiation
        and content hash — &ldquo;own&rdquo; is this organization&rsquo;s own decision,
        &ldquo;counterparty&rdquo; is the other side&rsquo;s, cryptographically verified on
        receipt before this row was ever written (docs/SECURITY.md&rsquo;s M5.1 section).
      </p>
      <table className="mb-10 w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-300 text-left text-slate-500">
            <th className="py-2">Negotiation</th>
            <th className="py-2">Source</th>
            <th className="py-2">Allow</th>
            <th className="py-2">Approval required</th>
            <th className="py-2">Policy version</th>
            <th className="py-2">Recorded</th>
          </tr>
        </thead>
        <tbody>
          {evidence.map((row, index) => (
            <tr key={index} className="border-b border-slate-100">
              <td className="py-2">{row.negotiation_id}</td>
              <td className="py-2">{row.source}</td>
              <td className="py-2">{String(row.allow)}</td>
              <td className="py-2">{String(row.human_approval_required)}</td>
              <td className="py-2">{row.policy_version}</td>
              <td className="py-2">{new Date(row.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 className="mb-2 text-lg font-semibold">Commitments</h2>
      <table className="mb-10 w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-300 text-left text-slate-500">
            <th className="py-2">Negotiation</th>
            <th className="py-2">State</th>
            <th className="py-2">External order id</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order, index) => (
            <tr key={index} className="border-b border-slate-100">
              <td className="py-2">{order.negotiation_id}</td>
              <td className="py-2">{order.state}</td>
              <td className="py-2 font-mono text-xs">{order.external_order_id ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 className="mb-2 text-lg font-semibold">Portal action log</h2>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-300 text-left text-slate-500">
            <th className="py-2">When</th>
            <th className="py-2">Who</th>
            <th className="py-2">Action</th>
            <th className="py-2">Detail</th>
          </tr>
        </thead>
        <tbody>
          {portalActions.map((row) => (
            <tr key={row.id} className="border-b border-slate-100">
              <td className="py-2">{new Date(row.createdAt).toLocaleString()}</td>
              <td className="py-2">{row.oidcSubject}</td>
              <td className="py-2">{row.action}</td>
              <td className="max-w-md truncate py-2 font-mono text-xs">{row.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Shell>
  );
}
