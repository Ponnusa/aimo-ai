import { Shell } from "../../components/Shell";
import {
  getIntegrationHealth,
  listOrders,
  listReconciliationHistory,
} from "../../lib/managementApi";
import { requireSession } from "../../lib/requireSession";

const UNCERTAIN_STATES = new Set(["RECONCILIATION_REQUIRED", "FAILED"]);

export default async function IntegrationPage() {
  const { session, roles } = await requireSession();
  const [health, orders, reconciliationHistory] = await Promise.all([
    getIntegrationHealth(session.organizationId),
    listOrders(session.organizationId),
    listReconciliationHistory(session.organizationId),
  ]);
  const uncertainOrders = orders.filter((order) => UNCERTAIN_STATES.has(order.state));

  return (
    <Shell session={session} roles={roles}>
      <h1 className="mb-6 text-2xl font-semibold">Integration health</h1>

      <section className="mb-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile label="Confirmed" value={health.confirmed_orders} />
        {health.committing_orders !== undefined ? (
          <StatTile label="Committing" value={health.committing_orders} />
        ) : null}
        <StatTile label="Reconciliation required" value={health.reconciliation_required_orders} />
        <StatTile label="Failed" value={health.failed_orders} />
      </section>

      <h2 className="mb-3 text-lg font-semibold">Order submission state</h2>
      <table className="mb-8 w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-300 text-left text-slate-500">
            <th className="py-2">Negotiation</th>
            <th className="py-2">Idempotency key</th>
            <th className="py-2">State</th>
            <th className="py-2">External order reference</th>
            <th className="py-2">Attempts</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr key={order.idempotency_key} className="border-b border-slate-100">
              <td className="py-2">{order.negotiation_id}</td>
              <td className="py-2">{order.idempotency_key}</td>
              <td className="py-2">{order.state}</td>
              <td className="py-2">{order.external_order_id ?? "—"}</td>
              <td className="py-2">{order.attempts}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 className="mb-3 text-lg font-semibold">Failed and uncertain transactions</h2>
      {uncertainOrders.length === 0 ? (
        <p className="mb-8 text-sm text-slate-500">None.</p>
      ) : (
        <table className="mb-8 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-300 text-left text-slate-500">
              <th className="py-2">Negotiation</th>
              <th className="py-2">State</th>
              <th className="py-2">Last error</th>
            </tr>
          </thead>
          <tbody>
            {uncertainOrders.map((order) => (
              <tr key={order.idempotency_key} className="border-b border-slate-100">
                <td className="py-2">{order.negotiation_id}</td>
                <td className="py-2">{order.state}</td>
                <td className="py-2">{order.last_error ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2 className="mb-3 text-lg font-semibold">Reconciliation history</h2>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-300 text-left text-slate-500">
            <th className="py-2">Negotiation</th>
            <th className="py-2">Previous state</th>
            <th className="py-2">New state</th>
            <th className="py-2">Detail</th>
            <th className="py-2">When</th>
          </tr>
        </thead>
        <tbody>
          {reconciliationHistory.map((event, index) => (
            <tr
              key={`${event.order_id}-${event.created_at}-${index}`}
              className="border-b border-slate-100"
            >
              <td className="py-2">{event.negotiation_id}</td>
              <td className="py-2">{event.previous_state}</td>
              <td className="py-2">{event.new_state}</td>
              <td className="py-2">{event.detail}</td>
              <td className="py-2">{new Date(event.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Shell>
  );
}

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded border border-slate-200 p-4">
      <div className="text-2xl font-semibold">{value}</div>
      <div className="text-sm text-slate-500">{label}</div>
    </div>
  );
}
