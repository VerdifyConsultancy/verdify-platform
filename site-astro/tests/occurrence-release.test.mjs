import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { link, mkdir, mkdtemp, readFile, readdir, rename, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";

import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
  currentMediaGenerationPayloadSha256,
  evaluateEventFreshness,
  evaluateOccurrenceFreshness,
  loadSelectedCurrentMediaGeneration,
  loadSelectedOccurrenceRelease,
  materializeOccurrenceBlobs,
  occurrenceReleasePayloadSha256,
  publishCurrentMediaGeneration,
  publishOccurrenceRelease,
  rollbackCurrentMediaGeneration,
  rollbackOccurrenceRelease,
  staticOccurrenceManifest,
  summarizeOccurrenceFreshness,
} from "../scripts/lib/occurrence-release.mjs";
import { decodePng, limits as pngLimits, validatePngFile } from "../scripts/lib/png-validation.mjs";

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

function png(r, g, b) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(2, 0);
  header.writeUInt32BE(1, 4);
  header[8] = 8;
  header[9] = 6;
  const scanline = Buffer.from([0, r, g, b, 255, r, g, b, 255]);
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(scanline)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function event(eventId, payloadSha256, eventType = "planner-completed", occurredAt = "2026-07-12T12:00:00Z") {
  return {
    contract: "verdify.lab-release-trigger",
    schemaVersion: 1,
    eventId,
    eventType,
    sourceId: "plan-public-20260712",
    sourceWatermark: "event-watermark-0001",
    occurredAt,
    payloadSha256,
  };
}

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

function verifiedCandidate(relativePath, bytes, instant, requestProvenanceSha256 = null) {
  return {
    relativePath,
    expectedSha256: digest(bytes),
    verifiedAt: instant,
    capturedAt: instant,
    ...(requestProvenanceSha256 === null ? {} : { requestProvenanceSha256 }),
  };
}

function bindReleaseEvent(request, eventId, eventType = "planner-completed", occurredAt = "2026-07-12T12:00:00Z") {
  if (!Object.hasOwn(request, "policySha256")) request.policySha256 = digest(`policy:${request.policyVersion}`);
  request.event = event(eventId, occurrenceReleasePayloadSha256(request), eventType, occurredAt);
  return request;
}

function bindMediaEvent(request, eventId, occurredAt = "2026-07-12T12:00:00Z") {
  if (!Object.hasOwn(request, "policySha256")) request.policySha256 = digest(`policy:${request.policyVersion}`);
  if (!Object.hasOwn(request, "requestProvenanceSha256")) {
    request.requestProvenanceSha256 = request.candidate.requestProvenanceSha256;
  }
  request.event = event(eventId, currentMediaGenerationPayloadSha256(request), "current-media-updated", occurredAt);
  return request;
}

async function workspace(context) {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-occurrence-release-"));
  const source = path.join(root, "source");
  const store = path.join(root, "store");
  await import("node:fs/promises").then(({ mkdir }) => Promise.all([mkdir(source), mkdir(store)]));
  context.after(() => rm(root, { recursive: true, force: true }));
  return { root, source, store };
}

function graphInput(candidate) {
  return {
    route: "/evidence",
    ordinal: 0,
    liveUrl: "https://graphs.verdify.ai/d-solo/site-home/public?panelId=7&from=now-24h&to=now&var-zone=all",
    title: "Climate evidence",
    renderCadenceSeconds: 600,
    probeStatus: "success",
    candidate,
  };
}

function mediaInput(candidate) {
  return {
    discovered: discoverCurrentMediaOccurrence({
      route: "/greenhouse/cameras",
      ordinal: 0,
      sourceUrl: "https://api.verdify.ai/api/v1/public/cameras/cam-public-01/latest.png?h=720",
      semanticRole: "Current greenhouse view",
      captureCadenceSeconds: 300,
    }),
    requestProvenanceSha256: candidate?.requestProvenanceSha256 ?? digest("approved-camera-request"),
    captureStatus: "success",
    candidate,
  };
}

function mediaReleaseInput(candidate) {
  const { discovered, requestProvenanceSha256 } = mediaInput(candidate);
  return { discovered, requestProvenanceSha256 };
}

