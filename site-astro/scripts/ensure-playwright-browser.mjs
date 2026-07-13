import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { chromium } from "@playwright/test";

const require = createRequire(import.meta.url);
const executable = chromium.executablePath();

async function browserIsReady() {
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    return true;
  } catch {
    return false;
  } finally {
    await browser?.close();
  }
}

if (await browserIsReady()) {
  process.stdout.write(`[lab-stage] pinned Playwright Chromium is ready: ${executable}\n`);
} else {
  const playwrightCli = require.resolve("playwright/cli");
  process.stdout.write(
    "[lab-stage] pinned Playwright Chromium is absent; installing browser and OS dependencies\n",
  );
  const result = spawnSync(process.execPath, [playwrightCli, "install", "--with-deps", "chromium"], {
    env: process.env,
    stdio: "inherit",
    timeout: 15 * 60 * 1000,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Playwright Chromium install failed with exit code ${result.status}`);
  if (!(await browserIsReady())) throw new Error(`Playwright Chromium remains unusable after installing ${executable}`);

  process.stdout.write(`[lab-stage] pinned Playwright Chromium installed: ${executable}\n`);
}
