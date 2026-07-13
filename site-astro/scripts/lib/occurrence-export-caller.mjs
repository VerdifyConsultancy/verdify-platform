import { createHash } from "node:crypto";

import {
  inspectOccurrenceExportCandidates,
  occurrenceExportPolicySha256,
  reportingFeedEnvelopeSha256,
  validateOccurrenceExportBatch,
  validatePolicyManifestBinding,
} from "./occurrence-export-contract.mjs";
import { currentMediaGenerationPayloadSha256 } from "./occurrence-release.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const EVENT_ID_RE = /^evt_[A-Za-z0-9_-]{8,128}$/u;
const EXPECTED_GRAPH_COUNT = 143;
const EXPECTED_MEDIA_COUNT = 2;

const OPERATION_KEYS = [
  "contract",
  "schemaVersion",
  "storeIdentitySha256",
  "publishCurrentMedia",
  "readCurrentMediaSelection",
  "readCurrentMediaGeneration",
  "readCurrentMediaEventIntent",
  "readPngBlob",
  "publishAggregateReconciliation",
  "readAggregateSelection",
  "readAggregateManifest",
  "readAggregateEventIntent",
  "compareAndSwapAggregateSelection",
];

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).join(",") === keys.join(",");
}

function digest(value, label) {
  if (typeof value !== "string" || !SHA256_RE.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function canonicalValue(value, label, expectedSha256 = null) {
  if (
    value === null
    || typeof value !== "object"
    || Array.isArray(value)
    || !Buffer.isBuffer(value.bytes)
    || value.document === null
    || typeof value.document !== "object"
    || Array.isArray(value.document)
    || !SHA256_RE.test(value.sha256)
  ) throw new Error(`${label} read result is invalid`);
  const bytes = canonicalBytes(value.document);
  if (!bytes.equals(value.bytes) || sha256(bytes) !== value.sha256) {
    throw new Error(`${label} is not canonical`);
  }
  if (expectedSha256 !== null && value.sha256 !== expectedSha256) {
    throw new Error(`${label} digest mismatch`);
  }
  return value;
}

function validateOperations(operations) {
  if (
    !exactKeys(operations, OPERATION_KEYS)
    || operations.contract !== "verdify.lab-occurrence-export-store-operations"
    || operations.schemaVersion !== 1
    || !SHA256_RE.test(operations.storeIdentitySha256)
    || OPERATION_KEYS.slice(3).some((key) => typeof operations[key] !== "function")
  ) throw new Error("occurrence export store operations do not use the closed v1 contract");
  return operations;
}

function validateGraphResult(graphResult, batch, discovered, expectedFeedSha256) {
  if (
    !exactKeys(graphResult, [
      "contract",
      "schemaVersion",
      "policyVersion",
      "policySha256",
      "sourceOccurrenceManifestSha256",
      "reportingFeedSha256",
      "rendererContract",
      "graphs",
    ])
    || graphResult.contract !== "verdify.lab-graph-export-result"
    || graphResult.schemaVersion !== 3
    || graphResult.policyVersion !== batch.policyVersion
    || graphResult.policySha256 !== batch.policySha256
    || graphResult.sourceOccurrenceManifestSha256 !== batch.sourceOccurrenceManifestSha256
    || graphResult.reportingFeedSha256 !== expectedFeedSha256
    || !exactKeys(graphResult.rendererContract, ["contract", "schemaVersion", "status", "failure"])
    || graphResult.rendererContract.contract !== "verdify.lab-graph-renderer-runtime-status"
    || graphResult.rendererContract.schemaVersion !== 1
    || !["satisfied", "failed"].includes(graphResult.rendererContract.status)
    || (graphResult.rendererContract.status === "satisfied") !== (graphResult.rendererContract.failure === null)
    || !Array.isArray(graphResult.graphs)
    || JSON.stringify(graphResult.graphs) !== JSON.stringify(batch.graphs)
    || JSON.stringify(graphResult.graphs.map(({ occurrenceId }) => occurrenceId))
      !== JSON.stringify(discovered.graphs.map(({ occurrenceId }) => occurrenceId))
  ) throw new Error("graph export result is not the exact feed-bound batch result");
  return sha256(canonicalBytes(graphResult));
}

function releaseEvent({ eventId, eventType, batch, payloadSha256 }) {
  return {
    contract: "verdify.lab-release-trigger",
    schemaVersion: 1,
    eventId,
    eventType,
    sourceId: batch.reportingFeed.sourceId,
    sourceWatermark: batch.reportingFeed.sourceWatermark,
    occurredAt: batch.reportingFeed.sourceWatermarkAt,
    payloadSha256,
  };
}

function deterministicEventId(prefix, value) {
  return `evt_${prefix}_${sha256(canonicalBytes(value)).slice(0, 32)}`;
}

function validateEvent(event, expected = null) {
  if (
    !exactKeys(event, [
      "contract",
      "schemaVersion",
      "eventId",
      "eventType",
      "sourceId",
      "sourceWatermark",
      "occurredAt",
      "payloadSha256",
    ])
    || event.contract !== "verdify.lab-release-trigger"
    || event.schemaVersion !== 1
    || !EVENT_ID_RE.test(event.eventId)
    || !SHA256_RE.test(event.payloadSha256)
    || (expected !== null && !canonicalBytes(event).equals(canonicalBytes(expected)))
  ) throw new Error("occurrence event identity mismatch");
  return event;
}

function validateMediaSelection(value, occurrenceId) {
  canonicalValue(value, "current media selection");
  const selection = value.document;
  if (
    !exactKeys(selection, [
      "contract",
      "schemaVersion",
      "occurrenceId",
      "generation",
      "current",
      "previous",
      "selectedAt",
      "reason",
    ])
    || selection.contract !== "verdify.lab-current-media-selection"
    || selection.schemaVersion !== 1
    || selection.occurrenceId !== occurrenceId
    || !Number.isSafeInteger(selection.generation)
    || selection.generation < 1
    || !exactKeys(selection.current, ["generationSha256", "blobSha256"])
    || !SHA256_RE.test(selection.current.generationSha256)
    || !SHA256_RE.test(selection.current.blobSha256)
  ) throw new Error("current media selection identity mismatch");
  return selection;
}

function validateFallback(fallback, blob, expectedBlobSha256 = null) {
  if (
    !exactKeys(fallback, [
      "publicPath",
      "sha256",
      "decodedSha256",
      "decodedBytes",
      "bytes",
      "mediaType",
      "width",
      "height",
      "capturedAt",
      "verifiedAt",
      "policyVersion",
    ])
    || !SHA256_RE.test(fallback.sha256)
    || !SHA256_RE.test(fallback.decodedSha256)
    || fallback.publicPath !== `/evidence/blobs/sha256/${fallback.sha256}.png`
    || fallback.mediaType !== "image/png"
    || (expectedBlobSha256 !== null && fallback.sha256 !== expectedBlobSha256)
    || blob.sha256 !== fallback.sha256
    || blob.decodedSha256 !== fallback.decodedSha256
    || blob.decodedBytes !== fallback.decodedBytes
    || blob.bytes !== fallback.bytes
    || blob.mediaType !== fallback.mediaType
    || blob.width !== fallback.width
    || blob.height !== fallback.height
    || !Buffer.isBuffer(blob.body)
    || sha256(blob.body) !== fallback.sha256
  ) throw new Error("current media blob identity mismatch");
  return fallback;
}

function validateMediaIntent(value, {
  operations,
  occurrenceId,
  generationSha256,
  generation,
  expectedSelectionSha256,
  enforceExpectedSelection,
}) {
  canonicalValue(value, "current media event intent");
  const intent = value.document;
  if (
    !exactKeys(intent, [
      "contract",
      "schemaVersion",
      "eventId",
      "storeIdentitySha256",
      "eventSha256",
      "payloadSha256",
      "policySha256",
      "requestProvenanceSha256",
      "occurrenceId",
      "generationSha256",
      "blobSha256",
      "expectedSelectionSha256",
    ])
    || intent.contract !== "verdify.lab-current-media-export-intent"
    || intent.schemaVersion !== 1
    || intent.eventId !== generation.event.eventId
    || intent.storeIdentitySha256 !== operations.storeIdentitySha256
    || intent.eventSha256 !== sha256(canonicalBytes(generation.event))
    || intent.payloadSha256 !== generation.event.payloadSha256
    || intent.policySha256 !== generation.policySha256
    || intent.requestProvenanceSha256 !== generation.requestProvenanceSha256
    || intent.occurrenceId !== occurrenceId
    || intent.generationSha256 !== generationSha256
    || intent.blobSha256 !== generation.fallback.sha256
    || (intent.expectedSelectionSha256 !== null && !SHA256_RE.test(intent.expectedSelectionSha256))
    || (enforceExpectedSelection && intent.expectedSelectionSha256 !== expectedSelectionSha256)
  ) throw new Error("current media event intent identity mismatch");
  return intent;
}

async function verifyGeneration({
  operations,
  occurrenceId,
  generationSha256,
  policyVersion,
  policySha256,
  requestProvenanceSha256,
  expectedEvent = null,
  expectedBlobSha256 = null,
  expectedFallback = null,
  expectedSelectionSha256 = null,
  enforceExpectedSelection = false,
}) {
  const generationValue = canonicalValue(
    await operations.readCurrentMediaGeneration(occurrenceId, generationSha256),
    "current media generation",
    generationSha256,
  );
  const generation = generationValue.document;
  if (
    !exactKeys(generation, [
      "contract",
      "schemaVersion",
      "occurrenceId",
      "sourceProvenanceSha256",
      "policySha256",
      "requestProvenanceSha256",
      "event",
      "policyVersion",
      "publishedAt",
      "fallback",
    ])
    || generation.contract !== "verdify.lab-current-media-generation"
    || generation.schemaVersion !== 3
    || generation.occurrenceId !== occurrenceId
    || generation.policyVersion !== policyVersion
    || generation.policySha256 !== policySha256
    || generation.requestProvenanceSha256 !== requestProvenanceSha256
    || !SHA256_RE.test(generation.sourceProvenanceSha256)
  ) throw new Error("current media generation identity mismatch");
  validateEvent(generation.event, expectedEvent);
  const intentValue = await operations.readCurrentMediaEventIntent(
    occurrenceId,
    generation.event.eventId,
  );
  validateMediaIntent(intentValue, {
    operations,
    occurrenceId,
    generationSha256,
    generation,
    expectedSelectionSha256,
    enforceExpectedSelection,
  });
  const blob = await operations.readPngBlob(generation.fallback.sha256);
  validateFallback(generation.fallback, blob, expectedBlobSha256);
  if (expectedFallback !== null && !canonicalBytes(generation.fallback).equals(canonicalBytes(expectedFallback))) {
    throw new Error("aggregate-bound current media fallback identity mismatch");
  }
  return { generation, generationSha256 };
}

async function verifySelectedMedia({ operations, request, expectedBlobSha256 }) {
  const selectionValue = await operations.readCurrentMediaSelection(request.occurrence.occurrenceId);
  if (selectionValue === null) throw new Error("current media selection is absent");
  const selection = validateMediaSelection(selectionValue, request.occurrence.occurrenceId);
  if (selection.current.blobSha256 !== expectedBlobSha256) {
    throw new Error("current media selector does not select the batch blob");
  }
  const verified = await verifyGeneration({
    operations,
    occurrenceId: request.occurrence.occurrenceId,
    generationSha256: selection.current.generationSha256,
    policyVersion: request.policyVersion,
    policySha256: request.policySha256,
    requestProvenanceSha256: request.requestProvenanceSha256,
    expectedEvent: request.event,
    expectedBlobSha256,
    expectedSelectionSha256: request.expectedSelectionSha256,
    enforceExpectedSelection: true,
  });
  if (verified.generation.sourceProvenanceSha256 !== request.occurrence.sourceProvenanceSha256) {
    throw new Error("current media generation source identity mismatch");
  }
  return {
    occurrenceId: request.occurrence.occurrenceId,
    disposition: "captured",
    selectionSha256: selectionValue.sha256,
    generationSha256: selection.current.generationSha256,
    blobSha256: selection.current.blobSha256,
    eventId: verified.generation.event.eventId,
    policySha256: request.policySha256,
    requestProvenanceSha256: request.requestProvenanceSha256,
  };
}

function validateAggregateSelection(value) {
  if (value === null) return null;
  canonicalValue(value, "aggregate occurrence selection");
  const selection = value.document;
  if (
    !exactKeys(selection, [
      "contract",
      "schemaVersion",
      "generation",
      "current",
      "previous",
      "selectedAt",
      "reason",
    ])
    || selection.contract !== "verdify.lab-occurrence-selection"
    || selection.schemaVersion !== 1
    || !Number.isSafeInteger(selection.generation)
    || selection.generation < 1
    || !exactKeys(selection.current, ["manifestSha256", "eventId"])
    || !SHA256_RE.test(selection.current.manifestSha256)
    || !EVENT_ID_RE.test(selection.current.eventId)
  ) throw new Error("aggregate occurrence selection identity mismatch");
  return selection;
}

function validateAggregateManifestValue(value, expectedManifestSha256, expectedEvent = null) {
  canonicalValue(value, "aggregate occurrence manifest", expectedManifestSha256);
  const manifest = value.document;
  if (
    manifest === null
    || typeof manifest !== "object"
    || Array.isArray(manifest)
    || manifest.contract !== "verdify.lab-specialist-occurrence-release"
    || manifest.schemaVersion !== 2
    || !Array.isArray(manifest.occurrences?.graphs)
    || !Array.isArray(manifest.occurrences?.currentMedia)
  ) throw new Error("aggregate occurrence manifest identity mismatch");
  validateEvent(manifest.event, expectedEvent);
  return manifest;
}

async function readInitialAggregate(operations) {
  const selectionValue = await operations.readAggregateSelection();
  const selection = validateAggregateSelection(selectionValue);
  if (selection === null) {
    return { selectionValue: null, selection: null, manifest: null };
  }
  const manifestValue = await operations.readAggregateManifest(selection.current.manifestSha256);
  const manifest = validateAggregateManifestValue(
    manifestValue,
    selection.current.manifestSha256,
  );
  if (manifest.event.eventId !== selection.current.eventId) {
    throw new Error("aggregate selector and manifest event identities differ");
  }
  return { selectionValue, selection, manifest };
}

async function retainedAggregateBinding({ operations, initialAggregate, occurrence, requestProvenanceSha256, policy }) {
  const record = initialAggregate.manifest?.occurrences.currentMedia.find(
    ({ occurrenceId }) => occurrenceId === occurrence.occurrenceId,
  ) ?? null;
  if (
    record === null
    || record.state !== "verified"
    || record.policySha256 !== policy.policySha256
    || record.requestProvenanceSha256 !== requestProvenanceSha256
    || record.pointer === null
    || record.fallback === null
    || !SHA256_RE.test(record.pointer.selectionSha256)
    || !SHA256_RE.test(record.pointer.currentGenerationSha256)
    || !SHA256_RE.test(record.fallback.sha256)
  ) throw new Error("failed capture has no exact aggregate-bound last-known-good generation");
  const verified = await verifyGeneration({
    operations,
    occurrenceId: occurrence.occurrenceId,
    generationSha256: record.pointer.currentGenerationSha256,
    policyVersion: policy.policyVersion,
    policySha256: policy.policySha256,
    requestProvenanceSha256,
    expectedBlobSha256: record.fallback.sha256,
    expectedFallback: record.fallback,
  });
  if (verified.generation.sourceProvenanceSha256 !== occurrence.sourceProvenanceSha256) {
    throw new Error("aggregate-bound generation source identity mismatch");
  }
  return {
    occurrenceId: occurrence.occurrenceId,
    disposition: "retained-aggregate-lkg",
    selectionSha256: record.pointer.selectionSha256,
    generationSha256: record.pointer.currentGenerationSha256,
    blobSha256: record.fallback.sha256,
    eventId: verified.generation.event.eventId,
    policySha256: policy.policySha256,
    requestProvenanceSha256,
  };
}

function publicMediaBinding(binding, status) {
  return {
    occurrenceId: binding.occurrenceId,
    status,
    eventId: binding.eventId,
    selectionSha256: binding.selectionSha256,
    generationSha256: binding.generationSha256,
    blobSha256: binding.blobSha256,
  };
}

function resultBase({ batch, reportingFeedSha256 }) {
  return {
    contract: "verdify.lab-occurrence-export-call-result",
    schemaVersion: 1,
    batchId: batch.batchId,
    policyVersion: batch.policyVersion,
    policySha256: batch.policySha256,
    sourceOccurrenceManifestSha256: batch.sourceOccurrenceManifestSha256,
    reportingFeedSha256,
  };
}

function failedResult(base, media, stage, code, occurrenceId = null, aggregate = null) {
  return {
    ...base,
    status: "failed",
    media,
    aggregate,
    failure: { stage, occurrenceId, code },
  };
}

function buildMediaRequest({ occurrence, record, candidate, policy, batch, publishedAt }) {
  const request = {
    policyVersion: policy.policyVersion,
    policySha256: batch.policySha256,
    requestProvenanceSha256: record.requestProvenanceSha256,
    publishedAt,
    occurrence,
    candidate,
    expectedSelectionSha256: record.expectedSelectionSha256,
    event: null,
  };
  const payloadSha256 = currentMediaGenerationPayloadSha256(request);
  request.event = releaseEvent({
    eventId: deterministicEventId("media", {
      batchId: batch.batchId,
      occurrenceId: occurrence.occurrenceId,
      payloadSha256,
    }),
    eventType: "current-media-updated",
    batch,
    payloadSha256,
  });
  return request;
}

function validateAggregateIntent(value, {
  operations,
  event,
  reconciliationSha256,
  expectedSelectionSha256,
  cameraBindings,
}) {
  canonicalValue(value, "aggregate occurrence event intent");
  const intent = value.document;
  if (
    !exactKeys(intent, [
      "contract",
      "schemaVersion",
      "eventId",
      "storeIdentitySha256",
      "eventSha256",
      "payloadSha256",
      "reconciliationSha256",
      "manifestSha256",
      "expectedSelectionSha256",
      "cameraSelections",
    ])
    || intent.contract !== "verdify.lab-exact-reconciliation-intent"
    || intent.schemaVersion !== 1
    || intent.eventId !== event.eventId
    || intent.storeIdentitySha256 !== operations.storeIdentitySha256
    || intent.eventSha256 !== sha256(canonicalBytes(event))
    || intent.payloadSha256 !== event.payloadSha256
    || intent.reconciliationSha256 !== reconciliationSha256
    || !SHA256_RE.test(intent.manifestSha256)
    || intent.expectedSelectionSha256 !== expectedSelectionSha256
    || JSON.stringify(intent.cameraSelections) !== JSON.stringify(
      cameraBindings.map(({ occurrenceId, selectionSha256 }) => ({ occurrenceId, selectionSha256 })),
    )
  ) throw new Error("aggregate occurrence event intent identity mismatch");
  return intent;
}

function validatePublishedManifest(manifest, {
  event,
  policy,
  batch,
  discovered,
  bindings,
}) {
  if (
    manifest.policyVersion !== policy.policyVersion
    || manifest.policySha256 !== batch.policySha256
    || manifest.sourceSnapshotManifestSha256 !== policy.sourceSnapshotManifestSha256
    || manifest.publishedAt === undefined
    || JSON.stringify(manifest.occurrences.graphs.map(({ occurrenceId }) => occurrenceId).sort())
      !== JSON.stringify(discovered.graphs.map(({ occurrenceId }) => occurrenceId).sort())
  ) throw new Error("published aggregate manifest does not match the exact graph batch");
  validateEvent(manifest.event, event);
  const mediaById = new Map(manifest.occurrences.currentMedia.map((record) => [record.occurrenceId, record]));
  if (mediaById.size !== bindings.length) throw new Error("published aggregate manifest media set is incomplete");
  for (const binding of bindings) {
    const record = mediaById.get(binding.occurrenceId);
    if (
      record === undefined
      || record.policySha256 !== binding.policySha256
      || record.requestProvenanceSha256 !== binding.requestProvenanceSha256
      || record.state !== "verified"
      || record.pointer?.selectionSha256 !== binding.selectionSha256
      || record.pointer?.currentGenerationSha256 !== binding.generationSha256
      || record.fallback?.sha256 !== binding.blobSha256
    ) throw new Error("published aggregate manifest is not bound to the exact camera generations");
  }
}

/**
 * Execute one complete, already-produced 143+2 occurrence batch.
 *
 * This source-only caller deliberately has no default store, client, endpoint,
 * credential, transport, or CLI. The injected operation contract must provide
 * durable immutable publication plus camera-aware aggregate CAS semantics.
 */
export async function executeOccurrenceExportBatch({
  policy,
  manifest,
  manifestSha256,
  batch,
  graphResult,
  sourceRoot,
  processingAt = new Date().toISOString(),
  operations: operationOverrides,
}) {
  const operations = validateOperations(operationOverrides);
  const discovered = validatePolicyManifestBinding(policy, manifest, manifestSha256);
  if (discovered.graphs.length !== EXPECTED_GRAPH_COUNT || discovered.currentMedia.length !== EXPECTED_MEDIA_COUNT) {
    throw new Error(`occurrence export caller requires exactly ${EXPECTED_GRAPH_COUNT}+${EXPECTED_MEDIA_COUNT} occurrences`);
  }
  if (
    policy.activation.state !== "approved"
    || policy.activation.approvedBy !== "jason"
    || !policy.activation.approvedAt
  ) throw new Error("occurrence export caller policy is not activated");
  const policySha256 = occurrenceExportPolicySha256(policy);
  if (batch.policySha256 !== policySha256) throw new Error("occurrence export caller policy digest mismatch");
  const feedFreshness = validateOccurrenceExportBatch(batch, policy, processingAt);
  const reportingFeedSha256 = reportingFeedEnvelopeSha256(batch.reportingFeed);
  const base = resultBase({ batch, reportingFeedSha256 });
  const graphResultSha256 = validateGraphResult(
    graphResult,
    batch,
    discovered,
    reportingFeedSha256,
  );
  if (
    JSON.stringify(batch.currentMedia.map(({ occurrenceId }) => occurrenceId))
      !== JSON.stringify(discovered.currentMedia.map(({ occurrenceId }) => occurrenceId))
  ) throw new Error("current media batch is not in manifest order");
  if (feedFreshness.status === "alert") {
    return failedResult(base, [], "validation", "reporting-feed-stale");
  }
  const inspected = await inspectOccurrenceExportCandidates({
    policy,
    batch,
    sourceRoot,
    processingAt,
  });

  let initialAggregate;
  try {
    initialAggregate = await readInitialAggregate(operations);
  } catch {
    return failedResult(base, [], "aggregate-initial-read", "evidence-unavailable");
  }
  if ((initialAggregate.selectionValue?.sha256 ?? null) !== batch.expectedSelectionSha256) {
    return failedResult(base, [], "aggregate-precondition", "selection-changed");
  }

  const mediaById = new Map(batch.currentMedia.map((record) => [record.occurrenceId, record]));
  const bindings = [];
  const mediaOutput = [];
  const capturedRequests = new Map();
  for (const occurrence of discovered.currentMedia) {
    const record = mediaById.get(occurrence.occurrenceId);
    const candidate = inspected.currentMediaCandidates.get(occurrence.occurrenceId);
    if (record.captureStatus !== "success" || candidate === null) {
      let binding;
      try {
        binding = await retainedAggregateBinding({
          operations,
          initialAggregate,
          occurrence,
          requestProvenanceSha256: record.requestProvenanceSha256,
          policy: { policyVersion: policy.policyVersion, policySha256 },
        });
      } catch {
        return failedResult(
          base,
          mediaOutput,
          "camera-retain",
          "exact-aggregate-lkg-unavailable",
          occurrence.occurrenceId,
        );
      }
      bindings.push(binding);
      mediaOutput.push(publicMediaBinding(binding, "retained-aggregate-lkg"));
      continue;
    }

    const request = buildMediaRequest({
      occurrence,
      record,
      candidate,
      policy,
      batch,
      publishedAt: inspected.feedFreshness.effectiveProcessingAt,
    });
    capturedRequests.set(occurrence.occurrenceId, { request, expectedBlobSha256: candidate.expectedSha256 });
    let publishFailed = false;
    try {
      await operations.publishCurrentMedia(request);
    } catch {
      publishFailed = true;
    }
    let binding;
    try {
      binding = await verifySelectedMedia({
        operations,
        request,
        expectedBlobSha256: candidate.expectedSha256,
      });
    } catch {
      return failedResult(
        base,
        mediaOutput,
        "camera-publish",
        publishFailed ? "selection-not-committed" : "selected-evidence-mismatch",
        occurrence.occurrenceId,
      );
    }
    bindings.push(binding);
    mediaOutput.push(publicMediaBinding(binding, publishFailed ? "selected-after-uncertain-write" : "selected"));
  }

  // Re-read both selectors and all referenced immutable evidence after every
  // camera write. A changed selector stops before an aggregate object exists.
  for (let index = 0; index < bindings.length; index += 1) {
    const binding = bindings[index];
    try {
      const selectionValue = await operations.readCurrentMediaSelection(binding.occurrenceId);
      const selection = validateMediaSelection(selectionValue, binding.occurrenceId);
      if (
        selectionValue.sha256 !== binding.selectionSha256
        || selection.current.generationSha256 !== binding.generationSha256
        || selection.current.blobSha256 !== binding.blobSha256
      ) throw new Error("current media selector changed before reconciliation");
      const captured = capturedRequests.get(binding.occurrenceId);
      if (captured) {
        await verifySelectedMedia({
          operations,
          request: captured.request,
          expectedBlobSha256: captured.expectedBlobSha256,
        });
      } else {
        await verifyGeneration({
          operations,
          occurrenceId: binding.occurrenceId,
          generationSha256: binding.generationSha256,
          policyVersion: policy.policyVersion,
          policySha256,
          requestProvenanceSha256: binding.requestProvenanceSha256,
          expectedBlobSha256: binding.blobSha256,
        });
      }
    } catch {
      return failedResult(
        base,
        mediaOutput,
        "camera-reread",
        "selection-changed",
        binding.occurrenceId,
      );
    }
  }

  const reconciliation = {
    contract: "verdify.lab-exact-occurrence-reconciliation",
    schemaVersion: 1,
    batchId: batch.batchId,
    policyVersion: policy.policyVersion,
    policySha256,
    sourceSnapshotManifestSha256: policy.sourceSnapshotManifestSha256,
    sourceOccurrenceManifestSha256: manifestSha256,
    reportingFeedSha256,
    graphResultSha256,
    cameraBindings: bindings,
    publishedAt: inspected.feedFreshness.effectiveProcessingAt,
  };
  const reconciliationSha256 = sha256(canonicalBytes(reconciliation));
  const event = releaseEvent({
    eventId: deterministicEventId("reconcile", {
      batchId: batch.batchId,
      reconciliationSha256,
    }),
    eventType: "reconciliation",
    batch,
    payloadSha256: reconciliationSha256,
  });
  const graphById = new Map(batch.graphs.map((record) => [record.occurrenceId, record]));
  const release = {
    sourceRoot,
    sourceSnapshotManifestSha256: policy.sourceSnapshotManifestSha256,
    policyVersion: policy.policyVersion,
    policySha256,
    publishedAt: inspected.feedFreshness.effectiveProcessingAt,
    graphs: discovered.graphs.map((occurrence) => {
      const record = graphById.get(occurrence.occurrenceId);
      const candidate = inspected.graphCandidates.get(occurrence.occurrenceId);
      return candidate
        ? { ...occurrence, probeStatus: record.probeStatus, candidate }
        : { ...occurrence, probeStatus: record.probeStatus };
    }),
    currentMedia: discovered.currentMedia.map((occurrence) => ({
      discovered: occurrence,
      requestProvenanceSha256: mediaById.get(occurrence.occurrenceId).requestProvenanceSha256,
    })),
    cameraBindings: bindings,
  };

  try {
    await operations.publishAggregateReconciliation({
      event,
      reconciliation,
      reconciliationSha256,
      release,
      expectedSelectionSha256: batch.expectedSelectionSha256,
    });
  } catch {
    // Immutable/event writes may have committed even when their response did
    // not arrive. The exact event intent below is the recovery authority.
  }
  let aggregateIntent;
  let aggregateManifest;
  try {
    const intentValue = await operations.readAggregateEventIntent(event.eventId);
    aggregateIntent = validateAggregateIntent(intentValue, {
      operations,
      event,
      reconciliationSha256,
      expectedSelectionSha256: batch.expectedSelectionSha256,
      cameraBindings: bindings,
    });
    const manifestValue = await operations.readAggregateManifest(aggregateIntent.manifestSha256);
    aggregateManifest = validateAggregateManifestValue(
      manifestValue,
      aggregateIntent.manifestSha256,
      event,
    );
    validatePublishedManifest(aggregateManifest, {
      event,
      policy,
      batch,
      discovered,
      bindings,
    });
  } catch {
    return failedResult(base, mediaOutput, "aggregate-publish", "immutable-evidence-unavailable");
  }

  const nextSelection = {
    contract: "verdify.lab-occurrence-selection",
    schemaVersion: 1,
    generation: (initialAggregate.selection?.generation ?? 0) + 1,
    current: {
      manifestSha256: aggregateIntent.manifestSha256,
      eventId: event.eventId,
    },
    previous: initialAggregate.selection?.current ?? null,
    selectedAt: inspected.feedFreshness.effectiveProcessingAt,
    reason: "publish",
  };
  try {
    await operations.compareAndSwapAggregateSelection({
      selection: nextSelection,
      expectedSelectionSha256: batch.expectedSelectionSha256,
      cameraSelectionPreconditions: bindings.map(({ occurrenceId, selectionSha256 }) => ({
        occurrenceId,
        selectionSha256,
      })),
    });
  } catch {
    // A conditional write can be known-failed or committed with an uncertain
    // response. Only the post-read below decides the public outcome.
  }
  let observed;
  try {
    observed = await operations.readAggregateSelection();
    validateAggregateSelection(observed);
  } catch {
    return failedResult(
      base,
      mediaOutput,
      "aggregate-post-read",
      "selection-unavailable",
      null,
      {
        status: "published-but-unconfirmed",
        eventId: event.eventId,
        manifestSha256: aggregateIntent.manifestSha256,
        selectionSha256: null,
      },
    );
  }
  const selected = observed !== null
    && observed.document.current.manifestSha256 === aggregateIntent.manifestSha256
    && observed.document.current.eventId === event.eventId;
  if (!selected) {
    return {
      ...base,
      status: "published-but-superseded",
      media: mediaOutput,
      aggregate: {
        status: "published-but-superseded",
        eventId: event.eventId,
        manifestSha256: aggregateIntent.manifestSha256,
        selectionSha256: null,
      },
      failure: {
        stage: "aggregate-cas",
        occurrenceId: null,
        code: "selection-precondition-lost",
      },
    };
  }
  return {
    ...base,
    status: "selected",
    media: mediaOutput,
    aggregate: {
      status: "selected",
      eventId: event.eventId,
      manifestSha256: aggregateIntent.manifestSha256,
      selectionSha256: observed.sha256,
    },
    failure: null,
  };
}

export const occurrenceExportCallerContract = Object.freeze({
  expectedGraphCount: EXPECTED_GRAPH_COUNT,
  expectedCurrentMediaCount: EXPECTED_MEDIA_COUNT,
  operations: Object.freeze({
    contract: "verdify.lab-occurrence-export-store-operations",
    schemaVersion: 1,
    aggregateCas: "camera-selection-preconditions-required",
  }),
  result: Object.freeze({
    contract: "verdify.lab-occurrence-export-call-result",
    schemaVersion: 1,
    statuses: Object.freeze(["selected", "published-but-superseded", "failed"]),
  }),
});
