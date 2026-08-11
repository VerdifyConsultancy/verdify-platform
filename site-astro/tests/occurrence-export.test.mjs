import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { deflateSync } from "node:zlib";

import {
  cameraRequestProvenanceSha256,
  draftBlockedOccurrenceExportPolicy,
  evaluateReportingFeedFreshness,
  inspectOccurrenceExportCandidates,
  occurrenceExportPolicySha256,
  prepareOccurrenceExportRequests,
  validateOccurrenceExportBatch,
  validateOccurrenceExportPolicy,
} from "../scripts/lib/occurrence-export-contract.mjs";
import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
  loadSelectedCurrentMediaGeneration,
  loadSelectedOccurrenceRelease,
  publishCurrentMediaGeneration,
  publishOccurrenceRelease,
  staticOccurrenceManifest,
} from "../scripts/lib/occurrence-release.mjs";

const run = promisify(execFile);
const MANAGE_OCCURRENCE_CLI = fileURLToPath(new URL("../scripts/manage-occurrence-release.mjs", import.meta.url));

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
    activatedAt: "2026-07-13T12:00:00Z",
    cameraSources: [{
      occurrenceId: media.occurrenceId,
      url: "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    }],
  });
  const active = {
    ...blocked,
    activation: {
      ...blocked.activation,
      state: "active",
      activatedBy: "direct-task",
      activatedAt: "2026-07-13T12:01:00Z",
    },
  };
  return { graph, media, manifest, manifestSha256, blocked, active };
}

function batch({ policy, graph, media, graphCandidate, mediaCandidate, id = "batch_fixture_0001", exportedAt = "2026-07-13T12:10:00Z", watermarkAt = "2026-07-13T12:00:00Z", releaseExpected = null, mediaExpected = null }) {
  const mediaPolicy = policy.currentMedia.find((record) => record.occurrenceId === media.occurrenceId);
  return {
    contract: "verdify.lab-occurrence-export-batch",
    schemaVersion: 2,
    batchId: id,
    policyVersion: policy.policyVersion,
    policySha256: occurrenceExportPolicySha256(policy),
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
      requestProvenanceSha256: mediaPolicy.requestProvenanceSha256,
      candidate: mediaCandidate,
      expectedSelectionSha256: mediaExpected,
    }],
  };
}

async function candidate(
  sourceRoot,
  kind,
  occurrenceId,
  bytes,
  capturedAt = "2026-07-13T12:00:00Z",
  requestProvenanceSha256 = null,
) {
  const sha = digest(bytes);
  const relativePath = `${kind}/${occurrenceId}/${sha}.png`;
  await mkdir(path.join(sourceRoot, kind, occurrenceId), { recursive: true });
  await writeFile(path.join(sourceRoot, ...relativePath.split("/")), bytes);
  return {
    relativePath,
    mediaType: "image/png",
    capturedAt,
    ...(kind === "current-media" ? { requestProvenanceSha256 } : {}),
  };
}

function requestProvenance(policy, occurrenceId) {
  return policy.currentMedia.find((record) => record.occurrenceId === occurrenceId).requestProvenanceSha256;
}

function processingAtFor(exportBatch, delayMilliseconds = 30_000) {
  return new Date(Date.parse(exportBatch.exportedAt) + delayMilliseconds).toISOString();
}

function fullFixture() {
  const graphs = Array.from({ length: 143 }, (_, index) => discoverGraphOccurrence({
    route: `/evidence/full-${index + 1}`,
    ordinal: index,
    liveUrl: `https://graphs.verdify.ai/d-solo/site-home/public?orgId=1&panelId=${index + 1}&theme=dark&from=now-24h&to=now`,
    title: `Full graph ${index + 1}`,
  }));
  const cameraUrls = [
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080",
  ];
  const currentMedia = cameraUrls.map((sourceUrl, index) => discoverCurrentMediaOccurrence({
    route: `/evidence/camera-${index + 1}`,
    ordinal: index,
    sourceUrl,
    semanticRole: `Full camera ${index + 1}`,
  }));
  const sourceSnapshotManifestSha256 = digest("full-143-plus-2-snapshot");
  const manifest = staticOccurrenceManifest({
    snapshotId: `sanitized-content-sha256:${sourceSnapshotManifestSha256}`,
    discoveredGraphs: graphs,
    discoveredCurrentMedia: currentMedia,
  });
  const manifestSha256 = digest(canonical(manifest));
  const blocked = draftBlockedOccurrenceExportPolicy({
    manifest,
    manifestSha256,
    policyVersion: "full-public-reporting-v2",
    activatedAt: "2026-07-13T12:00:00Z",
    cameraSources: currentMedia.map((occurrence, index) => ({
      occurrenceId: occurrence.occurrenceId,
      url: cameraUrls[index],
    })),
  });
  const active = {
    ...blocked,
    activation: {
      ...blocked.activation,
      state: "active",
      activatedBy: "direct-task",
      activatedAt: "2026-07-13T12:01:00Z",
    },
  };
  return { graphs, currentMedia, manifest, manifestSha256, active };
}

