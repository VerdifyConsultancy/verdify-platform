import { createHash } from "node:crypto";

import {
  occurrenceExportPolicySha256,
  reportingFeedEnvelopeSha256,
  validateOccurrenceExportBatch,
  validatePolicyManifestBinding,
} from "./occurrence-export-contract.mjs";
import {
  produceGraphExportCandidates,
  reportingDatasourceIdentitySha256,
} from "./graph-export-producer.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u;
const EXPECTED_GRAPH_COUNT = 143;
const EXPECTED_CURRENT_MEDIA_COUNT = 2;
const MEDIA_STATUSES = Object.freeze([
  "success",
  "timeout",
  "http-error",
  "decode-error",
  "missing",
  "policy-rejected",
]);

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).join(",") === keys.join(",");
}

function canonicalInstant(value, label) {
  if (typeof value !== "string" || !ISO_INSTANT_RE.test(value)) throw new Error(`${label} is invalid`);
  const milliseconds = Date.parse(value);
  const normalized = Number.isFinite(milliseconds) ? new Date(milliseconds).toISOString() : "";
  const expected = value.includes(".") ? normalized : normalized.replace(".000Z", "Z");
  if (value !== expected) throw new Error(`${label} is invalid`);
  return value;
}

function nullableDigest(value, label) {
  if (value !== null && (typeof value !== "string" || !SHA256_RE.test(value))) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function validateSelectorPreconditions(value, discovered) {
  if (
    !exactKeys(value, [
      "contract",
      "schemaVersion",
      "aggregateExpectedSelectionSha256",
      "currentMedia",
    ])
    || value.contract !== "verdify.lab-occurrence-export-selector-preconditions"
    || value.schemaVersion !== 1
    || !Array.isArray(value.currentMedia)
    || value.currentMedia.length !== EXPECTED_CURRENT_MEDIA_COUNT
  ) throw new Error("occurrence export selector preconditions do not use the closed v1 shape");
  nullableDigest(value.aggregateExpectedSelectionSha256, "aggregate selection precondition");
  const expectedIds = discovered.currentMedia.map(({ occurrenceId }) => occurrenceId);
  const observedIds = [];
  for (const record of value.currentMedia) {
    if (!exactKeys(record, ["occurrenceId", "expectedSelectionSha256"])) {
      throw new Error("current media selector precondition does not use the closed v1 shape");
    }
    observedIds.push(record.occurrenceId);
    nullableDigest(record.expectedSelectionSha256, "current media selection precondition");
  }
  if (JSON.stringify(observedIds) !== JSON.stringify(expectedIds)) {
    throw new Error("current media selector preconditions are not in manifest order");
  }
  return value;
}

function validateCurrentMediaCandidate(candidate, occurrenceId, requestProvenanceSha256) {
  const canonicalPath = typeof candidate?.relativePath === "string"
    ? /^current-media\/(media_[0-9a-f]{24})\/([0-9a-f]{64})\.png$/u.exec(candidate.relativePath)
    : null;
  if (
    !exactKeys(candidate, ["relativePath", "mediaType", "capturedAt", "requestProvenanceSha256"])
    || canonicalPath === null
    || canonicalPath[1] !== occurrenceId
    || candidate.mediaType !== "image/png"
    || candidate.requestProvenanceSha256 !== requestProvenanceSha256
  ) throw new Error("current media candidate does not use the closed batch shape");
  canonicalInstant(candidate.capturedAt, "current media capture time");
  return candidate;
}

function validateCurrentMediaRecords(records, policy, discovered, selectorPreconditions) {
  if (!Array.isArray(records) || records.length !== EXPECTED_CURRENT_MEDIA_COUNT) {
    throw new Error("current media export records are not complete");
  }
  const allowedById = new Map(policy.currentMedia.map((record) => [record.occurrenceId, record]));
  const preconditionById = new Map(selectorPreconditions.currentMedia.map((record) => [
    record.occurrenceId,
    record.expectedSelectionSha256,
  ]));
  const expectedIds = discovered.currentMedia.map(({ occurrenceId }) => occurrenceId);
  const observedIds = [];
  const canonicalRecords = [];
  for (const record of records) {
    if (!exactKeys(record, [
      "occurrenceId",
      "captureStatus",
      "requestProvenanceSha256",
      "candidate",
      "expectedSelectionSha256",
    ])) throw new Error("current media export record does not use the closed batch shape");
    const active = allowedById.get(record.occurrenceId);
    if (
      active === undefined
      || record.requestProvenanceSha256 !== active.requestProvenanceSha256
      || record.expectedSelectionSha256 !== preconditionById.get(record.occurrenceId)
      || !MEDIA_STATUSES.includes(record.captureStatus)
    ) throw new Error("current media export record is not policy- and selector-bound");
    observedIds.push(record.occurrenceId);
    if (record.captureStatus === "success") {
      validateCurrentMediaCandidate(
        record.candidate,
        record.occurrenceId,
        record.requestProvenanceSha256,
      );
    } else if (record.candidate !== null) {
      throw new Error("failed current media export record carries a candidate");
    }
    canonicalRecords.push({
      occurrenceId: record.occurrenceId,
      captureStatus: record.captureStatus,
      requestProvenanceSha256: record.requestProvenanceSha256,
      candidate: record.candidate === null ? null : { ...record.candidate },
      expectedSelectionSha256: record.expectedSelectionSha256,
    });
  }
  if (JSON.stringify(observedIds) !== JSON.stringify(expectedIds)) {
    throw new Error("current media export records are not in manifest order");
  }
  return canonicalRecords;
}

function assertUrlFreeGraphResult(graphResult) {
  const visit = (value, key = "") => {
    if (/^(?:url|endpoint|authorization|cookie|credential|secret)$/iu.test(key)) {
      throw new Error("graph export result contains a forbidden transport field");
    }
    if (typeof value === "string" && (value.includes("://") || /graphs\.verdify\.ai/iu.test(value))) {
      throw new Error("graph export result contains a forbidden transport value");
    }
    if (Array.isArray(value)) {
      for (const item of value) visit(item);
      return;
    }
    if (value !== null && typeof value === "object") {
      for (const [childKey, child] of Object.entries(value)) visit(child, childKey);
    }
  };
  visit(graphResult);
}

function deterministicBatchId({
  policySha256,
  sourceOccurrenceManifestSha256,
  reportingFeedSha256,
  reportingDatasourceIdentitySha256: datasourceIdentitySha256,
  exportedAt,
  expectedSelectionSha256,
  graphs,
  currentMedia,
}) {
  const identity = {
    contract: "verdify.lab-graph-occurrence-batch-identity",
    schemaVersion: 1,
    policySha256,
    sourceOccurrenceManifestSha256,
    reportingFeedSha256,
    reportingDatasourceIdentitySha256: datasourceIdentitySha256,
    exportedAt,
    expectedSelectionSha256,
    graphs,
    currentMedia,
  };
  return `batch_graph_${sha256(canonicalBytes(identity)).slice(0, 32)}`;
}

/**
 * Compose an already-validated, injected graph renderer into the exact documents
 * consumed by executeOccurrenceExportBatch(). This function has no default
 * transport, endpoint, datasource identity, credential, store, or activation.
 */
export async function assembleGraphOccurrenceExportBatch({
  policy,
  manifest,
  manifestSha256,
  reportingFeed,
  reportingDatasourceIdentity,
  outputRoot,
  renderer,
  selectorPreconditions,
  currentMediaRecords,
  now = () => new Date().toISOString(),
  timeoutMs,
  settlementGraceMs,
  concurrency,
  fileOperations,
}) {
  const discovered = validatePolicyManifestBinding(policy, manifest, manifestSha256);
  if (
    discovered.graphs.length !== EXPECTED_GRAPH_COUNT
    || discovered.currentMedia.length !== EXPECTED_CURRENT_MEDIA_COUNT
  ) throw new Error(`graph batch assembly requires exactly ${EXPECTED_GRAPH_COUNT}+${EXPECTED_CURRENT_MEDIA_COUNT} occurrences`);
  if (
    policy.activation.state !== "active"
    || policy.activation.activatedBy !== "direct-task"
    || !policy.activation.activatedAt
  ) throw new Error("graph batch assembly policy is not activated");
  if (typeof now !== "function") throw new Error("graph batch assembly clock is invalid");
  const selectors = validateSelectorPreconditions(selectorPreconditions, discovered);
  const mediaRecords = validateCurrentMediaRecords(
    currentMediaRecords,
    policy,
    discovered,
    selectors,
  );
  const datasourceIdentitySha256 = reportingDatasourceIdentitySha256(reportingDatasourceIdentity);
  const reportingFeedSha256 = reportingFeedEnvelopeSha256(reportingFeed);
  const graphResult = await produceGraphExportCandidates({
    policy,
    manifest,
    manifestSha256,
    reportingFeed,
    reportingDatasourceIdentity,
    outputRoot,
    renderer,
    now,
    timeoutMs,
    settlementGraceMs,
    concurrency,
    fileOperations,
  });
  assertUrlFreeGraphResult(graphResult);
  if (
    graphResult.graphs.length !== EXPECTED_GRAPH_COUNT
    || JSON.stringify(graphResult.graphs.map(({ occurrenceId }) => occurrenceId))
      !== JSON.stringify(discovered.graphs.map(({ occurrenceId }) => occurrenceId))
  ) throw new Error("graph export result is not complete and manifest-ordered");
  const exportedAt = canonicalInstant(now(), "graph occurrence batch export time");
  for (const record of [...graphResult.graphs, ...mediaRecords]) {
    if (record.candidate !== null && Date.parse(record.candidate.capturedAt) > Date.parse(exportedAt)) {
      throw new Error("occurrence candidate was captured after the export batch");
    }
  }
  const policySha256 = occurrenceExportPolicySha256(policy);
  const batchId = deterministicBatchId({
    policySha256,
    sourceOccurrenceManifestSha256: manifestSha256,
    reportingFeedSha256,
    reportingDatasourceIdentitySha256: datasourceIdentitySha256,
    exportedAt,
    expectedSelectionSha256: selectors.aggregateExpectedSelectionSha256,
    graphs: graphResult.graphs,
    currentMedia: mediaRecords,
  });
  const exportBatch = {
    contract: "verdify.lab-occurrence-export-batch",
    schemaVersion: 2,
    batchId,
    policyVersion: policy.policyVersion,
    policySha256,
    sourceOccurrenceManifestSha256: manifestSha256,
    reportingFeed: structuredClone(reportingFeed),
    exportedAt,
    expectedSelectionSha256: selectors.aggregateExpectedSelectionSha256,
    graphs: graphResult.graphs.map((record) => ({
      occurrenceId: record.occurrenceId,
      probeStatus: record.probeStatus,
      candidate: record.candidate === null ? null : { ...record.candidate },
    })),
    currentMedia: mediaRecords,
  };
  validateOccurrenceExportBatch(exportBatch, policy, exportedAt);
  if (JSON.stringify(graphResult.graphs) !== JSON.stringify(exportBatch.graphs)) {
    throw new Error("graph export result and occurrence batch diverged");
  }
  const graphResultSha256 = sha256(canonicalBytes(graphResult));
  const exportBatchSha256 = sha256(canonicalBytes(exportBatch));
  return {
    contract: "verdify.lab-graph-occurrence-batch-assembly",
    schemaVersion: 1,
    reportingDatasourceIdentitySha256: datasourceIdentitySha256,
    graphResult,
    graphResultSha256,
    exportBatch,
    exportBatchSha256,
  };
}

export const graphExportBatchContract = Object.freeze({
  expectedGraphCount: EXPECTED_GRAPH_COUNT,
  expectedCurrentMediaCount: EXPECTED_CURRENT_MEDIA_COUNT,
  currentMediaStatuses: MEDIA_STATUSES,
  selectorPreconditions: Object.freeze({
    contract: "verdify.lab-occurrence-export-selector-preconditions",
    schemaVersion: 1,
  }),
  result: Object.freeze({
    contract: "verdify.lab-graph-occurrence-batch-assembly",
    schemaVersion: 1,
  }),
});
