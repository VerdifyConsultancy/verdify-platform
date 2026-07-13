import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import sharp from "sharp";

import {
  draftBlockedOccurrenceExportPolicy,
  reportingFeedEnvelopeSha256,
} from "../scripts/lib/occurrence-export-contract.mjs";
import {
  graphExportProducerContract,
  reportingDatasourceIdentitySha256,
} from "../scripts/lib/graph-export-producer.mjs";
import {
  occurrenceProducerRunnerContract,
  runOccurrenceProducer,
} from "../scripts/lib/occurrence-producer-runner.mjs";
import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
  staticOccurrenceManifest,
} from "../scripts/lib/occurrence-release.mjs";

const REVIEWED_AT = "2026-07-13T11:59:00Z";
const APPROVED_AT = "2026-07-13T12:00:00Z";
const RUN_AT = "2026-07-13T12:01:00Z";
const DATASOURCE_IDENTITY = "operator-reporting-datasource-runner-fixture";
const DATASOURCE_IDENTITY_SHA256 = reportingDatasourceIdentitySha256(DATASOURCE_IDENTITY);
const AGGREGATE_SELECTED_SHA256 = "a".repeat(64);
const MEDIA_SELECTED_SHA256 = ["b".repeat(64), "c".repeat(64)];
const TRANSPORT_PRIVATE_SENTINEL = "fixture-credential-material-do-not-emit";
const LEGACY_DASHBOARDS = new Map([
  ["greenhouse-equipment", 5],
  ["greenhouse-hydroponics", 5],
  ["greenhouse-lighting", 13],
  ["greenhouse-soil", 10],
  ["greenhouse-weather", 7],
]);
const REPORTING_FEED = Object.freeze({
  contract: "verdify.operator-public-reporting-feed",
  schemaVersion: 1,
  sourceId: "operator-public-reporting-feed-runner-offline",
  sourceClass: "public-reporting-projection",
  credentialClass: "reporting-read-only",
  direction: "one-way-read-only",
  sourceWatermark: "wm_occurrence_runner_fixture_0001",
  sourceWatermarkAt: APPROVED_AT,
});

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

function fixture() {
  const dashboardUids = [];
  for (const [uid, count] of LEGACY_DASHBOARDS) {
    dashboardUids.push(...Array.from({ length: count }, () => uid));
  }
  dashboardUids.push(...Array.from(
    { length: graphExportProducerContract.expectedGraphCount - dashboardUids.length },
    () => "site-public-reporting",
  ));
  const graphs = dashboardUids.map((uid, index) => discoverGraphOccurrence({
    route: `/evidence/runner-graph-${String(index).padStart(3, "0")}`,
    ordinal: index,
    liveUrl: `https://graphs.verdify.ai/d-solo/${uid}/approved?orgId=1&panelId=${index + 1}&theme=light&from=now-24h&to=now`,
    title: `Runner graph ${index + 1}`,
  }));
  const cameraSources = [
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080",
  ];
  const currentMediaWithSources = cameraSources.map((sourceUrl, index) => ({
    sourceUrl,
    occurrence: discoverCurrentMediaOccurrence({
      route: "/",
      ordinal: index + 3,
      sourceUrl,
      semanticRole: `Latest public snapshot from greenhouse camera ${index + 1}`,
    }),
  }));
  const currentMedia = currentMediaWithSources.map(({ occurrence }) => occurrence);
  const manifest = staticOccurrenceManifest({
    snapshotId: `sanitized-content-sha256:${"d".repeat(64)}`,
    discoveredGraphs: graphs,
    discoveredCurrentMedia: currentMedia,
  });
  const manifestSha256 = digest(canonicalBytes(manifest));
  const blocked = draftBlockedOccurrenceExportPolicy({
    manifest,
    manifestSha256,
    policyVersion: "offline-occurrence-runner-v1",
    approvedAt: REVIEWED_AT,
    cameraSources: currentMediaWithSources.map(({ occurrence, sourceUrl }) => ({
      occurrenceId: occurrence.occurrenceId,
      url: sourceUrl,
    })),
  });
  const active = structuredClone(blocked);
  active.activation = {
    ...active.activation,
    state: "approved",
    approvedBy: "jason",
    approvedAt: APPROVED_AT,
  };
  const selectorPreconditions = {
    contract: "verdify.lab-occurrence-export-selector-preconditions",
    schemaVersion: 1,
    aggregateExpectedSelectionSha256: AGGREGATE_SELECTED_SHA256,
    currentMedia: manifest.currentMedia.map(({ occurrenceId }, index) => ({
      occurrenceId,
      expectedSelectionSha256: MEDIA_SELECTED_SHA256[index],
    })),
  };
  return {
    manifest,
    manifestSha256,
    blocked,
    active,
    selectorPreconditions,
  };
}

