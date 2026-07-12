import { createHash } from "node:crypto";
import { lstat, mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DESTINATION = path.join(ROOT, ".generated", "site-shell-root");
const ARCHIVE = path.join(
  ROOT,
  "vendor/site-shell/releases/verdify-site-shell-1.1.0.sha256-0645773ab3a952727251840e28dc73929a3e42b904450bcc9e7d25d8b03b1c91.tar.gz",
);
const RELEASE = path.join(
  ROOT,
  "vendor/site-shell/releases/verdify-site-shell-1.1.0.commit-7febbc479c6ed7d22f829e9c1e7109bc9bc7c6c0.release.json",
);
const PINS = Object.freeze({
  archive: "0645773ab3a952727251840e28dc73929a3e42b904450bcc9e7d25d8b03b1c91",
  release: "779620f2eda4d62677a2d9d61c65e2a1014e34de8cb2cec5008928caeef46a6d",
  manifest: "2864debbe67b23cd20ef5aa5fb57e86803ed1a1a9393b993c0acdd9182a6f585",
  sourceTree: "cb1053c8d793e7e60f78286541a88e9f00e0b54de7611e993d92fda3502115e7",
  wwwCommit: "7febbc479c6ed7d22f829e9c1e7109bc9bc7c6c0",
});
const KIT = Object.freeze({
  "scripts/site-shell/install-site-shell-artifact.mjs": "820b597132d7c3b82d6af9e789cbf2f63426afd8b7204f7c9619cbf5644e96c8",
  "scripts/site-shell/lib/site-shell-artifact.mjs": "1a6df1874f0a4deec0fd3c2da57e48d22f92803151c6213d8b4d77625484a984",
  "scripts/site-shell/lib/site-shell-install.mjs": "c45fff395cdc5925806ac35d4097577732fdd07609b2ef2dbaeaea5cfbf6b1b5",
  "scripts/site-shell/verify-site-shell-ready.mjs": "512fc3817c54be2a1e3bf317c031a81f372b1f67bd9709970ea362752a136128",
});

async function digestRegularFile(file) {
  const metadata = await lstat(file);
  if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.nlink !== 1) {
    throw new Error(`site-shell input is not a single-link regular file: ${path.relative(ROOT, file)}`);
  }
  return createHash("sha256").update(await readFile(file)).digest("hex");
}

function run(script, args) {
  const result = spawnSync(process.execPath, [script, ...args], {
    cwd: ROOT,
    encoding: "utf8",
    stdio: "inherit",
    timeout: 120_000,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${path.basename(script)} exited ${result.status}`);
}

if ((await digestRegularFile(ARCHIVE)) !== PINS.archive) throw new Error("site-shell archive pin mismatch");
if ((await digestRegularFile(RELEASE)) !== PINS.release) throw new Error("site-shell release-record pin mismatch");
for (const [relative, expected] of Object.entries(KIT)) {
  if ((await digestRegularFile(path.join(ROOT, relative))) !== expected) {
    throw new Error(`site-shell consumer-kit pin mismatch: ${relative}`);
  }
}

const release = JSON.parse(await readFile(RELEASE, "utf8"));
if (
  release.releaseContract !== "verdify.site-shell.release/v1"
  || release.schemaVersion !== 1
  || release.wwwCommit !== PINS.wwwCommit
  || release.archive?.sha256 !== `sha256:${PINS.archive}`
  || release.archive?.manifestSha256 !== `sha256:${PINS.manifest}`
  || release.contract?.sourceTreeDigest !== `sha256:${PINS.sourceTree}`
  || release.contract?.version !== "1.1.0"
  || release.contract?.installedFileCount !== 31
) {
  throw new Error("site-shell release record does not match independent consumer pins");
}

await mkdir(path.dirname(DESTINATION), { recursive: true });
let installed = false;
try {
  const metadata = await lstat(DESTINATION);
  if (!metadata.isDirectory() || metadata.isSymbolicLink()) throw new Error("site-shell destination is unsafe");
  installed = true;
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}

if (!installed) {
  run(path.join(ROOT, "scripts/site-shell/install-site-shell-artifact.mjs"), [
    "--artifact", ARCHIVE,
    "--archive-sha256", `sha256:${PINS.archive}`,
    "--source-tree-digest", `sha256:${PINS.sourceTree}`,
    "--destination", DESTINATION,
  ]);
}
run(path.join(ROOT, "scripts/site-shell/verify-site-shell-ready.mjs"), [
  "--destination", DESTINATION,
  "--archive-sha256", `sha256:${PINS.archive}`,
  "--source-tree-digest", `sha256:${PINS.sourceTree}`,
  "--manifest-sha256", `sha256:${PINS.manifest}`,
  "--contract-version", "1.1.0",
  "--installed-file-count", "31",
]);
