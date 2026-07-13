import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";

import {
  draftBlockedOccurrenceExportPolicy,
  evaluateReportingFeedFreshness,
  inspectOccurrenceExportCandidates,
  prepareOccurrenceExportRequests,
  validateOccurrenceExportBatch,
  validateOccurrenceExportPolicy,
} from "../scripts/lib/occurrence-export-contract.mjs";
import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
  loadSelectedOccurrenceRelease,
  publishCurrentMediaGeneration,
  publishOccurrenceRelease,
  staticOccurrenceManifest,
} from "../scripts/lib/occurrence-release.mjs";

const CRC_TABLE = Array.from({ length: 256 }, (_, value) => {
  let crc = value;
  for (let bit = 0; bit < 8; bit += 1) crc = (crc & 1) === 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  return crc >>> 0;
});

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const typeBytes = Buffer.from(type);
  const result = Buffer.alloc(12 + data.length);
  result.writeUInt32BE(data.length, 0);
  typeBytes.copy(result, 4);
  data.copy(result, 8);
  result.writeUInt32BE(crc32(Buffer.concat([typeBytes, data])), 8 + data.length);
  return result;
}

function png(width = 320, height = 180, rgba = [24, 96, 48, 255]) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 6;
  const row = Buffer.alloc(1 + width * 4);
  for (let column = 0; column < width; column += 1) Buffer.from(rgba).copy(row, 1 + column * 4);
  const scanlines = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(scanlines)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function digest(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function canonical(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

async function workspace(context) {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-occurrence-export-"));
  const sourceRoot = path.join(root, "source");
  const storeRoot = path.join(root, "store");
  await Promise.all([mkdir(sourceRoot), mkdir(storeRoot)]);
  context.after(() => rm(root, { recursive: true, force: true }));
  return { root, sourceRoot, storeRoot };
}

function fixture() {
  const graph = discoverGraphOccurrence({
    route: "/evidence",
    ordinal: 0,
    liveUrl: "https://graphs.verdify.ai/d-solo/site-home/public?orgId=1&panelId=30&theme=dark&from=now-24h&to=now",
    title: "Climate evidence",
  });
  const media = discoverCurrentMediaOccurrence({
    route: "/",
    ordinal: 3,
    sourceUrl: "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    semanticRole: "Latest public greenhouse view",
  });
  const sourceSnapshotManifestSha256 = digest("snapshot");
  const manifest = staticOccurrenceManifest({
    snapshotId: `sanitized-content-sha256:${sourceSnapshotManifestSha256}`,
    discoveredGraphs: [graph],
    discoveredCurrentMedia: [media],
  });
  const manifestSha256 = digest(canonical(manifest));
  const blocked = draftBlockedOccurrenceExportPolicy({
    manifest,
    manifestSha256,
    policyVersion: "test-public-reporting-v1",
    approvedAt: "2026-07-13T12:00:00Z",
    cameraSources: [{
      occurrenceId: media.occurrenceId,
      url: "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    }],
  });
  const active = {
    ...blocked,
    activation: {
      ...blocked.activation,
      state: "approved",
      approvedBy: "jason",
      approvedAt: "2026-07-13T12:01:00Z",
    },
  };
  return { graph, media, manifest, manifestSha256, blocked, active };
}

function batch({ policy, graph, media, graphCandidate, mediaCandidate, id = "batch_fixture_0001", exportedAt = "2026-07-13T12:10:00Z", watermarkAt = "2026-07-13T12:00:00Z", releaseExpected = null, mediaExpected = null }) {
  return {
    contract: "verdify.lab-occurrence-export-batch",
    schemaVersion: 1,
    batchId: id,
    policyVersion: policy.policyVersion,
    sourceOccurrenceManifestSha256: policy.sourceOccurrenceManifestSha256,
    reportingFeed: {
      contract: "verdify.operator-public-reporting-feed",
      schemaVersion: 1,
      sourceId: "operator-public-reporting-feed-1",
      sourceClass: "public-reporting-projection",
      credentialClass: "reporting-read-only",
      direction: "one-way-read-only",
      sourceWatermark: `wm_${id.slice("batch_".length)}`,
      sourceWatermarkAt: watermarkAt,
    },
    exportedAt,
    expectedSelectionSha256: releaseExpected,
    graphs: [{
      occurrenceId: graph.occurrenceId,
      probeStatus: graphCandidate ? "success" : "timeout",
      candidate: graphCandidate,
    }],
    currentMedia: [{
      occurrenceId: media.occurrenceId,
      captureStatus: mediaCandidate ? "success" : "timeout",
      candidate: mediaCandidate,
      expectedSelectionSha256: mediaExpected,
    }],
  };
}

async function candidate(sourceRoot, kind, occurrenceId, bytes, capturedAt = "2026-07-13T12:00:00Z") {
  const sha = digest(bytes);
  const relativePath = `${kind}/${occurrenceId}/${sha}.png`;
  await mkdir(path.join(sourceRoot, kind, occurrenceId), { recursive: true });
  await writeFile(path.join(sourceRoot, ...relativePath.split("/")), bytes);
  return { relativePath, mediaType: "image/png", capturedAt };
}

test("checked-in Phase 4c policy is an exact blocked 143+2 allowlist", async () => {
  const policy = JSON.parse(await readFile(new URL("../config/lab-stage-occurrence-export-policy.json", import.meta.url)));
  validateOccurrenceExportPolicy(policy);
  assert.equal(policy.activation.state, "blocked");
  assert.equal(policy.reportingFeed.authority, "operator-owned");
  assert.equal(policy.reportingFeed.existingAnonymousGraphsAllowed, false);
  assert.equal(policy.reportingFeed.trackAPrimaryRoleAllowed, false);
  assert.equal(policy.reportingFeed.p95TargetSeconds, 900);
  assert.equal(policy.reportingFeed.staleAfterSeconds, 1800);
  assert.equal(policy.graphs.length, 143);
  assert.equal(policy.currentMedia.length, 2);
  assert.equal(new Set(policy.graphs.map((item) => item.occurrenceId)).size, 143);
  assert.deepEqual(policy.cameraUpstream.sources.map((source) => source.url).sort(), [
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080",
  ]);
  assert.equal(policy.cameraUpstream.method, "GET");
  assert.equal(policy.cameraUpstream.redirectsAllowed, false);
  assert.equal(policy.cameraUpstream.authorization, "forbidden");
  assert.match(policy.cameraUpstream.sanitization, /decode-reencode.*metadata-free/);
});

test("offline producer compiler has no network, database, camera, or credential client", async () => {
  const sources = await Promise.all([
    readFile(new URL("../scripts/lib/occurrence-export-contract.mjs", import.meta.url), "utf8"),
    readFile(new URL("../scripts/prepare-occurrence-export.mjs", import.meta.url), "utf8"),
  ]);
  const implementation = sources.join("\n");
  assert.doesNotMatch(implementation, /node:https|node:http|undici|axios|fetch\s*\(|asyncpg|postgres|DATABASE_URL/);
  assert.doesNotMatch(implementation, /Bearer\s|secretKeyRef|createConnection\s*\(/i);
  assert.match(implementation, /authorization: "forbidden"/);
});

test("blocked policy validates candidates but cannot prepare publish requests", async (context) => {
  const { sourceRoot, storeRoot } = await workspace(context);
  const { graph, media, manifest, manifestSha256, blocked } = fixture();
  const graphCandidate = await candidate(sourceRoot, "graphs", graph.occurrenceId, png());
  const mediaCandidate = await candidate(sourceRoot, "current-media", media.occurrenceId, png(640, 360));
  const exportBatch = batch({ policy: blocked, graph, media, graphCandidate, mediaCandidate });
  const inspected = await inspectOccurrenceExportCandidates({ policy: blocked, batch: exportBatch, sourceRoot });
  assert.equal(inspected.feedFreshness.status, "fresh");
  await assert.rejects(
    () => prepareOccurrenceExportRequests({
      policy: blocked,
      manifest,
      manifestSha256,
      batch: exportBatch,
      sourceRoot,
      storeRoot,
    }),
    /blocked pending separate Jason-gated feed, tier, and credential work/,
  );
});

test("source watermark has <=15m target and >30m stale alert semantics", () => {
  const { graph, media, blocked } = fixture();
  const base = { policy: blocked, graph, media, graphCandidate: null, mediaCandidate: null };
  assert.equal(evaluateReportingFeedFreshness(batch({ ...base, exportedAt: "2026-07-13T12:15:00Z" })).status, "fresh");
  assert.equal(evaluateReportingFeedFreshness(batch({ ...base, id: "batch_fixture_0002", exportedAt: "2026-07-13T12:15:01Z" })).status, "late");
  assert.equal(evaluateReportingFeedFreshness(batch({ ...base, id: "batch_fixture_0003", exportedAt: "2026-07-13T12:30:00Z" })).status, "late");
  assert.equal(evaluateReportingFeedFreshness(batch({ ...base, id: "batch_fixture_0004", exportedAt: "2026-07-13T12:30:01Z" })).status, "alert");
});

test("producer batch is closed, opaque for cameras, and enforces PNG MIME, paths, dimensions, and bytes", async (context) => {
  const { sourceRoot } = await workspace(context);
  const { graph, media, active } = fixture();
  const goodGraph = await candidate(sourceRoot, "graphs", graph.occurrenceId, png());
  const goodMedia = await candidate(sourceRoot, "current-media", media.occurrenceId, png(640, 360));
  const valid = batch({ policy: active, graph, media, graphCandidate: goodGraph, mediaCandidate: goodMedia });
  await inspectOccurrenceExportCandidates({ policy: active, batch: valid, sourceRoot });
  assert.doesNotMatch(JSON.stringify(valid), /greenhouse_1|api\.verdify|frigate|go2rtc/i);

  const wrongMime = structuredClone(valid);
  wrongMime.currentMedia[0].candidate.mediaType = "image/jpeg";
  assert.throws(() => validateOccurrenceExportBatch(wrongMime, active), /MIME type is not image\/png/);

  const leaked = structuredClone(valid);
  leaked.currentMedia[0].sourceUrl = "https://api.verdify.ai/forbidden";
  assert.throws(() => validateOccurrenceExportBatch(leaked, active), /current-media export batch entry is invalid/);

  const tinyCandidate = await candidate(sourceRoot, "graphs", graph.occurrenceId, png(2, 1));
  const tiny = batch({ policy: active, graph, media, graphCandidate: tinyCandidate, mediaCandidate: goodMedia });
  await assert.rejects(
    () => inspectOccurrenceExportCandidates({ policy: active, batch: tiny, sourceRoot }),
    /outside the approved MIME, byte, or dimension bounds/,
  );

  const wrongPath = structuredClone(valid);
  wrongPath.graphs[0].candidate.relativePath = `graphs/${graph.occurrenceId}/not-content-addressed.png`;
  await assert.rejects(
    () => inspectOccurrenceExportCandidates({ policy: active, batch: wrongPath, sourceRoot }),
    /not opaque and content-addressed/,
  );

  const staleMediaCandidate = await candidate(
    sourceRoot,
    "current-media",
    media.occurrenceId,
    png(800, 450),
    "2026-07-13T11:54:59Z",
  );
  const staleMedia = batch({ policy: active, graph, media, graphCandidate: goodGraph, mediaCandidate: staleMediaCandidate });
  await assert.rejects(
    () => inspectOccurrenceExportCandidates({ policy: active, batch: staleMedia, sourceRoot }),
    /current-media candidate is stale; retain last-known-good/,
  );
});

test("canonical requests publish every occurrence and failed refresh retains graph and camera LKG", async (context) => {
  const { sourceRoot, storeRoot } = await workspace(context);
  const { graph, media, manifest, manifestSha256, active } = fixture();
  const graphCandidate = await candidate(sourceRoot, "graphs", graph.occurrenceId, png());
  const mediaCandidate = await candidate(sourceRoot, "current-media", media.occurrenceId, png(640, 360));
  const firstBatch = batch({ policy: active, graph, media, graphCandidate, mediaCandidate });
  const first = await prepareOccurrenceExportRequests({
    policy: active,
    manifest,
    manifestSha256,
    batch: firstBatch,
    sourceRoot,
    storeRoot,
  });
  assert.equal(first.mediaRequests.length, 1);
  assert.equal(first.releaseRequest.graphs.length, 1);
  assert.equal(first.releaseRequest.currentMedia.length, 1);
  assert.doesNotMatch(JSON.stringify(first), /greenhouse_1|latest\.jpg|api\.verdify/i);
  for (const request of first.mediaRequests) await publishCurrentMediaGeneration(request);
  await publishOccurrenceRelease(first.releaseRequest);
  const selected = await loadSelectedOccurrenceRelease(storeRoot);
  assert.equal(selected.current.occurrences.graphs[0].state, "verified");
  assert.equal(selected.current.occurrences.currentMedia[0].state, "verified");

  const mediaSelectionSha256 = selected.current.occurrences.currentMedia[0].pointer.selectionSha256;
  const failedBatch = batch({
    policy: active,
    graph,
    media,
    graphCandidate: null,
    mediaCandidate: null,
    id: "batch_fixture_0005",
    exportedAt: "2026-07-13T12:20:00Z",
    watermarkAt: "2026-07-13T12:15:00Z",
    releaseExpected: selected.selectionSha256,
    mediaExpected: mediaSelectionSha256,
  });
  const failed = await prepareOccurrenceExportRequests({
    policy: active,
    manifest,
    manifestSha256,
    batch: failedBatch,
    sourceRoot,
    storeRoot,
  });
  assert.equal(failed.mediaRequests.length, 0);
  await publishOccurrenceRelease(failed.releaseRequest);
  const retained = await loadSelectedOccurrenceRelease(storeRoot);
  assert.equal(retained.current.occurrences.graphs[0].state, "retained-last-known-good");
  assert.equal(retained.current.occurrences.currentMedia[0].state, "verified");
  assert.equal(
    retained.current.occurrences.graphs[0].fallback.sha256,
    selected.current.occurrences.graphs[0].fallback.sha256,
  );
  assert.equal(
    retained.current.occurrences.currentMedia[0].fallback.sha256,
    selected.current.occurrences.currentMedia[0].fallback.sha256,
  );
});

test("more-than-30-minute reporting watermark cannot replace LKG", async (context) => {
  const { sourceRoot, storeRoot } = await workspace(context);
  const { graph, media, manifest, manifestSha256, active } = fixture();
  const stale = batch({
    policy: active,
    graph,
    media,
    graphCandidate: null,
    mediaCandidate: null,
    exportedAt: "2026-07-13T12:30:01Z",
  });
  await assert.rejects(
    () => prepareOccurrenceExportRequests({
      policy: active,
      manifest,
      manifestSha256,
      batch: stale,
      sourceRoot,
      storeRoot,
    }),
    /more than 30 minutes stale; retain last-known-good/,
  );
});
