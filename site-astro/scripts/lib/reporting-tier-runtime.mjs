import { createHash } from "node:crypto";

import { validateReportingTargets } from "../generate-reporting-tier-assets.mjs";
import {
  reportingFeedEnvelopeSha256,
  validatePolicyManifestBinding,
} from "./occurrence-export-contract.mjs";
import { reportingDatasourceIdentitySha256 } from "./graph-export-producer.mjs";

export const REPORTING_PROJECTION_ORIGIN =
  "http://verdify-lab-reporting-projection.verdify-platform.svc.cluster.local:8080";
export const REPORTING_GATEWAY_ORIGIN =
  "http://verdify-lab-reporting-tier.verdify-platform.svc.cluster.local:8080";
export const REPORTING_DATASOURCE_IDENTITY = "verdify-lab-reporting-stage-v1";
export const REPORTING_FEED_ID = "lab-public-v1";
export const REPORTING_WATERMARK_PATH = "/v1/source-watermark";
export const REPORTING_WATERMARK_TIMEOUT_MS = 5_000;
export const REPORTING_GRAPH_CONCURRENCY = 2;
export const REPORTING_WATERMARK_SQL = [
  "SELECT feed_id, source_watermark,",
  "       to_char(source_watermark_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS source_watermark_at,",
  "       true AS projection_read_only,",
  "       false AS track_a_primary_credential",
  "FROM lab_reporting.source_watermark_v1",
  "WHERE feed_id = 'lab-public-v1'",
  "ORDER BY source_watermark_at DESC",
  "LIMIT 2;",
].join("\n");

const SOURCE_WATERMARK_RE = /^wm_[A-Za-z0-9_-]{8,128}$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u;
const MAX_WATERMARK_RESPONSE_BYTES = 4096;
const FIXED_RENDER_QUERY = Object.freeze({
  width: "1000",
  height: "400",
  tz: "America/Denver",
});

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
  if (typeof value !== "string" || !ISO_INSTANT_RE.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  const milliseconds = Date.parse(value);
  const normalized = Number.isFinite(milliseconds) ? new Date(milliseconds).toISOString() : "";
  const expected = value.includes(".") ? normalized : normalized.replace(".000Z", "Z");
  if (value !== expected) throw new Error(`${label} is invalid`);
  return value;
}

function validateFixedReportingFeed(feed) {
  if (
    !exactKeys(feed, [
      "contract",
      "schemaVersion",
      "sourceId",
      "sourceClass",
      "credentialClass",
      "direction",
      "sourceWatermark",
      "sourceWatermarkAt",
    ])
    || feed.contract !== "verdify.operator-public-reporting-feed"
    || feed.schemaVersion !== 1
    || feed.sourceId !== "operator-public-reporting-feed-lab-stage"
    || feed.sourceClass !== "public-reporting-projection"
    || feed.credentialClass !== "reporting-read-only"
    || feed.direction !== "one-way-read-only"
    || !SOURCE_WATERMARK_RE.test(feed.sourceWatermark)
  ) throw new Error("reporting projection feed is invalid");
  canonicalInstant(feed.sourceWatermarkAt, "reporting projection source watermark time");
  return feed;
}

function contentLength(response) {
  const value = response.headers?.get?.("content-length") ?? null;
  if (value === null || !/^(?:0|[1-9][0-9]*)$/u.test(value)) return null;
  const result = Number(value);
  return Number.isSafeInteger(result) ? result : null;
}

async function boundedResponseBytes(response, maximumBytes, registerCancel = () => {}) {
  const declared = contentLength(response);
  if (declared !== null && declared > maximumBytes) {
    await response.body?.cancel?.().catch(() => {});
    throw new Error("reporting projection response exceeds the byte limit");
  }
  if (response.body?.getReader instanceof Function) {
    const reader = response.body.getReader();
    registerCancel(() => reader.cancel());
    const chunks = [];
    let length = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!(value instanceof Uint8Array) || value.length === 0) {
          throw new Error("reporting projection response body is invalid");
        }
        length += value.length;
        if (length > maximumBytes) {
          await reader.cancel();
          throw new Error("reporting projection response exceeds the byte limit");
        }
        chunks.push(Buffer.from(value));
      }
    } finally {
      registerCancel(null);
      reader.releaseLock();
    }
    if (declared !== null && declared !== length) {
      throw new Error("reporting projection response length is invalid");
    }
    return Buffer.concat(chunks, length);
  }
  if (typeof response.arrayBuffer !== "function") {
    throw new Error("reporting projection response body is unavailable");
  }
  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.length === 0 || bytes.length > maximumBytes || (declared !== null && declared !== bytes.length)) {
    throw new Error("reporting projection response length is invalid");
  }
  return bytes;
}