function fullBatch({
  policy,
  graphs,
  currentMedia,
  candidates = new Map(),
  status = "success",
  id,
  exportedAt,
  watermarkAt,
  releaseExpected = null,
  mediaExpected = new Map(),
}) {
  const requestById = new Map(policy.currentMedia.map((record) => [
    record.occurrenceId,
    record.requestProvenanceSha256,
  ]));
  return {
    contract: "verdify.lab-occurrence-export-batch",
    schemaVersion: 2,
    batchId: id,
    policyVersion: policy.policyVersion,
    policySha256: occurrenceExportPolicySha256(policy),
    sourceOccurrenceManifestSha256: policy.sourceOccurrenceManifestSha256,
    reportingFeed: {
      contract: "verdify.operator-public-reporting-feed",
      schemaVersion: 1,
      sourceId: "operator-public-reporting-feed-full",
      sourceClass: "public-reporting-projection",
      credentialClass: "reporting-read-only",
      direction: "one-way-read-only",
      sourceWatermark: `wm_${id.slice("batch_".length)}`,
      sourceWatermarkAt: watermarkAt,
    },
    exportedAt,
    expectedSelectionSha256: releaseExpected,
    graphs: graphs.map((occurrence) => ({
      occurrenceId: occurrence.occurrenceId,
      probeStatus: status,
      candidate: status === "success" ? candidates.get(occurrence.occurrenceId) : null,
    })),
    currentMedia: currentMedia.map((occurrence) => ({
      occurrenceId: occurrence.occurrenceId,
      captureStatus: status,
      requestProvenanceSha256: requestById.get(occurrence.occurrenceId),
      candidate: status === "success" ? candidates.get(occurrence.occurrenceId) : null,
      expectedSelectionSha256: mediaExpected.get(occurrence.occurrenceId) ?? null,
    })),
  };
}

test("checked-in Phase 4c policy is an exact blocked 143+2 allowlist", async () => {
  const policy = JSON.parse(await readFile(new URL("../config/lab-stage-occurrence-export-policy.json", import.meta.url)));
  validateOccurrenceExportPolicy(policy);
  assert.equal(policy.schemaVersion, 2);
  assert.equal(policy.policyVersion, "lab-public-reporting-v2");
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
  for (const source of policy.cameraUpstream.sources) {
    const active = policy.currentMedia.find((record) => record.occurrenceId === source.occurrenceId);
    assert.equal(source.requestProvenanceSha256, cameraRequestProvenanceSha256(source));
    assert.equal(source.requestProvenanceSha256, active.requestProvenanceSha256);
  }

  const swappedUrls = structuredClone(policy);
  [swappedUrls.cameraUpstream.sources[0].url, swappedUrls.cameraUpstream.sources[1].url] = [
    swappedUrls.cameraUpstream.sources[1].url,
    swappedUrls.cameraUpstream.sources[0].url,
  ];
  assert.throws(() => validateOccurrenceExportPolicy(swappedUrls), /outside the exact public allowlist/);

  const swappedDigests = structuredClone(policy);
  [
    swappedDigests.cameraUpstream.sources[0].requestProvenanceSha256,
    swappedDigests.cameraUpstream.sources[1].requestProvenanceSha256,
  ] = [
    swappedDigests.cameraUpstream.sources[1].requestProvenanceSha256,
    swappedDigests.cameraUpstream.sources[0].requestProvenanceSha256,
  ];
  assert.throws(() => validateOccurrenceExportPolicy(swappedDigests), /outside the exact public allowlist/);
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
  const mediaCandidate = await candidate(
    sourceRoot,
    "current-media",
    media.occurrenceId,
    png(640, 360),
    "2026-07-13T12:00:00Z",
    requestProvenance(blocked, media.occurrenceId),
  );
  const exportBatch = batch({ policy: blocked, graph, media, graphCandidate, mediaCandidate });
  const inspected = await inspectOccurrenceExportCandidates({
    policy: blocked,
    batch: exportBatch,
    sourceRoot,
    processingAt: processingAtFor(exportBatch),
  });
  assert.equal(inspected.feedFreshness.status, "fresh");
  await assert.rejects(
    () => prepareOccurrenceExportRequests({
      policy: blocked,
      manifest,
      manifestSha256,
      batch: exportBatch,
      sourceRoot,
      storeRoot,
      processingAt: processingAtFor(exportBatch),
    }),
    /blocked pending separate safety-checked feed, tier, and credential work/,
  );
});

