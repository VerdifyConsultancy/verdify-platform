import assert from "node:assert/strict";
import { cp, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  aliasRecords,
  cameraSnapshotAsset,
  imageDimensions,
  normalizeRoute,
  renderMarkdown,
  routeFromSource,
  splitFrontmatter,
} from "../scripts/compile-snapshot.mjs";
import { verifySanitizationAttestation, verifySnapshot } from "../scripts/lib/snapshot.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FIXTURE = path.join(ROOT, "tests", "fixtures", "snapshot");

test("fixture snapshot is exact, local, and explicitly provisional", async () => {
  const snapshot = await verifySnapshot(FIXTURE, { allowSyntheticFixture: true });
  assert.equal(snapshot.files.size, 6);
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

test("camera rendering rewrites verified artifacts and removes the legacy refresher", async () => {
  const rendered = await renderMarkdown(
    '<a href="https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080"><img class="camera-snapshot" data-camera-src="https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080" src="https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080" alt="Camera"></a><script src="/static/camera-refresh.js"></script>',
    { relative: "index.md" },
    new Map(),
    new Map(),
    new Set(),
    new Map(),
    new Map([["static/cameras/greenhouse_1/latest.jpg", {}]]),
  );
  assert.match(rendered.html, /src="\/static\/cameras\/greenhouse_1\/latest\.jpg"/);
  assert.match(rendered.html, /data-camera-local-src="\/static\/cameras\/greenhouse_1\/latest\.jpg"/);
  assert.doesNotMatch(rendered.html, /data-camera-src|camera-refresh\.js/);
  assert.equal(rendered.cameras.length, 1);
  assert.equal(rendered.cameras[0].available, true);
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
