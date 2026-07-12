import { open as openFile, lstat, mkdir, unlink } from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const GENERATED = path.join(ROOT, ".generated");
const LOCK = path.join(ROOT, ".astro-stage-build.lock");
const checkOnly = process.argv.slice(2).includes("--check");
if (process.argv.length > (checkOnly ? 3 : 2) || (process.argv.length === 3 && !checkOnly)) {
  throw new Error("Usage: node scripts/build-stage.mjs [--check]");
}

function run(label, script, args = []) {
  process.stdout.write(`\n[lab-stage] ${label}\n`);
  const result = spawnSync(process.execPath, [script, ...args], {
    cwd: ROOT,
    env: process.env,
    stdio: "inherit",
    timeout: 15 * 60 * 1000,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${label} failed`);
}

await mkdir(GENERATED, { recursive: true });
let lock;
try {
  lock = await openFile(LOCK, "wx", 0o600);
} catch (error) {
  if (error.code === "EEXIST") throw new Error("another cooperating Lab stage build is active");
  throw error;
}
const identity = await lock.stat({ bigint: true });
try {
  run("verify shared shell", path.join(ROOT, "scripts/prepare-site-shell.mjs"));
  run("compile sanitized snapshot", path.join(ROOT, "scripts/compile-snapshot.mjs"));
  run("Astro diagnostics", path.join(ROOT, "node_modules/astro/bin/astro.mjs"), ["check"]);
  if (!checkOnly) {
    run("Astro static build", path.join(ROOT, "node_modules/astro/bin/astro.mjs"), ["build"]);
    run("finalize route shapes", path.join(ROOT, "scripts/finalize-output.mjs"));
    run("Pagefind index", path.join(ROOT, "node_modules/pagefind/lib/runner/bin.cjs"), ["--site", "dist"]);
    run("prune unused Pagefind UI bundles", path.join(ROOT, "scripts/prune-pagefind-output.mjs"));
    run("verify static output", path.join(ROOT, "scripts/verify-static.mjs"));
  }
} finally {
  await lock.close().catch(() => {});
  try {
    const selected = await lstat(LOCK, { bigint: true });
    if (selected.isFile() && selected.dev === identity.dev && selected.ino === identity.ino) await unlink(LOCK);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}
