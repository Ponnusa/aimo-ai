"use client";

import { useActionState } from "react";
import { DryRunResult, policyDryRunAction } from "./actions";

export function DryRunForm({
  availableVersions,
  counterpartyOrganizationId,
}: {
  availableVersions: string[];
  counterpartyOrganizationId: string;
}) {
  const [result, formAction, pending] = useActionState<DryRunResult | null, FormData>(
    policyDryRunAction,
    null,
  );

  return (
    <form action={formAction} className="flex max-w-md flex-col gap-3 rounded border border-slate-200 bg-white p-5">
      <h2 className="font-semibold">Dry-run evaluation</h2>
      <p className="text-xs text-slate-500">
        Read-only — never writes an evidence, approval or order row, regardless of outcome.
      </p>
      <label className="text-sm text-slate-700">
        Bundle version
        <select name="policy_version" className="mt-1 w-full rounded border border-slate-300 px-2 py-1">
          {availableVersions.map((version) => (
            <option key={version} value={version}>
              {version}
            </option>
          ))}
        </select>
      </label>
      <label className="text-sm text-slate-700">
        Counterparty organization
        <input
          name="counterparty_organization_id"
          defaultValue={counterpartyOrganizationId}
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
        />
      </label>
      <label className="text-sm text-slate-700">
        Sample total amount
        <input
          name="total_amount"
          defaultValue="100.00"
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
        />
      </label>
      <button
        type="submit"
        disabled={pending}
        className="rounded bg-slate-900 px-4 py-2 text-white hover:bg-slate-700 disabled:opacity-50"
      >
        {pending ? "Evaluating..." : "Run"}
      </button>
      {result && (
        <div className="rounded bg-slate-100 p-3 text-sm">
          <p>Allow: {String(result.allow)}</p>
          <p>Human approval required: {String(result.human_approval_required)}</p>
          {result.deny_reason && <p>Deny reason: {result.deny_reason}</p>}
        </div>
      )}
    </form>
  );
}
