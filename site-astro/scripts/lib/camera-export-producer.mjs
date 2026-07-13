import { createHash, randomUUID } from "node:crypto";
import { constants as fsConstants, link, mkdir, open, realpath, unlink } from "node:fs/promises";
import path from "node:path";

import sharp from "sharp";

import {
  occurrenceExportPolicySha256,
  validateOccurrenceExportPolicy,
} from "./occurrence-export-contract.mjs";
import { decodePng, validatePngFile } from "./png-validation.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const MAX_TIMEOUT_MS = 15_000;
const DEFAULT_TIMEOUT_MS = 10_000;
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

// #483 deliberately approved exactly these two public, read-only requests. Keep
// this producer closed if a later policy is broadened accidentally.
const APPROVED_CAMERA_REQUESTS = new Map([
  ["media_024bdac9f86794c7d1f36d48", {
    url: "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080",
    requestProvenanceSha256: "34d53abda8ab745e106c0719534a554769a9b1017f22b7bb40e5895a6be74a34",
  }],
  ["media_4e973f995789201d00aed8fd", {
    url: "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    requestProvenanceSha256: "0667d58e2f39c22e68bd906d3e4c754de1b41a845487eb55654aadba37c76fe0",
  }],
]);

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

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function assertApprovedPolicy(policy) {
  validateOccurrenceExportPolicy(policy);
  if (
    policy.activation.state !== "approved"
    || policy.activation.approvedBy !== "jason"
    || !policy.activation.approvedAt
  ) throw new Error("camera export policy is not activated");
  if (policy.cameraUpstream.sources.length !== APPROVED_CAMERA_REQUESTS.size) {
    throw new Error("camera export policy is not the closed #483 request set");
  }
  for (const source of policy.cameraUpstream.sources) {
    const approved = APPROVED_CAMERA_REQUESTS.get(source.occurrenceId);
    if (
      !approved
      || source.url !== approved.url
      || source.requestProvenanceSha256 !== approved.requestProvenanceSha256
    ) throw new Error("camera export policy is not the closed #483 request set");
  }
}

export function validateCameraExportRequest(request, policy) {
  assertApprovedPolicy(policy);
  if (!exactKeys(request, [
    "contract",
    "schemaVersion",
    "occurrenceId",
    "requestProvenanceSha256",
    "method",
    "url",
    "redirectsAllowed",
    "authorization",
    "cookies",
    "requestedAt",
    "expectedSelectionSha256",
  ]) || request.contract !== "verdify.lab-camera-export-request" || request.schemaVersion !== 1) {
    throw new Error("camera export request does not use the closed v1 shape");
  }
  const approved = APPROVED_CAMERA_REQUESTS.get(request.occurrenceId);
  const policySource = policy.cameraUpstream.sources.find(({ occurrenceId }) => occurrenceId === request.occurrenceId);
  if (
    !approved
    || !policySource
    || request.requestProvenanceSha256 !== approved.requestProvenanceSha256
    || request.requestProvenanceSha256 !== policySource.requestProvenanceSha256
    || request.method !== "GET"
    || request.url !== approved.url
    || request.url !== policySource.url
    || request.redirectsAllowed !== false
    || request.authorization !== "forbidden"
    || request.cookies !== "forbidden"
  ) throw new Error("camera export request is outside the exact public allowlist");
  canonicalInstant(request.requestedAt, "camera export request time");
  if (Date.parse(request.requestedAt) < Date.parse(policy.activation.approvedAt)) {
    throw new Error("camera export request predates policy activation");
  }
  if (request.expectedSelectionSha256 !== null && !SHA256_RE.test(request.expectedSelectionSha256)) {
    throw new Error("camera export selection precondition is invalid");
  }
  return request;
}

function validateTransportResponse(response, requestUrl) {
  if (!exactKeys(response, [
    "status",
    "redirected",
    "responseUrl",
    "contentType",
    "contentLength",
    "body",
  ])) throw new Error("camera response does not use the closed transport shape");
  if (!Number.isSafeInteger(response.status) || response.status !== 200) {
    throw new Error("camera response did not return HTTP 200");
  }
  if (response.redirected !== false || response.responseUrl !== requestUrl) {
    throw new Error("camera response attempted a redirect");
  }
  if (typeof response.contentType !== "string" || response.contentType.toLowerCase().split(";", 1)[0].trim() !== "image/jpeg") {
    throw new Error("camera response MIME type is not image/jpeg");
  }
  if (
    response.contentLength !== null
    && (!Number.isSafeInteger(response.contentLength) || response.contentLength < 0)
  ) throw new Error("camera response content length is invalid");
}

async function boundedBody(body, maximumBytes) {
  if (Buffer.isBuffer(body) || body instanceof Uint8Array) {
    if (body.length === 0 || body.length > maximumBytes) throw new Error("camera response is outside the byte limit");
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
    throw new Error("camera response body is not readable");
  }
  const chunks = [];
  let length = 0;
  for await (const chunk of iterable) {
    if (!(Buffer.isBuffer(chunk) || chunk instanceof Uint8Array) || chunk.length === 0) {
      throw new Error("camera response body contains an invalid chunk");
    }
    length += chunk.length;
    if (length > maximumBytes) throw new Error("camera response is outside the byte limit");
    chunks.push(Buffer.from(chunk));
  }
  if (length === 0) throw new Error("camera response is outside the byte limit");
  return Buffer.concat(chunks, length);
}

