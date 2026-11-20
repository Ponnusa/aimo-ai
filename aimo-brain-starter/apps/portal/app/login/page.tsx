import { isDevAuthAllowed } from "../../lib/config";
import { isRealOidcConfigured } from "../../lib/oidc";

const ERROR_MESSAGES: Record<string, string> = {
  invalid_state: "The login attempt could not be verified. Please try again.",
  unmapped_tenant:
    "Your identity provider is not yet linked to an AIMO BRAIN organization. Ask an administrator to configure it.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col gap-6 px-6 py-16">
      <h1 className="text-2xl font-semibold">Sign in to AIMO BRAIN</h1>
      {error && (
        <p className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
          {ERROR_MESSAGES[error] ?? error}
        </p>
      )}
      {isRealOidcConfigured() && (
        <a
          href="/api/auth/login"
          className="rounded bg-slate-900 px-4 py-2 text-center text-white hover:bg-slate-700"
        >
          Sign in with your organization&rsquo;s identity provider
        </a>
      )}
      {isDevAuthAllowed() && (
        <form action="/api/auth/dev-login" method="post" className="flex flex-col gap-3 rounded border border-amber-300 bg-amber-50 p-4">
          <p className="text-sm font-medium text-amber-900">
            Development login — never available in production.
          </p>
          <label className="text-sm text-slate-700">
            Organization id
            <input
              name="organization_id"
              defaultValue="org_fictional_bistro_01"
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            />
          </label>
          <label className="text-sm text-slate-700">
            Your subject id
            <input
              name="oidc_subject"
              defaultValue="dev-user-1"
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            />
          </label>
          <label className="text-sm text-slate-700">
            Email
            <input
              name="email"
              defaultValue="dev-user-1@example.com"
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            />
          </label>
          <label className="text-sm text-slate-700">
            Display name
            <input
              name="display_name"
              defaultValue="Dev User"
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            />
          </label>
          <button type="submit" className="rounded bg-amber-600 px-4 py-2 text-white hover:bg-amber-700">
            Continue (dev login)
          </button>
        </form>
      )}
      {!isRealOidcConfigured() && !isDevAuthAllowed() && (
        <p className="text-sm text-red-700">
          No identity provider is configured for this environment, and the development login
          adapter is disabled. Set OIDC_ISSUER/OIDC_CLIENT_ID or run with PORTAL_ENVIRONMENT=local.
        </p>
      )}
    </main>
  );
}
