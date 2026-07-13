import assert from "node:assert/strict";
import { cp, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  aliasRecords,
  cameraSnapshotAsset,
  folderRecords,
  imageDimensions,
  normalizeRoute,
  renderMarkdown,
  routeFromSource,
  socialImagePath,
  splitFrontmatter,
  tagRecords,
  verifyCompatAssets,
} from "../scripts/compile-snapshot.mjs";
import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
  occurrenceStateIndex,
} from "../scripts/lib/occurrence-release.mjs";
import { verifySanitizationAttestation, verifySnapshot } from "../scripts/lib/snapshot.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FIXTURE = path.join(ROOT, "tests", "fixtures", "snapshot");

test("fixture snapshot is exact, local, and explicitly provisional", async () => {
  const snapshot = await verifySnapshot(FIXTURE, { allowSyntheticFixture: true });
  assert.equal(snapshot.files.size, 10);
  assert.equal(snapshot.approvalEligible, false);
  assert.equal(snapshot.evidenceStatus, "provisional-only");
  assert.match(snapshot.manifestDigest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(snapshot.sanitization.fixtureOnly, true);
});

test("real snapshot mode refuses a synthetic fixture or an absent attestation", async () => {
  await assert.rejects(() => verifySnapshot(FIXTURE), /attestation/);
});

test("sanitization attestation is closed, canonical, bounded, and HLS-accounted", async (context) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-astro-attestation-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  const digest = "1".repeat(64);
  const inventory = new Map(Array.from({ length: 429 }, (_, index) => [
    index < 179 ? `static/video/media-${index}.bin` : `file-${index}.txt`,
    {},
  ]));
  const attestation = {
    contract: "verdify.lab-stage-sanitized-snapshot",
    schemaVersion: 1,
    evidenceStatus: "provisional-only",
    approvalEligible: false,
    sourceManifestSha256: "05d4373ebf59bef3a7899c5e94514971d663fd7264db09b2b5cb26fec78410b1",
    sanitizedManifestSha256: digest,
    sourceFileCount: 429,
    sanitizedFileCount: 429,
    policyVersion: "verdify-public-output-stage-v1",
    guardReportSha256: "2".repeat(64),
    guardSchemaVersion: 2,
    guardFindings: 0,
    transformations: {
      changedFiles: 8,
      textRedactionFiles: 3,
      invalidValueRepairFiles: 3,
      pngReencodeFiles: 3,
      hlsFilesPreserved: 179,
    },
  };
  await writeFile(path.join(root, "attestation.json"), `${JSON.stringify(attestation, null, 2)}\n`);
  const verified = await verifySanitizationAttestation(root, digest, inventory);
  assert.equal(verified.fixtureOnly, false);

  attestation.guardFindings = 1;
  await writeFile(path.join(root, "attestation.json"), `${JSON.stringify(attestation, null, 2)}\n`);
  await assert.rejects(() => verifySanitizationAttestation(root, digest, inventory), /release policy/);

  attestation.guardFindings = 0;
  attestation.transformations.changedFiles = 7;
  await writeFile(path.join(root, "attestation.json"), `${JSON.stringify(attestation, null, 2)}\n`);
  await assert.rejects(() => verifySanitizationAttestation(root, digest, inventory), /transformation counts/);
});

test("route contract distinguishes root, leaf, and folder physical outputs", () => {
  assert.deepEqual(routeFromSource("index.md"), { route: "/", kind: "root", physicalPath: "index.html" });
  assert.deepEqual(routeFromSource("start/about.md"), {
    route: "/start/about",
    kind: "page",
    physicalPath: "start/about.html",
  });
  assert.deepEqual(routeFromSource("data/plans/index.md"), {
    route: "/data/plans",
    kind: "folder",
    physicalPath: "data/plans/index.html",
  });
  assert.equal(normalizeRoute("//start//about/"), "/start/about");
  assert.throws(() => normalizeRoute("../outside"), /unsafe route/);
});

test("camera snapshots require a same-origin last-known-good artifact", () => {
  const source = "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080";
  assert.deepEqual(cameraSnapshotAsset(source, new Set()), {
    sourceUrl: source,
    relative: "static/cameras/greenhouse_1/latest.jpg",
    publicPath: "/static/cameras/greenhouse_1/latest.jpg",
    available: false,
  });
  assert.equal(cameraSnapshotAsset(source, new Set(["static/cameras/greenhouse_1/latest.jpg"])).available, true);
  for (const rejected of [
    "http://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg",
    "https://example.com/api/v1/public/cameras/greenhouse_1/latest.jpg",
    "https://api.verdify.ai/api/v1/public/cameras/../private/latest.jpg",
  ]) {
    assert.equal(cameraSnapshotAsset(rejected, new Set()), null);
  }
});

