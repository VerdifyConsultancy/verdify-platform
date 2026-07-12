import path from "node:path";
import { fileURLToPath } from "node:url";

import { readReleaseDescriptor, verifyHydratedSnapshot } from "./fetch-stage-snapshot.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function parseArgs(args) {
  if (args.length !== 4) {
    throw new Error("Usage: node scripts/verify-stage-snapshot.mjs --release DESCRIPTOR --snapshot SNAPSHOT_ROOT");
  }
  const values = {};
  for (let index = 0; index < args.length; index += 2) {
    const flag = args[index];
    if (!["--release", "--snapshot"].includes(flag) || !args[index + 1] || values[flag]) {
      throw new Error("Usage: node scripts/verify-stage-snapshot.mjs --release DESCRIPTOR --snapshot SNAPSHOT_ROOT");
    }
    values[flag] = args[index + 1];
  }
  if (!values["--release"] || !values["--snapshot"]) throw new Error("release and snapshot are required");
  return values;
}

const args = parseArgs(process.argv.slice(2));
try {
  const release = await readReleaseDescriptor(path.resolve(ROOT, args["--release"]), { allowPlaceholders: true });
  const snapshot = await verifyHydratedSnapshot(path.resolve(ROOT, args["--snapshot"]), release);
  process.stdout.write(
    `verified sanitized stage snapshot: files=${snapshot.files.size} manifest=${snapshot.sanitization.sanitizedManifestSha256}\n`,
  );
} catch (error) {
  process.stderr.write(`verify-stage-snapshot: ${error.message}\n`);
  process.exitCode = 1;
}
