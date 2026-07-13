import { createHash } from "node:crypto";
import path from "node:path";

import sharp from "sharp";

import {
  occurrenceExportPolicySha256,
  validatePolicyManifestBinding,
} from "./occurrence-export-contract.mjs";
import {
  canonicalCandidateDirectory,
  occurrenceCandidateFileOperations,
  persistOccurrenceCandidate,
} from "./occurrence-candidate-store.mjs";
import { decodePng } from "./png-validation.mjs";

const EXPECTED_GRAPH_COUNT = 143;
const MAX_CONCURRENCY = 4;
const DEFAULT_CONCURRENCY = 4;
const MAX_TIMEOUT_MS = 15_000;
const DEFAULT_TIMEOUT_MS = 10_000;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

class GraphProbeError extends Error {
  constructor(probeStatus, message) {
    super(message);
    this.probeStatus = probeStatus;
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

export function planGraphExportRequests({ policy, manifest, manifestSha256 }) {
  const discovered = validatePolicyManifestBinding(policy, manifest, manifestSha256);
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
    schemaVersion: 1,
    occurrenceId: occurrence.occurrenceId,
    occurrenceSha256: approvedById.get(occurrence.occurrenceId),
    target: {
      uid: occurrence.uid,
      panelId: occurrence.panelId,
      query: cloneQuery(occurrence.query),
      variables: cloneQuery(occurrence.variables),
      timeRange: { ...occurrence.timeRange },
    },
  }));
  return {
    contract: "verdify.lab-graph-export-plan",
    schemaVersion: 1,
    policyVersion: policy.policyVersion,
    policySha256: occurrenceExportPolicySha256(policy),
    sourceOccurrenceManifestSha256: manifestSha256,
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

async function cancelBody(body) {
  try {
    if (body?.cancel instanceof Function) await body.cancel();
    else if (body?.return instanceof Function) await body.return();
  } catch {
    // The stable probe classification must not reflect renderer-specific errors.
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

async function boundedBody(body, maximumBytes) {
  if (Buffer.isBuffer(body) || body instanceof Uint8Array) {
    if (body.length === 0 || body.length > maximumBytes) {
      throw new GraphProbeError("http-error", "graph renderer response is outside the byte limit");
    }
    return Buffer.from(body);
  }

  let iterable = body;
  if (body?.getReader instanceof Function) {
    iterable = {
      async *[Symbol.asyncIterator]() {
        const reader = body.getReader();
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) return;
            yield value;
          }
        } finally {
          reader.releaseLock();
        }
      },
    };
  }
  if (!iterable || !(Symbol.asyncIterator in Object(iterable))) {
    throw new GraphProbeError("http-error", "graph renderer response body is not readable");
  }
  const chunks = [];
  let length = 0;
  try {
    for await (const chunk of iterable) {
      if (!(Buffer.isBuffer(chunk) || chunk instanceof Uint8Array) || chunk.length === 0) {
        throw new GraphProbeError("http-error", "graph renderer response body contains an invalid chunk");
      }
      length += chunk.length;
      if (length > maximumBytes) {
        throw new GraphProbeError("http-error", "graph renderer response is outside the byte limit");
      }
      chunks.push(Buffer.from(chunk));
    }
  } catch (error) {
    if (error instanceof GraphProbeError) throw error;
    throw new GraphProbeError("http-error", "graph renderer response body could not be read");
  }
  if (length === 0) throw new GraphProbeError("http-error", "graph renderer response is outside the byte limit");
  return Buffer.concat(chunks, length);
}

async function timedRendererResponse({ renderer, request, maximumBytes, timeoutMs }) {
  const abortController = new AbortController();
  let timeout;
  let timedOut = false;
  let responseBody;
  const expired = new Promise((_, reject) => {
    timeout = setTimeout(() => {
      timedOut = true;
      abortController.abort();
      reject(new GraphProbeError("timeout", "graph renderer exceeded the time limit"));
    }, timeoutMs);
  });
  try {
    return await Promise.race([
      (async () => {
        let response;
        try {
          response = await renderer({ request, signal: abortController.signal });
        } catch {
          throw new GraphProbeError("missing", "graph renderer did not return a response");
        }
        responseBody = response?.body;
        try {
          validateRendererResponse(response);
          if (response.contentLength !== null && response.contentLength > maximumBytes) {
            throw new GraphProbeError("http-error", "graph renderer response is outside the byte limit");
          }
          const bytes = await boundedBody(response.body, maximumBytes);
          if (response.contentLength !== null && response.contentLength !== bytes.length) {
            throw new GraphProbeError("http-error", "graph renderer response length does not match its bytes");
          }
          return bytes;
        } catch (error) {
          await cancelBody(response?.body);
          throw error;
        }
      })(),
      expired,
    ]);
  } catch (error) {
    if (timedOut) {
      await cancelBody(responseBody);
      throw new GraphProbeError("timeout", "graph renderer exceeded the time limit");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
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

async function renderOne({ request, policy, outputRoot, renderer, now, timeoutMs, fileOperations }) {
  try {
    const source = await timedRendererResponse({
      renderer,
      request,
      maximumBytes: policy.imagePolicy.graphs.maxBytes,
      timeoutMs,
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
  outputRoot,
  renderer,
  now = () => new Date().toISOString(),
  timeoutMs = DEFAULT_TIMEOUT_MS,
  concurrency = DEFAULT_CONCURRENCY,
  fileOperations: fileOperationOverrides,
}) {
  const plan = planGraphExportRequests({ policy, manifest, manifestSha256 });
  assertApprovedPolicy(policy);
  if (typeof renderer !== "function" || typeof now !== "function") throw new Error("graph exporter dependency is invalid");
  if (
    typeof outputRoot !== "string"
    || outputRoot.length === 0
    || outputRoot.length > 4096
    || /[\u0000-\u001f\u007f]/u.test(outputRoot)
  ) throw new Error("graph exporter output root is invalid");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > MAX_TIMEOUT_MS) {
    throw new Error("graph exporter timeout is invalid");
  }
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > MAX_CONCURRENCY) {
    throw new Error("graph exporter concurrency is invalid");
  }
  const fileOperations = occurrenceCandidateFileOperations(fileOperationOverrides, "graph candidate");
  const canonicalOutputRoot = await canonicalCandidateDirectory(
    path.resolve(outputRoot),
    "graph candidate output root",
  );
  const results = new Array(plan.requests.length);
  let nextIndex = 0;
  async function worker() {
    while (nextIndex < plan.requests.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await renderOne({
        request: plan.requests[index],
        policy,
        outputRoot: canonicalOutputRoot,
        renderer,
        now,
        timeoutMs,
        fileOperations,
      });
    }
  }
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
  return {
    contract: "verdify.lab-graph-export-result",
    schemaVersion: 1,
    policyVersion: plan.policyVersion,
    policySha256: plan.policySha256,
    sourceOccurrenceManifestSha256: plan.sourceOccurrenceManifestSha256,
    graphs: results,
  };
}

export const graphExportProducerContract = Object.freeze({
  expectedGraphCount: EXPECTED_GRAPH_COUNT,
  defaultConcurrency: DEFAULT_CONCURRENCY,
  maxConcurrency: MAX_CONCURRENCY,
  defaultTimeoutMs: DEFAULT_TIMEOUT_MS,
  maxTimeoutMs: MAX_TIMEOUT_MS,
  probeStatuses: Object.freeze(["success", "timeout", "http-error", "decode-error", "missing"]),
});