test("PNG validation inflates and reconstructs bounded scanlines", async (context) => {
  const { source } = await workspace(context);
  const bytes = png(20, 80, 40);
  const decoded = decodePng(bytes);
  assert.equal(decoded.width, 2);
  assert.equal(decoded.height, 1);
  assert.equal(decoded.decodedBytes, 8);
  assert.match(decoded.decodedSha256, /^[0-9a-f]{64}$/);

  await writeFile(path.join(source, "valid.png"), bytes);
  const verified = await validatePngFile(source, "valid.png");
  assert.equal(verified.sha256, digest(bytes));

  await symlink("valid.png", path.join(source, "linked.png"));
  await assert.rejects(() => validatePngFile(source, "linked.png"), /without following links/);
  await link(path.join(source, "valid.png"), path.join(source, "hardlinked.png"));
  await assert.rejects(() => validatePngFile(source, "valid.png"), /single-link/);

  const corrupt = Buffer.from(bytes);
  corrupt[corrupt.length - 5] ^= 1;
  assert.throws(() => decodePng(corrupt), /checksum|end chunk/);

  const critical = Buffer.concat([bytes.subarray(0, 33), chunk("ABCD", Buffer.alloc(0)), bytes.subarray(33)]);
  assert.throws(() => decodePng(critical), /unsupported metadata or structural chunk/);
  const metadata = Buffer.concat([bytes.subarray(0, 33), chunk("tEXt", Buffer.from("author\0private")), bytes.subarray(33)]);
  assert.throws(() => decodePng(metadata), /unsupported metadata or structural chunk/);

  const emptyImageData = Buffer.concat([
    bytes.subarray(0, 33),
    ...Array.from({ length: 32 }, () => chunk("IDAT", Buffer.alloc(0))),
    bytes.subarray(33),
  ]);
  assert.throws(() => decodePng(emptyImageData), /image data chunk is empty/);

  const stormHeader = Buffer.alloc(13);
  stormHeader.writeUInt32BE(800, 0);
  stormHeader.writeUInt32BE(1, 4);
  stormHeader[8] = 8;
  stormHeader[9] = 6;
  const noisyScanline = Buffer.alloc(1 + (800 * 4));
  for (let index = 1; index < noisyScanline.length; index += 1) noisyScanline[index] = (index * 131) & 0xff;
  const noisyCompressed = deflateSync(noisyScanline, { level: 0 });
  assert.ok(noisyCompressed.length > pngLimits.maxIdatChunks);
  const highCardinality = Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", stormHeader),
    ...[...noisyCompressed].map((byte) => chunk("IDAT", Buffer.from([byte]))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
  assert.throws(() => decodePng(highCardinality), /image-data chunk-count limit/);

  const idatLength = bytes.readUInt32BE(33);
  const idatData = bytes.subarray(41, 41 + idatLength);
  const concatenated = Buffer.concat([
    bytes.subarray(0, 33),
    chunk("IDAT", Buffer.concat([idatData, deflateSync(Buffer.from("trailing stream"))])),
    bytes.subarray(45 + idatLength),
  ]);
  assert.throws(() => decodePng(concatenated), /trailing or concatenated streams/);

  const indexedHeader = Buffer.alloc(13);
  indexedHeader.writeUInt32BE(1, 0);
  indexedHeader.writeUInt32BE(1, 4);
  indexedHeader[8] = 8;
  indexedHeader[9] = 3;
  const indexed = Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", indexedHeader),
    chunk("PLTE", Buffer.from([255, 0, 0])),
    chunk("IDAT", deflateSync(Buffer.from([0, 0]))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
  assert.throws(() => decodePng(indexed), /unsupported dimensions or encoding/);
});

test("publisher selects decoded fallbacks and emits one atomic current/previous record", async (context) => {
  const { root, source, store } = await workspace(context);
  const graphBytes = png(10, 90, 30);
  const cameraBytes = png(90, 30, 10);
  await writeFile(path.join(source, "graph.png"), graphBytes);
  await writeFile(path.join(source, "camera.png"), cameraBytes);
  const candidate = (relativePath, bytes) => ({
    ...verifiedCandidate(relativePath, bytes, "2026-07-12T12:00:00Z"),
    verifiedAt: "2026-07-12T12:00:30Z",
  });
  const firstRequest = {
    storeRoot: store,
    sourceRoot: source,
    event: null,
    sourceSnapshotManifestSha256: digest("snapshot-one"),
    policyVersion: "verdify-public-output-v1",
    publishedAt: "2026-07-12T12:01:00Z",
    graphs: [graphInput(candidate("graph.png", graphBytes))],
    currentMedia: [mediaReleaseInput(candidate("camera.png", cameraBytes))],
  };
  bindReleaseEvent(firstRequest, "evt_plan_0001");
  const first = await publishOccurrenceRelease(firstRequest);
  assert.equal(first.idempotent, false);
  assert.equal(first.manifest.freshness.status, "fresh");
  assert.equal(first.manifest.occurrences.graphs[0].state, "verified");
  assert.equal(first.manifest.occurrences.graphs[0].staleAfterSeconds, 1800);
  assert.equal(first.manifest.occurrences.currentMedia[0].staleAfterSeconds, 900);
  assert.equal(first.manifest.occurrences.currentMedia[0].state, "missing");
  assert.equal(first.manifest.occurrences.currentMedia[0].pointer, null);
  assert.match(first.manifest.occurrences.graphs[0].fallback.publicPath, /^\/evidence\/blobs\/sha256\/[0-9a-f]{64}\.png$/);

  const selected = await loadSelectedOccurrenceRelease(store);
  assert.equal(selected.selection.current.manifestSha256, first.manifestSha256);
  assert.equal(selected.selection.previous, null);
  assert.equal(selected.selection.generation, 1);

  const selectionDocument = JSON.parse(await readFile(path.join(store, "selection.json"), "utf8"));
  assert.deepEqual(Object.keys(selectionDocument), [
    "contract",
    "schemaVersion",
    "generation",
    "current",
    "previous",
    "selectedAt",
    "reason",
  ]);

  const output = path.join(root, "output");
  const copied = await materializeOccurrenceBlobs(store, selected.current, output);
  assert.equal(copied, 1);
});

test("current media generations advance and roll back through their own CAS pointer", async (context) => {
  const { source, store } = await workspace(context);
  const cameraOneBytes = png(90, 30, 10);
  const cameraTwoBytes = png(10, 30, 90);
  const corruptBytes = "not an image";
  await writeFile(path.join(source, "camera-one.png"), cameraOneBytes);
  await writeFile(path.join(source, "camera-two.png"), cameraTwoBytes);
  const bytesByPath = new Map([
    ["camera-one.png", cameraOneBytes],
    ["camera-two.png", cameraTwoBytes],
    ["corrupt.png", corruptBytes],
  ]);
  const candidate = (relativePath, instant) => verifiedCandidate(
    relativePath,
    bytesByPath.get(relativePath),
    instant,
    digest("approved-camera-request"),
  );
  const firstOccurrence = mediaInput(candidate("camera-one.png", "2026-07-12T12:00:00Z")).discovered;
  const firstRequest = {
    storeRoot: store,
    sourceRoot: source,
    event: null,
    policyVersion: "verdify-public-output-v1",
    publishedAt: "2026-07-12T12:01:00Z",
    occurrence: firstOccurrence,
    candidate: candidate("camera-one.png", "2026-07-12T12:00:00Z"),
  };
  bindMediaEvent(firstRequest, "evt_media_0001");
  const first = await publishCurrentMediaGeneration(firstRequest);
  const firstPointer = await loadSelectedCurrentMediaGeneration(store, firstOccurrence.occurrenceId);
  assert.equal(firstPointer.selectionSha256, first.selected.selectionSha256);
  assert.equal(firstPointer.selection.previous, null);
  const malformedRequest = {
    ...firstRequest,
    event: null,
    occurrence: { ...firstOccurrence, sourceProvenanceSha256: "not-a-digest" },
    candidate: candidate("camera-two.png", "2026-07-12T12:05:00Z"),
    publishedAt: "2026-07-12T12:06:00Z",
    expectedSelectionSha256: firstPointer.selectionSha256,
  };
  bindMediaEvent(malformedRequest, "evt_media_bad1", "2026-07-12T12:05:00Z");
  await assert.rejects(() => publishCurrentMediaGeneration(malformedRequest), /closed v1 shape/);
  assert.equal(
    (await loadSelectedCurrentMediaGeneration(store, firstOccurrence.occurrenceId)).selectionSha256,
    firstPointer.selectionSha256,
  );
  await writeFile(path.join(source, "corrupt.png"), corruptBytes);
  const failedCapture = {
    ...firstRequest,
    event: null,
    candidate: candidate("corrupt.png", "2026-07-12T12:04:00Z"),
    publishedAt: "2026-07-12T12:04:30Z",
    expectedSelectionSha256: firstPointer.selectionSha256,
  };
  bindMediaEvent(failedCapture, "evt_media_fail1", "2026-07-12T12:04:00Z");
  await assert.rejects(() => publishCurrentMediaGeneration(failedCapture), /candidate image validation failed/);
  assert.equal(
    (await loadSelectedCurrentMediaGeneration(store, firstOccurrence.occurrenceId)).selectionSha256,
    firstPointer.selectionSha256,
  );

  const secondRequest = {
    storeRoot: store,
    sourceRoot: source,
    event: null,
    policyVersion: "verdify-public-output-v1",
    publishedAt: "2026-07-12T12:06:00Z",
    occurrence: firstOccurrence,
    candidate: candidate("camera-two.png", "2026-07-12T12:05:00Z"),
    expectedSelectionSha256: firstPointer.selectionSha256,
  };
  bindMediaEvent(secondRequest, "evt_media_0002", "2026-07-12T12:05:00Z");
  const second = await publishCurrentMediaGeneration(secondRequest);
  assert.equal(second.selected.selection.generation, 2);
  assert.equal(second.selected.selection.previous.generationSha256, first.selected.selection.current.generationSha256);
  const rolledBack = await rollbackCurrentMediaGeneration({
    storeRoot: store,
    occurrenceId: firstOccurrence.occurrenceId,
    expectedSelectionSha256: second.selected.selectionSha256,
    rolledBackAt: "2026-07-12T12:07:00Z",
  });
  assert.equal(rolledBack.selection.generation, 3);
  assert.equal(rolledBack.current.fallback.sha256, first.selected.current.fallback.sha256);
  assert.equal(rolledBack.previous.fallback.sha256, second.selected.current.fallback.sha256);
});

test("failed graph and camera updates retain last-known-good bytes", async (context) => {
  const { source, store } = await workspace(context);
  const graphBytes = png(10, 90, 30);
  const cameraBytes = png(90, 30, 10);
  const corruptBytes = "not a decoded image";
  await writeFile(path.join(source, "graph.png"), graphBytes);
  await writeFile(path.join(source, "camera.png"), cameraBytes);
  const bytesByPath = new Map([
    ["graph.png", graphBytes],
    ["camera.png", cameraBytes],
    ["corrupt.png", corruptBytes],
  ]);
  const candidate = (relativePath, instant) => verifiedCandidate(relativePath, bytesByPath.get(relativePath), instant);
  const firstRequest = {
    storeRoot: store,
    sourceRoot: source,
    event: null,
    sourceSnapshotManifestSha256: digest("snapshot-one"),
    policyVersion: "verdify-public-output-v1",
    publishedAt: "2026-07-12T12:01:00Z",
    graphs: [graphInput(candidate("graph.png", "2026-07-12T12:00:00Z"))],
    currentMedia: [mediaReleaseInput(candidate("camera.png", "2026-07-12T12:00:00Z"))],
  };
  bindReleaseEvent(firstRequest, "evt_plan_1001");
  const first = await publishOccurrenceRelease(firstRequest);
  await writeFile(path.join(source, "corrupt.png"), corruptBytes);
  const secondGraph = graphInput(candidate("corrupt.png", "2026-07-12T12:05:00Z"));
  const firstSelection = await loadSelectedOccurrenceRelease(store);
  const secondRequest = {
    storeRoot: store,
    sourceRoot: source,
    event: null,
    sourceSnapshotManifestSha256: digest("snapshot-two"),
    policyVersion: "verdify-public-output-v1",
    publishedAt: "2026-07-12T12:06:00Z",
    graphs: [secondGraph],
    expectedSelectionSha256: firstSelection.selectionSha256,
  };
  bindReleaseEvent(secondRequest, "evt_plan_1002", "planner-completed", "2026-07-12T12:05:00Z");
  const second = await publishOccurrenceRelease(secondRequest);
  const priorGraph = first.manifest.occurrences.graphs[0];
  const currentGraph = second.manifest.occurrences.graphs[0];
  assert.equal(currentGraph.probeStatus, "decode-error");
  assert.equal(currentGraph.state, "retained-last-known-good");
  assert.deepEqual(currentGraph.fallback, priorGraph.fallback);

  const thirdRequest = {
    storeRoot: store,
    sourceRoot: source,
    event: null,
    sourceSnapshotManifestSha256: digest("snapshot-three"),
    policyVersion: "verdify-public-output-v2",
    publishedAt: "2026-07-12T12:11:00Z",
    graphs: [graphInput(candidate("corrupt.png", "2026-07-12T12:10:00Z"))],
    currentMedia: firstRequest.currentMedia,
    expectedSelectionSha256: (await loadSelectedOccurrenceRelease(store)).selectionSha256,
  };
  bindReleaseEvent(thirdRequest, "evt_plan_1003", "planner-completed", "2026-07-12T12:10:00Z");
  const third = await publishOccurrenceRelease(thirdRequest);
  assert.equal(third.manifest.policyVersion, "verdify-public-output-v2");
  assert.equal(third.manifest.occurrences.graphs[0].state, "retained-last-known-good");
  assert.equal(
    third.manifest.occurrences.graphs[0].fallback.policyVersion,
    "verdify-public-output-v1",
    "carried evidence preserves the policy that actually approved its bytes",
  );

  const selected = await loadSelectedOccurrenceRelease(store);
  assert.equal(selected.selection.current.manifestSha256, third.manifestSha256);
  assert.equal(selected.selection.previous.manifestSha256, second.manifestSha256);
  assert.equal(selected.selection.generation, 3);

});

test("event idempotency, conditional promotion, and no-build rollback are enforced", async (context) => {
  const { source, store } = await workspace(context);
  const graphBytes = png(10, 90, 30);
  await writeFile(path.join(source, "graph.png"), graphBytes);
  const candidate = verifiedCandidate("graph.png", graphBytes, "2026-07-12T12:00:00Z");
  const request = (eventId, occurredAt, publishedAt, expectedSelectionSha256 = null) => {
    const value = {
      storeRoot: store,
      sourceRoot: source,
      event: null,
      sourceSnapshotManifestSha256: digest("snapshot"),
      policyVersion: "verdify-public-output-v1",
      publishedAt,
      graphs: [graphInput(candidate)],
      expectedSelectionSha256,
    };
    bindReleaseEvent(value, eventId, "planner-completed", occurredAt);
    return value;
  };
  const firstRequest = request("evt_plan_2001", "2026-07-12T12:00:00Z", "2026-07-12T12:01:00Z");
  const firstEvent = firstRequest.event;
  const first = await publishOccurrenceRelease(firstRequest);
  const retry = await publishOccurrenceRelease(firstRequest);
  assert.equal(retry.idempotent, true);
  assert.equal(retry.manifestSha256, first.manifestSha256);
  assert.equal((await loadSelectedOccurrenceRelease(store)).selection.generation, 1);
  const unchanged = await publishOccurrenceRelease(request(
    "evt_plan_2001b",
    "2026-07-12T12:01:30Z",
    "2026-07-12T12:01:45Z",
    (await loadSelectedOccurrenceRelease(store)).selectionSha256,
  ));
  assert.equal(unchanged.unchanged, true);
  assert.equal((await loadSelectedOccurrenceRelease(store)).selection.generation, 1);

  await assert.rejects(
    () => publishOccurrenceRelease({ ...firstRequest, event: { ...firstEvent, payloadSha256: digest("collision") } }),
    /payload digest mismatch/,
  );
  const collisionRequest = request("evt_plan_2001", "2026-07-12T12:00:00Z", "2026-07-12T12:01:00Z");
  collisionRequest.graphs[0] = { ...collisionRequest.graphs[0], title: "Changed payload" };
  bindReleaseEvent(collisionRequest, "evt_plan_2001");
  await assert.rejects(() => publishOccurrenceRelease(collisionRequest), /reused with different payload/);
  await assert.rejects(
    () => publishOccurrenceRelease({
      ...firstRequest,
      event: { ...firstEvent, sourceWatermark: "different-watermark" },
    }),
    /different envelope/,
  );
  const wrongPrecondition = request("evt_plan_2002", "2026-07-12T12:02:00Z", "2026-07-12T12:02:01Z", digest("wrong-current"));
  wrongPrecondition.sourceSnapshotManifestSha256 = digest("changed-snapshot");
  bindReleaseEvent(wrongPrecondition, "evt_plan_2002", "planner-completed", "2026-07-12T12:02:00Z");
  await assert.rejects(() => publishOccurrenceRelease(wrongPrecondition), /precondition failed/);

  const secondRequest = request(
    "evt_plan_2002",
    "2026-07-12T12:02:00Z",
    "2026-07-12T12:03:00Z",
    (await loadSelectedOccurrenceRelease(store)).selectionSha256,
  );
  secondRequest.sourceSnapshotManifestSha256 = digest("snapshot-two");
  bindReleaseEvent(secondRequest, "evt_plan_2002", "planner-completed", "2026-07-12T12:02:00Z");
  const second = await publishOccurrenceRelease(secondRequest);
  const lateReplay = await publishOccurrenceRelease(firstRequest);
  assert.equal(lateReplay.idempotent, true);
  assert.equal(lateReplay.manifestSha256, first.manifestSha256);
  assert.equal((await loadSelectedOccurrenceRelease(store)).selection.current.manifestSha256, second.manifestSha256);
  const rollback = await rollbackOccurrenceRelease({
    storeRoot: store,
    expectedSelectionSha256: (await loadSelectedOccurrenceRelease(store)).selectionSha256,
    rolledBackAt: "2026-07-12T12:04:00Z",
  });
  assert.equal(rollback.selection.current.manifestSha256, first.manifestSha256);
  assert.equal(rollback.selection.previous.manifestSha256, second.manifestSha256);
  assert.equal(rollback.selection.reason, "rollback");
  assert.equal((await loadSelectedOccurrenceRelease(store)).current.event.eventId, firstEvent.eventId);
});

test("selected releases fail closed when content-addressed image bytes change", async (context) => {
  const { source, store } = await workspace(context);
  const graphBytes = png(10, 90, 30);
  await writeFile(path.join(source, "graph.png"), graphBytes);
  const request = {
    storeRoot: store,
    sourceRoot: source,
    event: null,
    sourceSnapshotManifestSha256: digest("snapshot"),
    policyVersion: "verdify-public-output-v1",
    publishedAt: "2026-07-12T12:01:00Z",
    graphs: [graphInput(verifiedCandidate("graph.png", graphBytes, "2026-07-12T12:00:00Z"))],
  };
  bindReleaseEvent(request, "evt_plan_3001");
  const result = await publishOccurrenceRelease(request);
  const fallback = result.manifest.occurrences.graphs[0].fallback;
  await writeFile(path.join(store, "blobs", "sha256", `${fallback.sha256}.png`), png(90, 10, 30));
  await assert.rejects(() => loadSelectedOccurrenceRelease(store), /digest|metadata/);
});

test("release retention keeps ten manifests and permanent event tombstones", async (context) => {
  const { source, store } = await workspace(context);
  let selectionSha256 = null;
  let firstRequest;
  for (let index = 0; index < 12; index += 1) {
    const instant = `2026-07-12T12:${String(index).padStart(2, "0")}:00Z`;
    const request = {
      storeRoot: store,
      sourceRoot: source,
      event: null,
      sourceSnapshotManifestSha256: digest(`snapshot-${index}`),
      policyVersion: "verdify-public-output-v1",
      publishedAt: `2026-07-12T12:${String(index).padStart(2, "0")}:01Z`,
      graphs: [],
      currentMedia: [],
      expectedSelectionSha256: selectionSha256,
    };
    bindReleaseEvent(request, `evt_retain_${String(index).padStart(2, "0")}`, "reconciliation", instant);
    if (index === 0) firstRequest = request;
    await publishOccurrenceRelease(request);
    selectionSha256 = (await loadSelectedOccurrenceRelease(store)).selectionSha256;
  }
  assert.equal((await readdir(path.join(store, "manifests", "sha256"))).length, 10);
  assert.equal((await readdir(path.join(store, "events", "sha256"))).length, 12);
  const replay = await publishOccurrenceRelease(firstRequest);
  assert.equal(replay.idempotent, true);
  assert.equal(replay.retained, false);
  assert.equal((await loadSelectedOccurrenceRelease(store)).selectionSha256, selectionSha256);
});

test("current-media readers reject intermediate directory symlinks", async (context) => {
  const { root, source, store } = await workspace(context);
  const cameraBytes = png(90, 30, 10);
  await writeFile(path.join(source, "camera.png"), cameraBytes);
  const occurrence = mediaInput({
    relativePath: "camera.png",
    verifiedAt: "2026-07-12T12:00:00Z",
    capturedAt: "2026-07-12T12:00:00Z",
  }).discovered;
  const request = {
    storeRoot: store,
    sourceRoot: source,
    event: null,
    policyVersion: "verdify-public-output-v1",
    publishedAt: "2026-07-12T12:01:00Z",
    occurrence,
    candidate: {
      relativePath: "camera.png",
      expectedSha256: digest(cameraBytes),
      verifiedAt: "2026-07-12T12:00:00Z",
      capturedAt: "2026-07-12T12:00:00Z",
      requestProvenanceSha256: digest("approved-camera-request"),
    },
    expectedSelectionSha256: null,
  };
  bindMediaEvent(request, "evt_media_link1");
  await publishCurrentMediaGeneration(request);
  const occurrenceDirectory = path.join(store, "occurrences", occurrence.occurrenceId);
  await rename(occurrenceDirectory, `${occurrenceDirectory}.held`);
  const outside = path.join(root, "outside");
  await mkdir(outside);
  await symlink(outside, occurrenceDirectory);
  await assert.rejects(
    () => loadSelectedCurrentMediaGeneration(store, occurrence.occurrenceId),
    /store layout is invalid/,
  );
});

test("downstream event, publication, verification, selection, and rollback instants reject impossible dates", async (context) => {
  const impossible = "2026-02-30T12:00:00Z";
  const valid = "2026-03-02T12:00:00Z";
  assert.throws(
    () => evaluateEventFreshness(event("evt_bad_date_event", digest("event"), "planner-completed", impossible), valid),
    /release event occurrence time is invalid/,
  );
  assert.throws(
    () => evaluateEventFreshness(event("evt_bad_date_publish", digest("publish")), impossible),
    /release publication time is invalid/,
  );
  assert.throws(
    () => evaluateOccurrenceFreshness({ occurrences: { graphs: [], currentMedia: [] } }, impossible),
    /occurrence freshness evaluation time is invalid/,
  );

  const { source, store } = await workspace(context);
  const graphBytes = png(10, 90, 30);
  const cameraBytes = png(90, 30, 10);
  await writeFile(path.join(source, "graph.png"), graphBytes);
  await writeFile(path.join(source, "camera.png"), cameraBytes);

  const occurrence = mediaInput(verifiedCandidate(
    "camera.png",
    cameraBytes,
    valid,
    digest("strict-camera-request"),
  )).discovered;
  const invalidVerification = {
    storeRoot: store,
    sourceRoot: source,
    event: null,
    policyVersion: "strict-policy-v1",
    publishedAt: "2026-03-02T12:01:00Z",
    occurrence,
    candidate: verifiedCandidate(
      "camera.png",
      cameraBytes,
      impossible,
      digest("strict-camera-request"),
    ),
  };
  bindMediaEvent(invalidVerification, "evt_bad_date_verify", valid);
  await assert.rejects(
    () => publishCurrentMediaGeneration(invalidVerification),
    /image verification time is invalid/,
  );

  const validMedia = {
    ...invalidVerification,
    event: null,
    candidate: verifiedCandidate("camera.png", cameraBytes, valid, digest("strict-camera-request")),
  };
  bindMediaEvent(validMedia, "evt_valid_date_media", valid);
  const publishedMedia = await publishCurrentMediaGeneration(validMedia);
  await assert.rejects(
    () => rollbackCurrentMediaGeneration({
      storeRoot: store,
      occurrenceId: occurrence.occurrenceId,
      expectedSelectionSha256: publishedMedia.selected.selectionSha256,
      rolledBackAt: impossible,
    }),
    /current media rollback time is invalid/,
  );

  const release = {
    storeRoot: store,
    sourceRoot: source,
    event: null,
    sourceSnapshotManifestSha256: digest("strict-snapshot"),
    policyVersion: "strict-policy-v1",
    publishedAt: "2026-03-02T12:01:00Z",
    graphs: [graphInput(verifiedCandidate("graph.png", graphBytes, valid))],
    currentMedia: [],
  };
  bindReleaseEvent(release, "evt_valid_date_release", "planner-completed", valid);
  await publishOccurrenceRelease(release);
  const selected = await loadSelectedOccurrenceRelease(store);
  await assert.rejects(
    () => rollbackOccurrenceRelease({
      storeRoot: store,
      expectedSelectionSha256: selected.selectionSha256,
      rolledBackAt: impossible,
    }),
    /rollback time is invalid/,
  );

  const occurrenceSelectionPath = path.join(store, "selection.json");
  const occurrenceSelection = JSON.parse(await readFile(occurrenceSelectionPath, "utf8"));
  occurrenceSelection.selectedAt = impossible;
  await writeFile(occurrenceSelectionPath, `${JSON.stringify(occurrenceSelection, null, 2)}\n`);
  await assert.rejects(
    () => loadSelectedOccurrenceRelease(store),
    /occurrence selection time is invalid/,
  );

  const mediaSelectionPath = path.join(store, "occurrences", occurrence.occurrenceId, "selection.json");
  const mediaSelection = JSON.parse(await readFile(mediaSelectionPath, "utf8"));
  mediaSelection.selectedAt = impossible;
  await writeFile(mediaSelectionPath, `${JSON.stringify(mediaSelection, null, 2)}\n`);
  await assert.rejects(
    () => loadSelectedCurrentMediaGeneration(store, occurrence.occurrenceId),
    /current media selection time is invalid/,
  );
});

test("static manifests preserve normalized graph and opaque current-media identities", () => {
  const graph = discoverGraphOccurrence({
    route: "/evidence",
    ordinal: 0,
    liveUrl: "https://graphs.verdify.ai/d-solo/site-home/public?panelId=7&var-zone=north&var-zone=south&from=now-24h&to=now",
    title: "Climate evidence",
  });
  const media = discoverCurrentMediaOccurrence({
    route: "/greenhouse/cameras",
    ordinal: 0,
    sourceUrl: "https://api.verdify.ai/api/v1/public/cameras/cam-public-01/latest.png",
  });
  assert.deepEqual(graph.variables["var-zone"], ["north", "south"]);
  assert.equal(graph.timeRange.from, "now-24h");
  assert.match(media.occurrenceId, /^media_[0-9a-f]{24}$/);
  assert.doesNotMatch(JSON.stringify(media), /cam-public-01/);
  assert.throws(
    () => discoverCurrentMediaOccurrence({
      route: "/greenhouse/cameras",
      ordinal: 0,
      sourceUrl: "https://api.verdify.ai/api/v1/public/cameras/cam-public-01/latest.png?token=forbidden",
    }),
    /query is invalid/,
  );
  assert.throws(
    () => discoverGraphOccurrence({
      route: "/evidence",
      ordinal: 0,
      liveUrl: "https://graphs.verdify.ai/d-solo/site-home/public?panelId=7&api_key=forbidden",
    }),
    /credential-like query key/,
  );

  const manifest = staticOccurrenceManifest({
    snapshotId: `snapshot-sha256:${digest("snapshot")}`,
    discoveredGraphs: [graph],
    discoveredCurrentMedia: [media],
  });
  assert.equal(manifest.graphs[0].selected, null);
  assert.equal(manifest.currentMedia[0].stableTarget, `/evidence/current/${media.occurrenceId}`);
});

test("planner freshness contract distinguishes target, late, and alert thresholds", () => {
  const releaseEvent = event("evt_plan_4001", digest("payload"));
  assert.equal(evaluateEventFreshness(releaseEvent, "2026-07-12T12:04:59Z").status, "fresh");
  assert.equal(evaluateEventFreshness(releaseEvent, "2026-07-12T12:05:01Z").status, "late");
  assert.equal(evaluateEventFreshness(releaseEvent, "2026-07-12T12:15:00Z").status, "alert");
  assert.throws(() => evaluateEventFreshness(releaseEvent, "2026-07-12T11:59:59Z"), /precedes its event/);
});

test("graph and current-media freshness use their independent last-verified clocks", () => {
  const fallback = {
    verifiedAt: "2026-07-12T12:00:00Z",
    capturedAt: "2026-07-12T12:10:00Z",
  };
  const result = evaluateOccurrenceFreshness({
    occurrences: {
      graphs: [{ occurrenceId: `graph_${"1".repeat(24)}`, staleAfterSeconds: 1800, fallback }],
      currentMedia: [{ occurrenceId: `media_${"2".repeat(24)}`, staleAfterSeconds: 900, fallback }],
    },
  }, "2026-07-12T12:20:00Z");
  assert.equal(result.graphs[0].status, "fresh");
  assert.equal(result.currentMedia[0].status, "fresh");
  const stale = evaluateOccurrenceFreshness({
    occurrences: {
      graphs: [{ occurrenceId: `graph_${"1".repeat(24)}`, staleAfterSeconds: 1800, fallback }],
      currentMedia: [{ occurrenceId: `media_${"2".repeat(24)}`, staleAfterSeconds: 500, fallback }],
    },
  }, "2026-07-12T12:31:00Z");
  assert.equal(stale.graphs[0].status, "alert");
  assert.equal(stale.currentMedia[0].status, "alert");
  assert.deepEqual(summarizeOccurrenceFreshness({
    occurrences: {
      graphs: [{ occurrenceId: `graph_${"1".repeat(24)}`, staleAfterSeconds: 1800, fallback }],
      currentMedia: [{ occurrenceId: `media_${"2".repeat(24)}`, staleAfterSeconds: 500, fallback: null }],
    },
  }, "2026-07-12T12:31:00Z"), {
    evaluatedAt: "2026-07-12T12:31:00Z",
    status: "alert",
    graphs: { total: 1, fresh: 0, alert: 1, missing: 0 },
    currentMedia: { total: 1, fresh: 0, alert: 0, missing: 1 },
  });
  assert.throws(
    () => evaluateOccurrenceFreshness({
      occurrences: {
        graphs: [{ occurrenceId: `graph_${"1".repeat(24)}`, staleAfterSeconds: 1800, fallback }],
        currentMedia: [],
      },
    }, "2026-07-12T11:59:59Z"),
    /precedes its evidence/,
  );
});
