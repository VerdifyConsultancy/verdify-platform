import { createHash } from "node:crypto";

import {
  assembleGraphOccurrenceExportBatch,
  graphExportBatchContract,
} from "./graph-export-batch.mjs";
import {
  occurrenceExportPolicySha256,
  reportingFeedEnvelopeSha256,
  validatePolicyManifestBinding,
} from "./occurrence-export-contract.mjs";
import {
  graphExportProducerContract,
  planGraphExportRequests,
  reportingDatasourceIdentitySha256,
} from "./graph-export-producer.mjs";
import {
  cameraExportProducerContract,
  captureCameraOccurrence,
  validateCameraExportRequest,
} from "./camera-export-producer.mjs";
import { occurrenceProducerRunnerContract } from "./occurrence-producer-contracts.mjs";
import { validateOccurrenceProducerResult } from "./occurrence-producer-result-contract.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u;
const EXPECTED_GRAPH_COUNT = occurrenceProducerRunnerContract.expectedGraphCount;
const EXPECTED_CURRENT_MEDIA_COUNT = occurrenceProducerRunnerContract.expectedCurrentMediaCount;
const EXPECTED_LEGACY_OVERRIDE_COUNT = occurrenceProducerRunnerContract.expectedLegacyOverrideCount;
const EXPECTED_REPORTING_DEFAULT_COUNT = occurrenceProducerRunnerContract.expectedReportingDefaultCount;
const DEFAULT_CAMERA_CONCURRENCY = occurrenceProducerRunnerContract.defaultCameraConcurrency;
const MAX_CAMERA_CONCURRENCY = occurrenceProducerRunnerContract.maxCameraConcurrency;
const DEFAULT_CAMERA_MAX_ATTEMPTS = occurrenceProducerRunnerContract.defaultCameraMaxAttempts;
const MAX_CAMERA_MAX_ATTEMPTS = occurrenceProducerRunnerContract.maxCameraMaxAttempts;
const RETRYABLE_CAMERA_STATUSES = occurrenceProducerRunnerContract.retryableCameraStatuses;

if (
  EXPECTED_GRAPH_COUNT !== graphExportBatchContract.expectedGraphCount
  || EXPECTED_CURRENT_MEDIA_COUNT !== graphExportBatchContract.expectedCurrentMediaCount
) throw new Error("occurrence producer runner and export batch contracts disagree");

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

function validateActivePolicy(policy) {
  if (
    policy.activation.state !== "active"
    || policy.activation.activatedBy !== "direct-task"
    || !policy.activation.activatedAt
  ) throw new Error("occurrence producer policy is not activated");
}

function validateOutputRoot(outputRoot) {
  if (
    typeof outputRoot !== "string"
    || outputRoot.length === 0
    || outputRoot.length > 4096
    || /[\u0000-\u001f\u007f]/u.test(outputRoot)
  ) throw new Error("occurrence producer output root is invalid");
}

function validateBounds({
  graphConcurrency,
  graphTimeoutMs,
  graphSettlementGraceMs,
  cameraConcurrency,
  cameraTimeoutMs,
  cameraMaxAttempts,
}) {
  if (
    !Number.isSafeInteger(graphConcurrency)
    || graphConcurrency < 1
    || graphConcurrency > graphExportProducerContract.maxConcurrency
  ) throw new Error("occurrence producer graph concurrency is invalid");
  if (
    !Number.isSafeInteger(graphTimeoutMs)
    || graphTimeoutMs < 1
    || graphTimeoutMs > graphExportProducerContract.maxTimeoutMs
  ) throw new Error("occurrence producer graph timeout is invalid");
  if (
    !Number.isSafeInteger(graphSettlementGraceMs)
    || graphSettlementGraceMs < 1
    || graphSettlementGraceMs > graphExportProducerContract.maxSettlementGraceMs
  ) throw new Error("occurrence producer graph settlement grace is invalid");
  if (
    !Number.isSafeInteger(cameraConcurrency)
    || cameraConcurrency < 1
    || cameraConcurrency > MAX_CAMERA_CONCURRENCY
  ) throw new Error("occurrence producer camera concurrency is invalid");
  if (
    !Number.isSafeInteger(cameraTimeoutMs)
    || cameraTimeoutMs < 1
    || cameraTimeoutMs > cameraExportProducerContract.maxTimeoutMs
  ) throw new Error("occurrence producer camera timeout is invalid");
  if (
    !Number.isSafeInteger(cameraMaxAttempts)
    || cameraMaxAttempts < 1
    || cameraMaxAttempts > MAX_CAMERA_MAX_ATTEMPTS
  ) throw new Error("occurrence producer camera attempt limit is invalid");
}