function validateWatermarkDocument(value) {
  if (
    !exactKeys(value, [
      "contract",
      "schemaVersion",
      "feedId",
      "sourceWatermark",
      "sourceWatermarkAt",
      "projectionReadOnly",
      "trackAPrimaryCredential",
    ])
    || value.contract !== "verdify.lab-reporting-projection-watermark"
    || value.schemaVersion !== 1
    || value.feedId !== REPORTING_FEED_ID
    || !SOURCE_WATERMARK_RE.test(value.sourceWatermark)
    || value.projectionReadOnly !== true
    || value.trackAPrimaryCredential !== false
  ) throw new Error("reporting projection watermark does not use the closed read-only v1 shape");
  canonicalInstant(value.sourceWatermarkAt, "reporting projection source watermark time");
  return value;
}

function cancelResponseBody(response) {
  try {
    Promise.resolve(response?.body?.cancel?.()).catch(() => {});
  } catch {
    // Deadline cleanup is best effort after the transport has been aborted.
  }
}

async function fetchReportingProjectionWatermark(fetchImpl, signal, observeResponse, registerCancel) {
  const url = `${REPORTING_PROJECTION_ORIGIN}${REPORTING_WATERMARK_PATH}`;
  const response = await fetchImpl(url, {
    method: "GET",
    redirect: "error",
    credentials: "omit",
    headers: { accept: "application/json" },
    signal,
  });
  observeResponse(response);
  registerCancel(() => response?.body?.cancel?.());
  if (signal.aborted) {
    cancelResponseBody(response);
    throw new Error("reporting projection watermark request exceeded its deadline");
  }
  if (response?.status !== 200 || response.redirected !== false) {
    await response?.body?.cancel?.().catch(() => {});
    throw new Error("reporting projection watermark endpoint is unavailable");
  }
  const mediaType = (response.headers?.get?.("content-type") ?? "").toLowerCase().split(";", 1)[0].trim();
  if (mediaType !== "application/json") {
    await response.body?.cancel?.().catch(() => {});
    throw new Error("reporting projection watermark response is not JSON");
  }
  const bytes = await boundedResponseBytes(response, MAX_WATERMARK_RESPONSE_BYTES, registerCancel);
  registerCancel(null);
  let document;
  try {
    document = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("reporting projection watermark response is not JSON");
  }
  validateWatermarkDocument(document);
  return {
    contract: "verdify.operator-public-reporting-feed",
    schemaVersion: 1,
    sourceId: "operator-public-reporting-feed-lab-stage",
    sourceClass: "public-reporting-projection",
    credentialClass: "reporting-read-only",
    direction: "one-way-read-only",
    sourceWatermark: document.sourceWatermark,
    sourceWatermarkAt: document.sourceWatermarkAt,
  };
}

export async function readReportingProjectionWatermark({
  fetchImpl = globalThis.fetch,
  timeoutMs = REPORTING_WATERMARK_TIMEOUT_MS,
} = {}) {
  if (typeof fetchImpl !== "function") throw new Error("reporting projection transport is unavailable");
  if (
    !Number.isSafeInteger(timeoutMs)
    || timeoutMs < 1
    || timeoutMs > REPORTING_WATERMARK_TIMEOUT_MS
  ) throw new Error("reporting projection watermark deadline is invalid");
  const controller = new AbortController();
  let response = null;
  let cancelActiveRead = null;
  let timeout;
  const operation = fetchReportingProjectionWatermark(
    fetchImpl,
    controller.signal,
    (value) => { response = value; },
    (cancel) => { cancelActiveRead = cancel; },
  ).then(
    (value) => ({ state: "fulfilled", value }),
    (error) => ({ state: "rejected", error }),
  );
  const deadline = new Promise((resolve) => {
    timeout = setTimeout(() => {
      controller.abort();
      try {
        Promise.resolve(cancelActiveRead?.()).catch(() => {});
      } catch {
        cancelResponseBody(response);
      }
      resolve({ state: "deadline" });
    }, timeoutMs);
  });
  const outcome = await Promise.race([operation, deadline]);
  clearTimeout(timeout);
  if (outcome.state === "deadline") {
    throw new Error("reporting projection watermark request exceeded its deadline");
  }
  if (outcome.state === "rejected") throw outcome.error;
  return outcome.value;
}

