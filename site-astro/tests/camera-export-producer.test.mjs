import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, readdir, rm, symlink, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

import { runCameraExportCli } from "../scripts/export-camera-occurrence.mjs";
import {
  cameraExportProducerContract,
  captureCameraOccurrence,
} from "../scripts/lib/camera-export-producer.mjs";
import {
  inspectOccurrenceExportCandidates,
  occurrenceExportPolicySha256,
} from "../scripts/lib/occurrence-export-contract.mjs";
import { decodePng, validatePngFile } from "../scripts/lib/png-validation.mjs";

const SITE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const policyDocument = JSON.parse(await readFile(
  path.join(SITE_ROOT, "config/lab-stage-occurrence-export-policy.json"),
  "utf8",
));
const ACTIVE_AT = "2026-07-13T08:00:00Z";
const REQUESTED_AT = "2026-07-13T08:01:00Z";
const CAPTURED_AT = "2026-07-13T08:02:00Z";
const SANITIZED_AT = "2026-07-13T08:03:00Z";

function activePolicy() {
  const policy = structuredClone(policyDocument);
  policy.activation = {
    ...policy.activation,
    state: "approved",
    approvedBy: "jason",
    approvedAt: ACTIVE_AT,
  };
  return policy;
}

function cameraRequest(policy, occurrenceId, overrides = {}) {
  const source = policy.cameraUpstream.sources.find((candidate) => candidate.occurrenceId === occurrenceId);
  return {
    contract: "verdify.lab-camera-export-request",
    schemaVersion: 1,
    occurrenceId,
    requestProvenanceSha256: source?.requestProvenanceSha256 ?? "0".repeat(64),
    method: "GET",
    url: source?.url ?? "https://api.verdify.ai/invalid",
    redirectsAllowed: false,
    authorization: "forbidden",
    cookies: "forbidden",
    requestedAt: REQUESTED_AT,
    expectedSelectionSha256: null,
    ...overrides,
  };
}

function fixedClock(...values) {
  let index = 0;
  return () => values[Math.min(index++, values.length - 1)];
}