test("source watermark has <=15m target and >30m stale alert semantics", () => {
  const { graph, media, blocked } = fixture();
  const base = { policy: blocked, graph, media, graphCandidate: null, mediaCandidate: null };
  const evaluateAtExport = (exportBatch) => evaluateReportingFeedFreshness(exportBatch, exportBatch.exportedAt);
  assert.equal(evaluateAtExport(batch({ ...base, exportedAt: "2026-07-13T12:15:00Z" })).status, "fresh");
  assert.equal(evaluateAtExport(batch({ ...base, id: "batch_fixture_0002", exportedAt: "2026-07-13T12:15:01Z" })).status, "late");
  assert.equal(evaluateAtExport(batch({ ...base, id: "batch_fixture_0003", exportedAt: "2026-07-13T12:30:00Z" })).status, "late");
  assert.equal(evaluateAtExport(batch({ ...base, id: "batch_fixture_0004", exportedAt: "2026-07-13T12:30:01Z" })).status, "alert");
});

test("processing time rejects delayed and future replay at raw-millisecond boundaries", () => {
  const { graph, media, blocked } = fixture();
  const base = { policy: blocked, graph, media, graphCandidate: null, mediaCandidate: null };
  const replay = batch({ ...base, exportedAt: "2026-07-13T12:10:00Z" });
  const atDelayLimit = evaluateReportingFeedFreshness(replay, "2026-07-13T12:15:00Z");
  assert.equal(atDelayLimit.processingDelaySeconds, 300);
  assert.equal(atDelayLimit.status, "fresh");
  assert.throws(
    () => evaluateReportingFeedFreshness(replay, "2026-07-13T12:15:00.001Z"),
    /not processed within its replay window/,
  );

  const atFutureSkewLimit = evaluateReportingFeedFreshness(replay, "2026-07-13T12:09:00Z");
  assert.equal(atFutureSkewLimit.processingDelaySeconds, -60);
  assert.equal(atFutureSkewLimit.effectiveProcessingAt, replay.exportedAt);
  assert.throws(
    () => evaluateReportingFeedFreshness(replay, "2026-07-13T12:08:59.999Z"),
    /too far in the future/,
  );

  const staleByOneMillisecond = batch({
    ...base,
    id: "batch_fixture_stale_ms",
    exportedAt: "2026-07-13T12:29:00Z",
  });
  assert.equal(
    evaluateReportingFeedFreshness(staleByOneMillisecond, "2026-07-13T12:30:00.001Z").status,
    "alert",
  );
  assert.throws(
    () => evaluateReportingFeedFreshness(
      { ...replay, exportedAt: "2026-02-30T12:10:00Z" },
      "2026-03-02T12:10:00Z",
    ),
    /occurrence export time is invalid/,
  );
});

