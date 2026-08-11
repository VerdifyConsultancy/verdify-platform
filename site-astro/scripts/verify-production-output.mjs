import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PRODUCTION_ORIGIN = "https://lab.verdify.ai";

function parseArgs(values) {
  let allowFixture = false;
  let dist = path.join(PROJECT_ROOT, "dist");
  for (let index = 0; index < values.length; index += 1) {
    if (values[index] === "--allow-fixture") {
      allowFixture = true;
    } else if (values[index] === "--dist" && values[index + 1]) {
      dist = path.resolve(values[index + 1]);
      index += 1;
    } else {
      throw new Error("Usage: node scripts/verify-production-output.mjs [--allow-fixture] [--dist DIRECTORY]");
    }
  }
  return { allowFixture, dist };
}

function requireFragment(source, fragment, message) {
  if (!source.includes(fragment)) throw new Error(message);
}

function refreshTarget(html, physicalPath) {
  const match = /<meta http-equiv="refresh" content="0; URL='([^']+)'">/u.exec(html);
  if (!match || !match[1].startsWith("/")) {
    throw new Error(`production alias lost its same-origin refresh target: ${physicalPath}`);
  }
  return match[1];
}

function verifySelectedEvidence(build, occurrenceManifest) {
  const occurrences = [...occurrenceManifest.graphs, ...occurrenceManifest.currentMedia];
  if (occurrences.length === 0) return;
  const selectedMatch = /^sha256:([0-9a-f]{64})$/u.exec(build.selectedOccurrenceManifestSha256 ?? "");
  if (!selectedMatch) {
    throw new Error("production evidence has no selected immutable occurrence manifest");
  }
  if (occurrenceManifest.selectedManifestSha256 !== selectedMatch[1]) {
    throw new Error("production build and static occurrence manifest select different releases");
  }
  if (build.materializedOccurrenceBlobCount < 1) {
    throw new Error("production evidence selection materialized no immutable blobs");
  }
  for (const occurrence of occurrences) {
    const fallback = occurrence.selected?.fallback;
    if (
      !fallback
      || !/^[0-9a-f]{64}$/u.test(fallback.sha256 ?? "")
      || fallback.publicPath !== `/evidence/blobs/sha256/${fallback.sha256}.png`
    ) {
      throw new Error(`production occurrence is missing a selected verified fallback: ${occurrence.occurrenceId}`);
    }
  }
}

export { verifySelectedEvidence };

export async function verifyProductionOutput({ dist, allowFixture = false }) {
  const read = (relative) => readFile(path.join(dist, ...relative.split("/")), "utf8");
  const build = JSON.parse(await read("static-build.json"));
  const routeManifest = JSON.parse(await read("route-manifest.json"));
  const occurrenceManifest = JSON.parse(await read("occurrence-manifest.json"));

  if (
    build.contract !== "verdify.lab-astro-stage-build"
    || build.schemaVersion !== 1
    || build.siteOrigin !== PRODUCTION_ORIGIN
    || build.stageGlobalNoindex !== false
  ) {
    throw new Error("production build identity, origin, or global robots policy is invalid");
  }
  if (JSON.stringify(routeManifest.build) !== JSON.stringify(build)) {
    throw new Error("production route manifest is not bound to static-build.json");
  }
  const fixture = build.sanitization?.fixtureOnly === true;
  if (fixture && !allowFixture) {
    throw new Error("synthetic fixtures can never satisfy the production release verifier");
  }
  if (!fixture) {
    if (build.activationEligible !== true || build.localEvidenceStatus === "provisional-only") {
      throw new Error("production release is not backed by activation-eligible immutable evidence");
    }
    if (build.unavailableReferenceCount !== 0) {
      throw new Error("production release retains unavailable same-origin references");
    }
    verifySelectedEvidence(build, occurrenceManifest);
  }

  if (
    occurrenceManifest.contract !== "verdify.lab-static-occurrence-manifest"
    || occurrenceManifest.snapshotId !== build.snapshotId
    || occurrenceManifest.graphs.length !== build.grafanaOccurrenceCount
    || occurrenceManifest.currentMedia.length !== build.currentMediaOccurrenceCount
  ) {
    throw new Error("production occurrence evidence is incomplete or belongs to another snapshot");
  }

  let indexableRoutes = 0;
  for (const record of routeManifest.routes) {
    const html = await read(record.physicalPath);
    const expectedRobots = record.kind === "alias" || record.noindex ? "noindex,follow" : "index,follow";
    requireFragment(
      html,
      `<meta name="robots" content="${expectedRobots}">`,
      `production route has the wrong per-page robots policy: ${record.physicalPath}`,
    );
    if (expectedRobots === "index,follow") indexableRoutes += 1;

    const expectedCanonical = record.kind === "alias"
      ? `${PRODUCTION_ORIGIN}${refreshTarget(html, record.physicalPath)}`
      : `${PRODUCTION_ORIGIN}${record.canonicalPath}`;
    requireFragment(
      html,
      `<link rel="canonical" href="${expectedCanonical}">`,
      `production route has the wrong canonical URL: ${record.physicalPath}`,
    );
    requireFragment(
      html,
      `<meta property="og:url" content="${expectedCanonical}">`,
      `production route has the wrong Open Graph URL: ${record.physicalPath}`,
    );
    if (/Stage preview|globally noindex|not activation evidence|lab-stage\.verdify\.ai/u.test(html)) {
      throw new Error(`production route retains stage-only output: ${record.physicalPath}`);
    }
  }
  if (indexableRoutes < 1) throw new Error("production output has no indexable canonical route");

  const notFound = await read("404.html");
  requireFragment(notFound, '<meta name="robots" content="noindex,follow">', "production 404 must remain noindex");
  requireFragment(
    notFound,
    `<link rel="canonical" href="${PRODUCTION_ORIGIN}/404">`,
    "production 404 canonical URL is wrong",
  );

  const robots = await read("robots.txt");
  if (robots !== `User-agent: *\nAllow: /\nDisallow: /static/vision/\nDisallow: /greenhouse/lessons/raw\n\nSitemap: ${PRODUCTION_ORIGIN}/sitemap.xml\n`) {
    throw new Error("production robots.txt is not the canonical public-evidence policy");
  }
  for (const relative of ["sitemap.xml", "rss.xml"]) {
    const source = await read(relative);
    if (source.includes("lab-stage.verdify.ai") || !source.includes(PRODUCTION_ORIGIN)) {
      throw new Error(`production ${relative} uses the wrong origin`);
    }
  }

  const runtimeHeaders = await readFile(path.join(PROJECT_ROOT, "nginx/security-headers-production.inc"), "utf8");
  if (/X-Robots-Tag|noindex|nofollow|noarchive/iu.test(runtimeHeaders)) {
    throw new Error("production runtime headers globally override per-page robots metadata");
  }
  process.stdout.write(`verified production output: ${routeManifest.routes.length} routes, ${indexableRoutes} indexable\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const options = parseArgs(process.argv.slice(2));
  verifyProductionOutput(options).catch((error) => {
    process.stderr.write(`verify-production-output: ${error.message}\n`);
    process.exitCode = 1;
  });
}
