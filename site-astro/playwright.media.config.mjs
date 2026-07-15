import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "tests",
    testMatch: "quartz-media-autoload.browser.test.mjs",
    fullyParallel: false,
    workers: 1,
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
    projects: [
        { name: "chromium", use: { browserName: "chromium" } },
        { name: "webkit", use: { browserName: "webkit" } },
    ],
});