test("producer batch is closed, opaque for cameras, and enforces PNG MIME, paths, dimensions, and bytes", async (context) => {
  const { sourceRoot } = await workspace(context);
  const { graph, media, active } = fixture();
  const goodGraph = await candidate(sourceRoot, "graphs", graph.occurrenceId, png());
  const goodMedia = await candidate(
    sourceRoot,
    "current-media",
    media.occurrenceId,
    png(640, 360),
    "2026-07-13T12:00:00Z",
    requestProvenance(active, media.occurrenceId),
  );
  const valid = batch({ policy: active, graph, media, graphCandidate: goodGraph, mediaCandidate: goodMedia });
  await inspectOccurrenceExportCandidates({
    policy: active,
    batch: valid,
    sourceRoot,
    processingAt: processingAtFor(valid),
  });
  assert.doesNotMatch(JSON.stringify(valid), /greenhouse_1|api\.verdify|frigate|go2rtc/i);

  const wrongMime = structuredClone(valid);
  wrongMime.currentMedia[0].candidate.mediaType = "image/jpeg";
  assert.throws(
    () => validateOccurrenceExportBatch(wrongMime, active, processingAtFor(wrongMime)),
    /MIME type is not image\/png/,
  );

  const leaked = structuredClone(valid);
  leaked.currentMedia[0].sourceUrl = "https://api.verdify.ai/forbidden";
  assert.throws(
    () => validateOccurrenceExportBatch(leaked, active, processingAtFor(leaked)),
    /current-media export batch entry is invalid/,
  );

  const wrongCandidateProvenance = structuredClone(valid);
  wrongCandidateProvenance.currentMedia[0].candidate.requestProvenanceSha256 = "f".repeat(64);
  assert.throws(
    () => validateOccurrenceExportBatch(
      wrongCandidateProvenance,
      active,
      processingAtFor(wrongCandidateProvenance),
    ),
    /candidate is not bound to its active camera request/,
  );

  const wrongBatchProvenance = structuredClone(valid);
  wrongBatchProvenance.currentMedia[0].requestProvenanceSha256 = "e".repeat(64);
  assert.throws(
    () => validateOccurrenceExportBatch(wrongBatchProvenance, active, processingAtFor(wrongBatchProvenance)),
    /current-media export batch entry is invalid/,
  );

  const sameVersionPolicyMutation = structuredClone(active);
  sameVersionPolicyMutation.imagePolicy.graphs.maxWidth -= 1;
  assert.throws(
    () => validateOccurrenceExportBatch(valid, sameVersionPolicyMutation, processingAtFor(valid)),
    /not bound to the exact active policy bytes/,
  );

  const tinyCandidate = await candidate(sourceRoot, "graphs", graph.occurrenceId, png(2, 1));
  const tiny = batch({ policy: active, graph, media, graphCandidate: tinyCandidate, mediaCandidate: goodMedia });
  await assert.rejects(
    () => inspectOccurrenceExportCandidates({
      policy: active,
      batch: tiny,
      sourceRoot,
      processingAt: processingAtFor(tiny),
    }),
    /outside the active MIME, byte, or dimension bounds/,
  );

  const wrongPath = structuredClone(valid);
  wrongPath.graphs[0].candidate.relativePath = `graphs/${graph.occurrenceId}/not-content-addressed.png`;
  await assert.rejects(
    () => inspectOccurrenceExportCandidates({
      policy: active,
      batch: wrongPath,
      sourceRoot,
      processingAt: processingAtFor(wrongPath),
    }),
    /not opaque and content-addressed/,
  );

  const staleMediaCandidate = await candidate(
    sourceRoot,
    "current-media",
    media.occurrenceId,
    png(800, 450),
    "2026-07-13T11:54:59Z",
    requestProvenance(active, media.occurrenceId),
  );
  const staleMedia = batch({ policy: active, graph, media, graphCandidate: goodGraph, mediaCandidate: staleMediaCandidate });
  await assert.rejects(
    () => inspectOccurrenceExportCandidates({
      policy: active,
      batch: staleMedia,
      sourceRoot,
      processingAt: processingAtFor(staleMedia),
    }),
    /current-media candidate is stale; retain last-known-good/,
  );
});

test("prepared digests reject valid PNG replacement between inspection and publication", async (context) => {
  const { sourceRoot, storeRoot } = await workspace(context);
  const { graph, media, manifest, manifestSha256, active } = fixture();
  const graphCandidate = await candidate(sourceRoot, "graphs", graph.occurrenceId, png());
  const mediaCandidate = await candidate(
    sourceRoot,
    "current-media",
    media.occurrenceId,
    png(640, 360),
    "2026-07-13T12:00:00Z",
    requestProvenance(active, media.occurrenceId),
  );
  const exportBatch = batch({ policy: active, graph, media, graphCandidate, mediaCandidate });
  const prepared = await prepareOccurrenceExportRequests({
    policy: active,
    manifest,
    manifestSha256,
    batch: exportBatch,
    sourceRoot,
    storeRoot,
    processingAt: processingAtFor(exportBatch),
  });
  await assert.rejects(
    () => publishCurrentMediaGeneration({
      ...prepared.mediaRequests[0],
      sourceUrl: "https://api.verdify.ai/forbidden-public-leak",
    }),
    /current media generation request does not use the closed v3 shape/,
  );
  await assert.rejects(
    () => publishOccurrenceRelease({
      ...prepared.releaseRequest,
      cameraUrl: "https://api.verdify.ai/forbidden-public-leak",
    }),
    /occurrence release request does not use the closed v2 shape/,
  );
  await writeFile(path.join(sourceRoot, ...mediaCandidate.relativePath.split("/")), png(640, 360, [80, 20, 120, 255]));
  await assert.rejects(
    () => publishCurrentMediaGeneration(prepared.mediaRequests[0]),
    /changed after prepared verification/,
  );

  await writeFile(path.join(sourceRoot, ...graphCandidate.relativePath.split("/")), png(320, 180, [120, 20, 80, 255]));
  const released = await publishOccurrenceRelease(prepared.releaseRequest);
  assert.equal(released.manifest.occurrences.graphs[0].probeStatus, "decode-error");
  assert.equal(released.manifest.occurrences.graphs[0].state, "missing");
  assert.equal(released.manifest.occurrences.graphs[0].fallback, null);
});

