import { Shell } from "../../components/Shell";
import { listNegotiations } from "../../lib/managementApi";
import { requireSession } from "../../lib/requireSession";

const NONBINDING_STATES = new Set(["REQUESTED", "PROPOSED", "NEGOTIATING"]);
const AUTHORIZED_STATES = new Set(["PENDING_AUTHORIZATION", "AUTHORIZED"]);

function phase(state: string): string {
  if (NONBINDING_STATES.has(state)) return "Nonbinding";
  if (AUTHORIZED_STATES.has(state)) return "Authorized";
  if (["COMMITTING", "COMMITTED"].includes(state)) return "Executed";
  return "Terminal";
}

export default async function NegotiationsPage() {
  const { session, roles } = await requireSession();
  const negotiations = await listNegotiations(session.organizationId);

  return (
    <Shell session={session} roles={roles}>
      <h1 className="mb-6 text-2xl font-semibold">Negotiations</h1>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-300 text-left text-slate-500">
            <th className="py-2">Negotiation</th>
            <th className="py-2">Counterparty</th>
            <th className="py-2">State</th>
            <th className="py-2">Phase</th>
            <th className="py-2">Round</th>
            <th className="py-2">Updated</th>
          </tr>
        </thead>
        <tbody>
          {negotiations.map((negotiation) => (
            <tr key={negotiation.negotiation_id} className="border-b border-slate-100">
              <td className="py-2">{negotiation.negotiation_id}</td>
              <td className="py-2">{negotiation.counterparty_organization_id}</td>
              <td className="py-2">{negotiation.state}</td>
              <td className="py-2">{phase(negotiation.state)}</td>
              <td className="py-2">{negotiation.round_number}</td>
              <td className="py-2">{new Date(negotiation.updated_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Shell>
  );
}