test("camera rendering fails closed without a selected CAS generation and removes the legacy refresher", async () => {
  const rendered = await renderMarkdown(
    '<a href="https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080"><img class="camera-snapshot" data-camera-src="https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080" src="https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080" alt="Camera"></a><script src="/static/camera-refresh.js"></script>',
    { relative: "index.md", route: "/" },
    new Map(),
    new Map(),
    new Set(),
    new Map(),
    new Map(),
  );
  assert.match(rendered.html, /current-media-evidence--pending/);
  assert.doesNotMatch(rendered.html, /<img[^>]+api\.verdify\.ai|camera-refresh\.js/);
  assert.equal(rendered.currentMedia.length, 1);
});

test("missing local routes and images become explicit non-broken publication states", async () => {
  const rendered = await renderMarkdown(
    "[Missing plan](/plans/2099-01-01)\n\n![Missing proof](/static/vision/missing.jpg)",
    { relative: "index.md" },
    new Map(),
    new Map([["index.md", "/"]]),
    new Set(),
    new Map(),
    new Map(),
    new Set(["/"]),
  );
  assert.match(rendered.html, /class="unavailable-reference"/);
  assert.match(rendered.html, /class="media-unavailable" role="img"/);
  assert.doesNotMatch(rendered.html, /href="\/plans\/2099-01-01"|src="\/static\/vision\/missing\.jpg"/);
  assert.deepEqual(rendered.unavailable.map((item) => item.kind), ["link", "image"]);
});

test("rolling latest is generated once from the newest dated plan and every other alias collision fails", () => {
  const record = (date, aliases = ["/plans/latest"]) => ({
    route: `/plans/${date}`,
    canonicalPath: `/plans/${date}`,
    source: `plans/${date}.md`,
    title: date,
    aliases,
    date,
  });
  const result = aliasRecords([record("2026-06-07"), record("2026-07-12")]);
  assert.equal(result.aliases.length, 1);
  assert.equal(result.aliases[0].route, "/plans/latest");
  assert.equal(result.aliases[0].target, "/plans/2026-07-12");
  assert.equal(result.compatibility.suppressedDeclarationCount, 2);
  assert.deepEqual(result.compatibility.suppressedSources, ["plans/2026-06-07.md", "plans/2026-07-12.md"]);

  assert.throws(
    () => aliasRecords([record("2026-06-07", ["/duplicate"]), record("2026-07-12", ["/duplicate"])]),
    /duplicate alias/,
  );
});

test("missing top-level folder indexes preserve direct child and breadcrumb discovery", () => {
  const records = [
    { route: "/data/forecast", canonicalPath: "/data/forecast", title: "Forecast", tags: ["weather"] },
    { route: "/data/plans", canonicalPath: "/data/plans", title: "Plans", tags: ["planning"] },
    { route: "/water/irrigation", canonicalPath: "/water/irrigation", title: "Irrigation", tags: [] },
  ];
  const folders = folderRecords(records, new Set(records.map((record) => record.route)));
  assert.deepEqual(folders.map((record) => record.route), ["/data", "/water"]);
  assert.match(folders[0].html, /href="\/data\/forecast"/);
  assert.match(folders[0].html, /href="\/tags\/planning"/);
});

test("tag collections preserve counts, dates, item metadata, and baseline noindex policy", () => {
  const records = [
    {
      route: "/reference/architecture",
      canonicalPath: "/reference/architecture",
      title: "Architecture",
      tags: ["architecture", "planning"],
      date: "2026-05-19",
    },
    {
      route: "/reference/safety",
      canonicalPath: "/reference/safety",
      title: "Safety",
      tags: ["architecture"],
      date: "2026-05-18",
    },
  ];
  const tags = tagRecords(records, new Set(records.map((record) => record.route)));
  const architecture = tags.find((record) => record.route === "/tags/architecture");
  const index = tags.find((record) => record.route === "/tags");
  assert.equal(architecture.noindex, true);
  assert.equal(index.noindex, true);
  assert.match(architecture.html, /2 items with this tag/);
  assert.match(architecture.html, /May 19, 2026/);
  assert.match(architecture.html, /href="\/tags\/planning"/);
  assert.match(index.html, /<h1>Tag Index<\/h1>/);
  assert.match(index.html, /<h2><a href="\/tags\/architecture">architecture<\/a><\/h2>/);
});

test("social images are exact same-origin snapshot assets", () => {
  const assets = new Set(["static/photos/about.jpeg"]);
  assert.equal(socialImagePath("/static/photos/about.jpeg", assets, "about.md"), "/static/photos/about.jpeg");
  assert.equal(socialImagePath(undefined, assets, "about.md"), "");
  assert.throws(() => socialImagePath("https://example.com/about.jpeg", assets, "about.md"), /same-origin/);
  assert.throws(() => socialImagePath("/static/photos/missing.jpeg", assets, "about.md"), /absent/);
});

test("frontmatter parser preserves nested YAML and rejects ambiguity", () => {
  const [frontmatter, body] = splitFrontmatter("---\ntitle: Test\naliases: [old]\n---\n# Body\n", "test.md");
  assert.equal(frontmatter.title, "Test");
  assert.deepEqual(frontmatter.aliases, ["old"]);
  assert.equal(body, "# Body\n");
  assert.throws(() => splitFrontmatter("---\ntitle: one\ntitle: two\n---\n", "bad.md"), /invalid YAML/);
});