test("prepared v3 media and v2 release JSON publish end to end through the real CLI", async (context) => {
  const { root, sourceRoot, storeRoot } = await workspace(context);
  const { graph, media, manifest, manifestSha256, active } = fixture();
  const graphCandidate = await candidate(sourceRoot, "graphs", graph.occurrenceId, png());
  const mediaCandidate = await candidate(
    sourceRoot,
    "current-media",
    media.occurrenceId,
    png(640, 360),
    "2026-07-13T12:00:00Z",
    requestProvenance(active, media.occurrenceId),
  );
  const exportBatch = batch({
    policy: active,
    graph,
    media,
    graphCandidate,
    mediaCandidate,
    id: "batch_cli_end_to_end_0001",
  });
  const prepared = await prepareOccurrenceExportRequests({
    policy: active,
    manifest,
    manifestSha256,
    batch: exportBatch,
    sourceRoot,
    storeRoot,
    processingAt: processingAtFor(exportBatch),
  });
  const requestRoot = path.join(root, "requests");
  await mkdir(requestRoot);
  async function requestFile(name, document) {
    const file = path.join(requestRoot, name);
    await writeFile(file, canonical(document));
    return file;
  }
  async function rejectsCli(command, file, pattern) {
    await assert.rejects(
      () => run(process.execPath, [MANAGE_OCCURRENCE_CLI, command, "--request", file]),
      (error) => {
        assert.match(error.stderr, pattern);
        return true;
      },
    );
  }

  for (const field of ["policySha256", "requestProvenanceSha256"]) {
    const missing = structuredClone(prepared.mediaRequests[0]);
    delete missing[field];
    await rejectsCli(
      "publish-media",
      await requestFile(`media-missing-${field}.json`, missing),
      /release request does not use the closed v3 shape/,
    );
  }
  await rejectsCli(
    "publish-media",
    await requestFile("media-extra-url.json", {
      ...prepared.mediaRequests[0],
      sourceUrl: "https://api.verdify.ai/forbidden-public-leak",
    }),
    /release request does not use the closed v3 shape/,
  );
  await rejectsCli(
    "publish-media",
    await requestFile("media-invalid-policy-digest.json", {
      ...prepared.mediaRequests[0],
      policySha256: "not-a-digest",
    }),
    /current media policy digest is invalid/,
  );
  await rejectsCli(
    "publish-media",
    await requestFile("media-wrong-request-digest.json", {
      ...prepared.mediaRequests[0],
      requestProvenanceSha256: "f".repeat(64),
    }),
    /candidate does not match its expected camera request provenance/,
  );
  const mediaFile = await requestFile("media.request.json", prepared.mediaRequests[0]);
  const mediaResult = await run(process.execPath, [
    MANAGE_OCCURRENCE_CLI,
    "publish-media",
    "--request",
    mediaFile,
  ]);
  const mediaOutput = JSON.parse(mediaResult.stdout);
  assert.equal(mediaOutput.contract, "verdify.lab-current-media-publish-result");
  assert.equal(mediaOutput.generation, 1);
  assert.doesNotMatch(mediaResult.stdout, /greenhouse_[12]|latest\.jpg|api\.verdify/i);
  const selectedMedia = await loadSelectedCurrentMediaGeneration(storeRoot, media.occurrenceId);
  assert.equal(selectedMedia.current.schemaVersion, 3);
  assert.equal(selectedMedia.current.policySha256, exportBatch.policySha256);
  assert.equal(selectedMedia.current.requestProvenanceSha256, requestProvenance(active, media.occurrenceId));
  assert.equal(selectedMedia.selection.previous, null);

  const missingReleasePolicy = structuredClone(prepared.releaseRequest);
  delete missingReleasePolicy.policySha256;
  await rejectsCli(
    "publish",
    await requestFile("release-missing-policy.json", missingReleasePolicy),
    /release request does not use the closed v2 shape/,
  );
  await rejectsCli(
    "publish",
    await requestFile("release-extra-url.json", {
      ...prepared.releaseRequest,
      sourceUrl: "https://api.verdify.ai/forbidden-public-leak",
    }),
    /release request does not use the closed v2 shape/,
  );
  await rejectsCli(
    "publish",
    await requestFile("release-invalid-policy-digest.json", {
      ...prepared.releaseRequest,
      policySha256: "not-a-digest",
    }),
    /occurrence policy digest is invalid/,
  );
  const releaseFile = await requestFile("release.request.json", prepared.releaseRequest);
  const releaseResult = await run(process.execPath, [
    MANAGE_OCCURRENCE_CLI,
    "publish",
    "--request",
    releaseFile,
  ]);
  const releaseOutput = JSON.parse(releaseResult.stdout);
  assert.equal(releaseOutput.contract, "verdify.lab-occurrence-publish-result");
  assert.equal(releaseOutput.graphCount, 1);
  assert.equal(releaseOutput.currentMediaCount, 1);
  assert.doesNotMatch(releaseResult.stdout, /greenhouse_[12]|latest\.jpg|api\.verdify/i);

  const selectedRelease = await loadSelectedOccurrenceRelease(storeRoot);
  assert.equal(selectedRelease.selection.generation, 1);
  assert.equal(selectedRelease.current.schemaVersion, 2);
  assert.equal(selectedRelease.current.policySha256, exportBatch.policySha256);
  const releasedMedia = selectedRelease.current.occurrences.currentMedia[0];
  assert.equal(releasedMedia.policySha256, exportBatch.policySha256);
  assert.equal(releasedMedia.requestProvenanceSha256, requestProvenance(active, media.occurrenceId));
  assert.equal(releasedMedia.state, "verified");
  assert.equal(
    releasedMedia.pointer.currentGenerationSha256,
    selectedMedia.selection.current.generationSha256,
  );
  assert.doesNotMatch(JSON.stringify(selectedRelease.current), /greenhouse_[12]|latest\.jpg|api\.verdify/i);
});