async function timedCameraResponse({ transport, request, maximumBytes, timeoutMs }) {
  const abortController = new AbortController();
  let timeout;
  const expired = new Promise((_, reject) => {
    timeout = setTimeout(() => {
      abortController.abort();
      reject(new Error("camera response exceeded the time limit"));
    }, timeoutMs);
  });
  try {
    return await Promise.race([
      (async () => {
        let response;
        try {
          response = await transport({
            method: "GET",
            url: request.url,
            redirect: "manual",
            credentials: "omit",
            headers: { accept: "image/jpeg" },
            signal: abortController.signal,
          });
        } catch {
          throw new Error("camera transport failed");
        }
        validateTransportResponse(response, request.url);
        if (response.contentLength !== null && response.contentLength > maximumBytes) {
          throw new Error("camera response is outside the byte limit");
        }
        let bytes;
        try {
          bytes = await boundedBody(response.body, maximumBytes);
        } catch (error) {
          if (![
            "camera response body is not readable",
            "camera response body contains an invalid chunk",
            "camera response is outside the byte limit",
          ].includes(error.message)) throw new Error("camera response body could not be read");
          throw error;
        }
        if (response.contentLength !== null && response.contentLength !== bytes.length) {
          throw new Error("camera response content length does not match its bytes");
        }
        return bytes;
      })(),
      expired,
    ]);
  } finally {
    clearTimeout(timeout);
  }
}

