// SPDX-License-Identifier: MIT
/**
 * The guard in front of the browser suite (weather's web/e2e/verify-browser-tools.mjs): `pnpm test:browser` runs it
 * first, so the suite runs only on the in-cluster dashboard step's image (jvallery/agents#5994), never on the Mac.
 * Weather's copy also checks WEATHER_CI_IN_CLUSTER; this one does not, because the Playwright config, not the CI step,
 * sets DASHBOARD_CI_IN_CLUSTER for the three servers.
 */
import { accessSync, constants } from "node:fs";
import { createRequire } from "node:module";
import { chromium, webkit } from "@playwright/test";
if (process.platform !== "linux") throw new Error("Browser checks require approved in-cluster CI");
if (process.env.PLAYWRIGHT_BROWSERS_PATH !== "/opt/ms-playwright") throw new Error("Use the qualified CI browser closure");
const require = createRequire(import.meta.url);
if (require("@playwright/test/package.json").version !== "1.64.1") throw new Error("Browser package differs from the qualified lock");
for (const engine of [chromium, webkit]) {
  const executable = engine.executablePath();
  if (!executable.startsWith("/opt/ms-playwright/")) throw new Error("Browser executable is outside the qualified image closure");
  accessSync(executable, constants.R_OK | constants.X_OK);
}
process.stdout.write("Qualified Playwright 1.64.1 executable paths are readable by the non-root CI identity\n");