test("canonical requests publish every occurrence and failed refresh retains graph and camera LKG", async (context) => {
  const { sourceRoot, storeRoot } = await workspace(context);
  const { graph, media, manifest, manifestSha256, active } = fixture();
  const graphCandidate = await candidate(sourceRoot, "graphs", graph.occurrenceId, png());
  const mediaCandidate = await candidate(
    sourceRoot,
    "current-media",
    media.occurrenceId,
    png(640, 360),
    "2026-07-13T12:00:00Z",
    requestProvenance(active, media.occurrenceId),
  );
  const firstBatch = batch({ policy: active, graph, media, graphCandidate, mediaCandidate });
  const first = await prepareOccurrenceExportRequests({
    policy: active,
    manifest,
    manifestSha256,
    batch: firstBatch,
    sourceRoot,
    storeRoot,
    processingAt: processingAtFor(firstBatch),
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
    processingAt: processingAtFor(failedBatch),
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

  const rejectedReplays = [
    {
      exportBatch: batch({
        policy: active,
        graph,
        media,
        graphCandidate: null,
        mediaCandidate: null,
        id: "batch_delayed_replay",
        exportedAt: "2026-07-13T12:21:00Z",
        watermarkAt: "2026-07-13T12:20:00Z",
        releaseExpected: retained.selectionSha256,
      }),
      processingAt: "2026-07-13T12:26:00.001Z",
      message: /not processed within its replay window/,
    },
    {
      exportBatch: batch({
        policy: active,
        graph,
        media,
        graphCandidate: null,
        mediaCandidate: null,
        id: "batch_future_replay",
        exportedAt: "2026-07-13T12:30:00Z",
        watermarkAt: "2026-07-13T12:29:00Z",
        releaseExpected: retained.selectionSha256,
      }),
      processingAt: "2026-07-13T12:28:59.999Z",
      message: /too far in the future/,
    },
  ];
  for (const replay of rejectedReplays) {
    await assert.rejects(
      () => prepareOccurrenceExportRequests({
        policy: active,
        manifest,
        manifestSha256,
        batch: replay.exportBatch,
        sourceRoot,
        storeRoot,
        processingAt: replay.processingAt,
      }),
      replay.message,
    );
    const unchanged = await loadSelectedOccurrenceRelease(storeRoot);
    assert.equal(unchanged.selectionSha256, retained.selectionSha256);
    assert.equal(
      unchanged.current.occurrences.currentMedia[0].fallback.sha256,
      retained.current.occurrences.currentMedia[0].fallback.sha256,
    );
  }
});

test("same-version policy and camera URL mutation cannot retain a generation from the prior exact policy", async (context) => {
  const { sourceRoot, storeRoot } = await workspace(context);
  const { graph, media, manifest, manifestSha256, active } = fixture();
  const graphCandidate = await candidate(sourceRoot, "graphs", graph.occurrenceId, png());
  const mediaCandidate = await candidate(
    sourceRoot,
    "current-media",
    media.occurrenceId,
    png(640, 360),
    "2026-07-13T12:00:00Z",
    requestProvenance(active, media.occurrenceId),
  );
  const initialBatch = batch({ policy: active, graph, media, graphCandidate, mediaCandidate });
  const initial = await prepareOccurrenceExportRequests({
    policy: active,
    manifest,
    manifestSha256,
    batch: initialBatch,
    sourceRoot,
    storeRoot,
    processingAt: processingAtFor(initialBatch),
  });
  await publishCurrentMediaGeneration(initial.mediaRequests[0]);
  await publishOccurrenceRelease(initial.releaseRequest);
  const selected = await loadSelectedOccurrenceRelease(storeRoot);
  const selectedMedia = selected.current.occurrences.currentMedia[0];
  assert.equal(selectedMedia.state, "verified");

  const mutated = structuredClone(active);
  const cameraSource = mutated.cameraUpstream.sources.find((source) => source.occurrenceId === media.occurrenceId);
  cameraSource.url = "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080";
  cameraSource.requestProvenanceSha256 = cameraRequestProvenanceSha256(cameraSource);
  mutated.currentMedia.find((record) => record.occurrenceId === media.occurrenceId).requestProvenanceSha256 =
    cameraSource.requestProvenanceSha256;
  validateOccurrenceExportPolicy(mutated);
  assert.equal(mutated.policyVersion, active.policyVersion);
  assert.notEqual(occurrenceExportPolicySha256(mutated), occurrenceExportPolicySha256(active));
  assert.notEqual(requestProvenance(mutated, media.occurrenceId), requestProvenance(active, media.occurrenceId));

  const mutatedBatch = batch({
    policy: mutated,
    graph,
    media,
    graphCandidate: null,
    mediaCandidate: null,
    id: "batch_same_version_camera_mutation",
    exportedAt: "2026-07-13T12:20:00Z",
    watermarkAt: "2026-07-13T12:19:00Z",
    releaseExpected: selected.selectionSha256,
    mediaExpected: selectedMedia.pointer.selectionSha256,
  });
  const prepared = await prepareOccurrenceExportRequests({
    policy: mutated,
    manifest,
    manifestSha256,
    batch: mutatedBatch,
    sourceRoot,
    storeRoot,
    processingAt: processingAtFor(mutatedBatch),
  });
  assert.equal(prepared.mediaRequests.length, 0);
  assert.equal(prepared.releaseRequest.policySha256, occurrenceExportPolicySha256(mutated));
  assert.equal(
    prepared.releaseRequest.currentMedia[0].requestProvenanceSha256,
    requestProvenance(mutated, media.occurrenceId),
  );
  assert.doesNotMatch(JSON.stringify(prepared), /greenhouse_[12]|latest\.jpg|api\.verdify/i);

  const released = await publishOccurrenceRelease(prepared.releaseRequest);
  const currentMedia = released.manifest.occurrences.currentMedia[0];
  assert.equal(released.manifest.policySha256, occurrenceExportPolicySha256(mutated));
  assert.equal(currentMedia.policySha256, occurrenceExportPolicySha256(mutated));
  assert.equal(currentMedia.requestProvenanceSha256, requestProvenance(mutated, media.occurrenceId));
  assert.equal(currentMedia.state, "missing");
  assert.equal(currentMedia.fallback, null);
  assert.equal(currentMedia.pointer, null);
  assert.doesNotMatch(JSON.stringify(released.manifest), /greenhouse_[12]|latest\.jpg|api\.verdify/i);

  const refreshedMediaCandidate = await candidate(
    sourceRoot,
    "current-media",
    media.occurrenceId,
    png(640, 360, [48, 112, 64, 255]),
    "2026-07-13T12:29:00Z",
    requestProvenance(mutated, media.occurrenceId),
  );
  const refreshedBatch = batch({
    policy: mutated,
    graph,
    media,
    graphCandidate: null,
    mediaCandidate: refreshedMediaCandidate,
    id: "batch_same_version_camera_refresh",
    exportedAt: "2026-07-13T12:30:00Z",
    watermarkAt: "2026-07-13T12:29:00Z",
    releaseExpected: released.selectionSha256,
    mediaExpected: selectedMedia.pointer.selectionSha256,
  });
  const refreshed = await prepareOccurrenceExportRequests({
    policy: mutated,
    manifest,
    manifestSha256,
    batch: refreshedBatch,
    sourceRoot,
    storeRoot,
    processingAt: processingAtFor(refreshedBatch),
  });
  const refreshedGeneration = await publishCurrentMediaGeneration(refreshed.mediaRequests[0]);
  assert.equal(refreshedGeneration.selected.current.policySha256, occurrenceExportPolicySha256(mutated));
  assert.equal(
    refreshedGeneration.selected.current.requestProvenanceSha256,
    requestProvenance(mutated, media.occurrenceId),
  );
  assert.equal(
    refreshedGeneration.selected.current.sourceProvenanceSha256,
    refreshed.releaseRequest.currentMedia[0].discovered.sourceProvenanceSha256,
  );
  assert.equal(refreshedGeneration.selected.current.policyVersion, refreshed.releaseRequest.policyVersion);
  assert.equal(refreshedGeneration.selected.current.policySha256, refreshed.releaseRequest.policySha256);
  assert.equal(
    refreshedGeneration.selected.current.requestProvenanceSha256,
    refreshed.releaseRequest.currentMedia[0].requestProvenanceSha256,
  );
  assert.equal(
    refreshedGeneration.selected.selection.previous,
    null,
    "a policy/request identity change must break the rollback chain to the prior camera generation",
  );
  const refreshedRelease = await publishOccurrenceRelease(refreshed.releaseRequest);
  assert.equal(refreshedRelease.manifest.occurrences.currentMedia[0].state, "verified");
  assert.equal(
    refreshedRelease.manifest.occurrences.currentMedia[0].fallback.sha256,
    refreshedGeneration.selected.current.fallback.sha256,
  );
});

test("full 143+2 policy-rejected reconciliation publishes then retains every LKG", async (context) => {
  const { sourceRoot, storeRoot } = await workspace(context);
  const { graphs, currentMedia, manifest, manifestSha256, active } = fullFixture();
  const candidates = new Map();
  const graphPng = png();
  const mediaPng = png(640, 360);
  for (const occurrence of graphs) {
    candidates.set(occurrence.occurrenceId, await candidate(
      sourceRoot,
      "graphs",
      occurrence.occurrenceId,
      graphPng,
      "2026-07-13T12:05:00Z",
    ));
  }
  for (const occurrence of currentMedia) {
    candidates.set(occurrence.occurrenceId, await candidate(
      sourceRoot,
      "current-media",
      occurrence.occurrenceId,
      mediaPng,
      "2026-07-13T12:05:00Z",
      requestProvenance(active, occurrence.occurrenceId),
    ));
  }

  const successBatch = fullBatch({
    policy: active,
    graphs,
    currentMedia,
    candidates,
    status: "success",
    id: "batch_full_success_0001",
    exportedAt: "2026-07-13T12:10:00Z",
    watermarkAt: "2026-07-13T12:09:00Z",
  });
  const first = await prepareOccurrenceExportRequests({
    policy: active,
    manifest,
    manifestSha256,
    batch: successBatch,
    sourceRoot,
    storeRoot,
    processingAt: "2026-07-13T12:10:30Z",
  });
  assert.equal(first.mediaRequests.length, 2);
  assert.equal(first.releaseRequest.graphs.length, 143);
  assert.equal(first.releaseRequest.currentMedia.length, 2);
  assert.doesNotMatch(JSON.stringify(first), /api\.verdify|greenhouse_[12]|latest\.jpg|authorization|cookies/i);
  for (const request of first.mediaRequests) await publishCurrentMediaGeneration(request);
  await publishOccurrenceRelease(first.releaseRequest);
  const selected = await loadSelectedOccurrenceRelease(storeRoot);
  assert.equal(selected.current.occurrences.graphs.filter((item) => item.state === "verified").length, 143);
  assert.equal(selected.current.occurrences.currentMedia.filter((item) => item.state === "verified").length, 2);

  const mediaExpected = new Map(selected.current.occurrences.currentMedia.map((occurrence) => [
    occurrence.occurrenceId,
    occurrence.pointer.selectionSha256,
  ]));
  const rejectedBatch = fullBatch({
    policy: active,
    graphs,
    currentMedia,
    status: "policy-rejected",
    id: "batch_full_rejected_0002",
    exportedAt: "2026-07-13T12:20:00Z",
    watermarkAt: "2026-07-13T12:19:00Z",
    releaseExpected: selected.selectionSha256,
    mediaExpected,
  });
  const rejected = await prepareOccurrenceExportRequests({
    policy: active,
    manifest,
    manifestSha256,
    batch: rejectedBatch,
    sourceRoot,
    storeRoot,
    processingAt: "2026-07-13T12:20:30Z",
  });
  assert.equal(rejected.mediaRequests.length, 0);
  assert.equal(rejected.releaseRequest.graphs.filter((item) => item.probeStatus === "policy-rejected").length, 143);
  await publishOccurrenceRelease(rejected.releaseRequest);
  const retained = await loadSelectedOccurrenceRelease(storeRoot);
  const priorGraphs = new Map(selected.current.occurrences.graphs.map((occurrence) => [occurrence.occurrenceId, occurrence]));
  for (const occurrence of retained.current.occurrences.graphs) {
    assert.equal(occurrence.probeStatus, "policy-rejected");
    assert.equal(occurrence.state, "retained-last-known-good");
    assert.equal(occurrence.fallback.sha256, priorGraphs.get(occurrence.occurrenceId).fallback.sha256);
  }
  const priorMedia = new Map(selected.current.occurrences.currentMedia.map((occurrence) => [occurrence.occurrenceId, occurrence]));
  for (const occurrence of retained.current.occurrences.currentMedia) {
    assert.equal(occurrence.state, "verified");
    assert.equal(occurrence.fallback.sha256, priorMedia.get(occurrence.occurrenceId).fallback.sha256);
    assert.equal(occurrence.pointer.currentGenerationSha256, priorMedia.get(occurrence.occurrenceId).pointer.currentGenerationSha256);
  }
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
      processingAt: stale.exportedAt,
    }),
    /more than 30 minutes stale; retain last-known-good/,
  );
});
