// Portal server-side configuration. Every secret here is read from the
// environment and never sent to the browser — Next.js only exposes
// NEXT_PUBLIC_* variables client-side, and nothing here is prefixed
// that way. See docs/ARCHITECTURE.md's M6 section.

function env(name: string, fallback?: string): string {
  const value = process.env[name];
  if (value !== undefined && value !== "") return value;
  if (fallback !== undefined) return fallback;
  throw new Error(`Missing required environment variable: ${name}`);
}

export const config = {
  // "local" | "staging" | "test" allow the dev-login adapter; anything
  // else (most importantly "production") refuses it outright — the
  // same gate buyer_agent/supplier_agent apply to identity.dev_oidc.
  environment: env("PORTAL_ENVIRONMENT", "local"),

  networkApiUrl: env("NETWORK_API_URL", "http://localhost:8000"),
  registryApiUrl: env("REGISTRY_API_URL", "http://localhost:8003"),
  networkAdminApiKey: env("NETWORK_ADMIN_API_KEY", "dev-only-admin-key-change-me"),
  networkInternalServiceKey: env(
    "NETWORK_INTERNAL_SERVICE_KEY",
    "dev-only-internal-key-change-me",
  ),
  serviceCredentialSigningSecret: env(
    "SERVICE_CREDENTIAL_SIGNING_SECRET",
    "dev-only-signing-secret-change-me",
  ),

  portalOrganizationId: env("PORTAL_ORGANIZATION_ID", "org_aimo_portal"),
  portalSigningPrivateKeyPem: env("PORTAL_SIGNING_PRIVATE_KEY_PEM", ""),
  portalSigningKid: env("PORTAL_SIGNING_KID", ""),

  // M6 reference deployment: exactly one buyer organization/agent and one
  // supplier organization/agent, matching every other milestone's fixed
  // fictional pair. A real multi-tenant deployment would resolve an
  // agent's base URL from registry-api's own agent record instead of a
  // fixed env map; this is the honest scope boundary for M6, not a
  // design the portal's own code assumes elsewhere.
  buyerOrganizationId: env("BUYER_ORGANIZATION_ID", "org_fictional_bistro_01"),
  buyerAgentUrl: env("BUYER_AGENT_URL", "http://localhost:8001"),
  supplierOrganizationId: env("SUPPLIER_ORGANIZATION_ID", "org_fictional_wholesale_01"),
  supplierAgentUrl: env("SUPPLIER_AGENT_URL", "http://localhost:8002"),

  // Standard OIDC relying-party config (Entra ID or any compliant IdP).
  // Only consulted outside dev-login; see lib/oidc.ts.
  oidcIssuer: process.env.OIDC_ISSUER ?? "",
  oidcClientId: process.env.OIDC_CLIENT_ID ?? "",
  oidcClientSecret: process.env.OIDC_CLIENT_SECRET ?? "",
  oidcRedirectUri: process.env.OIDC_REDIRECT_URI ?? "",

  // Next.js deliberately ignores the inbound Host header when
  // resolving request.nextUrl/request.url (hardening against host-
  // header injection into self-referential redirects), so every
  // absolute redirect this app issues to itself is built from this
  // configured origin instead — never from the request. A real
  // deployment sets this to its real public URL.
  portalPublicUrl: env("PORTAL_PUBLIC_URL", "http://localhost:3000"),

  sessionCookieName: "aimo_portal_session",
  sqlitePath: env("PORTAL_DATABASE_PATH", "./portal.sqlite3"),

  // M6 dual-control / step-up threshold mirrors the agents' own
  // BUYER_REQUIRE_SECOND_APPROVER_ABOVE_AMOUNT — the portal re-prompts
  // for a fresh IdP login before minting an assertion above this
  // amount; the agent independently enforces the actual dual-control
  // requirement regardless of what the portal does.
  stepUpThresholdAmount: process.env.PORTAL_STEP_UP_THRESHOLD_AMOUNT ?? null,
} as const;

export function isDevAuthAllowed(): boolean {
  return ["local", "staging", "test"].includes(config.environment);
}
