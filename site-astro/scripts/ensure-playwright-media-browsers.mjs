import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { chromium, webkit } from "@playwright/test";

const require = createRequire(import.meta.url);
const playwrightCli = require.resolve("@playwright/test/cli");
const requiredBrowsers = [
    { cliName: "chromium", displayName: "Chromium", browserType: chromium },
    { cliName: "webkit", displayName: "WebKit", browserType: webkit },
];

async function browserIsReady(browserType) {
    let browser;
    try {
        browser = await browserType.launch({ headless: true });
        return true;
    } catch {
        return false;
    } finally {
        await browser?.close();
    }
}

const missing = [];
for (const required of requiredBrowsers) {
    if (await browserIsReady(required.browserType)) {
        process.stdout.write(
            `[lab-stage] pinned Playwright ${required.displayName} is ready: ${required.browserType.executablePath()}\n`,
        );
    } else {
        missing.push(required);
    }
}

if (missing.length > 0) {
    const names = missing.map((required) => required.displayName).join(" and ");
    process.stdout.write(
        `[lab-stage] pinned Playwright ${names} absent; installing only required media browsers and OS dependencies\n`,
    );
    const result = spawnSync(
        process.execPath,
        [
            playwrightCli,
            "install",
            "--with-deps",
            ...missing.map((required) => required.cliName),
        ],
        {
            env: process.env,
            stdio: "inherit",
            timeout: 20 * 60 * 1000,
        },
    );
    if (result.error) throw result.error;
    if (result.status !== 0) {
        throw new Error(
            `Playwright media browser install failed with exit code ${result.status}`,
        );
    }

    for (const required of missing) {
        if (!(await browserIsReady(required.browserType))) {
            throw new Error(
                `Playwright ${required.displayName} remains unusable after installing ${required.browserType.executablePath()}`,
            );
        }
        process.stdout.write(
            `[lab-stage] pinned Playwright ${required.displayName} installed: ${required.browserType.executablePath()}\n`,
        );
    }
}