async function workspace(context) {
  const root = await mkdtemp(path.join(os.tmpdir(), "verdify-occurrence-runner-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  const outputRoot = path.join(root, "candidates");
  await mkdir(outputRoot);
  return outputRoot;
}

async function graphPng() {
  return sharp({
    create: {
      width: 320,
      height: 180,
      channels: 3,
      background: { r: 30, g: 110, b: 150 },
    },
  }).png().toBuffer();
}

async function cameraJpeg() {
  return sharp({
    create: {
      width: 320,
      height: 180,
      channels: 3,
      background: { r: 24, g: 96, b: 48 },
    },
  }).jpeg({ quality: 90, chromaSubsampling: "4:4:4" }).toBuffer();
}

function graphResponse(bytes, overrides = {}) {
  return {
    status: 200,
    contentType: "image/png",
    contentLength: bytes.length,
    body: bytes,
    ...overrides,
  };
}

function cameraResponse(bytes, responseUrl, overrides = {}) {
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

function rendererContract(render) {
  return {
    contract: "verdify.lab-graph-renderer",
    schemaVersion: 3,
    sourceClass: "operator-owned-reporting-tier",
    anonymousAccess: false,
    reportingFeedSha256: reportingFeedEnvelopeSha256(REPORTING_FEED),
    reportingDatasourceIdentitySha256: DATASOURCE_IDENTITY_SHA256,
    abortCooperation: "settle-within-grace-after-abort",
    render,
  };
}

function selectorReader(read) {
  return {
    contract: "verdify.lab-occurrence-selector-precondition-reader",
    schemaVersion: 1,
    read,
  };
}

function runnerInput(value, outputRoot, { renderer, cameraTransport, reader, ...overrides }) {
  return {
    policy: value.active,
    manifest: value.manifest,
    manifestSha256: value.manifestSha256,
    reportingFeed: REPORTING_FEED,
    reportingDatasourceIdentity: DATASOURCE_IDENTITY,
    outputRoot,
    renderer,
    cameraTransport,
    selectorPreconditionReader: reader,
    now: () => RUN_AT,
    graphTimeoutMs: 1_000,
    graphSettlementGraceMs: 50,
    cameraTimeoutMs: 1_000,
    ...overrides,
  };
}

test("offline runner emits one complete URL-free 143+2 batch with exact datasource binding proof", async (context) => {
  const outputRoot = await workspace(context);
  const value = fixture();
  const png = await graphPng();
  const jpeg = await cameraJpeg();
  const graphCalls = [];
  const cameraCalls = [];
  const selectorCalls = [];
  let activeCameraCalls = 0;
  let peakCameraCalls = 0;
  const renderer = rendererContract(async (options) => {
    assert.equal(TRANSPORT_PRIVATE_SENTINEL.length > 0, true);
    graphCalls.push(options);
    return graphResponse(png);
  });
  const cameraTransport = async (options) => {
    assert.equal(TRANSPORT_PRIVATE_SENTINEL.length > 0, true);
    cameraCalls.push(options);
    activeCameraCalls += 1;
    peakCameraCalls = Math.max(peakCameraCalls, activeCameraCalls);
    await new Promise((resolve) => setImmediate(resolve));
    activeCameraCalls -= 1;
    return cameraResponse(jpeg, options.url);
  };
  const result = await runOccurrenceProducer(runnerInput(value, outputRoot, {
    renderer,
    cameraTransport,
    reader: selectorReader(async (request) => {
      selectorCalls.push(request);
      return value.selectorPreconditions;
    }),
  }));

  assert.equal(result.contract, occurrenceProducerRunnerContract.result.contract);
  assert.equal(result.schemaVersion, occurrenceProducerRunnerContract.result.schemaVersion);
  assert.equal(result.graphResult.graphs.length, 143);
  assert.equal(result.exportBatch.graphs.length, 143);
  assert.equal(result.exportBatch.currentMedia.length, 2);
  assert.equal(result.exportBatch.graphs.every(({ probeStatus }) => probeStatus === "success"), true);
  assert.equal(result.exportBatch.currentMedia.every(({ captureStatus }) => captureStatus === "success"), true);
  assert.equal(result.graphResultSha256, digest(canonicalBytes(result.graphResult)));
  assert.equal(result.exportBatchSha256, digest(canonicalBytes(result.exportBatch)));
  assert.equal(result.reportingFeedSha256, reportingFeedEnvelopeSha256(REPORTING_FEED));
  assert.equal(result.exportBatch.reportingFeed.sourceWatermark, REPORTING_FEED.sourceWatermark);
  assert.equal(result.exportBatch.reportingFeed.sourceWatermarkAt, REPORTING_FEED.sourceWatermarkAt);

  assert.deepEqual(result.datasourceBindingProof, {
    contract: "verdify.lab-graph-datasource-binding-proof",
    schemaVersion: 1,
    graphCount: 143,
    legacyOverrideCount: 40,
    reportingDefaultCount: 103,
    legacyByDashboard: [...LEGACY_DASHBOARDS].map(([uid, count]) => ({ uid, count })),
    planSha256: result.datasourceBindingProof.planSha256,
  });
  assert.match(result.datasourceBindingProof.planSha256, /^[0-9a-f]{64}$/u);
  assert.equal(graphCalls.length, 143);
  assert.equal(cameraCalls.length, 2);
  assert.equal(peakCameraCalls >= 1, true);
  assert.equal(peakCameraCalls <= occurrenceProducerRunnerContract.maxCameraConcurrency, true);
  assert.equal(selectorCalls.length, 1);
  assert.deepEqual(selectorCalls[0], {
    contract: "verdify.lab-occurrence-selector-precondition-read-request",
    schemaVersion: 1,
    policySha256: result.policySha256,
    sourceOccurrenceManifestSha256: value.manifestSha256,
    currentMediaOccurrenceIds: value.manifest.currentMedia.map(({ occurrenceId }) => occurrenceId),
  });
  assert.equal(graphCalls.every(({ signal }) => signal.aborted), true);
  assert.equal(cameraCalls.every(({ signal }) => signal.aborted), true);
  assert.deepEqual(result.cameraAttempts.map(({ attempts }) => attempts), [1, 1]);

  const serialized = JSON.stringify(result);
  assert.doesNotMatch(serialized, /https?:|graphs\.verdify\.ai/iu);
  assert.equal(serialized.includes(DATASOURCE_IDENTITY), false);
  assert.equal(serialized.includes(TRANSPORT_PRIVATE_SENTINEL), false);
  for (const source of value.active.cameraUpstream.sources) {
    assert.equal(serialized.includes(source.url), false);
  }
});

test("camera retries are bounded and exhausted failures retain exact LKG preconditions", async (context) => {
  const outputRoot = await workspace(context);
  const value = fixture();
  const invalidJpeg = Buffer.from("not-a-jpeg");
  const callsByUrl = new Map();
  const cameraTransport = async (options) => {
    callsByUrl.set(options.url, (callsByUrl.get(options.url) ?? 0) + 1);
    if (options.url.includes("greenhouse_1")) return new Promise(() => {});
    return cameraResponse(invalidJpeg, options.url);
  };
  const result = await runOccurrenceProducer(runnerInput(value, outputRoot, {
    renderer: rendererContract(async () => graphResponse(Buffer.from("unused"), { status: 503 })),
    cameraTransport,
    reader: selectorReader(async () => value.selectorPreconditions),
    cameraTimeoutMs: 5,
    cameraMaxAttempts: 2,
  }));

  const statusById = new Map(result.exportBatch.currentMedia.map((record) => [record.occurrenceId, record]));
  for (const source of value.active.cameraUpstream.sources) {
    const record = statusById.get(source.occurrenceId);
    assert.equal(record.candidate, null);
    assert.equal(record.expectedSelectionSha256, value.selectorPreconditions.currentMedia
      .find(({ occurrenceId }) => occurrenceId === source.occurrenceId).expectedSelectionSha256);
    assert.equal(
      record.captureStatus,
      source.url.includes("greenhouse_1") ? "timeout" : "decode-error",
    );
    assert.equal(callsByUrl.get(source.url), 2);
  }
  assert.deepEqual(result.cameraAttempts.map(({ attempts }) => attempts), [2, 2]);
  assert.equal(result.executionBounds.cameraMaxAttempts, 2);
  assert.equal(result.exportBatch.graphs.length, 143);
  assert.equal(result.exportBatch.graphs.every(({ probeStatus }) => probeStatus === "http-error"), true);
});

test("the selector reader is sampled once and later race input cannot rewrite validated inputs", async (context) => {
  const outputRoot = await workspace(context);
  const value = fixture();
  const jpeg = await cameraJpeg();
  const mutableSelectors = structuredClone(value.selectorPreconditions);
  const expectedPolicySha256 = digest(canonicalBytes(value.active));
  let selectorReads = 0;
  let mutated = false;
  const result = await runOccurrenceProducer(runnerInput(value, outputRoot, {
    renderer: rendererContract(async () => graphResponse(Buffer.from("unused"), { status: 503 })),
    reader: selectorReader(async () => {
      selectorReads += 1;
      value.active.activation.state = "blocked";
      value.active.cameraUpstream.sources[0].url = "https://example.invalid/mutated-after-validation";
      return mutableSelectors;
    }),
    cameraTransport: async (options) => {
      if (!mutated) {
        mutated = true;
        mutableSelectors.aggregateExpectedSelectionSha256 = "e".repeat(64);
        mutableSelectors.currentMedia[0].expectedSelectionSha256 = "f".repeat(64);
      }
      return cameraResponse(jpeg, options.url);
    },
  }));

  assert.equal(selectorReads, 1);
  assert.equal(result.policySha256, expectedPolicySha256);
  assert.equal(result.exportBatch.expectedSelectionSha256, AGGREGATE_SELECTED_SHA256);
  assert.deepEqual(
    result.exportBatch.currentMedia.map(({ expectedSelectionSha256 }) => expectedSelectionSha256),
    MEDIA_SELECTED_SHA256,
  );
  assert.equal(result.selectorPreconditionsSha256, digest(canonicalBytes(value.selectorPreconditions)));
});

test("blocked policy stops before the selector, clock, renderer, camera, or file dependency", async (context) => {
  const outputRoot = await workspace(context);
  const value = fixture();
  const calls = {
    selector: 0,
    clock: 0,
    renderer: 0,
    camera: 0,
    file: 0,
  };
  await assert.rejects(runOccurrenceProducer({
    ...runnerInput(value, outputRoot, {
      renderer: rendererContract(async () => {
        calls.renderer += 1;
        return graphResponse(Buffer.from("unused"));
      }),
      cameraTransport: async () => {
        calls.camera += 1;
      },
      reader: selectorReader(async () => {
        calls.selector += 1;
        return value.selectorPreconditions;
      }),
      now: () => {
        calls.clock += 1;
        return RUN_AT;
      },
      fileOperations: {
        mkdir: async () => { calls.file += 1; },
      },
    }),
    policy: value.blocked,
  }), /not activated/u);
  assert.deepEqual(calls, {
    selector: 0,
    clock: 0,
    renderer: 0,
    camera: 0,
    file: 0,
  });
});

test("runner source has no default request, environment, store, deployment, or activation binding", async () => {
  const source = await import("node:fs/promises").then(({ readFile }) => readFile(
    new URL("../scripts/lib/occurrence-producer-runner.mjs", import.meta.url),
    "utf8",
  ));
  assert.doesNotMatch(source, /process\.env|fetch\s*\(|@aws-sdk|kubernetes|kubectl|writeAggregate|publishAggregate/iu);
  assert.equal(occurrenceProducerRunnerContract.expectedGraphCount, 143);
  assert.equal(occurrenceProducerRunnerContract.expectedCurrentMediaCount, 2);
  assert.equal(occurrenceProducerRunnerContract.expectedLegacyOverrideCount, 40);
  assert.equal(occurrenceProducerRunnerContract.expectedReportingDefaultCount, 103);
  assert.equal(occurrenceProducerRunnerContract.maxCameraConcurrency, 2);
  assert.equal(occurrenceProducerRunnerContract.maxCameraMaxAttempts, 3);
});