function validateSelectorReader(reader) {
  if (
    !exactKeys(reader, ["contract", "schemaVersion", "read"])
    || reader.contract !== "verdify.lab-occurrence-selector-precondition-reader"
    || reader.schemaVersion !== 1
    || typeof reader.read !== "function"
  ) throw new Error("occurrence selector reader does not use the closed v1 contract");
  return reader;
}

function validateRenderer(renderer, reportingFeedSha256, datasourceIdentitySha256) {
  if (
    !exactKeys(renderer, [
      "contract",
      "schemaVersion",
      "sourceClass",
      "anonymousAccess",
      "reportingFeedSha256",
      "reportingDatasourceIdentitySha256",
      "abortCooperation",
      "render",
    ])
    || renderer.contract !== graphExportProducerContract.renderer.contract
    || renderer.schemaVersion !== graphExportProducerContract.renderer.schemaVersion
    || renderer.sourceClass !== graphExportProducerContract.renderer.sourceClass
    || renderer.anonymousAccess !== false
    || renderer.reportingFeedSha256 !== reportingFeedSha256
    || renderer.reportingDatasourceIdentitySha256 !== datasourceIdentitySha256
    || renderer.abortCooperation !== "settle-within-grace-after-abort"
    || typeof renderer.render !== "function"
  ) throw new Error("occurrence producer renderer does not use the closed feed-bound contract");
  return renderer;
}

function validateSelectorPreconditions(value, discovered) {
  if (
    !exactKeys(value, [
      "contract",
      "schemaVersion",
      "aggregateExpectedSelectionSha256",
      "currentMedia",
    ])
    || value.contract !== graphExportBatchContract.selectorPreconditions.contract
    || value.schemaVersion !== graphExportBatchContract.selectorPreconditions.schemaVersion
    || !Array.isArray(value.currentMedia)
    || value.currentMedia.length !== EXPECTED_CURRENT_MEDIA_COUNT
  ) throw new Error("occurrence selector reader returned an invalid precondition snapshot");
  nullableDigest(value.aggregateExpectedSelectionSha256, "aggregate selection precondition");
  const expectedIds = discovered.currentMedia.map(({ occurrenceId }) => occurrenceId);
  const observedIds = [];
  for (const record of value.currentMedia) {
    if (!exactKeys(record, ["occurrenceId", "expectedSelectionSha256"])) {
      throw new Error("occurrence selector reader returned an invalid current-media precondition");
    }
    observedIds.push(record.occurrenceId);
    nullableDigest(record.expectedSelectionSha256, "current-media selection precondition");
  }
  if (JSON.stringify(observedIds) !== JSON.stringify(expectedIds)) {
    throw new Error("occurrence selector reader returned preconditions outside manifest order");
  }
  return value;
}

function datasourceBindingProof(plan) {
  const legacyByDashboard = graphExportProducerContract.legacyDatasourceDashboardUids.map((uid) => ({
    uid,
    count: plan.requests.filter(({ target }) => (
      target.uid === uid
      && target.datasourceBinding.mode === "legacy-dashboard-dedicated-override"
    )).length,
  }));
  const legacyOverrideCount = legacyByDashboard.reduce((total, { count }) => total + count, 0);
  const reportingDefaultCount = plan.requests.filter(({ target }) => (
    target.datasourceBinding.mode === "reporting-tier-dedicated-default"
  )).length;
  const knownModes = legacyOverrideCount + reportingDefaultCount;
  if (
    plan.requests.length !== EXPECTED_GRAPH_COUNT
    || legacyOverrideCount !== EXPECTED_LEGACY_OVERRIDE_COUNT
    || reportingDefaultCount !== EXPECTED_REPORTING_DEFAULT_COUNT
    || knownModes !== plan.requests.length
  ) throw new Error("graph datasource bindings are not the exact 40 legacy and 103 default plan");
  return {
    contract: "verdify.lab-graph-datasource-binding-proof",
    schemaVersion: 1,
    graphCount: plan.requests.length,
    legacyOverrideCount,
    reportingDefaultCount,
    legacyByDashboard,
    planSha256: sha256(canonicalBytes(plan)),
  };
}

function classifyCameraFailure(error) {
  const message = error instanceof Error ? error.message : "";
  if (/time limit/iu.test(message)) return "timeout";
  if (
    /camera export (?:policy|request)|public allowlist|predates policy|selection precondition/iu.test(message)
  ) return "policy-rejected";
  if (
    /JPEG|sanitized camera PNG|dimensions|decoded|release contract|invalid framing|critical chunk/iu.test(message)
  ) return "decode-error";
  if (
    /camera (?:transport|response)|HTTP|redirect|MIME|byte limit|content length|body/iu.test(message)
  ) return "http-error";
  return "missing";
}

function failedCameraRecord(request, captureStatus) {
  return {
    occurrenceId: request.occurrenceId,
    captureStatus,
    requestProvenanceSha256: request.requestProvenanceSha256,
    candidate: null,
    expectedSelectionSha256: request.expectedSelectionSha256,
  };
}

