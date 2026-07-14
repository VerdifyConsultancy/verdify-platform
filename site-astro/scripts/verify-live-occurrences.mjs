#!/usr/bin/env node

import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";

import {
  LIVE_OCCURRENCE_EXPECTATIONS,
  normalizeLiveOccurrenceOrigin,
  validateLiveOccurrenceDocuments,
} from "./lib/live-occurrence-acceptance.mjs";

const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_CONCURRENCY = 4;
const MAX_JSON_BYTES = Object.freeze({
  "/static-build.json": 1024 * 1024,
  "/occurrence-manifest.json": 8 * 1024 * 1024,
});
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const textDecoder = new TextDecoder("utf-8", { fatal: true });

function usage() {
  return "Usage: node scripts/verify-live-occurrences.mjs --origin https://lab-stage.verdify.ai";
}

function parseArgs(values) {
  if (values.length !== 2 || values[0] !== "--origin" || !values[1]) {
    throw new Error(usage());
  }
  return { origin: normalizeLiveOccurrenceOrigin(values[1]) };
}

function contentType(response) {
  return (response.headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase();
}

function validateContentLength(response, maximumBytes, expectedBytes = null) {
  const value = response.headers.get("content-length");
  if (value === null) return;
  if (!/^(?:0|[1-9][0-9]*)$/u.test(value)) {
    throw new Error("live occurrence response has an invalid Content-Length");
  }
  const bytes = Number(value);
  if (!Number.isSafeInteger(bytes) || bytes > maximumBytes) {
    throw new Error("live occurrence response exceeds its byte bound");
  }
  if (expectedBytes !== null && bytes !== expectedBytes) {
    throw new Error("live occurrence blob Content-Length differs from selected metadata");
  }
}

async function requireResponse(url, response, expectedMediaType) {
  if (
    response.status !== 200
    || response.redirected !== false
    || response.url !== url
  ) {
    await response.body?.cancel().catch(() => {});
    throw new Error(`live occurrence request did not return an unredirected HTTP 200: ${new URL(url).pathname}`);
  }
  const encoding = response.headers.get("content-encoding");
  if (encoding !== null && encoding !== "identity") {
    await response.body?.cancel().catch(() => {});
    throw new Error("live occurrence response uses unsupported content encoding");
  }
  if (contentType(response) !== expectedMediaType) {
    await response.body?.cancel().catch(() => {});
    throw new Error(`live occurrence response has the wrong MIME type: ${new URL(url).pathname}`);
  }
  if (!response.body) throw new Error("live occurrence response has no body");
  return response;
}

async function readBoundedBody(response, maximumBytes, { expectedBytes = null, collect = false } = {}) {
  try {
    validateContentLength(response, maximumBytes, expectedBytes);
  } catch (error) {
    await response.body.cancel().catch(() => {});
    throw error;
  }
  const reader = response.body.getReader();
  const chunks = [];
  const digest = createHash("sha256");
  let prefix = Buffer.alloc(0);
  let bytes = 0;
  let complete = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        complete = true;
        break;
      }
      const chunk = Buffer.from(value);
      bytes += chunk.length;
      if (bytes > maximumBytes || (expectedBytes !== null && bytes > expectedBytes)) {
        throw new Error("live occurrence response exceeded its byte bound while streaming");
      }
      digest.update(chunk);
      if (prefix.length < PNG_SIGNATURE.length) {
        prefix = Buffer.concat([prefix, chunk.subarray(0, PNG_SIGNATURE.length - prefix.length)]);
      }
      if (collect) chunks.push(chunk);
    }
  } finally {
    if (!complete) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  if (expectedBytes !== null && bytes !== expectedBytes) {
    throw new Error("live occurrence blob byte count differs from selected metadata");
  }
  return {
    bytes,
    sha256: digest.digest("hex"),
    prefix,
    body: collect ? Buffer.concat(chunks, bytes) : null,
  };
}

async function fetchJson(origin, pathname, fetchImpl, timeoutMs) {
  const url = new URL(pathname, `${origin}/`).href;
  const response = await fetchImpl(url, {
    method: "GET",
    redirect: "manual",
    signal: AbortSignal.timeout(timeoutMs),
    headers: {
      Accept: "application/json",
      "Accept-Encoding": "identity",
      "User-Agent": "verdify-lab-live-occurrence-acceptance/1",
    },
  });
  await requireResponse(url, response, "application/json");
  const body = await readBoundedBody(response, MAX_JSON_BYTES[pathname], { collect: true });
  let document;
  try {
    document = JSON.parse(textDecoder.decode(body.body));
  } catch {
    throw new Error(`live occurrence document is not valid UTF-8 JSON: ${pathname}`);
  }
  return { document, bytes: body.body };
}

