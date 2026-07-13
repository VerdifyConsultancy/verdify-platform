import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests",
  testMatch: "*.browser.test.mjs",
  fullyParallel: false,
  workers: 1,
  // Stage CI shares worker nodes. Retry once so a transient scheduling stall
  // is visible as flaky while a reproducible performance regression stays red.
  retries: 1,
  reporter: "line",
  expect: {
    timeout: 5_000,
  },
  use: {
    headless: true,
    viewport: { width: 1280, height: 900 },
    reducedMotion: "reduce",
    trace: "retain-on-failure",
  },
});
