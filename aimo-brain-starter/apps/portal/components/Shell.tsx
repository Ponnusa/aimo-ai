import Link from "next/link";
import type { PortalSession } from "../lib/session";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/approvals", label: "Approvals" },
  { href: "/negotiations", label: "Negotiations" },
  { href: "/integration", label: "Integration" },
  { href: "/agents", label: "Agents" },
  { href: "/relationships", label: "Relationships" },
  { href: "/policy", label: "Policy" },
  { href: "/audit", label: "Audit" },
  { href: "/members", label: "Members" },
];

export function Shell({
  session,
  roles,
  children,
}: {
  session: PortalSession;
  roles: string[];
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <div className="flex items-center gap-6">
          <span className="text-lg font-semibold">AIMO BRAIN</span>
          <nav className="flex gap-4 text-sm text-slate-600">
            {NAV_ITEMS.map((item) => (
              <Link key={item.href} href={item.href} className="hover:text-slate-900">
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3 text-sm text-slate-600">
          <span>
            {session.displayName} · {session.organizationId} · {roles.join(", ") || "no roles"}
          </span>
          <form action="/api/auth/logout" method="post">
            <button className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-100">
              Sign out
            </button>
          </form>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
    </div>
  );
}
