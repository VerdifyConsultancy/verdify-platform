import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(ROOT, "dist");
const build = JSON.parse(await readFile(path.join(DIST, "static-build.json"), "utf8"));
const routes = JSON.parse(await readFile(path.join(DIST, "route-manifest.json"), "utf8"));

if (
  build.contract !== "verdify.lab-astro-stage-build"
  || build.schemaVersion !== 1
  || build.sanitization?.fixtureOnly !== false
  || build.snapshotManifestDigest !== "sha256:2dbcb7256f475be6bd620427101900c53814fb065a815e0129b19451d7467d86"
  || build.sanitization?.guardReportSha256 !== "8da094d7f9eb0957d38fff47dcd6b80f2d906676433c1b57613ad8aa632bf20d"
  || build.sourceCount !== 152
  || build.aliasCount !== 84
  || build.tagRouteCount !== 84
  || build.grafanaOccurrenceCount !== 143
  || build.snapshotAssetCount !== 277
  || build.copiedSnapshotAssetCount !== 276
  || build.preservedMediaCount !== 179
  || build.rollingPlanCompatibility?.suppressedDeclarationCount !== 2
  || build.stageGlobalNoindex !== true
  || routes.routes?.length !== 323
) {
  throw new Error("dist is not the reviewed 429-file sanitized Lab stage build");
}

let htmlFiles = 0;
const pending = [DIST];
while (pending.length > 0) {
  const directory = pending.pop();
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) pending.push(absolute);
    else if (entry.isFile() && entry.name.endsWith(".html")) htmlFiles += 1;
  }
}
if (htmlFiles !== 324) throw new Error("real Lab stage HTML route count changed");
process.stdout.write(`verified real sanitized Lab build: routes=${routes.routes.length} html=${htmlFiles} media=${build.preservedMediaCount}\n`);
