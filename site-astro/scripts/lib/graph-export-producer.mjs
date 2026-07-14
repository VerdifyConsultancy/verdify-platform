import { createHash } from "node:crypto";
import path from "node:path";

import sharp from "sharp";

import { graphExportProducerContract } from "./occurrence-producer-contracts.mjs";
import {
  occurrenceExportPolicySha256,
  reportingFeedEnvelopeSha256,
  validatePolicyManifestBinding,
} from "./occurrence-export-contract.mjs";
import {
  canonicalCandidateDirectory,
  occurrenceCandidateFileOperations,
  persistOccurrenceCandidate,
} from "./occurrence-candidate-store.mjs";
import { decodePng } from "./png-validation.mjs";

const EXPECTED_GRAPH_COUNT = graphExportProducerContract.expectedGraphCount;
const MAX_CONCURRENCY = graphExportProducerContract.maxConcurrency;
const DEFAULT_CONCURRENCY = graphExportProducerContract.defaultConcurrency;
const MAX_TIMEOUT_MS = graphExportProducerContract.maxTimeoutMs;
const DEFAULT_TIMEOUT_MS = graphExportProducerContract.defaultTimeoutMs;
const MAX_SETTLEMENT_GRACE_MS = graphExportProducerContract.maxSettlementGraceMs;
const DEFAULT_SETTLEMENT_GRACE_MS = graphExportProducerContract.defaultSettlementGraceMs;
const SHA256_RE = /^[0-9a-f]{64}$/;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const WAIT_TIMEOUT = Symbol("wait-timeout");
const LEGACY_DATASOURCE_DASHBOARD_UIDS = graphExportProducerContract.legacyDatasourceDashboardUids;
const LEGACY_DATASOURCE_DASHBOARD_UID_SET = new Set(LEGACY_DATASOURCE_DASHBOARD_UIDS);
const FORBIDDEN_DATASOURCE_IDENTITIES = new Set([
  "P44368ADAD746BC27",
  "verdify-tsdb",
]);

class GraphProbeError extends Error {
  constructor(probeStatus, message) {
    super(message);
    this.probeStatus = probeStatus;
  }
}

