import { Shell } from "../../components/Shell";
import { getJwks } from "../../lib/networkApi";
import { canManageAgentsAndRelationships } from "../../lib/rbac";
import { listAgentsForOrganization } from "../../lib/registryApi";
import { requireSession } from "../../lib/requireSession";
import { transitionAgentAction } from "./actions";

export default async function AgentsPage() {
  const { session, roles } = await requireSession();
  const [agents, jwks] = await Promise.all([
    listAgentsForOrganization(session.organizationId),
    getJwks(session.organizationId).catch(() => ({ keys: [] })),
  ]);
  const canManage = canManageAgentsAndRelationships(roles);

  return (
    <Shell session={session} roles={roles}>
      <h1 className="mb-6 text-2xl font-semibold">Agents</h1>
      <table className="mb-10 w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-300 text-left text-slate-500">
            <th className="py-2">Agent</th>
            <th className="py-2">Status</th>
            <th className="py-2">Capabilities</th>
            <th className="py-2">Card fingerprint</th>
            <th className="py-2">Last verified</th>
            {canManage && <th className="py-2" />}
          </tr>
        </thead>
        <tbody>
          {agents.map((agent) => (
            <tr key={agent.agent_id} className="border-b border-slate-100">
              <td className="py-2">{agent.display_name || agent.agent_id}</td>
              <td className="py-2">{agent.status}</td>
              <td className="py-2">{agent.capabilities.join(", ")}</td>
              <td className="max-w-xs truncate py-2 font-mono text-xs">{agent.card_fingerprint}</td>
              <td className="py-2">
                {agent.last_verified_at ? new Date(agent.last_verified_at).toLocaleString() : "never"}
              </td>
              {canManage && (
                <td className="flex gap-2 py-2">
                  {agent.status !== "suspended" && (
                    <AgentButton agentId={agent.agent_id} action="suspend" label="Suspend" />
                  )}
                  {agent.status === "suspended" && (
                    <AgentButton agentId={agent.agent_id} action="reactivate" label="Reactivate" />
                  )}
                  {agent.status !== "revoked" && (
                    <AgentButton agentId={agent.agent_id} action="revoke" label="Revoke" />
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>

      <h2 className="mb-3 text-lg font-semibold">Signing keys (public only)</h2>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-300 text-left text-slate-500">
            <th className="py-2">Key id</th>
            <th className="py-2">Algorithm</th>
          </tr>
        </thead>
        <tbody>
          {jwks.keys.map((key) => (
            <tr key={key.kid} className="border-b border-slate-100">
              <td className="py-2 font-mono text-xs">{key.kid}</td>
              <td className="py-2">{key.alg ?? key.crv}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Shell>
  );
}

function AgentButton({
  agentId,
  action,
  label,
}: {
  agentId: string;
  action: "suspend" | "reactivate" | "revoke";
  label: string;
}) {
  return (
    <form action={transitionAgentAction}>
      <input type="hidden" name="agent_id" value={agentId} />
      <input type="hidden" name="action" value={action} />
      <button type="submit" className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-100">
        {label}
      </button>
    </form>
  );
}