test("image dimensions are read from bounded static image headers", () => {
  const svg = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630"></svg>');
  assert.deepEqual(imageDimensions(svg, "evidence.svg"), { width: 1200, height: 630 });
  assert.equal(imageDimensions(Buffer.from("not an image"), "evidence.bin"), null);
});

test("video evidence defers media bytes until the visitor requests playback", async () => {
  const rendered = await renderMarkdown(
    '<video controls preload="metadata"><source src="/static/video/proof.mp4" type="video/mp4"></video>',
    { relative: "index.md" },
    new Map(),
    new Map([["index.md", "/"]]),
    new Set(["static/video/proof.mp4"]),
    new Map(),
    new Map(),
    new Set(["/"]),
  );
  assert.match(rendered.html, /<video controls preload="none">/);
});

test("legacy public compatibility assets are closed and digest-bound", async () => {
  const assets = await verifyCompatAssets();
  assert.equal(assets.length, 11);
  assert.ok(assets.every((asset) => asset.relative && asset.bytes.length > 0));
});

test("specialist renderer uses only selected same-origin decoded fallbacks", async () => {
  const graphUrl = "https://graphs.verdify.ai/d-solo/site-home/public?panelId=7&from=now-24h&to=now";
  const cameraUrl = "https://api.verdify.ai/api/v1/public/cameras/cam-public-fixture/latest.png";
  const graph = discoverGraphOccurrence({
    route: "/",
    ordinal: 0,
    liveUrl: graphUrl,
    title: "Climate evidence",
  });
  const media = discoverCurrentMediaOccurrence({
    route: "/",
    ordinal: 0,
    sourceUrl: cameraUrl,
    semanticRole: "Current greenhouse view",
  });
  const fallback = {
    publicPath: `/evidence/blobs/sha256/${"1".repeat(64)}.png`,
    sha256: "1".repeat(64),
    decodedSha256: "2".repeat(64),
    decodedBytes: 640 * 360 * 4,
    bytes: 100,
    mediaType: "image/png",
    width: 640,
    height: 360,
    capturedAt: "2026-07-12T12:00:00Z",
    verifiedAt: "2026-07-12T12:00:30Z",
    policyVersion: "synthetic-fixture-only",
  };
  const selected = occurrenceStateIndex({
    occurrences: {
      graphs: [{ ...graph, state: "verified", fallback }],
      currentMedia: [{ ...media, state: "verified", fallback }],
    },
  });
  const rendered = await renderMarkdown(
    `<img src="${cameraUrl}" alt="Current greenhouse view">\n\n<iframe src="${graphUrl}" title="Climate evidence"></iframe>`,
    { relative: "index.md", route: "/" },
    new Map(),
    new Map(),
    new Set(),
    new Map(),
    new Map(),
    new Set(["/"]),
    selected,
  );
  assert.match(rendered.html, /src="\/evidence\/blobs\/sha256\/1{64}\.png"/);
  assert.match(rendered.html, /data-current-media-target="\/evidence\/current\/media_[0-9a-f]{24}"/);
  assert.match(rendered.html, /data-image-sha256="1{64}"/);
  assert.doesNotMatch(rendered.html, /data-image-src="https:\/\/graphs\.verdify\.ai/);
  assert.equal(rendered.grafana.length, 1);
  assert.equal(rendered.currentMedia.length, 1);
});

test("snapshot verification refuses tampering, additions, and links", async (context) => {
  async function copiedFixture() {
    const root = await mkdtemp(path.join(tmpdir(), "verdify-astro-snapshot-"));
    await cp(FIXTURE, root, { recursive: true });
    context.after(() => rm(root, { recursive: true, force: true }));
    return root;
  }

  const tampered = await copiedFixture();
  await writeFile(path.join(tampered, "content", "index.md"), "tampered\n");
  await assert.rejects(() => verifySnapshot(tampered, { allowSyntheticFixture: true }), /digest mismatch/);

  const addition = await copiedFixture();
  await writeFile(path.join(addition, "content", "unexpected.txt"), "unexpected\n");
  await assert.rejects(() => verifySnapshot(addition, { allowSyntheticFixture: true }), /tree membership/);

  const linked = await copiedFixture();
  await symlink("index.md", path.join(linked, "content", "linked.md"));
  await assert.rejects(() => verifySnapshot(linked, { allowSyntheticFixture: true }), /symlink/);
});

test("content builder has no database, object-store, Grafana, or HTTP client", async () => {
  const sources = await Promise.all(
    ["scripts/compile-snapshot.mjs", "scripts/lib/snapshot.mjs"].map((relative) =>
      readFile(path.join(ROOT, relative), "utf8"),
    ),
  );
  const code = sources.join("\n");
  for (const forbidden of [
    /from ["']node:https?["']/,
    /\bfetch\s*\(/,
    /\bpg\b/,
    /postgres/i,
    /aws-sdk/i,
    /s3:\/\//i,
  ]) {
    assert.doesNotMatch(code, forbidden);
  }
});