class RendererContractError extends Error {
  constructor(failure) {
    super(`graph renderer contract failed: ${failure}`);
    this.failure = failure;
  }
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

function cloneQuery(value) {
  return Object.fromEntries(Object.entries(value).map(([key, values]) => [key, [...values]]));
}

export function reportingDatasourceIdentitySha256(identity) {
  if (
    typeof identity !== "string"
    || identity.length < 8
    || identity.length > 256
    || /[\u0000-\u0020\u007f]/u.test(identity)
    || /(?:^|[-_])anonymous(?:$|[-_])/iu.test(identity)
    || identity.includes("://")
    || FORBIDDEN_DATASOURCE_IDENTITIES.has(identity)
  ) throw new Error("dedicated reporting datasource identity is invalid");
  return createHash("sha256")
    .update(Buffer.from(`${JSON.stringify({
      contract: "verdify.operator-reporting-datasource-identity",
      schemaVersion: 1,
      identity,
    }, null, 2)}\n`))
    .digest("hex");
}

export function planGraphExportRequests({
  policy,
  manifest,
  manifestSha256,
  reportingFeedSha256,
  reportingDatasourceIdentitySha256: datasourceIdentitySha256,
}) {
  const discovered = validatePolicyManifestBinding(policy, manifest, manifestSha256);
  if (!SHA256_RE.test(reportingFeedSha256)) throw new Error("graph export reporting feed digest is invalid");
  const effectiveDatasourceIdentitySha256 = datasourceIdentitySha256 === undefined
    && policy.activation.state === "blocked"
    ? "0".repeat(64)
    : datasourceIdentitySha256;
  if (!SHA256_RE.test(effectiveDatasourceIdentitySha256)) {
    throw new Error("graph export reporting datasource identity digest is invalid");
  }
  const canonicalManifestSha256 = createHash("sha256")
    .update(Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`))
    .digest("hex");
  if (canonicalManifestSha256 !== manifestSha256) {
    throw new Error("graph export manifest object does not match its canonical byte digest");
  }
  if (discovered.graphs.length !== EXPECTED_GRAPH_COUNT || policy.graphs.length !== EXPECTED_GRAPH_COUNT) {
    throw new Error(`graph export plan must contain exactly ${EXPECTED_GRAPH_COUNT} requests`);
  }
  const approvedById = new Map(policy.graphs.map((record) => [record.occurrenceId, record.occurrenceSha256]));
  const requests = discovered.graphs.map((occurrence) => ({
    contract: "verdify.lab-graph-render-request",
    schemaVersion: 3,
    occurrenceId: occurrence.occurrenceId,
    occurrenceSha256: approvedById.get(occurrence.occurrenceId),
    reportingFeedSha256,
    target: {
      uid: occurrence.uid,
      panelId: occurrence.panelId,
      query: cloneQuery(occurrence.query),
      variables: cloneQuery(occurrence.variables),
      timeRange: { ...occurrence.timeRange },
      datasourceBinding: {
        mode: datasourceIdentitySha256 === undefined
          ? "blocked-offline-unbound"
          : LEGACY_DATASOURCE_DASHBOARD_UID_SET.has(occurrence.uid)
            ? "legacy-dashboard-dedicated-override"
            : "reporting-tier-dedicated-default",
        identitySha256: effectiveDatasourceIdentitySha256,
      },
    },
  }));
  return {
    contract: "verdify.lab-graph-export-plan",
    schemaVersion: 3,
    policyVersion: policy.policyVersion,
    policySha256: occurrenceExportPolicySha256(policy),
    sourceOccurrenceManifestSha256: manifestSha256,
    reportingFeedSha256,
    requests,
  };
}

function assertApprovedPolicy(policy) {
  if (
    policy.activation.state !== "approved"
    || policy.activation.approvedBy !== "jason"
    || !policy.activation.approvedAt
  ) throw new Error("graph export policy is not activated");
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
    || renderer.contract !== "verdify.lab-graph-renderer"
    || renderer.schemaVersion !== 3
    || renderer.sourceClass !== "operator-owned-reporting-tier"
    || renderer.anonymousAccess !== false
    || renderer.reportingFeedSha256 !== reportingFeedSha256
    || renderer.reportingDatasourceIdentitySha256 !== datasourceIdentitySha256
    || renderer.abortCooperation !== "settle-within-grace-after-abort"
    || typeof renderer.render !== "function"
  ) throw new Error("graph exporter renderer does not use the dedicated feed-bound abort-cooperative v3 contract");
  return renderer;
}

function outcome(promise) {
  return promise.then(
    (value) => ({ state: "fulfilled", value }),
    (error) => ({ state: "rejected", error }),
  );
}

async function waitWithin(outcomePromise, timeoutMs) {
  if (timeoutMs <= 0) return WAIT_TIMEOUT;
  let timeout;
  try {
    return await Promise.race([
      outcomePromise,
      new Promise((resolve) => {
        timeout = setTimeout(() => resolve(WAIT_TIMEOUT), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timeout);
  }
}

function remainingMilliseconds(deadline) {
  return Math.max(0, deadline - Date.now());
}

function emptyCleanup() {
  return Promise.resolve();
}

function unsupportedCleanup() {
  return Promise.reject(new Error("graph renderer body does not expose bounded cleanup"));
}

function rawBodyCleanup(body) {
  try {
    if (Buffer.isBuffer(body) || body instanceof Uint8Array || body === undefined || body === null) {
      return emptyCleanup;
    }
    if (body.cancel instanceof Function) return () => body.cancel();
    if (body[Symbol.asyncIterator] instanceof Function) {
      let iterator;
      return async () => {
        iterator ??= body[Symbol.asyncIterator]();
        if (!(iterator?.return instanceof Function)) return unsupportedCleanup();
        await iterator.return();
      };
    }
    if (body.return instanceof Function) return () => body.return();
    return unsupportedCleanup;
  } catch (error) {
    return () => Promise.reject(error);
  }
}

async function requireBoundedCleanup({ cleanup, pendingOutcome = null, graceMs }) {
  const deadline = Date.now() + graceMs;
  const cleanupResult = await waitWithin(
    outcome(Promise.resolve().then(cleanup)),
    remainingMilliseconds(deadline),
  );
  if (cleanupResult === WAIT_TIMEOUT) throw new RendererContractError("body-cleanup-timeout");
  if (cleanupResult.state !== "fulfilled") throw new RendererContractError("body-cleanup-rejected");
  if (pendingOutcome !== null) {
    const pendingResult = await waitWithin(pendingOutcome, remainingMilliseconds(deadline));
    if (pendingResult === WAIT_TIMEOUT) throw new RendererContractError("body-settlement-timeout");
  }
}

async function settleRendererAfterAbort(renderOutcome, graceMs) {
  const deadline = Date.now() + graceMs;
  const settled = await waitWithin(renderOutcome, remainingMilliseconds(deadline));
  if (settled === WAIT_TIMEOUT) throw new RendererContractError("renderer-settlement-timeout");
  if (settled.state === "fulfilled") {
    await requireBoundedCleanup({
      cleanup: rawBodyCleanup(settled.value?.body),
      graceMs: remainingMilliseconds(deadline),
    });
  }
}

function validateRendererResponse(response) {
  if (!exactKeys(response, ["status", "contentType", "contentLength", "body"])) {
    throw new GraphProbeError("http-error", "graph renderer response does not use the closed v1 shape");
  }
  if (!Number.isSafeInteger(response.status) || response.status !== 200) {
    throw new GraphProbeError("http-error", "graph renderer did not return HTTP 200");
  }
  if (
    typeof response.contentType !== "string"
    || response.contentType.toLowerCase().split(";", 1)[0].trim() !== "image/png"
  ) throw new GraphProbeError("http-error", "graph renderer response MIME type is not image/png");
  if (
    response.contentLength !== null
    && (!Number.isSafeInteger(response.contentLength) || response.contentLength < 0)
  ) throw new GraphProbeError("http-error", "graph renderer response content length is invalid");
}

function appendChunk(chunks, chunk, length, maximumBytes) {
  if (!(Buffer.isBuffer(chunk) || chunk instanceof Uint8Array) || chunk.length === 0) {
    throw new GraphProbeError("http-error", "graph renderer response body contains an invalid chunk");
  }
  const nextLength = length + chunk.length;
  if (nextLength > maximumBytes) {
    throw new GraphProbeError("http-error", "graph renderer response is outside the byte limit");
  }
  chunks.push(Buffer.from(chunk));
  return nextLength;
}

function boundedBodyOperation(body, maximumBytes) {
  if (Buffer.isBuffer(body) || body instanceof Uint8Array) {
    return {
      read: async () => {
        if (body.length === 0 || body.length > maximumBytes) {
          throw new GraphProbeError("http-error", "graph renderer response is outside the byte limit");
        }
        return Buffer.from(body);
      },
      cleanup: emptyCleanup,
    };
  }

  if (body?.getReader instanceof Function) {
    let reader;
    try {
      reader = body.getReader();
    } catch {
      throw new GraphProbeError("http-error", "graph renderer response body is not readable");
    }
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      reader.releaseLock();
    };
    return {
      read: async () => {
        const chunks = [];
        let length = 0;
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) {
              if (length === 0) {
                throw new GraphProbeError("http-error", "graph renderer response is outside the byte limit");
              }
              release();
              return Buffer.concat(chunks, length);
            }
            length = appendChunk(chunks, value, length, maximumBytes);
          }
        } catch (error) {
          if (error instanceof GraphProbeError) throw error;
          throw new GraphProbeError("http-error", "graph renderer response body could not be read");
        }
      },
      cleanup: async () => {
        try {
          await reader.cancel();
        } finally {
          release();
        }
      },
    };
  }
  if (!body || !(body[Symbol.asyncIterator] instanceof Function)) {
    throw new GraphProbeError("http-error", "graph renderer response body is not readable");
  }
  let iterator;
  try {
    iterator = body[Symbol.asyncIterator]();
  } catch {
    throw new GraphProbeError("http-error", "graph renderer response body is not readable");
  }
  if (!iterator || !(iterator.next instanceof Function)) {
    throw new GraphProbeError("http-error", "graph renderer response body is not readable");
  }
  return {
    read: async () => {
      const chunks = [];
      let length = 0;
      try {
        while (true) {
          const { done, value } = await iterator.next();
          if (done) {
            if (length === 0) {
              throw new GraphProbeError("http-error", "graph renderer response is outside the byte limit");
            }
            return Buffer.concat(chunks, length);
          }
          length = appendChunk(chunks, value, length, maximumBytes);
        }
      } catch (error) {
        if (error instanceof GraphProbeError) throw error;
        throw new GraphProbeError("http-error", "graph renderer response body could not be read");
      }
    },
    cleanup: async () => {
      if (!(iterator.return instanceof Function)) return unsupportedCleanup();
      await iterator.return();
    },
  };
}

async function timedRendererResponse({ renderer, request, maximumBytes, timeoutMs, settlementGraceMs }) {
  const abortController = new AbortController();
  const deadline = Date.now() + timeoutMs;
  const renderOutcome = outcome(Promise.resolve().then(() => renderer.render({
    request,
    signal: abortController.signal,
  })));
  try {
    const renderResult = await waitWithin(renderOutcome, remainingMilliseconds(deadline));
    if (renderResult === WAIT_TIMEOUT) {
      abortController.abort();
      await settleRendererAfterAbort(renderOutcome, settlementGraceMs);
      throw new GraphProbeError("timeout", "graph renderer exceeded the time limit");
    }
    if (renderResult.state !== "fulfilled") {
      throw new GraphProbeError("missing", "graph renderer did not return a response");
    }
    const response = renderResult.value;
    try {
      validateRendererResponse(response);
      if (response.contentLength !== null && response.contentLength > maximumBytes) {
        throw new GraphProbeError("http-error", "graph renderer response is outside the byte limit");
      }
    } catch (error) {
      abortController.abort();
      await requireBoundedCleanup({ cleanup: rawBodyCleanup(response?.body), graceMs: settlementGraceMs });
      throw error;
    }

    let bodyOperation;
    try {
      bodyOperation = boundedBodyOperation(response.body, maximumBytes);
    } catch (error) {
      abortController.abort();
      await requireBoundedCleanup({ cleanup: rawBodyCleanup(response.body), graceMs: settlementGraceMs });
      throw error;
    }
    const bodyOutcome = outcome(Promise.resolve().then(bodyOperation.read));
    const bodyResult = await waitWithin(bodyOutcome, remainingMilliseconds(deadline));
    if (bodyResult === WAIT_TIMEOUT) {
      abortController.abort();
      await requireBoundedCleanup({
        cleanup: bodyOperation.cleanup,
        pendingOutcome: bodyOutcome,
        graceMs: settlementGraceMs,
      });
      throw new GraphProbeError("timeout", "graph renderer exceeded the time limit");
    }
    if (bodyResult.state !== "fulfilled") {
      abortController.abort();
      await requireBoundedCleanup({ cleanup: bodyOperation.cleanup, graceMs: settlementGraceMs });
      throw bodyResult.error;
    }
    const bytes = bodyResult.value;
    if (response.contentLength !== null && response.contentLength !== bytes.length) {
      throw new GraphProbeError("http-error", "graph renderer response length does not match its bytes");
    }
    return bytes;
  } finally {
    if (!abortController.signal.aborted) abortController.abort();
  }
}

function stripPngAncillaryChunks(bytes) {
  if (!Buffer.isBuffer(bytes) || !bytes.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw new GraphProbeError("decode-error", "normalized graph PNG has invalid framing");
  }
  const chunks = [PNG_SIGNATURE];
  let offset = PNG_SIGNATURE.length;
  let ended = false;
  while (offset < bytes.length) {
    if (offset + 12 > bytes.length) throw new GraphProbeError("decode-error", "normalized graph PNG has invalid framing");
    const length = bytes.readUInt32BE(offset);
    const end = offset + 12 + length;
    if (end > bytes.length) throw new GraphProbeError("decode-error", "normalized graph PNG has invalid framing");
    const type = bytes.subarray(offset + 4, offset + 8).toString("ascii");
    if (!/^[A-Za-z]{4}$/.test(type)) throw new GraphProbeError("decode-error", "normalized graph PNG has invalid framing");
    if (["IHDR", "IDAT", "IEND"].includes(type)) chunks.push(bytes.subarray(offset, end));
    else if (type[0] === type[0].toUpperCase()) {
      throw new GraphProbeError("decode-error", "normalized graph PNG contains an unexpected critical chunk");
    }
    offset = end;
    if (type === "IEND") {
      ended = true;
      break;
    }
  }
  if (!ended || offset !== bytes.length) throw new GraphProbeError("decode-error", "normalized graph PNG has invalid framing");
  return Buffer.concat(chunks);
}

async function normalizeGraphPng(bytes, bounds) {
  const inputPixels = bounds.maxWidth * bounds.maxHeight;
  let metadata;
  try {
    metadata = await sharp(bytes, {
      failOn: "error",
      limitInputPixels: inputPixels,
      sequentialRead: true,
    }).metadata();
  } catch {
    throw new GraphProbeError("decode-error", "graph image could not be decoded within bounds");
  }
  if (
    metadata.format !== "png"
    || !Number.isSafeInteger(metadata.width)
    || !Number.isSafeInteger(metadata.height)
    || metadata.width < bounds.minWidth
    || metadata.width > bounds.maxWidth
    || metadata.height < bounds.minHeight
    || metadata.height > bounds.maxHeight
  ) throw new GraphProbeError("decode-error", "graph image dimensions are outside the approved bounds");

  let encoded;
  try {
    encoded = await sharp(bytes, {
      failOn: "error",
      limitInputPixels: inputPixels,
      sequentialRead: true,
    })
      .rotate()
      .removeAlpha()
      .toColourspace("srgb")
      .png({
        adaptiveFiltering: false,
        compressionLevel: 9,
        effort: 10,
        palette: false,
      })
      .toBuffer();
  } catch {
    throw new GraphProbeError("decode-error", "graph image could not be normalized within bounds");
  }
  const png = stripPngAncillaryChunks(encoded);
  if (png.length === 0 || png.length > bounds.maxBytes) {
    throw new GraphProbeError("decode-error", "normalized graph PNG is outside the byte limit");
  }
  let decoded;
  try {
    decoded = decodePng(png);
  } catch {
    throw new GraphProbeError("decode-error", "normalized graph PNG failed the release contract");
  }
  if (
    decoded.colorType !== 2
    || decoded.width < bounds.minWidth
    || decoded.width > bounds.maxWidth
    || decoded.height < bounds.minHeight
    || decoded.height > bounds.maxHeight
  ) throw new GraphProbeError("decode-error", "normalized graph PNG is outside the approved bounds");
  return png;
}

async function renderOne({
  request,
  policy,
  outputRoot,
  renderer,
  now,
  timeoutMs,
  settlementGraceMs,
  fileOperations,
}) {
  try {
    const source = await timedRendererResponse({
      renderer,
      request,
      maximumBytes: policy.imagePolicy.graphs.maxBytes,
      timeoutMs,
      settlementGraceMs,
    });
    const capturedAt = canonicalInstant(now(), "graph capture time");
    if (Date.parse(capturedAt) < Date.parse(policy.activation.approvedAt)) {
      throw new GraphProbeError("missing", "graph capture time predates policy activation");
    }
    const png = await normalizeGraphPng(source, policy.imagePolicy.graphs);
    const persisted = await persistOccurrenceCandidate({
      outputRoot,
      collection: "graphs",
      occurrenceId: request.occurrenceId,
      png,
      fileOperations,
      label: "graph candidate",
      collectionLabel: "graph",
    });
    return {
      occurrenceId: request.occurrenceId,
      probeStatus: "success",
      candidate: {
        relativePath: persisted.relativePath,
        mediaType: "image/png",
        capturedAt,
      },
    };
  } catch (error) {
    if (error instanceof RendererContractError) throw error;
    return {
      occurrenceId: request.occurrenceId,
      probeStatus: error instanceof GraphProbeError ? error.probeStatus : "missing",
      candidate: null,
    };
  }
}

export async function produceGraphExportCandidates({
  policy,
  manifest,
  manifestSha256,
  reportingFeed,
  reportingDatasourceIdentity,
  outputRoot,
  renderer,
  now = () => new Date().toISOString(),
  timeoutMs = DEFAULT_TIMEOUT_MS,
  settlementGraceMs = DEFAULT_SETTLEMENT_GRACE_MS,
  concurrency = DEFAULT_CONCURRENCY,
  fileOperations: fileOperationOverrides,
}) {
  const reportingFeedSha256 = reportingFeedEnvelopeSha256(reportingFeed);
  const datasourceIdentitySha256 = reportingDatasourceIdentitySha256(reportingDatasourceIdentity);
  const plan = planGraphExportRequests({
    policy,
    manifest,
    manifestSha256,
    reportingFeedSha256,
    reportingDatasourceIdentitySha256: datasourceIdentitySha256,
  });
  assertApprovedPolicy(policy);
  const validatedRenderer = validateRenderer(renderer, reportingFeedSha256, datasourceIdentitySha256);
  if (typeof now !== "function") throw new Error("graph exporter dependency is invalid");
  if (
    typeof outputRoot !== "string"
    || outputRoot.length === 0
    || outputRoot.length > 4096
    || /[\u0000-\u001f\u007f]/u.test(outputRoot)
  ) throw new Error("graph exporter output root is invalid");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > MAX_TIMEOUT_MS) {
    throw new Error("graph exporter timeout is invalid");
  }
  if (
    !Number.isSafeInteger(settlementGraceMs)
    || settlementGraceMs < 1
    || settlementGraceMs > MAX_SETTLEMENT_GRACE_MS
  ) throw new Error("graph exporter settlement grace is invalid");
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > MAX_CONCURRENCY) {
    throw new Error("graph exporter concurrency is invalid");
  }
  const fileOperations = occurrenceCandidateFileOperations(fileOperationOverrides, "graph candidate");
  const canonicalOutputRoot = await canonicalCandidateDirectory(
    path.resolve(outputRoot),
    "graph candidate output root",
  );
  const results = new Array(plan.requests.length);
  const rendererContractFailures = new Array(plan.requests.length);
  let nextIndex = 0;
  let stopScheduling = false;
  async function worker() {
    while (!stopScheduling && nextIndex < plan.requests.length) {
      const index = nextIndex;
      nextIndex += 1;
      try {
        results[index] = await renderOne({
          request: plan.requests[index],
          policy,
          outputRoot: canonicalOutputRoot,
          renderer: validatedRenderer,
          now,
          timeoutMs,
          settlementGraceMs,
          fileOperations,
        });
      } catch (error) {
        if (!(error instanceof RendererContractError)) throw error;
        rendererContractFailures[index] = error.failure;
        stopScheduling = true;
      }
    }
  }
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
  const rendererContractFailure = rendererContractFailures.find((failure) => failure !== undefined) ?? null;
  const contractFailed = rendererContractFailure !== null;
  const graphResults = contractFailed
    ? plan.requests.map(({ occurrenceId }) => ({ occurrenceId, probeStatus: "missing", candidate: null }))
    : results;
  return {
    contract: "verdify.lab-graph-export-result",
    schemaVersion: 3,
    policyVersion: plan.policyVersion,
    policySha256: plan.policySha256,
    sourceOccurrenceManifestSha256: plan.sourceOccurrenceManifestSha256,
    reportingFeedSha256,
    rendererContract: {
      contract: "verdify.lab-graph-renderer-runtime-status",
      schemaVersion: 1,
      status: contractFailed ? "failed" : "satisfied",
      failure: rendererContractFailure,
    },
    graphs: graphResults,
  };
}

export { graphExportProducerContract };