async function verifyBlob(asset, fetchImpl, timeoutMs) {
  const response = await fetchImpl(asset.url, {
    method: "GET",
    redirect: "manual",
    signal: AbortSignal.timeout(timeoutMs),
    headers: {
      Accept: "image/png",
      "Accept-Encoding": "identity",
      "User-Agent": "verdify-lab-live-occurrence-acceptance/1",
    },
  });
  await requireResponse(asset.url, response, "image/png");
  if (response.headers.get("cache-control") !== LIVE_OCCURRENCE_EXPECTATIONS.immutableCacheControl) {
    await response.body.cancel().catch(() => {});
    throw new Error(`live occurrence blob is not served with immutable caching: ${asset.publicPath}`);
  }
  const body = await readBoundedBody(response, LIVE_OCCURRENCE_EXPECTATIONS.maximumBlobBytes, {
    expectedBytes: asset.bytes,
  });
  if (!body.prefix.equals(PNG_SIGNATURE)) {
    throw new Error(`live occurrence blob does not have a PNG signature: ${asset.publicPath}`);
  }
  if (body.sha256 !== asset.sha256) {
    throw new Error(`live occurrence blob bytes do not match their selected SHA-256: ${asset.publicPath}`);
  }
  return Object.freeze({
    publicPath: asset.publicPath,
    sha256: asset.sha256,
    bytes: body.bytes,
    mediaType: "image/png",
    cacheControl: LIVE_OCCURRENCE_EXPECTATIONS.immutableCacheControl,
  });
}

async function mapBounded(values, concurrency, operation) {
  const results = new Array(values.length);
  let next = 0;
  let stopped = false;
  let firstError = null;
  const worker = async () => {
    while (!stopped) {
      const index = next;
      next += 1;
      if (index >= values.length) return;
      try {
        results[index] = await operation(values[index]);
      } catch (error) {
        stopped = true;
        firstError ??= error;
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, values.length) }, () => worker()));
  if (firstError) throw firstError;
  return results;
}

export async function verifyLiveOccurrences({
  origin,
  fetchImpl = globalThis.fetch,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  concurrency = DEFAULT_CONCURRENCY,
} = {}) {
  const normalizedOrigin = normalizeLiveOccurrenceOrigin(origin);
  if (typeof fetchImpl !== "function") throw new Error("live occurrence verifier requires a fetch implementation");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 30_000) {
    throw new Error("live occurrence request timeout must be between 100 and 30000 milliseconds");
  }
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 8) {
    throw new Error("live occurrence request concurrency must be between 1 and 8");
  }

  const [buildValue, occurrenceValue] = await Promise.all([
    fetchJson(normalizedOrigin, "/static-build.json", fetchImpl, timeoutMs),
    fetchJson(normalizedOrigin, "/occurrence-manifest.json", fetchImpl, timeoutMs),
  ]);
  const documents = validateLiveOccurrenceDocuments({
    origin: normalizedOrigin,
    build: buildValue.document,
    occurrenceManifest: occurrenceValue.document,
    occurrenceManifestBytes: occurrenceValue.bytes,
  });
  const blobs = await mapBounded(
    documents.assets,
    concurrency,
    (asset) => verifyBlob(asset, fetchImpl, timeoutMs),
  );
  const totalBlobBytes = blobs.reduce((total, blob) => total + blob.bytes, 0);
  return Object.freeze({
    contract: "verdify.lab-live-occurrence-acceptance",
    schemaVersion: 1,
    checkedAt: new Date().toISOString(),
    origin: normalizedOrigin,
    selectedManifestSha256: documents.selectedManifestSha256,
    occurrenceManifestSha256: documents.occurrenceManifestSha256,
    counts: documents.counts,
    totalBlobBytes,
    blobs: Object.freeze(blobs),
  });
}

async function main() {
  const { origin } = parseArgs(process.argv.slice(2));
  const report = await verifyLiveOccurrences({ origin });
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

const executablePath = process.argv[1];
if (executablePath && import.meta.url === pathToFileURL(executablePath).href) {
  main().catch((error) => {
    process.stderr.write(`verify-live-occurrences: ${error.message}\n`);
    process.exitCode = 1;
  });
}
