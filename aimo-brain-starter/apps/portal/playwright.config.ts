import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

// M6 browser-level test for the critical human-approval flow
// (docs/ARCHITECTURE.md's M6 section, "Verification"). Starts the real
// portal (production build) alongside a minimal real backend
// (tests/e2e/serve_portal_demo.py: network-api + buyer-agent, seeded
// with one pending approval) — never a mocked API layer.
const PORTAL_PORT = 18100;
const REPO_ROOT = path.resolve(__dirname, "..", "..");

const PORTAL_ENV = {
  PORTAL_ENVIRONMENT: "local",
  PORTAL_DATABASE_PATH: path.resolve(__dirname, ".portal-e2e.sqlite3"),
  NETWORK_API_URL: "http://127.0.0.1:18000",
  REGISTRY_API_URL: "http://127.0.0.1:1",
  NETWORK_ADMIN_API_KEY: "portal-e2e-admin-key",
  NETWORK_INTERNAL_SERVICE_KEY: "portal-e2e-internal-key",
  SERVICE_CREDENTIAL_SIGNING_SECRET: "portal-e2e-signing-secret",
  PORTAL_ORGANIZATION_ID: "org_aimo_portal",
  PORTAL_SIGNING_PRIVATE_KEY_PEM:
    "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIJcuGCd6PaRYUXmuxcK/Y4E6RebCdIQhtEEAa0s0kToo\n-----END PRIVATE KEY-----\n",
  PORTAL_SIGNING_KID: "portal-e2e-k1",
  BUYER_ORGANIZATION_ID: "org_fictional_bistro_01",
  BUYER_AGENT_URL: "http://127.0.0.1:18001",
  SUPPLIER_ORGANIZATION_ID: "org_fictional_wholesale_01",
  SUPPLIER_AGENT_URL: "http://127.0.0.1:1",
  PORT: String(PORTAL_PORT),
  PORTAL_PUBLIC_URL: `http://127.0.0.1:${PORTAL_PORT}`,
};

export default defineConfig({
  testDir: "./tests-e2e",
  timeout: 30_000,
  fullyParallel: false,
  retries: 0,
  use: {
    baseURL: `http://127.0.0.1:${PORTAL_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `uv run --project ${REPO_ROOT} python tests/e2e/serve_portal_demo.py`,
      cwd: REPO_ROOT,
      url: "http://127.0.0.1:18001/health",
      reuseExistingServer: false,
      timeout: 30_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: "npm run start -- -p " + PORTAL_PORT,
      cwd: __dirname,
      url: `http://127.0.0.1:${PORTAL_PORT}/login`,
      reuseExistingServer: false,
      timeout: 30_000,
      env: PORTAL_ENV,
    },
  ],
});
