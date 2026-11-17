export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-4 px-6 py-16">
      <h1 className="text-3xl font-semibold">AIMO BRAIN</h1>
      <p className="text-slate-600">
        Management portal shell — milestone M0. Organization/agent onboarding,
        registry search, policy controls and the approval inbox are not
        implemented yet; they land in M6 per{" "}
        <code className="rounded bg-slate-200 px-1">docs/ROADMAP.md</code>.
      </p>
      <p className="text-slate-600">
        The portal is a management interface, not the agent runtime: buyer
        and supplier agents run as independent services regardless of
        whether this portal is online.
      </p>
    </main>
  );
}