async function sanitizeJpeg(bytes, bounds) {
  const inputPixels = bounds.maxWidth * bounds.maxHeight;
  let metadata;
  try {
    metadata = await sharp(bytes, {
      failOn: "error",
      limitInputPixels: inputPixels,
      sequentialRead: true,
    }).metadata();
  } catch {
    throw new Error("camera JPEG could not be decoded within bounds");
  }
  if (
    metadata.format !== "jpeg"
    || !Number.isSafeInteger(metadata.width)
    || !Number.isSafeInteger(metadata.height)
    || metadata.width < bounds.minWidth
    || metadata.width > bounds.maxWidth
    || metadata.height < bounds.minHeight
    || metadata.height > bounds.maxHeight
  ) throw new Error("camera JPEG dimensions are outside the approved bounds");

  let encodedPng;
  try {
    encodedPng = await sharp(bytes, {
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
    throw new Error("camera JPEG could not be sanitized within bounds");
  }
  const png = stripPngAncillaryChunks(encodedPng);
  if (png.length === 0 || png.length > bounds.maxBytes) throw new Error("sanitized camera PNG is outside the byte limit");
  let decoded;
  try {
    decoded = decodePng(png);
  } catch {
    throw new Error("sanitized camera PNG failed the release contract");
  }
  if (
    decoded.width < bounds.minWidth
    || decoded.width > bounds.maxWidth
    || decoded.height < bounds.minHeight
    || decoded.height > bounds.maxHeight
  ) throw new Error("sanitized camera PNG dimensions are outside the approved bounds");
  return { png, decoded };
}

function stripPngAncillaryChunks(bytes) {
  if (!Buffer.isBuffer(bytes) || !bytes.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw new Error("sanitized camera PNG has invalid framing");
  }
  const chunks = [PNG_SIGNATURE];
  let offset = PNG_SIGNATURE.length;
  let ended = false;
  while (offset < bytes.length) {
    if (offset + 12 > bytes.length) throw new Error("sanitized camera PNG has invalid framing");
    const length = bytes.readUInt32BE(offset);
    const end = offset + 12 + length;
    if (end > bytes.length) throw new Error("sanitized camera PNG has invalid framing");
    const type = bytes.subarray(offset + 4, offset + 8).toString("ascii");
    if (!/^[A-Za-z]{4}$/.test(type)) throw new Error("sanitized camera PNG has invalid framing");
    if (["IHDR", "IDAT", "IEND"].includes(type)) {
      chunks.push(bytes.subarray(offset, end));
    } else if (type[0] === type[0].toUpperCase()) {
      throw new Error("sanitized camera PNG contains an unexpected critical chunk");
    }
    offset = end;
    if (type === "IEND") {
      ended = true;
      break;
    }
  }
  if (!ended || offset !== bytes.length) throw new Error("sanitized camera PNG has invalid framing");
  return Buffer.concat(chunks);
}

async function persistCandidate(outputRoot, occurrenceId, png) {
  const requestedRoot = path.resolve(outputRoot);
  await mkdir(requestedRoot, { recursive: true, mode: 0o700 });
  const root = await realpath(requestedRoot);
  const mediaRoot = path.join(root, "current-media");
  const occurrenceRoot = path.join(mediaRoot, occurrenceId);
  await mkdir(mediaRoot, { mode: 0o700 }).catch((error) => {
    if (error.code !== "EEXIST") throw error;
  });
  await mkdir(occurrenceRoot, { mode: 0o700 }).catch((error) => {
    if (error.code !== "EEXIST") throw error;
  });
  if ((await realpath(mediaRoot)) !== mediaRoot || (await realpath(occurrenceRoot)) !== occurrenceRoot) {
    throw new Error("camera candidate directory resolves through a link");
  }

  const digest = sha256(png);
  const relativePath = `current-media/${occurrenceId}/${digest}.png`;
  const target = path.join(occurrenceRoot, `${digest}.png`);
  const temporary = path.join(occurrenceRoot, `.${digest}.${randomUUID()}.tmp`);
  const handle = await open(
    temporary,
    fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_EXCL | fsConstants.O_NOFOLLOW,
    0o600,
  );
  try {
    await handle.writeFile(png);
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await link(temporary, target);
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
  } finally {
    await unlink(temporary).catch(() => {});
  }
  const verified = await validatePngFile(root, relativePath);
  if (verified.sha256 !== digest || verified.bytes !== png.length) {
    throw new Error("camera candidate does not match its content address");
  }
  return { root, relativePath, verified };
}

async function fetchCameraTransport(options) {
  const response = await fetch(options.url, {
    method: options.method,
    redirect: options.redirect,
    credentials: options.credentials,
    headers: options.headers,
    signal: options.signal,
  });
  const contentLengthText = response.headers.get("content-length");
  const contentLength = contentLengthText === null || !/^\d+$/.test(contentLengthText)
    ? null
    : Number(contentLengthText);
  return {
    status: response.status,
    redirected: response.redirected,
    responseUrl: response.url,
    contentType: response.headers.get("content-type") ?? "",
    contentLength,
    body: response.body,
  };
}

export async function captureCameraOccurrence({
  policy,
  request,
  outputRoot,
  transport = fetchCameraTransport,
  now = () => new Date().toISOString(),
  timeoutMs = DEFAULT_TIMEOUT_MS,
}) {
  validateCameraExportRequest(request, policy);
  if (typeof transport !== "function" || typeof now !== "function") throw new Error("camera exporter dependency is invalid");
  if (
    typeof outputRoot !== "string"
    || outputRoot.length === 0
    || outputRoot.length > 4096
    || /[\u0000-\u001f\u007f]/u.test(outputRoot)
  ) throw new Error("camera exporter output root is invalid");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > MAX_TIMEOUT_MS) {
    throw new Error("camera exporter timeout is invalid");
  }
  const bounds = policy.imagePolicy.currentMedia;
  const source = await timedCameraResponse({ transport, request, maximumBytes: bounds.maxBytes, timeoutMs });
  const capturedAt = canonicalInstant(now(), "camera capture time");
  if (Date.parse(capturedAt) < Date.parse(request.requestedAt)) {
    throw new Error("camera capture time predates its request");
  }
  const { png, decoded } = await sanitizeJpeg(source, bounds);
  const sanitizedAt = canonicalInstant(now(), "camera sanitization time");
  if (Date.parse(sanitizedAt) < Date.parse(capturedAt)) {
    throw new Error("camera sanitization time predates capture");
  }
  const persisted = await persistCandidate(outputRoot, request.occurrenceId, png);
  const candidate = {
    relativePath: persisted.relativePath,
    mediaType: "image/png",
    bytes: persisted.verified.bytes,
    width: persisted.verified.width,
    height: persisted.verified.height,
    sha256: persisted.verified.sha256,
    decodedSha256: decoded.decodedSha256,
    capturedAt,
    requestProvenanceSha256: request.requestProvenanceSha256,
  };
  return {
    contract: "verdify.lab-camera-export-result",
    schemaVersion: 1,
    occurrenceId: request.occurrenceId,
    policyVersion: policy.policyVersion,
    policySha256: occurrenceExportPolicySha256(policy),
    requestProvenanceSha256: request.requestProvenanceSha256,
    requestedAt: request.requestedAt,
    capturedAt,
    sanitizedAt,
    sourceMediaType: "image/jpeg",
    sourceBytes: source.length,
    sourceSha256: sha256(source),
    candidate,
    batchRecord: {
      occurrenceId: request.occurrenceId,
      captureStatus: "success",
      requestProvenanceSha256: request.requestProvenanceSha256,
      candidate: {
        relativePath: candidate.relativePath,
        mediaType: candidate.mediaType,
        capturedAt: candidate.capturedAt,
        requestProvenanceSha256: candidate.requestProvenanceSha256,
      },
      expectedSelectionSha256: request.expectedSelectionSha256,
    },
  };
}

export const cameraExportProducerContract = {
  approvedOccurrenceIds: [...APPROVED_CAMERA_REQUESTS.keys()],
  defaultTimeoutMs: DEFAULT_TIMEOUT_MS,
  maxTimeoutMs: MAX_TIMEOUT_MS,
};
