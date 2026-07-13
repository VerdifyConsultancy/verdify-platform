import { createHash } from "node:crypto";

import {
  inspectOccurrenceExportCandidates,
  occurrenceExportPolicySha256,
  reportingFeedEnvelopeSha256,
  validateOccurrenceExportBatch,
  validatePolicyManifestBinding,
} from "./occurrence-export-contract.mjs";
import {
  currentMediaGenerationPayloadSha256,
  loadCurrentMediaGeneration,
  loadOccurrenceReleaseManifest,
  loadSelectedCurrentMediaGeneration,
  loadSelectedOccurrenceRelease,
} from "./occurrence-release.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const EVENT_ID_RE = /^evt_[A-Za-z0-9_-]{8,128}$/u;
const EXPECTED_GRAPH_COUNT = 143;
const EXPECTED_MEDIA_COUNT = 2;

const OPERATION_KEYS = [
  "contract",
  "schemaVersion",
  "storeIdentitySha256",
  "evidenceStore",
  "publishCurrentMedia",
  "readCurrentMediaEventIntent",
  "publishAggregateReconciliation",
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
    || operations.evidenceStore?.identity?.sha256 !== operations.storeIdentitySha256
    || OPERATION_KEYS.slice(4).some((key) => typeof operations[key] !== "function")
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

async function verifyGenerationEvidence({
  operations,
  occurrenceId,
  generationSha256,
  generation,
  sourceProvenanceSha256,
  policyVersion,
  policySha256,
  requestProvenanceSha256,
  expectedEvent = null,
  expectedCandidate = null,
  expectedFallback = null,
  expectedSelectionSha256 = null,
  enforceExpectedSelection = false,
}) {
  if (
    generation.occurrenceId !== occurrenceId
    || generation.sourceProvenanceSha256 !== sourceProvenanceSha256
    || generation.policyVersion !== policyVersion
    || generation.policySha256 !== policySha256
    || generation.requestProvenanceSha256 !== requestProvenanceSha256
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
  if (
    expectedCandidate !== null
    && (
      generation.fallback.sha256 !== expectedCandidate.expectedSha256
      || generation.fallback.capturedAt !== expectedCandidate.capturedAt
      || generation.fallback.verifiedAt !== expectedCandidate.verifiedAt
      || generation.fallback.policyVersion !== policyVersion
    )
  ) throw new Error("current media generation does not match the inspected candidate");
  if (expectedFallback !== null && !canonicalBytes(generation.fallback).equals(canonicalBytes(expectedFallback))) {
    throw new Error("aggregate-bound current media fallback identity mismatch");
  }
  return { generation, generationSha256 };
}

async function verifySelectedMedia({ operations, request, expectedCandidate }) {
  const selected = await loadSelectedCurrentMediaGeneration(
    operations.evidenceStore,
    request.occurrence.occurrenceId,
  );
  if (selected === null) throw new Error("current media selection is absent");
  if (
    selected.selection.current.blobSha256 !== expectedCandidate.expectedSha256
    || selected.current.fallback.sha256 !== expectedCandidate.expectedSha256
  ) {
    throw new Error("current media selector does not select the batch blob");
  }
  const verified = await verifyGenerationEvidence({
    operations,
    occurrenceId: request.occurrence.occurrenceId,
    generationSha256: selected.selection.current.generationSha256,
    generation: selected.current,
    sourceProvenanceSha256: request.occurrence.sourceProvenanceSha256,
    policyVersion: request.policyVersion,
    policySha256: request.policySha256,
    requestProvenanceSha256: request.requestProvenanceSha256,
    expectedEvent: request.event,
    expectedCandidate,
    expectedSelectionSha256: request.expectedSelectionSha256,
    enforceExpectedSelection: true,
  });
  return {
    occurrenceId: request.occurrence.occurrenceId,
    disposition: "captured",
    selectionSha256: selected.selectionSha256,
    selectionGeneration: selected.selection.generation,
    generationSha256: selected.selection.current.generationSha256,
    previousGenerationSha256: selected.selection.previous?.generationSha256 ?? null,
    blobSha256: selected.selection.current.blobSha256,
    eventId: verified.generation.event.eventId,
    sourceProvenanceSha256: request.occurrence.sourceProvenanceSha256,
    policySha256: request.policySha256,
    requestProvenanceSha256: request.requestProvenanceSha256,
    fallback: verified.generation.fallback,
  };
}

function bindSelectedOccurrenceRelease(selected) {
  if (
    selected.selection !== null
    && (
      selected.current?.event.eventId !== selected.selection.current.eventId
      || (selected.selection.previous !== null
        && selected.previous?.event.eventId !== selected.selection.previous.eventId)
    )
  ) throw new Error("aggregate selector and manifest event identities differ");
  return selected;
}

async function readInitialAggregate(operations) {
  const selected = bindSelectedOccurrenceRelease(
    await loadSelectedOccurrenceRelease(operations.evidenceStore),
  );
  return {
    selectionSha256: selected.selectionSha256,
    selection: selected.selection,
    manifest: selected.current,
  };
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
  const loaded = await loadCurrentMediaGeneration(
    operations.evidenceStore,
    occurrence.occurrenceId,
    record.pointer.currentGenerationSha256,
  );
  const verified = await verifyGenerationEvidence({
    operations,
    occurrenceId: occurrence.occurrenceId,
    generationSha256: record.pointer.currentGenerationSha256,
    generation: loaded.generation,
    sourceProvenanceSha256: occurrence.sourceProvenanceSha256,
    policyVersion: policy.policyVersion,
    policySha256: policy.policySha256,
    requestProvenanceSha256,
    expectedFallback: record.fallback,
  });
  return {
    occurrenceId: occurrence.occurrenceId,
    disposition: "retained-aggregate-lkg",
    selectionSha256: record.pointer.selectionSha256,
    selectionGeneration: record.pointer.generation,
    generationSha256: record.pointer.currentGenerationSha256,
    previousGenerationSha256: record.pointer.previousGenerationSha256,
    blobSha256: record.fallback.sha256,
    eventId: verified.generation.event.eventId,
    sourceProvenanceSha256: occurrence.sourceProvenanceSha256,
    policySha256: policy.policySha256,
    requestProvenanceSha256,
    fallback: verified.generation.fallback,
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
  inspected,
  initialAggregate,
  publishedAt,
}) {
  if (
    manifest.policyVersion !== policy.policyVersion
    || manifest.policySha256 !== batch.policySha256
    || manifest.sourceSnapshotManifestSha256 !== policy.sourceSnapshotManifestSha256
    || manifest.publishedAt !== publishedAt
    || manifest.occurrences.graphs.length !== discovered.graphs.length
    || manifest.occurrences.currentMedia.length !== discovered.currentMedia.length
  ) throw new Error("published aggregate manifest does not match the exact graph batch");
  validateEvent(manifest.event, event);
  const graphBatchById = new Map(batch.graphs.map((record) => [record.occurrenceId, record]));
  const priorGraphById = new Map(
    (initialAggregate.manifest?.occurrences.graphs ?? []).map((record) => [record.occurrenceId, record]),
  );
  for (let index = 0; index < discovered.graphs.length; index += 1) {
    const occurrence = discovered.graphs[index];
    const actual = manifest.occurrences.graphs[index];
    const batchRecord = graphBatchById.get(occurrence.occurrenceId);
    const candidate = inspected.graphCandidates.get(occurrence.occurrenceId);
    if (
      actual.occurrenceId !== occurrence.occurrenceId
      || Object.keys(occurrence).some(
        (key) => JSON.stringify(actual[key]) !== JSON.stringify(occurrence[key]),
      )
      || actual.staleAfterSeconds !== Math.max(occurrence.renderCadenceSeconds * 2, 1800)
      || actual.probeStatus !== batchRecord.probeStatus
    ) throw new Error("published graph occurrence is not the ordered inspected batch record");
    if (candidate !== null) {
      if (
        actual.state !== "verified"
        || actual.fallback?.sha256 !== candidate.expectedSha256
        || actual.fallback?.capturedAt !== candidate.capturedAt
        || actual.fallback?.verifiedAt !== candidate.verifiedAt
        || actual.fallback?.policyVersion !== policy.policyVersion
      ) throw new Error("published graph fallback does not match its inspected candidate");
      continue;
    }
    const prior = priorGraphById.get(occurrence.occurrenceId);
    if (prior?.fallback) {
      if (
        actual.state !== "retained-last-known-good"
        || !canonicalBytes(actual.fallback).equals(canonicalBytes(prior.fallback))
      ) throw new Error("failed graph did not retain its exact aggregate-bound fallback");
    } else if (actual.state !== "missing" || actual.fallback !== null) {
      throw new Error("failed graph without aggregate LKG was not published missing");
    }
  }
  for (let index = 0; index < bindings.length; index += 1) {
    const binding = bindings[index];
    const occurrence = discovered.currentMedia[index];
    const record = manifest.occurrences.currentMedia[index];
    const expected = {
      ...occurrence,
      policySha256: binding.policySha256,
      requestProvenanceSha256: binding.requestProvenanceSha256,
      staleAfterSeconds: Math.max(occurrence.captureCadenceSeconds * 2, 900),
      captureStatus: "selected-generation",
      state: "verified",
      fallback: binding.fallback,
      pointer: {
        selectionSha256: binding.selectionSha256,
        generation: binding.selectionGeneration,
        currentGenerationSha256: binding.generationSha256,
        previousGenerationSha256: binding.previousGenerationSha256,
      },
    };
    if (
      binding.occurrenceId !== occurrence.occurrenceId
      || !canonicalBytes(record).equals(canonicalBytes(expected))
    ) throw new Error("published aggregate manifest is not bound to the exact ordered camera evidence");
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
  if (sha256(canonicalBytes(manifest)) !== manifestSha256) {
    throw new Error("occurrence export manifest object does not match its canonical byte digest");
  }
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
  if (Date.parse(batch.exportedAt) < Date.parse(policy.activation.approvedAt)) {
    throw new Error("occurrence export batch predates policy activation");
  }
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
  const operations = validateOperations(operationOverrides);
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
  if (initialAggregate.selectionSha256 !== batch.expectedSelectionSha256) {
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
    capturedRequests.set(occurrence.occurrenceId, { request, expectedCandidate: candidate });
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
        expectedCandidate: candidate,
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
      const selected = await loadSelectedCurrentMediaGeneration(
        operations.evidenceStore,
        binding.occurrenceId,
      );
      if (
        selected === null
        || selected.selectionSha256 !== binding.selectionSha256
        || selected.selection.generation !== binding.selectionGeneration
        || selected.selection.current.generationSha256 !== binding.generationSha256
        || selected.selection.current.blobSha256 !== binding.blobSha256
        || (selected.selection.previous?.generationSha256 ?? null) !== binding.previousGenerationSha256
      ) throw new Error("current media selector changed before reconciliation");
      const captured = capturedRequests.get(binding.occurrenceId);
      if (captured) {
        await verifySelectedMedia({
          operations,
          request: captured.request,
          expectedCandidate: captured.expectedCandidate,
        });
      } else {
        await verifyGenerationEvidence({
          operations,
          occurrenceId: binding.occurrenceId,
          generationSha256: binding.generationSha256,
          generation: selected.current,
          sourceProvenanceSha256: binding.sourceProvenanceSha256,
          policyVersion: policy.policyVersion,
          policySha256,
          requestProvenanceSha256: binding.requestProvenanceSha256,
          expectedFallback: binding.fallback,
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
    aggregateManifest = (await loadOccurrenceReleaseManifest(
      operations.evidenceStore,
      aggregateIntent.manifestSha256,
    )).manifest;
    validatePublishedManifest(aggregateManifest, {
      event,
      policy,
      batch,
      discovered,
      bindings,
      inspected,
      initialAggregate,
      publishedAt: inspected.feedFreshness.effectiveProcessingAt,
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
    observed = bindSelectedOccurrenceRelease(
      await loadSelectedOccurrenceRelease(operations.evidenceStore),
    );
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
  const selected = observed.selection !== null
    && observed.selection.current.manifestSha256 === aggregateIntent.manifestSha256
    && observed.selection.current.eventId === event.eventId;
  if (selected) {
    try {
      validatePublishedManifest(observed.current, {
        event,
        policy,
        batch,
        discovered,
        bindings,
        inspected,
        initialAggregate,
        publishedAt: inspected.feedFreshness.effectiveProcessingAt,
      });
    } catch {
      return failedResult(
        base,
        mediaOutput,
        "aggregate-post-read",
        "selected-evidence-mismatch",
      );
    }
  }
  if (!selected) {
    if (observed.selectionSha256 === batch.expectedSelectionSha256) {
      return failedResult(
        base,
        mediaOutput,
        "aggregate-cas",
        "selection-not-committed-retryable",
        null,
        {
          status: "published-unselected",
          eventId: event.eventId,
          manifestSha256: aggregateIntent.manifestSha256,
          selectionSha256: observed.selectionSha256,
        },
      );
    }
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
      selectionSha256: observed.selectionSha256,
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