async function workspace(context, label = "verdify-camera-export-") {
  const root = await mkdtemp(path.join(tmpdir(), label));
  context.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

async function jpeg(width = 320, height = 180) {
  return sharp({
    create: {
      width,
      height,
      channels: 3,
      background: { r: 24, g: 96, b: 48 },
    },
  }).jpeg({ quality: 90, chromaSubsampling: "4:4:4" }).toBuffer();
}

function jpegWithComment(bytes, comment = "secret-camera-metadata") {
  assert.deepEqual([...bytes.subarray(0, 2)], [0xff, 0xd8]);
  const data = Buffer.from(comment);
  const segment = Buffer.alloc(data.length + 4);
  segment[0] = 0xff;
  segment[1] = 0xfe;
  segment.writeUInt16BE(data.length + 2, 2);
  data.copy(segment, 4);
  return Buffer.concat([bytes.subarray(0, 2), segment, bytes.subarray(2)]);
}

function response(bytes, responseUrl, overrides = {}) {
  return {
    status: 200,
    redirected: false,
    responseUrl,
    contentType: "image/jpeg",
    contentLength: bytes.length,
    body: bytes,
    ...overrides,
  };
}

function observedAbortAwareResponse(bytes, responseUrl, signal, overrides = {}) {
  const observation = { chunksConsumed: 0, cancellations: 0 };
  const body = new ReadableStream({
    pull(controller) {
      observation.chunksConsumed += 1;
      controller.enqueue(bytes);
      controller.close();
    },
    cancel() {
      observation.cancellations += 1;
    },
  }, { highWaterMark: 0 });
  signal.addEventListener("abort", () => { void body.cancel(); }, { once: true });
  return {
    candidate: response(bytes, responseUrl, { body, ...overrides }),
    observation,
  };
}

async function successfulCapture({ policy, request, root, bytes, calls = [], fileOperations }) {
  return captureCameraOccurrence({
    policy,
    request,
    outputRoot: root,
    now: fixedClock(CAPTURED_AT, SANITIZED_AT),
    transport: async (options) => {
      calls.push(options);
      return response(bytes, request.url);
    },
    ...(fileOperations === undefined ? {} : { fileOperations }),
  });
}

function occurrenceDirectory(root, occurrenceId) {
  return path.join(root, "current-media", occurrenceId);
}

test("the closed producer sanitizes both #483 camera occurrences into downstream-valid candidates", async (context) => {
  const root = await workspace(context);
  const policy = activePolicy();
  const bytes = await jpeg();
  const results = [];
  const calls = [];

  for (const occurrenceId of cameraExportProducerContract.approvedOccurrenceIds) {
    const request = cameraRequest(policy, occurrenceId);
    const result = await successfulCapture({ policy, request, root, bytes, calls });
    results.push(result);
    assert.equal(result.occurrenceId, occurrenceId);
    assert.equal(result.requestProvenanceSha256, request.requestProvenanceSha256);
    assert.equal(result.candidate.capturedAt, CAPTURED_AT);
    assert.equal(result.sanitizedAt, SANITIZED_AT);
    assert.match(result.candidate.relativePath, new RegExp(`^current-media/${occurrenceId}/[0-9a-f]{64}\\.png$`));
    assert.deepEqual(result.batchRecord.candidate, {
      relativePath: result.candidate.relativePath,
      mediaType: "image/png",
      capturedAt: CAPTURED_AT,
      requestProvenanceSha256: request.requestProvenanceSha256,
    });
    const verified = await validatePngFile(root, result.candidate.relativePath);
    assert.equal(verified.sha256, result.candidate.sha256);
    assert.equal(verified.decodedSha256, result.candidate.decodedSha256);
    assert.equal(verified.colorType, 2, "sanitized output is RGB");
    assert.doesNotMatch(JSON.stringify(result), /https?:\\?\//);
  }

  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.deepEqual(Object.keys(call), ["method", "url", "redirect", "credentials", "headers", "signal"]);
    assert.equal(call.method, "GET");
    assert.equal(call.redirect, "manual");
    assert.equal(call.credentials, "omit");
    assert.deepEqual(call.headers, { accept: "image/jpeg" });
    assert.equal("authorization" in call.headers, false);
    assert.equal("cookie" in call.headers, false);
    assert.equal(call.signal.aborted, true);
  }

  const batch = {
    contract: "verdify.lab-occurrence-export-batch",
    schemaVersion: 2,
    batchId: "batch_camera_fixture_0001",
    policyVersion: policy.policyVersion,
    policySha256: occurrenceExportPolicySha256(policy),
    sourceOccurrenceManifestSha256: policy.sourceOccurrenceManifestSha256,
    reportingFeed: {
      contract: "verdify.operator-public-reporting-feed",
      schemaVersion: 1,
      sourceId: "operator-public-reporting-feed-camera",
      sourceClass: "public-reporting-projection",
      credentialClass: "reporting-read-only",
      direction: "one-way-read-only",
      sourceWatermark: "wm_camera_fixture_0001",
      sourceWatermarkAt: ACTIVE_AT,
    },
    exportedAt: SANITIZED_AT,
    expectedSelectionSha256: null,
    graphs: policy.graphs.map(({ occurrenceId }) => ({
      occurrenceId,
      probeStatus: "timeout",
      candidate: null,
    })),
    currentMedia: results.map(({ batchRecord }) => batchRecord),
  };
  const inspected = await inspectOccurrenceExportCandidates({
    policy,
    batch,
    sourceRoot: root,
    processingAt: "2026-07-13T08:03:30Z",
  });
  assert.equal(inspected.feedFreshness.status, "fresh");
  assert.equal([...inspected.currentMediaCandidates.values()].filter(Boolean).length, 2);
  assert.doesNotMatch(JSON.stringify(batch.currentMedia), /url|https?:\\?\//i);
});

test("request validation rejects URL, query, method, redirect, auth, cookie, and shape drift before transport", async (context) => {
  const root = await workspace(context);
  const policy = activePolicy();
  const occurrenceId = cameraExportProducerContract.approvedOccurrenceIds[0];
  const original = cameraRequest(policy, occurrenceId);
  const cases = [
    ["origin", { url: original.url.replace("api.verdify.ai", "example.com") }],
    ["path", { url: original.url.replace("latest.jpg", "other.jpg") }],
    ["query", { url: original.url.replace("h=1080", "h=720") }],
    ["method", { method: "POST" }],
    ["redirect", { redirectsAllowed: true }],
    ["authorization", { authorization: "allowed" }],
    ["cookies", { cookies: "allowed" }],
    ["provenance", { requestProvenanceSha256: "0".repeat(64) }],
    ["selection", { expectedSelectionSha256: "not-a-digest" }],
    ["occurrence", { occurrenceId: "media_aaaaaaaaaaaaaaaaaaaaaaaa" }],
  ];
  for (const [label, override] of cases) {
    let calls = 0;
    await assert.rejects(
      captureCameraOccurrence({
        policy,
        request: cameraRequest(policy, occurrenceId, override),
        outputRoot: root,
        transport: async () => { calls += 1; },
      }),
      undefined,
      label,
    );
    assert.equal(calls, 0, `${label} must fail closed before transport`);
  }

  let calls = 0;
  await assert.rejects(captureCameraOccurrence({
    policy,
    request: { ...original, headers: { authorization: "secret" } },
    outputRoot: root,
    transport: async () => { calls += 1; },
  }), /closed v1 shape/);
  assert.equal(calls, 0);

  await assert.rejects(captureCameraOccurrence({
    policy: policyDocument,
    request: original,
    outputRoot: root,
    transport: async () => { calls += 1; },
  }), /not activated/);
  assert.equal(calls, 0);

  await assert.rejects(captureCameraOccurrence({
    policy,
    request: original,
    outputRoot: "",
    transport: async () => { calls += 1; },
  }), /output root/);
  assert.equal(calls, 0);
});

test("response handling fails closed on HTTP, redirects, MIME, lengths, bytes, decode, and dimensions", async (context) => {
  const root = await workspace(context);
  const policy = activePolicy();
  const request = cameraRequest(policy, cameraExportProducerContract.approvedOccurrenceIds[0]);
  const valid = await jpeg();
  const png = await sharp(valid).png().toBuffer();
  const oversized = Buffer.alloc(policy.imagePolicy.currentMedia.maxBytes + 1, 1);
  const low = await jpeg(319, 180);
  const wide = await jpeg(2161, 180);
  const cases = [
    ["HTTP status", response(valid, request.url, { status: 503 }), /HTTP 200/],
    ["HTTP redirect status", response(valid, request.url, { status: 302 }), /HTTP 200/],
    ["redirect flag", response(valid, request.url, { redirected: true }), /redirect/],
    ["redirect URL", response(valid, "https://api.verdify.ai/other"), /redirect/],
    ["content type", response(valid, request.url, { contentType: "image/png" }), /MIME/],
    ["declared size", response(valid, request.url, { contentLength: policy.imagePolicy.currentMedia.maxBytes + 1 }), /byte limit/],
    ["length mismatch", response(valid, request.url, { contentLength: valid.length + 1 }), /does not match/],
    ["actual size", response(oversized, request.url, { contentLength: null }), /byte limit/],
    ["empty body", response(Buffer.alloc(0), request.url), /byte limit/],
    ["decode", response(Buffer.from("not a jpeg"), request.url), /JPEG/],
    ["wrong decoded format", response(png, request.url), /JPEG/],
    ["minimum dimensions", response(low, request.url), /dimensions/],
    ["maximum dimensions", response(wide, request.url), /dimensions/],
  ];
  for (const [label, candidate, expected] of cases) {
    let signal;
    await assert.rejects(captureCameraOccurrence({
      policy,
      request,
      outputRoot: root,
      now: fixedClock(CAPTURED_AT, SANITIZED_AT),
      transport: async (options) => {
        signal = options.signal;
        return candidate;
      },
    }), expected, label);
    assert.equal(signal.aborted, true, `${label} must terminate its transport`);
  }
});

test("early response rejection aborts transport and cancels the unread body", async (context) => {
  const root = await workspace(context);
  const policy = activePolicy();
  const request = cameraRequest(policy, cameraExportProducerContract.approvedOccurrenceIds[0]);
  const valid = await jpeg();
  const cases = [
    ["invalid status", { status: 503 }, /HTTP 200/],
    ["declared oversize", { contentLength: policy.imagePolicy.currentMedia.maxBytes + 1 }, /byte limit/],
  ];

  for (const [label, overrides, expected] of cases) {
    let signal;
    let observation;
    let caught;
    try {
      await captureCameraOccurrence({
        policy,
        request,
        outputRoot: root,
        transport: async (options) => {
          signal = options.signal;
          const observed = observedAbortAwareResponse(valid, request.url, signal, overrides);
          observation = observed.observation;
          return observed.candidate;
        },
      });
    } catch (error) {
      caught = error;
    }
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(caught?.message ?? "", expected, label);
    assert.doesNotMatch(caught?.message ?? "", /https?:\\?\//);
    assert.equal(signal.aborted, true, `${label} must abort the passed signal`);
    assert.equal(observation.chunksConsumed, 0, `${label} must reject before consuming the body`);
    assert.equal(observation.cancellations, 1, `${label} must cancel the unread body`);
  }
});

test("network and time failures are bounded and do not reflect the approved URL", async (context) => {
  const root = await workspace(context);
  const policy = activePolicy();
  const request = cameraRequest(policy, cameraExportProducerContract.approvedOccurrenceIds[0]);
  const NativeAbortController = globalThis.AbortController;
  let timeoutAbortCalls = 0;
  globalThis.AbortController = class CountingAbortController extends NativeAbortController {
    abort(reason) {
      timeoutAbortCalls += 1;
      return super.abort(reason);
    }
  };
  let timeoutSignal;
  try {
    const timeout = captureCameraOccurrence({
      policy,
      request,
      outputRoot: root,
      timeoutMs: 10,
      transport: async ({ signal }) => {
        timeoutSignal = signal;
        return new Promise(() => {});
      },
    });
    await assert.rejects(timeout, /time limit/);
  } finally {
    globalThis.AbortController = NativeAbortController;
  }
  assert.equal(timeoutSignal.aborted, true);
  assert.equal(timeoutAbortCalls, 1);

  let error;
  try {
    await captureCameraOccurrence({
      policy,
      request,
      outputRoot: root,
      transport: async () => { throw new Error(`failed at ${request.url}`); },
    });
  } catch (caught) {
    error = caught;
  }
  assert.equal(error?.message, "camera transport failed");
  assert.doesNotMatch(error?.message ?? "", /https?:\\?\//);

  const brokenBody = {
    async *[Symbol.asyncIterator]() {
      throw new Error(`body failed at ${request.url}`);
    },
  };
  let brokenSignal;
  await assert.rejects(captureCameraOccurrence({
    policy,
    request,
    outputRoot: root,
    transport: async ({ signal }) => {
      brokenSignal = signal;
      return response(Buffer.from("unused"), request.url, {
        contentLength: null,
        body: brokenBody,
      });
    },
  }), (caught) => {
    assert.equal(caught.message, "camera response body could not be read");
    assert.doesNotMatch(caught.message, /https?:\\?\//);
    return true;
  });
  assert.equal(brokenSignal.aborted, true);
});

test("JPEG metadata is removed and repeated pixel content has deterministic PNG output", async (context) => {
  const root = await workspace(context);
  const secondRoot = await workspace(context, "verdify-camera-export-second-");
  const policy = activePolicy();
  const request = cameraRequest(policy, cameraExportProducerContract.approvedOccurrenceIds[1]);
  const base = await jpeg();
  const tagged = jpegWithComment(base);
  assert.notDeepEqual(tagged, base);

  const first = await successfulCapture({ policy, request, root, bytes: base });
  const second = await successfulCapture({ policy, request, root: secondRoot, bytes: tagged });
  assert.notEqual(first.sourceSha256, second.sourceSha256);
  assert.equal(first.candidate.sha256, second.candidate.sha256);
  assert.equal(first.candidate.decodedSha256, second.candidate.decodedSha256);
  assert.equal(first.candidate.relativePath, second.candidate.relativePath);

  const output = await readFile(path.join(root, ...first.candidate.relativePath.split("/")));
  const decoded = decodePng(output);
  assert.equal(decoded.colorType, 2);
  assert.equal(output.includes(Buffer.from("secret-camera-metadata")), false);
});

test("candidate persistence rejects linked roots and directories before writing outside its canonical root", async (context) => {
  const root = await workspace(context);
  const policy = activePolicy();
  const occurrenceId = cameraExportProducerContract.approvedOccurrenceIds[0];
  const request = cameraRequest(policy, occurrenceId);
  const bytes = await jpeg();
  const linkedRoot = path.join(root, "linked-output");
  const externalRoot = path.join(root, "external-output");
  await mkdir(externalRoot);
  await symlink(externalRoot, linkedRoot, "dir");

  const calls = [];
  await assert.rejects(successfulCapture({
    policy,
    request,
    root: linkedRoot,
    bytes,
    calls,
  }), /canonical real directory/);
  assert.equal(calls.length, 0, "a linked output root must fail before transport");
  assert.deepEqual(await readdir(externalRoot), []);

  const outputRoot = path.join(root, "output");
  const externalMediaRoot = path.join(root, "external-media");
  await mkdir(outputRoot);
  await mkdir(externalMediaRoot);
  await symlink(externalMediaRoot, path.join(outputRoot, "current-media"), "dir");
  await assert.rejects(successfulCapture({
    policy,
    request,
    root: outputRoot,
    bytes,
  }), /canonical real directory/);
  assert.deepEqual(
    await readdir(externalMediaRoot),
    [],
    "the occurrence directory must not be created through a linked media root",
  );

  await rm(path.join(outputRoot, "current-media"));
  await mkdir(path.join(outputRoot, "current-media"));
  const externalOccurrenceRoot = path.join(root, "external-occurrence");
  await mkdir(externalOccurrenceRoot);
  await symlink(externalOccurrenceRoot, occurrenceDirectory(outputRoot, occurrenceId), "dir");
  await assert.rejects(successfulCapture({
    policy,
    request,
    root: outputRoot,
    bytes,
  }), /canonical real directory/);
  assert.deepEqual(await readdir(externalOccurrenceRoot), []);
});

test("write and sync failures remove temporary candidates and permit a clean retry", async (context) => {
  const policy = activePolicy();
  const occurrenceId = cameraExportProducerContract.approvedOccurrenceIds[0];
  const request = cameraRequest(policy, occurrenceId);
  const bytes = await jpeg();

  for (const [label, fileOperations] of [
    ["write", { writeFile: async () => { throw new Error("fixture write failure"); } }],
    ["sync", { sync: async () => { throw new Error("fixture sync failure"); } }],
  ]) {
    const root = await workspace(context, `verdify-camera-${label}-failure-`);
    await assert.rejects(successfulCapture({
      policy,
      request,
      root,
      bytes,
      fileOperations,
    }), /temporary write failed/);
    assert.deepEqual(await readdir(occurrenceDirectory(root, occurrenceId)), []);

    const retried = await successfulCapture({ policy, request, root, bytes });
    assert.match(retried.candidate.relativePath, /\.png$/);
    assert.deepEqual(
      await readdir(occurrenceDirectory(root, occurrenceId)),
      [`${retried.candidate.sha256}.png`],
    );
  }
});

test("temporary unlink failure rolls back its new digest target and leaves a retryable directory", async (context) => {
  const root = await workspace(context);
  const policy = activePolicy();
  const occurrenceId = cameraExportProducerContract.approvedOccurrenceIds[1];
  const request = cameraRequest(policy, occurrenceId);
  const bytes = await jpeg();
  let injected = false;

  await assert.rejects(successfulCapture({
    policy,
    request,
    root,
    bytes,
    fileOperations: {
      unlink: async (target) => {
        if (!injected && path.basename(target).startsWith(".")) {
          injected = true;
          const error = new Error("fixture unlink failure");
          error.code = "EIO";
          throw error;
        }
        await unlink(target);
      },
    },
  }), /publication failed/);
  assert.equal(injected, true);
  assert.deepEqual(await readdir(occurrenceDirectory(root, occurrenceId)), []);

  const retried = await successfulCapture({ policy, request, root, bytes });
  assert.deepEqual(
    await readdir(occurrenceDirectory(root, occurrenceId)),
    [`${retried.candidate.sha256}.png`],
  );
});

test("CLI reads canonical documents and emits a URL-free canonical result with injected transport", async (context) => {
  const root = await workspace(context);
  const outputRoot = path.join(root, "output");
  const policy = activePolicy();
  const request = cameraRequest(policy, cameraExportProducerContract.approvedOccurrenceIds[0]);
  const policyPath = path.join(root, "policy.json");
  const requestPath = path.join(root, "request.json");
  await mkdir(outputRoot);
  await writeFile(policyPath, `${JSON.stringify(policy, null, 2)}\n`);
  await writeFile(requestPath, `${JSON.stringify(request, null, 2)}\n`);
  const bytes = await jpeg();
  let stdout = "";
  const result = await runCameraExportCli([
    "capture",
    "--policy", policyPath,
    "--request", requestPath,
    "--output-root", outputRoot,
    "--timeout-ms", "1000",
  ], {
    now: fixedClock(CAPTURED_AT, SANITIZED_AT),
    transport: async () => response(bytes, request.url),
    stdout: { write: (chunk) => { stdout += chunk; } },
  });
  assert.deepEqual(JSON.parse(stdout), result);
  assert.equal(stdout, `${JSON.stringify(result, null, 2)}\n`);
  assert.doesNotMatch(stdout, /url|https?:\\?\//i);
});
