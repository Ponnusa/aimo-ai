import { Shell } from "../../components/Shell";
import { getPolicyCatalog } from "../../lib/managementApi";
import { canActivatePolicy } from "../../lib/rbac";
import { requireSession } from "../../lib/requireSession";
import { activatePolicyAction } from "./actions";
import { DryRunForm } from "./DryRunForm";

export default async function PolicyPage() {
  const { session, roles } = await requireSession();
  const catalog = await getPolicyCatalog(session.organizationId);
  const canActivate = canActivatePolicy(roles);

  return (
    <Shell session={session} roles={roles}>
      <h1 className="mb-6 text-2xl font-semibold">Policy</h1>
      <div className="mb-8 rounded border border-slate-200 bg-white p-5 text-sm">
        <p>
          Role: <span className="font-mono">{catalog.role}</span>
        </p>
        <p>
          Active version: <span className="font-mono">{catalog.active_version}</span>
        </p>
        <p>Available versions: {catalog.available_versions.join(", ")}</p>
      </div>

      {canActivate && (
        <form action={activatePolicyAction} className="mb-8 flex max-w-md items-end gap-3 rounded border border-slate-200 bg-white p-5">
          <label className="flex-1 text-sm text-slate-700">
            Activate bundle version
            <select name="policy_version" className="mt-1 w-full rounded border border-slate-300 px-2 py-1">
              {catalog.available_versions.map((version) => (
                <option key={version} value={version}>
                  {version}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="rounded bg-slate-900 px-4 py-2 text-white hover:bg-slate-700">
            Activate
          </button>
        </form>
      )}

      <DryRunForm
        availableVersions={catalog.available_versions}
        counterpartyOrganizationId={session.organizationId}
      />
    </Shell>
  );
}