async function captureCameraWithRetries({
  policy,
  request,
  outputRoot,
  transport,
  now,
  timeoutMs,
  maxAttempts,
  fileOperations,
}) {
  let captureStatus = "missing";
  let attempts = 0;
  while (attempts < maxAttempts) {
    attempts += 1;
    try {
      const result = await captureCameraOccurrence({
        policy,
        request,
        outputRoot,
        transport,
        now,
        timeoutMs,
        fileOperations,
      });
      return {
        record: result.batchRecord,
        attempts,
      };
    } catch (error) {
      captureStatus = classifyCameraFailure(error);
      if (!RETRYABLE_CAMERA_STATUSES.includes(captureStatus)) break;
    }
  }
  return {
    record: failedCameraRecord(request, captureStatus),
    attempts,
  };
}

async function captureCurrentMedia({
  policy,
  requests,
  outputRoot,
  transport,
  now,
  timeoutMs,
  maxAttempts,
  concurrency,
  fileOperations,
}) {
  const results = new Array(requests.length);
  let nextIndex = 0;
  async function worker() {
    while (nextIndex < requests.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await captureCameraWithRetries({
        policy,
        request: requests[index],
        outputRoot,
        transport,
        now,
        timeoutMs,
        maxAttempts,
        fileOperations,
      });
    }
  }
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
  return results;
}

function assertTransportFreeResult(result, rawDatasourceIdentity) {
  const hasRawDatasourceIdentity = (value) => (
    typeof rawDatasourceIdentity === "string"
    && rawDatasourceIdentity.length > 0
    && value.includes(rawDatasourceIdentity)
  );
  const visit = (value, key = "") => {
    if (/^(?:url|endpoint|authorization|cookie|secret|credential)$/iu.test(key)) {
      throw new Error("occurrence producer result contains a transport field");
    }
    if (
      typeof value === "string"
      && (
        value.includes("://")
        || hasRawDatasourceIdentity(value)
        || /graphs\.verdify\.ai/iu.test(value)
      )
    ) throw new Error("occurrence producer result contains transport identity");
    if (Array.isArray(value)) {
      for (const child of value) visit(child);
      return;
    }
    if (value !== null && typeof value === "object") {
      for (const [childKey, child] of Object.entries(value)) visit(child, childKey);
    }
  };
  visit(result);
}

/**
 * Run the complete 143 graph + 2 current-media producer path with injected
 * offline dependencies. The function has no default transport, selector
 * reader, endpoint, credential, store operation, or activation surface.
 */
