import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { reportingFeedEnvelopeSha256 } from "./lib/occurrence-export-contract.mjs";
import { planGraphExportRequests } from "./lib/graph-export-producer.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(ROOT, "dist");
const build = JSON.parse(await readFile(path.join(DIST, "static-build.json"), "utf8"));
const routes = JSON.parse(await readFile(path.join(DIST, "route-manifest.json"), "utf8"));
const occurrenceManifestBytes = await readFile(path.join(DIST, "occurrence-manifest.json"));
const occurrenceManifest = JSON.parse(occurrenceManifestBytes.toString("utf8"));
const occurrencePolicy = JSON.parse(await readFile(
  path.join(ROOT, "config/lab-stage-occurrence-export-policy.json"),
  "utf8",
));
const occurrenceManifestSha256 = createHash("sha256").update(occurrenceManifestBytes).digest("hex");
// This canonical envelope is an offline planning fixture only. It proves the
// digest binding and makes no claim that a live feed exists or is fresh.
const OFFLINE_NON_LIVE_REPORTING_FEED = Object.freeze({
  contract: "verdify.operator-public-reporting-feed",
  schemaVersion: 1,
  sourceId: "operator-public-reporting-feed-offline-planning-proof",
  sourceClass: "public-reporting-projection",
  credentialClass: "reporting-read-only",
  direction: "one-way-read-only",
  sourceWatermark: "wm_offline_non_live_planning_proof",
  sourceWatermarkAt: "2026-07-13T00:00:00Z",
});
const reportingFeedSha256 = reportingFeedEnvelopeSha256(OFFLINE_NON_LIVE_REPORTING_FEED);
const graphPlan = planGraphExportRequests({
  policy: occurrencePolicy,
  manifest: occurrenceManifest,
  manifestSha256: occurrenceManifestSha256,
  reportingFeedSha256,
});

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
if (
  occurrenceManifestSha256 !== occurrencePolicy.sourceOccurrenceManifestSha256
  || graphPlan.reportingFeedSha256 !== reportingFeedSha256
  || graphPlan.requests.length !== 143
  || graphPlan.requests.some((request) => request.reportingFeedSha256 !== reportingFeedSha256)
  || JSON.stringify(graphPlan.requests.map(({ occurrenceId }) => occurrenceId))
    !== JSON.stringify(occurrenceManifest.graphs.map(({ occurrenceId }) => occurrenceId))
  || /sourceId|sourceWatermark|endpoint|https?:|graphs\.verdify\.ai|credential/i.test(JSON.stringify(graphPlan))
) {
  throw new Error("real occurrence manifest is not byte-bound to the exact 143-request graph plan");
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
process.stdout.write(`verified real sanitized Lab build: routes=${routes.routes.length} html=${htmlFiles} media=${build.preservedMediaCount} graphPlan=${graphPlan.requests.length}\n`);
