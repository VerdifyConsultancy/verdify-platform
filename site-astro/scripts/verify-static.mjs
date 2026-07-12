import { access, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { sha256File } from "./lib/snapshot.mjs";

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST_ROOT = path.join(PROJECT_ROOT, "dist");
const records = JSON.parse(await readFile(path.join(PROJECT_ROOT, ".generated", "content-records.json"), "utf8"));
const assets = JSON.parse(await readFile(path.join(PROJECT_ROOT, ".generated", "asset-records.json"), "utf8"));
const build = JSON.parse(await readFile(path.join(PROJECT_ROOT, ".generated", "build.json"), "utf8"));

if (build.sourceCount !== build.snapshotMarkdownCount - build.excludedDrafts.length) {
  throw new Error("compiled source accounting is incomplete");
}
if (build.approvalEligible !== false || build.localEvidenceStatus !== "provisional-only") {
  throw new Error("stage build must not claim immutable-snapshot approval");
}
if (!build.stageGlobalNoindex) throw new Error("stage build must be globally noindex");
if (build.copiedSnapshotAssetCount !== assets.length) throw new Error("snapshot asset accounting is incomplete");
if (
  build.rollingPlanCompatibility?.contract !== "verdify.rolling-plan-latest/v1"
  || build.rollingPlanCompatibility?.route !== "/plans/latest"
  || !build.rollingPlanCompatibility?.target
) {
  throw new Error("rolling latest-plan compatibility evidence is incomplete");
}
if (!build.sanitization.fixtureOnly && build.rollingPlanCompatibility.suppressedDeclarationCount !== 2) {
  throw new Error("frozen corpus rolling-plan collision count changed");
}
if (
  build.siteShell?.contractVersion !== "1.0.0"
  || build.siteShell?.wwwCommit !== "c9c0d56f654d6b9198352f16c620717dbee71612"
  || build.siteShell?.archiveDigest !== "sha256:6600525856f7a32b2fe7b30b4043fc29cdb26346f5b4689b20343cdff4efce61"
) {
  throw new Error("stage output is not bound to the reviewed WWW shell release");
}

for (const record of records) {
  const output = path.join(DIST_ROOT, ...record.physicalPath.split("/"));
  const html = await readFile(output, "utf8");
  if (!html.includes('<meta name="robots" content="noindex,follow">')) {
    throw new Error(`stage output lacks noindex metadata: ${record.physicalPath}`);
  }
  if (!html.includes("known frozen-baseline integrity blockers remain open")) {
    throw new Error(`stage output lacks the blocker label: ${record.physicalPath}`);
  }
  if (/<iframe[^>]+graphs\.verdify\.ai/i.test(html)) {
    throw new Error(`stage output embeds Grafana instead of linking evidence: ${record.physicalPath}`);
  }
  if (record.kind === "alias" && (!html.includes("http-equiv=\"refresh\"") || !html.includes(record.target))) {
    throw new Error(`alias output lost redirect semantics: ${record.physicalPath}`);
  }
}

const assetPaths = new Set(assets.map((asset) => asset.relative));
for (const asset of assets) {
  const output = path.join(DIST_ROOT, ...asset.relative.split("/"));
  await access(output);
  if ((await sha256File(output)) !== asset.sha256) {
    throw new Error(`snapshot asset bytes changed during the Astro build: ${asset.relative}`);
  }
}
if (build.preservedMediaCount !== assets.filter((asset) => asset.relative.startsWith("static/video/")).length) {
  throw new Error("preserved media accounting is incomplete");
}
if (!build.sanitization.fixtureOnly) {
  if (build.preservedMediaCount !== build.sanitization.transformations.hlsFilesPreserved) {
    throw new Error("stage output media count does not match the sanitization attestation");
  }
  const referencedSegments = new Set();
  const playlists = assets.filter((asset) => asset.relative.startsWith("static/video/") && asset.relative.endsWith(".m3u8"));
  for (const playlist of playlists) {
    const source = await readFile(path.join(DIST_ROOT, ...playlist.relative.split("/")), "utf8");
    for (const line of source.split(/\r?\n/u).map((value) => value.trim()).filter((value) => value && !value.startsWith("#"))) {
      if (/^(?:[a-z]+:|\/\/|\/)/i.test(line) || line.includes("\\") || line.includes("?") || line.includes("#")) {
        throw new Error(`HLS playlist contains an external or unsafe reference: ${playlist.relative}`);
      }
      const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(playlist.relative), line));
      if (!resolved.startsWith("static/video/") || !assetPaths.has(resolved)) {
        throw new Error(`HLS playlist reference is missing or traverses its media root: ${playlist.relative}`);
      }
      if (resolved.endsWith(".ts")) referencedSegments.add(resolved);
    }
  }
  const allSegments = assets.filter((asset) => asset.relative.startsWith("static/video/") && asset.relative.endsWith(".ts"));
  if (allSegments.some((asset) => !referencedSegments.has(asset.relative))) {
    throw new Error("stage output contains an orphaned HLS segment");
  }
}

for (const required of [
  "index.html",
  "404.html",
  "robots.txt",
  "rss.xml",
  "sitemap.xml",
  "route-manifest.json",
  "static-build.json",
  "assets/verdify-lab-lockup.svg",
  "assets/verdify-site-shell/fonts/ibm-plex-sans-latin-wght-normal.woff2",
  "assets/verdify-site-shell/fonts/ibm-plex-mono-latin-400-normal.woff2",
  "pagefind/pagefind.js",
  "pagefind/pagefind-entry.json",
]) {
  await access(path.join(DIST_ROOT, ...required.split("/")));
}

const robots = await readFile(path.join(DIST_ROOT, "robots.txt"), "utf8");
if (!robots.includes("Disallow: /")) throw new Error("stage robots policy must disallow crawling");
process.stdout.write(`verified ${records.length} noindex stage routes\n`);
