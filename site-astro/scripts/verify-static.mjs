import { access, lstat, readFile } from "node:fs/promises";
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
if (build.copiedSnapshotAssetCount + build.generatedResponsiveImageCount !== assets.length) {
  throw new Error("snapshot and generated asset accounting is incomplete");
}
const occurrenceManifestBytes = await readFile(path.join(DIST_ROOT, "occurrence-manifest.json"));
const occurrenceManifest = JSON.parse(occurrenceManifestBytes.toString("utf8"));
if (
  occurrenceManifest.contract !== "verdify.lab-static-occurrence-manifest"
  || occurrenceManifest.schemaVersion !== 1
  || occurrenceManifest.snapshotId !== build.snapshotId
  || occurrenceManifest.graphs.length !== build.grafanaOccurrenceCount
  || occurrenceManifest.currentMedia.length !== build.currentMediaOccurrenceCount
  || `sha256:${await sha256File(path.join(DIST_ROOT, "occurrence-manifest.json"))}` !== build.occurrenceManifestDigest
) {
  throw new Error("static occurrence manifest is incomplete or not bound to the build");
}
if (build.selectedOccurrenceManifestSha256 === null && build.materializedOccurrenceBlobCount !== 0) {
  throw new Error("unselected occurrence blobs were materialized");
}
if (
  occurrenceManifest.graphs.some((occurrence) => !/^graph_[0-9a-f]{24}$/.test(occurrence.occurrenceId))
  || occurrenceManifest.currentMedia.some((occurrence) => !/^media_[0-9a-f]{24}$/.test(occurrence.occurrenceId))
) {
  throw new Error("static occurrence manifest contains an invalid occurrence identity");
}
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
  build.siteShell?.contractVersion !== "1.1.0"
  || build.siteShell?.wwwCommit !== "7febbc479c6ed7d22f829e9c1e7109bc9bc7c6c0"
  || build.siteShell?.archiveDigest !== "sha256:0645773ab3a952727251840e28dc73929a3e42b904450bcc9e7d25d8b03b1c91"
) {
  throw new Error("stage output is not bound to the reviewed WWW shell release");
}

const routePaths = new Set(records.flatMap((record) => [record.route, record.canonicalPath].map((value) => value.replace(/\/$/, "") || "/")));
let localReferenceCount = 0;

async function isFile(relative) {
  try {
    return (await lstat(path.join(DIST_ROOT, ...relative.split("/")))).isFile();
  } catch (error) {
    if (error.code === "ENOENT" || error.code === "ENOTDIR") return false;
    throw error;
  }
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
  for (const match of build.sanitization.fixtureOnly
    ? []
    : html.matchAll(/\b(?:href|src|poster|action)=(?:"([^"]*)"|'([^']*)')/gu)) {
    const raw = match[1] ?? match[2];
    if (!raw || raw.startsWith("#") || /^(?:data:|mailto:|tel:|javascript:)/i.test(raw)) continue;
    let target;
    try {
      target = new URL(raw, `${build.siteOrigin}${record.canonicalPath}`);
    } catch {
      throw new Error(`stage output contains an invalid URL reference: ${record.physicalPath}`);
    }
    if (target.origin !== build.siteOrigin) continue;
    localReferenceCount += 1;
    const pathname = decodeURIComponent(target.pathname).replace(/\/$/, "") || "/";
    if (routePaths.has(pathname)) continue;
    const relative = pathname.replace(/^\/+/, "");
    if (
      await isFile(relative)
      || await isFile(`${relative}.html`)
      || await isFile(`${relative}/index.html`)
    ) continue;
    throw new Error(`stage output contains a broken same-origin reference: ${record.physicalPath}`);
  }
}

const snapshotAssets = assets.filter((asset) => typeof asset.relative === "string");
const generatedAssets = assets.filter((asset) => typeof asset.path === "string");
if (snapshotAssets.length !== build.copiedSnapshotAssetCount || generatedAssets.length !== build.generatedResponsiveImageCount) {
  throw new Error("snapshot and generated asset classes are not independently accounted");
}
const assetPaths = new Set(snapshotAssets.map((asset) => asset.relative));
for (const asset of assets) {
  const relative = asset.relative ?? asset.path;
  const output = path.join(DIST_ROOT, ...relative.split("/"));
  await access(output);
  if ((await sha256File(output)) !== asset.sha256) {
    throw new Error(`published asset bytes changed during the Astro build: ${relative}`);
  }
}
if (build.preservedMediaCount !== snapshotAssets.filter((asset) => asset.relative.startsWith("static/video/")).length) {
  throw new Error("preserved media accounting is incomplete");
}
if (!build.sanitization.fixtureOnly) {
  if (build.preservedMediaCount !== build.sanitization.transformations.hlsFilesPreserved) {
    throw new Error("stage output media count does not match the sanitization attestation");
  }
  const referencedSegments = new Set();
  const playlists = snapshotAssets.filter((asset) => asset.relative.startsWith("static/video/") && asset.relative.endsWith(".m3u8"));
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
  const allSegments = snapshotAssets.filter((asset) => asset.relative.startsWith("static/video/") && asset.relative.endsWith(".ts"));
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
  "occurrence-manifest.json",
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
process.stdout.write(`verified ${records.length} noindex stage routes and ${localReferenceCount} same-origin references\n`);
