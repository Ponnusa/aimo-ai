import { Shell } from "../../components/Shell";
import { config } from "../../lib/config";
import { listOrganizations, listRelationshipsForOrganization } from "../../lib/networkApi";
import { canManageAgentsAndRelationships } from "../../lib/rbac";
import { requireSession } from "../../lib/requireSession";
import { requestRelationshipAction, transitionRelationshipAction } from "./actions";

export default async function RelationshipsPage() {
  const { session, roles } = await requireSession();
  const [relationships, organizations] = await Promise.all([
    listRelationshipsForOrganization(session.organizationId),
    listOrganizations(),
  ]);
  const canManage = canManageAgentsAndRelationships(roles);
  const others = organizations.filter(
    (o) => o.organization_id !== session.organizationId && o.organization_id !== config.portalOrganizationId,
  );

  return (
    <Shell session={session} roles={roles}>
      <h1 className="mb-2 text-2xl font-semibold">Trading relationships</h1>
      <p className="mb-6 text-sm text-slate-500">
        Discovery through the registry is not, by itself, permission to trade — these rows are
        the actual authorization, looked up by registry-api for every discovery request.
      </p>
      <table className="mb-10 w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-300 text-left text-slate-500">
            <th className="py-2">Buyer</th>
            <th className="py-2">Supplier</th>
            <th className="py-2">Status</th>
            {canManage && <th className="py-2" />}
          </tr>
        </thead>
        <tbody>
          {relationships.map((relationship) => (
            <tr key={relationship.id} className="border-b border-slate-100">
              <td className="py-2">{relationship.buyer_organization_id}</td>
              <td className="py-2">{relationship.supplier_organization_id}</td>
              <td className="py-2">{relationship.status}</td>
              {canManage && (
                <td className="flex gap-2 py-2">
                  {relationship.status !== "suspended" && (
                    <RelButton id={relationship.id} action="suspend" label="Suspend" />
                  )}
                  {relationship.status !== "active" && (
                    <RelButton id={relationship.id} action="reactivate" label="Reactivate" />
                  )}
                  {relationship.status !== "revoked" && (
                    <RelButton id={relationship.id} action="revoke" label="Revoke" />
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>

      {canManage && (
        <form action={requestRelationshipAction} className="flex max-w-md flex-col gap-3 rounded border border-slate-200 bg-white p-5">
          <h2 className="font-semibold">Request a new trading relationship</h2>
          <label className="text-sm text-slate-700">
            Counterparty organization
            <select name="counterparty_organization_id" className="mt-1 w-full rounded border border-slate-300 px-2 py-1">
              {others.map((org) => (
                <option key={org.organization_id} value={org.organization_id}>
                  {org.display_name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" name="as_buyer" defaultChecked />
            We are the buyer side
          </label>
          <button type="submit" className="rounded bg-slate-900 px-4 py-2 text-white hover:bg-slate-700">
            Request relationship
          </button>
        </form>
      )}
    </Shell>
  );
}

function RelButton({
  id,
  action,
  label,
}: {
  id: number;
  action: "suspend" | "revoke" | "reactivate";
  label: string;
}) {
  return (
    <form action={transitionRelationshipAction}>
      <input type="hidden" name="relationship_id" value={id} />
      <input type="hidden" name="action" value={action} />
      <button type="submit" className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-100">
        {label}
      </button>
    </form>
  );
}
