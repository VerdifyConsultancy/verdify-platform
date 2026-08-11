import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PRODUCTION_ORIGIN = "https://lab.verdify.ai";
const args = process.argv.slice(2);
const fixture = args.length === 1 && args[0] === "--fixture";
if (args.length !== (fixture ? 1 : 0)) {
  throw new Error("Usage: node scripts/build-production.mjs [--fixture]");
}

const environment = {
  ...process.env,
  LAB_BUILD_TARGET: "production",
  SITE_ORIGIN: PRODUCTION_ORIGIN,
  STAGE_GLOBAL_NOINDEX: "false",
  ALLOW_SYNTHETIC_FIXTURE: fixture ? "true" : "false",
};
if (fixture) {
  environment.LAB_SNAPSHOT = "tests/fixtures/snapshot";
} else if (!environment.LAB_SNAPSHOT) {
  throw new Error("LAB_SNAPSHOT must name an activation-eligible production snapshot");
}

function run(script, scriptArgs = []) {
  const result = spawnSync(process.execPath, [script, ...scriptArgs], {
    cwd: ROOT,
    env: environment,
    stdio: "inherit",
    timeout: 15 * 60 * 1000,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${path.basename(script)} failed`);
}

run(path.join(ROOT, "scripts/build-stage.mjs"));