function sameJson(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function validateRenderRequest(request, target, reportingFeedDigest, datasourceIdentityDigest) {
  if (
    request === null
    || typeof request !== "object"
    || Array.isArray(request)
    || request.contract !== "verdify.lab-graph-render-request"
    || request.schemaVersion !== 3
    || request.occurrenceId !== target.occurrenceId
    || request.reportingFeedSha256 !== reportingFeedDigest
    || request.target?.uid !== target.uid
    || request.target?.panelId !== target.panelId
    || !sameJson(request.target.query, target.query)
    || !sameJson(request.target.variables, target.variables)
    || !sameJson(request.target.timeRange, target.timeRange)
    || request.target?.datasourceBinding?.identitySha256 !== datasourceIdentityDigest
    || !["reporting-tier-dedicated-default", "legacy-dashboard-dedicated-override"].includes(
      request.target?.datasourceBinding?.mode,
    )
  ) throw new Error("graph render request is outside the exact reporting target inventory");
}

function renderUrl(target) {
  const url = new URL(target.renderPath, REPORTING_GATEWAY_ORIGIN);
  for (const [key, values] of Object.entries(target.query)) {
    for (const value of values) url.searchParams.append(key, value);
  }
  for (const [key, values] of Object.entries(target.variables)) {
    for (const value of values) url.searchParams.append(`var-${key}`, value);
  }
  for (const [key, value] of Object.entries(FIXED_RENDER_QUERY)) {
    url.searchParams.set(key, value);
  }
  return url;
}

export function createReportingTierRenderer({
  targets,
  reportingFeed,
  fetchImpl = globalThis.fetch,
}) {
  const validatedTargets = validateReportingTargets(structuredClone(targets));
  if (typeof fetchImpl !== "function") throw new Error("reporting renderer transport is unavailable");
  validateFixedReportingFeed(reportingFeed);
  const reportingFeedDigest = reportingFeedEnvelopeSha256(reportingFeed);
  const datasourceIdentityDigest = reportingDatasourceIdentitySha256(REPORTING_DATASOURCE_IDENTITY);
  const targetByOccurrence = new Map(validatedTargets.occurrences.map((target) => [target.occurrenceId, target]));
  return Object.freeze({
    contract: "verdify.lab-graph-renderer",
    schemaVersion: 3,
    sourceClass: "operator-owned-reporting-tier",
    anonymousAccess: false,
    reportingFeedSha256: reportingFeedDigest,
    reportingDatasourceIdentitySha256: datasourceIdentityDigest,
    abortCooperation: "settle-within-grace-after-abort",
    render: async ({ request, signal }) => {
      const target = targetByOccurrence.get(request?.occurrenceId);
      if (target === undefined) throw new Error("graph render request is not allowlisted");
      validateRenderRequest(request, target, reportingFeedDigest, datasourceIdentityDigest);
      if (!(signal instanceof AbortSignal)) throw new Error("graph render request has no abort signal");
      const response = await fetchImpl(renderUrl(target), {
        method: "GET",
        redirect: "error",
        credentials: "omit",
        headers: { accept: "image/png" },
        signal,
      });
      return {
        status: response.status,
        contentType: response.headers?.get?.("content-type") ?? "",
        contentLength: contentLength(response),
        body: response.body,
      };
    },
  });
}

function cameraTransport(fetchImpl) {
  return async (options) => {
    const response = await fetchImpl(options.url, {
      method: options.method,
      redirect: options.redirect,
      credentials: options.credentials,
      headers: options.headers,
      signal: options.signal,
    });
    return {
      status: response.status,
      redirected: response.redirected,
      responseUrl: response.url,
      contentType: response.headers?.get?.("content-type") ?? "",
      contentLength: contentLength(response),
      body: response.body,
    };
  };
}

async function defaultProducer(input) {
  const { runOccurrenceProducer } = await import("./occurrence-producer-runner.mjs");
  return runOccurrenceProducer(input);
}

export async function runReportingOccurrenceProducerOnce({
  policy,
  manifest,
  manifestSha256,
  targets,
  outputRoot,
  selectorPreconditionReader,
  fetchImpl = globalThis.fetch,
  now = () => new Date().toISOString(),
  produce = defaultProducer,
}) {
  const policySnapshot = structuredClone(policy);
  const manifestSnapshot = structuredClone(manifest);
  const validatedTargets = validateReportingTargets(structuredClone(targets));
  const canonicalManifestSha256 = sha256(canonicalBytes(manifestSnapshot));
  validatePolicyManifestBinding(policySnapshot, manifestSnapshot, manifestSha256);
  if (
    canonicalManifestSha256 !== manifestSha256
    || policySnapshot.activation.state !== "approved"
    || policySnapshot.activation.approvedBy !== "jason"
    || typeof policySnapshot.activation.approvedAt !== "string"
    || policySnapshot.sourceOccurrenceManifestSha256 !== manifestSha256
    || validatedTargets.sourceOccurrenceManifestSha256 !== manifestSha256
  ) throw new Error("reporting occurrence producer is not bound to the approved source inventory");
  if (typeof produce !== "function" || typeof fetchImpl !== "function" || typeof now !== "function") {
    throw new Error("reporting occurrence producer dependency is invalid");
  }
  const reportingFeed = await readReportingProjectionWatermark({ fetchImpl });
  const renderer = createReportingTierRenderer({ targets: validatedTargets, reportingFeed, fetchImpl });
  return produce({
    policy: policySnapshot,
    manifest: manifestSnapshot,
    manifestSha256,
    reportingFeed,
    reportingDatasourceIdentity: REPORTING_DATASOURCE_IDENTITY,
    outputRoot,
    renderer,
    cameraTransport: cameraTransport(fetchImpl),
    selectorPreconditionReader,
    now,
    graphConcurrency: REPORTING_GRAPH_CONCURRENCY,
  });
}
