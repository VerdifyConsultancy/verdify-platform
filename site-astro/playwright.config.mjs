import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests",
  testMatch: "runtime.browser.test.mjs",
  fullyParallel: false,
  workers: 1,
  reporter: "line",
  use: {
    headless: true,
    viewport: { width: 1280, height: 900 },
  },
});
