import { Shell } from "../../components/Shell";
import { listApprovals, listNegotiations } from "../../lib/managementApi";
import { listRelationshipsForOrganization } from "../../lib/networkApi";
import { requireSession } from "../../lib/requireSession";

export default async function DashboardPage() {
  const { session, roles } = await requireSession();

  const [pendingApprovals, negotiations, relationships] = await Promise.all([
    listApprovals(session.organizationId, "PENDING").catch(() => []),
    listNegotiations(session.organizationId).catch(() => []),
    listRelationshipsForOrganization(session.organizationId).catch(() => []),
  ]);

  return (
    <Shell session={session} roles={roles}>
      <h1 className="mb-6 text-2xl font-semibold">Organization overview</h1>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Pending approvals" value={pendingApprovals.length} href="/approvals" />
        <StatCard label="Active negotiations" value={negotiations.length} href="/negotiations" />
        <StatCard
          label="Trading relationships"
          value={relationships.filter((r) => r.status === "active").length}
          href="/relationships"
        />
      </div>
    </Shell>
  );
}

function StatCard({ label, value, href }: { label: string; value: number; href: string }) {
  return (
    <a
      href={href}
      className="rounded border border-slate-200 bg-white p-5 shadow-sm hover:border-slate-400"
    >
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-2 text-3xl font-semibold">{value}</p>
    </a>
  );
}