export async function runOccurrenceProducer({
  policy,
  manifest,
  manifestSha256,
  reportingFeed,
  reportingDatasourceIdentity,
  outputRoot,
  renderer,
  cameraTransport,
  selectorPreconditionReader,
  now = () => new Date().toISOString(),
  graphConcurrency = graphExportProducerContract.defaultConcurrency,
  graphTimeoutMs = graphExportProducerContract.defaultTimeoutMs,
  graphSettlementGraceMs = graphExportProducerContract.defaultSettlementGraceMs,
  cameraConcurrency = DEFAULT_CAMERA_CONCURRENCY,
  cameraTimeoutMs = cameraExportProducerContract.defaultTimeoutMs,
  cameraMaxAttempts = DEFAULT_CAMERA_MAX_ATTEMPTS,
  fileOperations,
}) {
  // The policy/manifest/feed/plan gate intentionally precedes every injected
  // reader, renderer, camera transport, and filesystem operation.
  const policySnapshot = structuredClone(policy);
  const manifestSnapshot = structuredClone(manifest);
  const reportingFeedSnapshot = structuredClone(reportingFeed);
  const discovered = validatePolicyManifestBinding(
    policySnapshot,
    manifestSnapshot,
    manifestSha256,
  );
  if (
    discovered.graphs.length !== EXPECTED_GRAPH_COUNT
    || discovered.currentMedia.length !== EXPECTED_CURRENT_MEDIA_COUNT
  ) throw new Error(`occurrence producer requires exactly ${EXPECTED_GRAPH_COUNT}+${EXPECTED_CURRENT_MEDIA_COUNT} occurrences`);
  validateActivePolicy(policySnapshot);
  const policySha256 = occurrenceExportPolicySha256(policySnapshot);
  const reportingFeedSha256 = reportingFeedEnvelopeSha256(reportingFeedSnapshot);
  const datasourceIdentitySha256 = reportingDatasourceIdentitySha256(reportingDatasourceIdentity);
  const plan = planGraphExportRequests({
    policy: policySnapshot,
    manifest: manifestSnapshot,
    manifestSha256,
    reportingFeedSha256,
    reportingDatasourceIdentitySha256: datasourceIdentitySha256,
  });
  const bindingProof = datasourceBindingProof(plan);
  assertTransportFreeResult({
    policyVersion: policySnapshot.policyVersion,
    reportingFeed: reportingFeedSnapshot,
    datasourceBindingProof: bindingProof,
  }, reportingDatasourceIdentity);

  validateOutputRoot(outputRoot);
  if (typeof now !== "function" || typeof cameraTransport !== "function") {
    throw new Error("occurrence producer dependency is invalid");
  }
  validateBounds({
    graphConcurrency,
    graphTimeoutMs,
    graphSettlementGraceMs,
    cameraConcurrency,
    cameraTimeoutMs,
    cameraMaxAttempts,
  });
  const validatedRenderer = Object.freeze({
    ...validateRenderer(renderer, reportingFeedSha256, datasourceIdentitySha256),
  });
  const selectorReader = validateSelectorReader(selectorPreconditionReader);

  const readRequest = {
    contract: "verdify.lab-occurrence-selector-precondition-read-request",
    schemaVersion: 1,
    policySha256,
    sourceOccurrenceManifestSha256: manifestSha256,
    currentMediaOccurrenceIds: discovered.currentMedia.map(({ occurrenceId }) => occurrenceId),
  };
  const selectorPreconditions = validateSelectorPreconditions(
    structuredClone(await selectorReader.read(readRequest)),
    discovered,
  );
  const selectorPreconditionsSha256 = sha256(canonicalBytes(selectorPreconditions));
  const requestedAt = canonicalInstant(now(), "occurrence producer request time");
  const selectorById = new Map(selectorPreconditions.currentMedia.map((record) => [
    record.occurrenceId,
    record.expectedSelectionSha256,
  ]));
  const cameraSourceById = new Map(policySnapshot.cameraUpstream.sources.map((source) => [
    source.occurrenceId,
    source,
  ]));
  const cameraRequests = discovered.currentMedia.map(({ occurrenceId }) => {
    const source = cameraSourceById.get(occurrenceId);
    const request = {
      contract: "verdify.lab-camera-export-request",
      schemaVersion: 1,
      occurrenceId,
      requestProvenanceSha256: source?.requestProvenanceSha256,
      method: "GET",
      url: source?.url,
      redirectsAllowed: false,
      authorization: "forbidden",
      cookies: "forbidden",
      requestedAt,
      expectedSelectionSha256: selectorById.get(occurrenceId),
    };
    return validateCameraExportRequest(request, policySnapshot);
  });

  const cameraResults = await captureCurrentMedia({
    policy: policySnapshot,
    requests: cameraRequests,
    outputRoot,
    transport: cameraTransport,
    now,
    timeoutMs: cameraTimeoutMs,
    maxAttempts: cameraMaxAttempts,
    concurrency: cameraConcurrency,
    fileOperations,
  });
  const assembly = await assembleGraphOccurrenceExportBatch({
    policy: policySnapshot,
    manifest: manifestSnapshot,
    manifestSha256,
    reportingFeed: reportingFeedSnapshot,
    reportingDatasourceIdentity,
    outputRoot,
    renderer: validatedRenderer,
    selectorPreconditions,
    currentMediaRecords: cameraResults.map(({ record }) => record),
    now,
    timeoutMs: graphTimeoutMs,
    settlementGraceMs: graphSettlementGraceMs,
    concurrency: graphConcurrency,
    fileOperations,
  });
  const result = {
    contract: "verdify.lab-occurrence-producer-run",
    schemaVersion: 1,
    policySha256,
    sourceOccurrenceManifestSha256: manifestSha256,
    reportingFeedSha256,
    selectorPreconditionsSha256,
    datasourceBindingProof: bindingProof,
    executionBounds: {
      graphConcurrency,
      graphTimeoutMs,
      graphSettlementGraceMs,
      graphMaxAttempts: 1,
      cameraConcurrency,
      cameraTimeoutMs,
      cameraMaxAttempts,
    },
    cameraAttempts: cameraResults.map(({ record, attempts }) => ({
      occurrenceId: record.occurrenceId,
      attempts,
      captureStatus: record.captureStatus,
    })),
    graphResult: assembly.graphResult,
    graphResultSha256: assembly.graphResultSha256,
    exportBatch: assembly.exportBatch,
    exportBatchSha256: assembly.exportBatchSha256,
  };
  validateOccurrenceProducerResult(result, {
    policySha256,
    sourceOccurrenceManifestSha256: manifestSha256,
    sourceId: reportingFeedSnapshot.sourceId,
    sourceWatermark: reportingFeedSnapshot.sourceWatermark,
    sourceWatermarkAt: reportingFeedSnapshot.sourceWatermarkAt,
  });
  assertTransportFreeResult(result, reportingDatasourceIdentity);
  return result;
}

export { occurrenceProducerRunnerContract };
