import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";

import {
  draftBlockedOccurrenceExportPolicy,
  occurrenceExportPolicySha256,
  reportingFeedEnvelopeSha256,
} from "../scripts/lib/occurrence-export-contract.mjs";
import {
  executeOccurrenceExportBatch,
  occurrenceExportCallerContract,
} from "../scripts/lib/occurrence-export-caller.mjs";
import { LocalOccurrenceReleaseStore } from "../scripts/lib/occurrence-release-store.mjs";
import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
  evaluateEventFreshness,
  loadSelectedOccurrenceRelease,
  staticOccurrenceManifest,
} from "../scripts/lib/occurrence-release.mjs";
import { validatePngFile } from "../scripts/lib/png-validation.mjs";

const REVIEWED_AT = "2026-07-13T11:59:00Z";
const APPROVED_AT = "2026-07-13T12:00:00Z";
const EXPORTED_AT = "2026-07-13T12:10:00Z";
const PROCESSING_AT = "2026-07-13T12:10:30Z";

const CRC_TABLE = Array.from({ length: 256 }, (_, value) => {
  let crc = value;
  for (let bit = 0; bit < 8; bit += 1) {
    crc = (crc & 1) === 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  }
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
  for (let column = 0; column < width; column += 1) {
    Buffer.from(rgba).copy(row, 1 + column * 4);
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.concat(Array.from({ length: height }, () => row)))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalValue(document, storeIdentitySha256) {
  const bytes = canonicalBytes(document);
  return {
    document,
    bytes,
    sha256: sha256(bytes),
    etag: null,
    storeIdentitySha256,
  };
}

function releaseEvent({ eventId, eventType, payloadSha256, sourceWatermark, occurredAt }) {
  return {
    contract: "verdify.lab-release-trigger",
    schemaVersion: 1,
    eventId,
    eventType,
    sourceId: "operator-public-reporting-feed-caller-offline",
    sourceWatermark,
    occurredAt,
    payloadSha256,
  };
}

async function fixture(context) {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-occurrence-caller-"));
  const sourceRoot = path.join(root, "source");
  const storeRoot = path.join(root, "store");
  await Promise.all([mkdir(sourceRoot), mkdir(storeRoot)]);
  context.after(() => rm(root, { recursive: true, force: true }));

  const graphs = Array.from({ length: occurrenceExportCallerContract.expectedGraphCount }, (_, index) => discoverGraphOccurrence({
    route: `/evidence/caller-graph-${String(index).padStart(3, "0")}`,
    ordinal: index,
    liveUrl: `https://graphs.verdify.ai/d-solo/public-reporting/caller?orgId=1&panelId=${index + 1}&from=now-24h&to=now`,
    title: `Caller graph ${index + 1}`,
  }));
  const cameraUrls = [
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080",
  ];
  const currentMedia = cameraUrls.map((sourceUrl, index) => discoverCurrentMediaOccurrence({
    route: `/evidence/caller-camera-${index + 1}`,
    ordinal: index,
    sourceUrl,
    semanticRole: `Caller camera ${index + 1}`,
  }));
  const sourceSnapshotManifestSha256 = sha256(Buffer.from("caller-snapshot"));
  const manifest = staticOccurrenceManifest({
    snapshotId: `sanitized-content-sha256:${sourceSnapshotManifestSha256}`,
    discoveredGraphs: graphs,
    discoveredCurrentMedia: currentMedia,
  });
  const manifestSha256 = sha256(canonicalBytes(manifest));
  const blocked = draftBlockedOccurrenceExportPolicy({
    manifest,
    manifestSha256,
    policyVersion: "caller-offline-policy-v1",
    approvedAt: REVIEWED_AT,
    cameraSources: currentMedia.map((occurrence, index) => ({
      occurrenceId: occurrence.occurrenceId,
      url: cameraUrls[index],
    })),
  });
  const policy = structuredClone(blocked);
  policy.activation = {
    ...policy.activation,
    state: "approved",
    approvedBy: "jason",
    approvedAt: APPROVED_AT,
  };
  const policySha256 = occurrenceExportPolicySha256(policy);
  const image = png();
  const imageSha256 = sha256(image);

  async function candidate(kind, occurrenceId, requestProvenanceSha256 = null) {
    const relativePath = `${kind}/${occurrenceId}/${imageSha256}.png`;
    await mkdir(path.join(sourceRoot, kind, occurrenceId), { recursive: true });
    await writeFile(path.join(sourceRoot, ...relativePath.split("/")), image);
    return {
      relativePath,
      mediaType: "image/png",
      capturedAt: "2026-07-13T12:05:00Z",
      ...(requestProvenanceSha256 === null ? {} : { requestProvenanceSha256 }),
    };
  }

  const graphRecords = [];
  for (const occurrence of graphs) {
    graphRecords.push({
      occurrenceId: occurrence.occurrenceId,
      probeStatus: "success",
      candidate: await candidate("graphs", occurrence.occurrenceId),
    });
  }
  const approvedMedia = new Map(policy.currentMedia.map((record) => [record.occurrenceId, record]));
  const mediaRecords = [];
  for (const occurrence of currentMedia) {
    const requestProvenanceSha256 = approvedMedia.get(occurrence.occurrenceId).requestProvenanceSha256;
    mediaRecords.push({
      occurrenceId: occurrence.occurrenceId,
      captureStatus: "success",
      requestProvenanceSha256,
      candidate: await candidate("current-media", occurrence.occurrenceId, requestProvenanceSha256),
      expectedSelectionSha256: null,
    });
  }
  const batch = {
    contract: "verdify.lab-occurrence-export-batch",
    schemaVersion: 2,
    batchId: "batch_caller_offline_0001",
    policyVersion: policy.policyVersion,
    policySha256,
    sourceOccurrenceManifestSha256: manifestSha256,
    reportingFeed: {
      contract: "verdify.operator-public-reporting-feed",
      schemaVersion: 1,
      sourceId: "operator-public-reporting-feed-caller-offline",
      sourceClass: "public-reporting-projection",
      credentialClass: "reporting-read-only",
      direction: "one-way-read-only",
      sourceWatermark: "wm_caller_offline_0001",
      sourceWatermarkAt: APPROVED_AT,
    },
    exportedAt: EXPORTED_AT,
    expectedSelectionSha256: null,
    graphs: graphRecords,
    currentMedia: mediaRecords,
  };
  const graphResult = graphResultFor(batch);
  return {
    root,
    sourceRoot,
    storeRoot,
    graphs,
    currentMedia,
    manifest,
    manifestSha256,
    policy,
    batch,
    graphResult,
  };
}

function graphResultFor(batch) {
  return {
    contract: "verdify.lab-graph-export-result",
    schemaVersion: 3,
    policyVersion: batch.policyVersion,
    policySha256: batch.policySha256,
    sourceOccurrenceManifestSha256: batch.sourceOccurrenceManifestSha256,
    reportingFeedSha256: reportingFeedEnvelopeSha256(batch.reportingFeed),
    rendererContract: {
      contract: "verdify.lab-graph-renderer-runtime-status",
      schemaVersion: 1,
      status: "satisfied",
      failure: null,
    },
    graphs: structuredClone(batch.graphs),
  };
}

async function fakeOperations({
  storeRoot,
  sourceRoot,
  mediaCasFailure = new Set(),
  crashReadAfterPublish = null,
  raceOnSecondRead = null,
  competingAggregateWriter = false,
  aggregateCommitThenError = false,
} = {}) {
  const store = await new LocalOccurrenceReleaseStore(storeRoot).initialize({ create: true });
  const calls = [];
  const mediaReadCounts = new Map();
  let crashReadArmed = null;

  async function selectedMedia(occurrenceId) {
    return store.readCurrentMediaSelection(occurrenceId);
  }

  async function publishCurrentMedia(request) {
    calls.push({ operation: "publish-current-media", occurrenceId: request.occurrence.occurrenceId });
    const occurrenceId = request.occurrence.occurrenceId;
    const existingIntent = await store.readCurrentMediaEventIntent(occurrenceId, request.event.eventId);
    if (existingIntent !== null) {
      const intent = existingIntent.document;
      if (
        intent.payloadSha256 !== request.event.payloadSha256
        || intent.policySha256 !== request.policySha256
        || intent.requestProvenanceSha256 !== request.requestProvenanceSha256
      ) throw new Error("event identity changed");
      const selected = await selectedMedia(occurrenceId);
      if (selected?.document.current.generationSha256 === intent.generationSha256) return;
      if ((selected?.sha256 ?? null) !== intent.expectedSelectionSha256) throw new Error("camera CAS changed");
      const generation = await store.readCurrentMediaGeneration(occurrenceId, intent.generationSha256);
      const next = {
        contract: "verdify.lab-current-media-selection",
        schemaVersion: 1,
        occurrenceId,
        generation: (selected?.document.generation ?? 0) + 1,
        current: {
          generationSha256: intent.generationSha256,
          blobSha256: intent.blobSha256,
        },
        previous: selected?.document.current ?? null,
        selectedAt: generation.document.publishedAt,
        reason: "publish",
      };
      await store.writeCurrentMediaSelection(occurrenceId, next, selected?.sha256 ?? null);
      return;
    }

    const selected = await selectedMedia(occurrenceId);
    if ((selected?.sha256 ?? null) !== request.expectedSelectionSha256) {
      throw new Error("camera CAS changed");
    }
    const verified = await validatePngFile(sourceRoot, request.candidate.relativePath);
    const bytes = await readFile(verified.sourcePath);
    const blob = await store.publishPngBlob(bytes, request.candidate.expectedSha256);
    const fallback = {
      publicPath: `/evidence/blobs/sha256/${blob.sha256}.png`,
      sha256: blob.sha256,
      decodedSha256: blob.decodedSha256,
      decodedBytes: blob.decodedBytes,
      bytes: blob.bytes,
      mediaType: blob.mediaType,
      width: blob.width,
      height: blob.height,
      capturedAt: request.candidate.capturedAt,
      verifiedAt: request.candidate.verifiedAt,
      policyVersion: request.policyVersion,
    };
    const generation = {
      contract: "verdify.lab-current-media-generation",
      schemaVersion: 3,
      occurrenceId,
      sourceProvenanceSha256: request.occurrence.sourceProvenanceSha256,
      policySha256: request.policySha256,
      requestProvenanceSha256: request.requestProvenanceSha256,
      event: request.event,
      policyVersion: request.policyVersion,
      publishedAt: request.publishedAt,
      fallback,
    };
    const generationSha256 = await store.publishCurrentMediaGeneration(occurrenceId, generation);
    await store.publishCurrentMediaEventIntent(occurrenceId, request.event.eventId, {
      contract: "verdify.lab-current-media-export-intent",
      schemaVersion: 1,
      eventId: request.event.eventId,
      storeIdentitySha256: store.identity.sha256,
      eventSha256: sha256(canonicalBytes(request.event)),
      payloadSha256: request.event.payloadSha256,
      policySha256: request.policySha256,
      requestProvenanceSha256: request.requestProvenanceSha256,
      occurrenceId,
      generationSha256,
      blobSha256: fallback.sha256,
      expectedSelectionSha256: request.expectedSelectionSha256,
    });
    if (mediaCasFailure.has(occurrenceId)) throw new Error("camera CAS did not commit");
    const next = {
      contract: "verdify.lab-current-media-selection",
      schemaVersion: 1,
      occurrenceId,
      generation: (selected?.document.generation ?? 0) + 1,
      current: { generationSha256, blobSha256: fallback.sha256 },
      previous: selected?.document.current ?? null,
      selectedAt: request.publishedAt,
      reason: "publish",
    };
    await store.writeCurrentMediaSelection(occurrenceId, next, selected?.sha256 ?? null);
    if (crashReadAfterPublish === occurrenceId) crashReadArmed = occurrenceId;
  }

  async function readCurrentMediaSelection(occurrenceId) {
    calls.push({ operation: "read-current-media-selection", occurrenceId });
    if (crashReadArmed === occurrenceId) {
      crashReadArmed = null;
      throw new Error("simulated process interruption");
    }
    const count = (mediaReadCounts.get(occurrenceId) ?? 0) + 1;
    mediaReadCounts.set(occurrenceId, count);
    if (raceOnSecondRead === occurrenceId && count === 2) {
      const selected = await store.readCurrentMediaSelection(occurrenceId);
      const competing = {
        contract: "verdify.lab-current-media-selection",
        schemaVersion: 1,
        occurrenceId,
        generation: selected.document.generation + 1,
        current: {
          generationSha256: "d".repeat(64),
          blobSha256: "e".repeat(64),
        },
        previous: selected.document.current,
        selectedAt: "2026-07-13T12:10:31Z",
        reason: "publish",
      };
      await store.writeCurrentMediaSelection(occurrenceId, competing, selected.sha256);
    }
    return store.readCurrentMediaSelection(occurrenceId);
  }

  async function graphRecord(input, prior) {
    if (input.candidate === undefined) {
      if (prior !== undefined) return prior;
      const { probeStatus, ...discovered } = input;
      return {
        ...discovered,
        staleAfterSeconds: Math.max(input.renderCadenceSeconds * 2, 1800),
        probeStatus,
        state: "missing",
        fallback: null,
      };
    }
    const { candidate, probeStatus, ...discovered } = input;
    const verified = await validatePngFile(sourceRoot, candidate.relativePath);
    const bytes = await readFile(verified.sourcePath);
    const blob = await store.publishPngBlob(bytes, candidate.expectedSha256);
    return {
      ...discovered,
      staleAfterSeconds: Math.max(input.renderCadenceSeconds * 2, 1800),
      probeStatus,
      state: "verified",
      fallback: {
        publicPath: `/evidence/blobs/sha256/${blob.sha256}.png`,
        sha256: blob.sha256,
        decodedSha256: blob.decodedSha256,
        decodedBytes: blob.decodedBytes,
        bytes: blob.bytes,
        mediaType: blob.mediaType,
        width: blob.width,
        height: blob.height,
        capturedAt: candidate.capturedAt,
        verifiedAt: candidate.verifiedAt,
        policyVersion: null,
      },
    };
  }

  async function publishAggregateReconciliation(command) {
    calls.push({ operation: "publish-aggregate-reconciliation" });
    const selected = await store.readAggregateSelection();
    const priorManifest = selected === null
      ? null
      : (await store.readAggregateManifest(selected.document.current.manifestSha256)).document;
    const priorGraphs = new Map(
      (priorManifest?.occurrences.graphs ?? []).map((record) => [record.occurrenceId, record]),
    );
    const graphs = [];
    for (const input of command.release.graphs) {
      const record = await graphRecord(input, priorGraphs.get(input.occurrenceId));
      if (record.fallback !== null) record.fallback.policyVersion = command.release.policyVersion;
      graphs.push(record);
    }
    const mediaById = new Map(command.release.currentMedia.map((entry) => [entry.discovered.occurrenceId, entry]));
    const currentMedia = [];
    for (const binding of command.reconciliation.cameraBindings) {
      const entry = mediaById.get(binding.occurrenceId);
      const generation = (await store.readCurrentMediaGeneration(
        binding.occurrenceId,
        binding.generationSha256,
      )).document;
      const selectedMedia = await store.readCurrentMediaSelection(binding.occurrenceId);
      currentMedia.push({
        ...entry.discovered,
        policySha256: binding.policySha256,
        requestProvenanceSha256: binding.requestProvenanceSha256,
        staleAfterSeconds: Math.max(entry.discovered.captureCadenceSeconds * 2, 900),
        captureStatus: "selected-generation",
        state: "verified",
        fallback: generation.fallback,
        pointer: {
          selectionSha256: binding.selectionSha256,
          generation: selectedMedia.document.generation,
          currentGenerationSha256: binding.generationSha256,
          previousGenerationSha256: selectedMedia.document.previous?.generationSha256 ?? null,
        },
      });
    }
    const manifest = {
      contract: "verdify.lab-specialist-occurrence-release",
      schemaVersion: 2,
      event: command.event,
      policyVersion: command.release.policyVersion,
      policySha256: command.release.policySha256,
      sourceSnapshotManifestSha256: command.release.sourceSnapshotManifestSha256,
      publishedAt: command.release.publishedAt,
      freshness: evaluateEventFreshness(command.event, command.release.publishedAt),
      occurrences: { graphs, currentMedia },
    };
    const manifestSha256 = await store.publishAggregateManifest(manifest);
    await store.publishAggregateEventIntent(command.event.eventId, {
      contract: "verdify.lab-exact-reconciliation-intent",
      schemaVersion: 1,
      eventId: command.event.eventId,
      storeIdentitySha256: store.identity.sha256,
      eventSha256: sha256(canonicalBytes(command.event)),
      payloadSha256: command.event.payloadSha256,
      reconciliationSha256: command.reconciliationSha256,
      manifestSha256,
      expectedSelectionSha256: command.expectedSelectionSha256,
      cameraSelections: command.reconciliation.cameraBindings.map(({ occurrenceId, selectionSha256 }) => ({
        occurrenceId,
        selectionSha256,
      })),
    });
  }

  async function compareAndSwapAggregateSelection(command) {
    calls.push({ operation: "compare-and-swap-aggregate" });
    for (const precondition of command.cameraSelectionPreconditions) {
      const selected = await store.readCurrentMediaSelection(precondition.occurrenceId);
      if (selected?.sha256 !== precondition.selectionSha256) {
        throw new Error("camera selection precondition failed");
      }
    }
    if (competingAggregateWriter) {
      const event = releaseEvent({
        eventId: "evt_competing_aggregate_0001",
        eventType: "reconciliation",
        payloadSha256: "f".repeat(64),
        sourceWatermark: "wm_competing_aggregate_0001",
        occurredAt: APPROVED_AT,
      });
      const manifest = {
        contract: "verdify.lab-specialist-occurrence-release",
        schemaVersion: 2,
        event,
        policyVersion: "competing-policy",
        policySha256: "c".repeat(64),
        sourceSnapshotManifestSha256: "d".repeat(64),
        publishedAt: EXPORTED_AT,
        freshness: evaluateEventFreshness(event, EXPORTED_AT),
        occurrences: { graphs: [], currentMedia: [] },
      };
      const manifestSha256 = await store.publishAggregateManifest(manifest);
      const current = await store.readAggregateSelection();
      await store.writeAggregateSelection({
        contract: "verdify.lab-occurrence-selection",
        schemaVersion: 1,
        generation: (current?.document.generation ?? 0) + 1,
        current: { manifestSha256, eventId: event.eventId },
        previous: current?.document.current ?? null,
        selectedAt: EXPORTED_AT,
        reason: "publish",
      }, current?.sha256 ?? null);
      throw new Error("aggregate precondition lost");
    }
    await store.writeAggregateSelection(
      command.selection,
      command.expectedSelectionSha256,
    );
    if (aggregateCommitThenError) throw new Error("response unavailable after aggregate commit");
  }

  const operations = {
    contract: "verdify.lab-occurrence-export-store-operations",
    schemaVersion: 1,
    storeIdentitySha256: store.identity.sha256,
    publishCurrentMedia,
    readCurrentMediaSelection,
    readCurrentMediaGeneration: (occurrenceId, digest) => store.readCurrentMediaGeneration(occurrenceId, digest),
    readCurrentMediaEventIntent: (occurrenceId, eventId) => store.readCurrentMediaEventIntent(occurrenceId, eventId),
    readPngBlob: (digest) => store.readPngBlob(digest),
    publishAggregateReconciliation,
    readAggregateSelection: () => store.readAggregateSelection(),
    readAggregateManifest: (digest) => store.readAggregateManifest(digest),
    readAggregateEventIntent: (eventId) => store.readAggregateEventIntent(eventId),
    compareAndSwapAggregateSelection,
  };

  async function publishSideGeneration({ occurrence, policy, requestProvenanceSha256 }) {
    const selected = await store.readCurrentMediaSelection(occurrence.occurrenceId);
    const priorGeneration = (await store.readCurrentMediaGeneration(
      occurrence.occurrenceId,
      selected.document.current.generationSha256,
    )).document;
    const event = releaseEvent({
      eventId: `evt_side_${occurrence.occurrenceId.slice("media_".length)}`,
      eventType: "current-media-updated",
      payloadSha256: "9".repeat(64),
      sourceWatermark: "wm_side_channel_generation_0001",
      occurredAt: "2026-07-13T12:10:31Z",
    });
    const generation = {
      ...priorGeneration,
      event,
      publishedAt: "2026-07-13T12:10:31Z",
      policySha256: occurrenceExportPolicySha256(policy),
      requestProvenanceSha256,
    };
    const generationSha256 = await store.publishCurrentMediaGeneration(occurrence.occurrenceId, generation);
    await store.publishCurrentMediaEventIntent(occurrence.occurrenceId, event.eventId, {
      contract: "verdify.lab-current-media-export-intent",
      schemaVersion: 1,
      eventId: event.eventId,
      storeIdentitySha256: store.identity.sha256,
      eventSha256: sha256(canonicalBytes(event)),
      payloadSha256: event.payloadSha256,
      policySha256: generation.policySha256,
      requestProvenanceSha256,
      occurrenceId: occurrence.occurrenceId,
      generationSha256,
      blobSha256: generation.fallback.sha256,
      expectedSelectionSha256: selected.sha256,
    });
    await store.writeCurrentMediaSelection(occurrence.occurrenceId, {
      contract: "verdify.lab-current-media-selection",
      schemaVersion: 1,
      occurrenceId: occurrence.occurrenceId,
      generation: selected.document.generation + 1,
      current: { generationSha256, blobSha256: generation.fallback.sha256 },
      previous: selected.document.current,
      selectedAt: generation.publishedAt,
      reason: "publish",
    }, selected.sha256);
  }

  return { operations, store, calls, publishSideGeneration };
}

function inputFrom(value, operations) {
  return {
    policy: value.policy,
    manifest: value.manifest,
    manifestSha256: value.manifestSha256,
    batch: value.batch,
    graphResult: value.graphResult,
    sourceRoot: value.sourceRoot,
    processingAt: PROCESSING_AT,
    operations,
  };
}

test("full 143+2 injected pass selects one exact URL-free aggregate", async (context) => {
  const value = await fixture(context);
  const fake = await fakeOperations(value);
  const result = await executeOccurrenceExportBatch(inputFrom(value, fake.operations));
  assert.equal(result.status, "selected");
  assert.equal(result.media.length, 2);
  assert.deepEqual(result.media.map(({ occurrenceId }) => occurrenceId), value.currentMedia.map(({ occurrenceId }) => occurrenceId));
  assert.ok(result.media.every(({ status }) => status === "selected"));
  assert.equal(result.aggregate.status, "selected");
  assert.equal((await fake.store.readAggregateSelection()).sha256, result.aggregate.selectionSha256);
  const selected = await loadSelectedOccurrenceRelease(fake.store);
  assert.equal(selected.current.occurrences.graphs.length, 143);
  assert.equal(selected.current.occurrences.currentMedia.length, 2);
  assert.equal(fake.calls.filter(({ operation }) => operation === "publish-current-media").length, 2);
  assert.equal(fake.calls.filter(({ operation }) => operation === "publish-aggregate-reconciliation").length, 1);
  assert.doesNotMatch(
    JSON.stringify(result),
    /https?:|endpoint|credential|authorization|cookie|sourceUrl|relativePath|sourceRoot/i,
  );
});

test("crash after camera A leaves aggregate LKG and the same batch retries A then B", async (context) => {
  const value = await fixture(context);
  const cameraA = value.currentMedia[0].occurrenceId;
  const cameraB = value.currentMedia[1].occurrenceId;
  const fake = await fakeOperations({ ...value, crashReadAfterPublish: cameraA });
  const first = await executeOccurrenceExportBatch(inputFrom(value, fake.operations));
  assert.equal(first.status, "failed");
  assert.equal(first.failure.stage, "camera-publish");
  assert.equal(first.failure.occurrenceId, cameraA);
  assert.equal(await fake.store.readAggregateSelection(), null);
  assert.equal(fake.calls.filter(({ operation, occurrenceId }) => operation === "publish-current-media" && occurrenceId === cameraB).length, 0);

  const retried = await executeOccurrenceExportBatch(inputFrom(value, fake.operations));
  assert.equal(retried.status, "selected");
  assert.deepEqual(
    fake.calls.filter(({ operation }) => operation === "publish-current-media").map(({ occurrenceId }) => occurrenceId),
    [cameraA, cameraA, cameraB],
  );
});

test("camera B CAS failure cannot publish camera A's partial generation", async (context) => {
  const value = await fixture(context);
  const cameraB = value.currentMedia[1].occurrenceId;
  const fake = await fakeOperations({ ...value, mediaCasFailure: new Set([cameraB]) });
  const result = await executeOccurrenceExportBatch(inputFrom(value, fake.operations));
  assert.equal(result.status, "failed");
  assert.equal(result.failure.stage, "camera-publish");
  assert.equal(result.failure.occurrenceId, cameraB);
  assert.equal(await fake.store.readAggregateSelection(), null);
  assert.equal(fake.calls.filter(({ operation }) => operation === "publish-aggregate-reconciliation").length, 0);
});

test("a camera selector race on the mandatory second read leaves aggregate LKG", async (context) => {
  const value = await fixture(context);
  const cameraA = value.currentMedia[0].occurrenceId;
  const fake = await fakeOperations({ ...value, raceOnSecondRead: cameraA });
  const result = await executeOccurrenceExportBatch(inputFrom(value, fake.operations));
  assert.equal(result.status, "failed");
  assert.deepEqual(result.failure, {
    stage: "camera-reread",
    occurrenceId: cameraA,
    code: "selection-changed",
  });
  assert.equal(await fake.store.readAggregateSelection(), null);
  assert.equal(fake.calls.filter(({ operation }) => operation === "publish-aggregate-reconciliation").length, 0);
});

test("a competing aggregate writer is reported as published-but-superseded", async (context) => {
  const value = await fixture(context);
  const fake = await fakeOperations({ ...value, competingAggregateWriter: true });
  const result = await executeOccurrenceExportBatch(inputFrom(value, fake.operations));
  assert.equal(result.status, "published-but-superseded");
  assert.equal(result.aggregate.status, "published-but-superseded");
  assert.equal(result.failure.stage, "aggregate-cas");
  assert.notEqual(
    (await fake.store.readAggregateSelection()).document.current.manifestSha256,
    result.aggregate.manifestSha256,
  );
});

test("an uncertain aggregate CAS response recovers only from the exact post-read", async (context) => {
  const value = await fixture(context);
  const fake = await fakeOperations({ ...value, aggregateCommitThenError: true });
  const result = await executeOccurrenceExportBatch(inputFrom(value, fake.operations));
  assert.equal(result.status, "selected");
  assert.equal(
    (await fake.store.readAggregateSelection()).document.current.manifestSha256,
    result.aggregate.manifestSha256,
  );
});

test("stale reporting feed returns failure before any injected store write", async (context) => {
  const value = await fixture(context);
  const fake = await fakeOperations(value);
  const stale = structuredClone(value.batch);
  stale.batchId = "batch_caller_stale_0002";
  stale.reportingFeed.sourceWatermark = "wm_caller_stale_0002";
  stale.reportingFeed.sourceWatermarkAt = "2026-07-13T11:30:00Z";
  stale.exportedAt = "2026-07-13T12:01:00Z";
  const result = await executeOccurrenceExportBatch({
    ...inputFrom(value, fake.operations),
    batch: stale,
    graphResult: graphResultFor(stale),
    processingAt: "2026-07-13T12:01:30Z",
  });
  assert.equal(result.status, "failed");
  assert.deepEqual(result.failure, {
    stage: "validation",
    occurrenceId: null,
    code: "reporting-feed-stale",
  });
  assert.equal(fake.calls.length, 0);
  assert.equal(await fake.store.readAggregateSelection(), null);
});

test("graph result feed drift is rejected before any injected store operation", async (context) => {
  const value = await fixture(context);
  const fake = await fakeOperations(value);
  const drifted = structuredClone(value.graphResult);
  drifted.reportingFeedSha256 = "f".repeat(64);
  await assert.rejects(
    executeOccurrenceExportBatch({
      ...inputFrom(value, fake.operations),
      graphResult: drifted,
    }),
    /exact feed-bound batch result/,
  );
  assert.equal(fake.calls.length, 0);
});

test("failed captures never adopt a newer side selector outside the selected aggregate", async (context) => {
  const value = await fixture(context);
  const fake = await fakeOperations(value);
  const first = await executeOccurrenceExportBatch(inputFrom(value, fake.operations));
  assert.equal(first.status, "selected");
  const aggregateBefore = await fake.store.readAggregateSelection();
  const cameraA = value.currentMedia[0];
  await fake.publishSideGeneration({
    occurrence: cameraA,
    policy: value.policy,
    requestProvenanceSha256: value.batch.currentMedia[0].requestProvenanceSha256,
  });

  const failedCaptures = structuredClone(value.batch);
  failedCaptures.batchId = "batch_caller_failed_captures_0002";
  failedCaptures.reportingFeed.sourceWatermark = "wm_caller_failed_captures_0002";
  failedCaptures.expectedSelectionSha256 = aggregateBefore.sha256;
  for (const record of failedCaptures.currentMedia) {
    record.captureStatus = "timeout";
    record.candidate = null;
  }
  const result = await executeOccurrenceExportBatch({
    ...inputFrom(value, fake.operations),
    batch: failedCaptures,
    graphResult: graphResultFor(failedCaptures),
  });
  assert.equal(result.status, "failed");
  assert.equal(result.failure.stage, "camera-reread");
  assert.equal(result.failure.occurrenceId, cameraA.occurrenceId);
  assert.equal((await fake.store.readAggregateSelection()).sha256, aggregateBefore.sha256);
});
