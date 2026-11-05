// SPDX-License-Identifier: MIT
import { defineConfig, devices } from "@playwright/test";
import { suitePorts } from "./e2e/ports.mjs";

// `pnpm test:browser` runs this suite as shards at once (`e2e/run-shards.mjs`, `--shard=i/N`). Each shard is one
// `playwright test` with its own fixture API, BFF and ingress, PW_PORT_OFFSET ports above the next (`e2e/ports.mjs`),
// because one fixture API holds one state; PW_SHARD names its output. Run alone, it is one shard on the base ports.
const PORTS = suitePorts();
const SHARD = /^\d+$/.test(process.env.PW_SHARD ?? "") ? `-shard-${process.env.PW_SHARD}` : "";

// Each server's env carries the flag #1122's fixture API and #1121's localhost exception key on. No value below is a
// secret: the BFF needs its settings to start, and no request here reaches Authentik or a real operator API. OIDC_ISSUER
// is a closed port on localhost, so /auth/login renders the sign-in-unavailable card.
const CI = { DASHBOARD_CI_IN_CLUSTER: "1" };
const BFF = {
  ...CI,
  NODE_ENV: "production",
  PORT: String(PORTS.web),
  PUBLIC_ORIGIN: "https://dashboard.verdify.ai",
  OPERATOR_API_URL: `http://localhost:${PORTS.api}`,
  OPERATOR_API_TOKEN: "fixture-placeholder-operator-api-token",
  OIDC_ISSUER: `https://localhost:${PORTS.oidc}/application/o/verdify-dashboard/`,
  OIDC_CLIENT_ID: "verdify-dashboard-fixture",
  OIDC_CLIENT_SECRET: "fixture-placeholder-client-secret",
  COOKIE_SECRET: "fixture-placeholder-cookie-secret-32-characters",
};

export default defineConfig({
  // forbidOnly: a stray test.only would otherwise pass the step having skipped every route but one.
  // fullyParallel makes each test its own unit, so the shards split the suite by test, not by whole file; one worker
  // still runs one test at a time against its shard's one fixture state, and each test selects its fixture first.
  testDir: "./e2e", testMatch: "**/*.spec.ts", forbidOnly: true, fullyParallel: true, workers: 1,
  // expect: the CI pod is shared and runs about five times slower than a laptop.
  timeout: 30_000, expect: { timeout: 10_000 }, retries: 0, outputDir: `../artifacts/playwright${SHARD}`,
  // The failure reporter prints each failure as it happens, so a run the step's deadline kills still names them.
  reporter: [["list"], ["./e2e/failure-reporter.ts"], ["json", { outputFile: `../artifacts/playwright-results${SHARD}.json` }]],
  use: { baseURL: `http://localhost:${PORTS.ingress}`, trace: "retain-on-failure" },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    // Phones run Safari: the 360 and 390 px checks and the viewer check run here too, tagged @webkit.
    { name: "webkit", use: { ...devices["Desktop Safari"] }, grep: /@webkit/ },
  ],
  webServer: [
    { command: "node e2e/fixture-api.mjs", url: `http://localhost:${PORTS.api}/healthz`, reuseExistingServer: false, timeout: 30_000, env: { ...CI, FIXTURE_API_PORT: String(PORTS.api) } },
    { command: "node server.mjs", url: `http://localhost:${PORTS.web}/healthz`, reuseExistingServer: false, timeout: 30_000, env: BFF },
    {
      command: "node e2e/fixture-ingress.mjs", url: `http://localhost:${PORTS.ingress}/healthz`, reuseExistingServer: false, timeout: 30_000,
      env: { ...CI, INGRESS_PORT: String(PORTS.ingress), FIXTURE_API_PORT: String(PORTS.api), WEB_PORT: String(PORTS.web) },
    },
  ],
});
