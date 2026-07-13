import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, readdir, rm, symlink } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

import {
  draftBlockedOccurrenceExportPolicy,
  occurrenceExportPolicySha256,
} from "../scripts/lib/occurrence-export-contract.mjs";
import {
  graphExportProducerContract,
  planGraphExportRequests,
  produceGraphExportCandidates,
} from "../scripts/lib/graph-export-producer.mjs";
import { persistOccurrenceCandidate } from "../scripts/lib/occurrence-candidate-store.mjs";
import {
  discoverGraphOccurrence,
  staticOccurrenceManifest,
} from "../scripts/lib/occurrence-release.mjs";
import { decodePng, validatePngFile } from "../scripts/lib/png-validation.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REVIEWED_AT = "2026-07-13T11:59:00Z";
const APPROVED_AT = "2026-07-13T12:00:00Z";
const CAPTURED_AT = "2026-07-13T12:01:00Z";

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function digest(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function fixture() {
  const graphs = Array.from({ length: graphExportProducerContract.expectedGraphCount }, (_, index) => discoverGraphOccurrence({
    route: `/evidence/graph-${String(index).padStart(3, "0")}`,
    ordinal: index,
    liveUrl: `https://graphs.verdify.ai/d-solo/public-reporting/approved?orgId=1&panelId=${index + 1}&theme=light&from=now-24h&to=now&var-zone=greenhouse`,
    title: `Approved graph ${index + 1}`,
  }));
  const manifest = staticOccurrenceManifest({
    snapshotId: `sanitized-content-sha256:${"a".repeat(64)}`,
    discoveredGraphs: graphs,
    discoveredCurrentMedia: [],
  });
  const manifestSha256 = digest(canonicalBytes(manifest));
  const blocked = draftBlockedOccurrenceExportPolicy({
    manifest,
    manifestSha256,
    policyVersion: "offline-graph-producer-v1",
    approvedAt: REVIEWED_AT,
  });
  const active = structuredClone(blocked);
  active.activation = {
    ...active.activation,
    state: "approved",
    approvedBy: "jason",
    approvedAt: APPROVED_AT,
  };
  return { graphs, manifest, manifestSha256, blocked, active };
}

async function workspace(context, prefix = "verdify-graph-export-") {
  const root = await mkdtemp(path.join(os.tmpdir(), prefix));
  context.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

async function graphPng({ compressionLevel = 6, metadata = false } = {}) {
  const width = 320;
  const height = 180;
  const pixels = Buffer.alloc(width * height * 4);
  for (let offset = 0; offset < pixels.length; offset += 4) {
    pixels[offset] = 24;
    pixels[offset + 1] = 96;
    pixels[offset + 2] = 144;
    pixels[offset + 3] = 128;
  }
  let image = sharp(pixels, { raw: { width, height, channels: 4 } });
  if (metadata) image = image.withMetadata({ orientation: 1, density: 144 });
  return image.png({ compressionLevel, adaptiveFiltering: compressionLevel > 0 }).toBuffer();
}

function response(bytes, overrides = {}) {
  return {
    status: 200,
    contentType: "image/png",
    contentLength: bytes.length,
    body: bytes,
    ...overrides,
  };
}

function rendererContract(render) {
  return {
    contract: "verdify.lab-graph-renderer",
    schemaVersion: 1,
    abortCooperation: "settle-within-grace-after-abort",
    render,
  };
}

test("the pure planner byte-binds exactly 143 manifest-ordered, endpoint-free targets", () => {
  const { graphs, manifest, manifestSha256, blocked } = fixture();
  const plan = planGraphExportRequests({ policy: blocked, manifest, manifestSha256 });
  assert.equal(plan.contract, "verdify.lab-graph-export-plan");
  assert.equal(plan.policySha256, occurrenceExportPolicySha256(blocked));
  assert.equal(plan.sourceOccurrenceManifestSha256, manifestSha256);
  assert.equal(plan.requests.length, 143);
  assert.deepEqual(plan.requests.map(({ occurrenceId }) => occurrenceId), graphs.map(({ occurrenceId }) => occurrenceId));
  assert.equal(new Set(plan.requests.map(({ occurrenceId }) => occurrenceId)).size, 143);
  for (const [index, request] of plan.requests.entries()) {
    assert.deepEqual(Object.keys(request), [
      "contract",
      "schemaVersion",
      "occurrenceId",
      "occurrenceSha256",
      "target",
    ]);
    assert.equal(request.occurrenceId, graphs[index].occurrenceId);
    assert.equal(request.target.uid, graphs[index].uid);
    assert.equal(request.target.panelId, graphs[index].panelId);
    assert.deepEqual(request.target.query, graphs[index].query);
    assert.deepEqual(request.target.variables, graphs[index].variables);
    assert.deepEqual(request.target.timeRange, graphs[index].timeRange);
  }
  assert.doesNotMatch(JSON.stringify(plan), /https?:|graphs\.verdify\.ai|credential|authorization|cookie|secret/i);
});

test("manifest, byte-digest, and policy drift fail before a plan or renderer call", async (context) => {
  const root = await workspace(context);
  const { manifest, manifestSha256, blocked, active } = fixture();
  assert.throws(
    () => planGraphExportRequests({ policy: blocked, manifest, manifestSha256: "b".repeat(64) }),
    /does not match the supplied manifest bytes/,
  );

  const driftedManifest = structuredClone(manifest);
  driftedManifest.graphs[0].semanticRole = "Drifted graph";
  assert.throws(
    () => planGraphExportRequests({ policy: blocked, manifest: driftedManifest, manifestSha256 }),
    /not canonical|not exactly allowlisted/,
  );

  const reducedManifest = structuredClone(manifest);
  reducedManifest.graphs.pop();
  assert.throws(
    () => planGraphExportRequests({ policy: blocked, manifest: reducedManifest, manifestSha256 }),
    /allowlist is not complete/,
  );

  const reorderedManifest = structuredClone(manifest);
  [reorderedManifest.graphs[0], reorderedManifest.graphs[1]] = [
    reorderedManifest.graphs[1],
    reorderedManifest.graphs[0],
  ];
  assert.throws(
    () => planGraphExportRequests({ policy: blocked, manifest: reorderedManifest, manifestSha256 }),
    /does not match its canonical byte digest/,
  );

  let calls = 0;
  await assert.rejects(produceGraphExportCandidates({
    policy: blocked,
    manifest,
    manifestSha256,
    outputRoot: root,
    renderer: rendererContract(async () => {
      calls += 1;
      return response(await graphPng());
    }),
  }), /not activated/);
  assert.equal(calls, 0);

  await assert.rejects(produceGraphExportCandidates({
    policy: active,
    manifest,
    manifestSha256,
    outputRoot: root,
  }), /closed abort-cooperative v1 contract/);

  const validRenderer = rendererContract(async () => response(await graphPng()));
  for (const candidate of [
    validRenderer.render,
    { ...validRenderer, endpoint: "not-accepted" },
    { ...validRenderer, abortCooperation: "best-effort" },
  ]) {
    await assert.rejects(produceGraphExportCandidates({
      policy: active,
      manifest,
      manifestSha256,
      outputRoot: root,
      renderer: candidate,
    }), /closed abort-cooperative v1 contract/);
  }
});

test("the injected renderer never exceeds four calls and normalizes every graph to the same metadata-free RGB PNG", async (context) => {
  const root = await workspace(context);
  const { graphs, manifest, manifestSha256, active } = fixture();
  const plain = await graphPng({ compressionLevel: 0 });
  const tagged = await graphPng({ compressionLevel: 9, metadata: true });
  assert.notDeepEqual(plain, tagged);
  let activeCalls = 0;
  let maximumActive = 0;
  const observedCalls = [];
  const result = await produceGraphExportCandidates({
    policy: active,
    manifest,
    manifestSha256,
    outputRoot: root,
    now: () => CAPTURED_AT,
    concurrency: 4,
    renderer: rendererContract(async (options) => {
      activeCalls += 1;
      maximumActive = Math.max(maximumActive, activeCalls);
      observedCalls.push(options);
      await new Promise((resolve) => setTimeout(resolve, 1));
      activeCalls -= 1;
      const index = graphs.findIndex(({ occurrenceId }) => occurrenceId === options.request.occurrenceId);
      return response(index % 2 === 0 ? plain : tagged);
    }),
  });

  assert.equal(maximumActive, 4);
  assert.equal(result.rendererContract.status, "satisfied");
  assert.equal(result.rendererContract.failure, null);
  assert.equal(observedCalls.length, 143);
  assert.deepEqual(result.graphs.map(({ occurrenceId }) => occurrenceId), graphs.map(({ occurrenceId }) => occurrenceId));
  assert.equal(result.graphs.filter(({ probeStatus }) => probeStatus === "success").length, 143);
  const outputDigests = new Set();
  for (const item of result.graphs) {
    assert.match(item.candidate.relativePath, new RegExp(`^graphs/${item.occurrenceId}/[0-9a-f]{64}\\.png$`));
    assert.deepEqual(Object.keys(item.candidate), ["relativePath", "mediaType", "capturedAt"]);
    assert.equal(item.candidate.mediaType, "image/png");
    assert.equal(item.candidate.capturedAt, CAPTURED_AT);
    outputDigests.add(path.basename(item.candidate.relativePath));
  }
  assert.equal(outputDigests.size, 1, "source encoding and metadata must not change normalized bytes");
  const verified = await validatePngFile(root, result.graphs[0].candidate.relativePath);
  assert.equal(verified.colorType, 2);
  const bytes = await readFile(path.join(root, ...result.graphs[0].candidate.relativePath.split("/")));
  assert.equal(decodePng(bytes).colorType, 2);
  assert.deepEqual([...pngChunkTypes(bytes)], ["IHDR", "IDAT", "IEND"]);
  assert.doesNotMatch(JSON.stringify(result), /url|https?:|credential|authorization|cookie|secret/i);
  for (const call of observedCalls) {
    assert.deepEqual(Object.keys(call), ["request", "signal"]);
    assert.equal(call.signal.aborted, true);
  }
});

test("mixed renderer failures classify deterministically and still return every graph exactly once", async (context) => {
  const root = await workspace(context);
  const { graphs, manifest, manifestSha256, active } = fixture();
  const valid = await graphPng();
  const tooSmall = await sharp({
    create: { width: 319, height: 180, channels: 3, background: { r: 1, g: 2, b: 3 } },
  }).png().toBuffer();
  const indexById = new Map(graphs.map(({ occurrenceId }, index) => [occurrenceId, index]));
  const result = await produceGraphExportCandidates({
    policy: active,
    manifest,
    manifestSha256,
    outputRoot: root,
    now: () => CAPTURED_AT,
    timeoutMs: 10,
    renderer: rendererContract(async ({ request, signal }) => {
      switch (indexById.get(request.occurrenceId)) {
        case 0:
          throw new Error("renderer-specific details must not escape");
        case 1:
          return response(valid, { status: 503 });
        case 2:
          return response(Buffer.from("not a PNG"));
        case 3:
          return new Promise((_, reject) => signal.addEventListener("abort", () => reject(new Error("stopped")), { once: true }));
        case 4:
          return response(valid, { contentLength: active.imagePolicy.graphs.maxBytes + 1 });
        case 5:
          return response(tooSmall);
        case 6:
          return response(valid, { contentType: "image/jpeg" });
        default:
          return response(valid);
      }
    }),
  });

  assert.equal(result.rendererContract.status, "satisfied");
  assert.equal(result.graphs.length, 143);
  assert.equal(new Set(result.graphs.map(({ occurrenceId }) => occurrenceId)).size, 143);
  assert.deepEqual(result.graphs.map(({ occurrenceId }) => occurrenceId), graphs.map(({ occurrenceId }) => occurrenceId));
  assert.deepEqual(result.graphs.slice(0, 7).map(({ probeStatus }) => probeStatus), [
    "missing",
    "http-error",
    "decode-error",
    "timeout",
    "http-error",
    "decode-error",
    "http-error",
  ]);
  assert.equal(result.graphs.slice(7).every(({ probeStatus }) => probeStatus === "success"), true);
  assert.equal(result.graphs.slice(0, 7).every(({ candidate }) => candidate === null), true);
  assert.equal(result.graphs.slice(7).every(({ candidate }) => candidate?.mediaType === "image/png"), true);
  assert.doesNotMatch(JSON.stringify(result), /renderer-specific|stopped|url|https?:|credential|authorization|cookie|secret/i);
});

test("a renderer that ignores abort stops the batch at four unsettled calls and returns a complete closed result", async (context) => {
  const root = await workspace(context);
  const { graphs, manifest, manifestSha256, active } = fixture();
  let calls = 0;
  let activeCalls = 0;
  let maximumActive = 0;
  const startedAt = Date.now();
  const result = await produceGraphExportCandidates({
    policy: active,
    manifest,
    manifestSha256,
    outputRoot: root,
    timeoutMs: 10,
    settlementGraceMs: 20,
    concurrency: 4,
    renderer: rendererContract(async () => {
      calls += 1;
      activeCalls += 1;
      maximumActive = Math.max(maximumActive, activeCalls);
      return new Promise(() => {});
    }),
  });
  const elapsedMs = Date.now() - startedAt;

  assert.equal(calls, 4);
  assert.equal(activeCalls, 4);
  assert.equal(maximumActive, 4);
  assert.ok(elapsedMs < 500, `non-cooperative batch exceeded its bound: ${elapsedMs}ms`);
  assert.deepEqual(result.rendererContract, {
    contract: "verdify.lab-graph-renderer-runtime-status",
    schemaVersion: 1,
    status: "failed",
    failure: "renderer-settlement-timeout",
  });
  assert.equal(result.graphs.length, 143);
  assert.deepEqual(result.graphs.map(({ occurrenceId }) => occurrenceId), graphs.map(({ occurrenceId }) => occurrenceId));
  assert.equal(result.graphs.every(({ probeStatus, candidate }) => probeStatus === "missing" && candidate === null), true);
  assert.doesNotMatch(JSON.stringify(result), /url|https?:|credential|authorization|cookie|secret/i);

  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(calls, 4, "returning must not release workers to schedule more calls");
  assert.equal(activeCalls, 4, "the four unsettled calls remain the absolute upper bound");
});

test("a body whose read and cleanup never settle stops scheduling and returns all 143 null records within bounds", async (context) => {
  const root = await workspace(context);
  const { graphs, manifest, manifestSha256, active } = fixture();
  let calls = 0;
  let activeReads = 0;
  let maximumActiveReads = 0;
  let cleanups = 0;
  const stuckBody = () => {
    const iterator = {
      next: () => {
        activeReads += 1;
        maximumActiveReads = Math.max(maximumActiveReads, activeReads);
        return new Promise(() => {});
      },
      return: () => {
        cleanups += 1;
        return new Promise(() => {});
      },
    };
    return { [Symbol.asyncIterator]: () => iterator };
  };
  const startedAt = Date.now();
  const result = await produceGraphExportCandidates({
    policy: active,
    manifest,
    manifestSha256,
    outputRoot: root,
    timeoutMs: 10,
    settlementGraceMs: 20,
    concurrency: 4,
    renderer: rendererContract(async () => {
      calls += 1;
      return response(Buffer.alloc(1), { contentLength: null, body: stuckBody() });
    }),
  });
  const elapsedMs = Date.now() - startedAt;

  assert.equal(calls, 4);
  assert.equal(activeReads, 4);
  assert.equal(maximumActiveReads, 4);
  assert.equal(cleanups, 4);
  assert.ok(elapsedMs < 500, `non-cooperative cleanup exceeded its bound: ${elapsedMs}ms`);
  assert.equal(result.rendererContract.status, "failed");
  assert.equal(result.rendererContract.failure, "body-cleanup-timeout");
  assert.equal(result.graphs.length, 143);
  assert.deepEqual(result.graphs.map(({ occurrenceId }) => occurrenceId), graphs.map(({ occurrenceId }) => occurrenceId));
  assert.equal(result.graphs.every(({ probeStatus, candidate }) => probeStatus === "missing" && candidate === null), true);
  assert.doesNotMatch(JSON.stringify(result), /url|https?:|credential|authorization|cookie|secret/i);

  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(calls, 4);
  assert.equal(activeReads, 4);
});

test("the shared candidate store rejects linked paths and cleans interrupted graph writes", async (context) => {
  const root = await workspace(context);
  const external = await workspace(context, "verdify-graph-external-");
  const linked = path.join(root, "linked");
  const png = strictPng(await graphPng());
  const occurrenceId = "graph_aaaaaaaaaaaaaaaaaaaaaaaa";
  await symlink(external, linked, "dir");
  await assert.rejects(persistOccurrenceCandidate({
    outputRoot: linked,
    collection: "graphs",
    occurrenceId,
    png,
    label: "graph candidate",
    collectionLabel: "graph",
  }), /canonical real directory/);
  assert.deepEqual(await readdir(external), []);

  const output = path.join(root, "output");
  await mkdir(output);
  await symlink(external, path.join(output, "graphs"), "dir");
  await assert.rejects(persistOccurrenceCandidate({
    outputRoot: output,
    collection: "graphs",
    occurrenceId,
    png,
    label: "graph candidate",
    collectionLabel: "graph",
  }), /canonical real directory/);
  assert.deepEqual(await readdir(external), []);

  await rm(path.join(output, "graphs"));
  await assert.rejects(persistOccurrenceCandidate({
    outputRoot: output,
    collection: "graphs",
    occurrenceId,
    png,
    label: "graph candidate",
    collectionLabel: "graph",
    fileOperations: { writeFile: async () => { throw new Error("injected write failure"); } },
  }), /temporary write failed/);
  assert.deepEqual(await readdir(path.join(output, "graphs", occurrenceId)), []);

  const first = await persistOccurrenceCandidate({
    outputRoot: output,
    collection: "graphs",
    occurrenceId,
    png,
    label: "graph candidate",
    collectionLabel: "graph",
  });
  const second = await persistOccurrenceCandidate({
    outputRoot: output,
    collection: "graphs",
    occurrenceId,
    png,
    label: "graph candidate",
    collectionLabel: "graph",
  });
  assert.equal(first.relativePath, second.relativePath);
  assert.deepEqual(await readdir(path.join(output, "graphs", occurrenceId)), [`${first.verified.sha256}.png`]);
});

test("the graph producer has no default network, service, credential, database, or object-store client", async () => {
  const source = await readFile(path.join(ROOT, "scripts/lib/graph-export-producer.mjs"), "utf8");
  assert.doesNotMatch(source, /\bfetch\s*\(|https?:\/\/|from ["']@aws-sdk|kubectl|grafana|postgres|secret(?:name|key)?/i);
  assert.match(source, /renderer\.render\(\{/);
});

function* pngChunkTypes(bytes) {
  let offset = 8;
  while (offset < bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const type = bytes.subarray(offset + 4, offset + 8).toString("ascii");
    yield type;
    offset += 12 + length;
    if (type === "IEND") return;
  }
}

function strictPng(bytes) {
  const chunks = [bytes.subarray(0, 8)];
  let offset = 8;
  while (offset < bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const end = offset + 12 + length;
    const type = bytes.subarray(offset + 4, offset + 8).toString("ascii");
    if (["IHDR", "IDAT", "IEND"].includes(type)) chunks.push(bytes.subarray(offset, end));
    offset = end;
    if (type === "IEND") break;
  }
  return Buffer.concat(chunks);
}
