// SPDX-License-Identifier: MIT
import { defineConfig, devices } from "@playwright/test";

// Each server's env carries the flag #1122's fixture API and #1121's localhost exception key on. No value below is a
// secret: the BFF needs its settings to start, and no request here reaches Authentik or a real operator API. OIDC_ISSUER
// is a closed port on localhost, so /auth/login renders the sign-in-unavailable card.
const CI = { DASHBOARD_CI_IN_CLUSTER: "1" };
const BFF = {
  ...CI,
  NODE_ENV: "production",
  PORT: "4175",
  PUBLIC_ORIGIN: "https://dashboard.verdify.ai",
  OPERATOR_API_URL: "http://localhost:4174",
  OPERATOR_API_TOKEN: "fixture-placeholder-operator-api-token",
  OIDC_ISSUER: "https://localhost:4179/application/o/verdify-dashboard/",
  OIDC_CLIENT_ID: "verdify-dashboard-fixture",
  OIDC_CLIENT_SECRET: "fixture-placeholder-client-secret",
  COOKIE_SECRET: "fixture-placeholder-cookie-secret-32-characters",
};

export default defineConfig({
  // forbidOnly: a stray test.only would otherwise pass the step having skipped every route but one.
  testDir: "./e2e", testMatch: "**/*.spec.ts", forbidOnly: true, fullyParallel: false, workers: 1,
  // expect: a role query across /settings' hundreds of rows on the CI pod can outlast the 5 s default.
  timeout: 30_000, expect: { timeout: 10_000 }, retries: 0, outputDir: "../artifacts/playwright",
  reporter: [["list"], ["json", { outputFile: "../artifacts/playwright-results.json" }]],
  use: { baseURL: "http://localhost:4173", trace: "retain-on-failure" },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    // Phones run Safari: the 360 and 390 px checks and the viewer check run here too, tagged @webkit.
    { name: "webkit", use: { ...devices["Desktop Safari"] }, grep: /@webkit/ },
  ],
  webServer: [
    { command: "node e2e/fixture-api.mjs", url: "http://localhost:4174/healthz", reuseExistingServer: false, timeout: 30_000, env: CI },
    { command: "node server.mjs", url: "http://localhost:4175/healthz", reuseExistingServer: false, timeout: 30_000, env: BFF },
    { command: "node e2e/fixture-ingress.mjs", url: "http://localhost:4173/healthz", reuseExistingServer: false, timeout: 30_000, env: CI },
  ],
});
