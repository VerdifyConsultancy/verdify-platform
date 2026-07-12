import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests",
  testMatch: "*.browser.test.mjs",
  fullyParallel: false,
  workers: 1,
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
