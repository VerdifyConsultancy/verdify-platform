import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import sharp from "sharp";

import {
  assembleGraphOccurrenceExportBatch,
  graphExportBatchContract,
} from "../scripts/lib/graph-export-batch.mjs";
import {
  draftBlockedOccurrenceExportPolicy,
  occurrenceExportPolicySha256,
  reportingFeedEnvelopeSha256,
  validateOccurrenceExportBatch,
} from "../scripts/lib/occurrence-export-contract.mjs";
import {
  graphExportProducerContract,
  reportingDatasourceIdentitySha256,
} from "../scripts/lib/graph-export-producer.mjs";
import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
  staticOccurrenceManifest,
} from "../scripts/lib/occurrence-release.mjs";

const VALIDATED_AT = "2026-07-13T11:59:00Z";
const ALLOWED_AT = "2026-07-13T12:00:00Z";
const CAPTURED_AT = "2026-07-13T12:01:00Z";
const DATASOURCE_IDENTITY = "operator-reporting-datasource-fixture";
const DATASOURCE_IDENTITY_SHA256 = reportingDatasourceIdentitySha256(DATASOURCE_IDENTITY);
const SELECTED_SHA256 = "a".repeat(64);
const MEDIA_SELECTED_SHA256 = ["b".repeat(64), "c".repeat(64)];
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
  sourceId: "operator-public-reporting-feed-batch-offline",
  sourceClass: "public-reporting-projection",
  credentialClass: "reporting-read-only",
  direction: "one-way-read-only",
  sourceWatermark: "wm_graph_batch_fixture_0001",
  sourceWatermarkAt: ALLOWED_AT,
});

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function digest(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
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
    route: `/evidence/graph-${String(index).padStart(3, "0")}`,
    ordinal: index,
    liveUrl: `https://graphs.verdify.ai/d-solo/${uid}/active?orgId=1&panelId=${index + 1}&theme=light&from=now-24h&to=now`,
    title: `Active graph ${index + 1}`,
  }));
  const cameraSources = [
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080",
  ];
  const currentMediaWithSources = cameraSources
    .map((sourceUrl, index) => ({
      sourceUrl,
      occurrence: discoverCurrentMediaOccurrence({
        route: index === 0 ? "/" : "/greenhouse",
        ordinal: index,
        sourceUrl,
        semanticRole: `Active current still ${index + 1}`,
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
    policyVersion: "offline-graph-batch-v1",
    activatedAt: VALIDATED_AT,
    cameraSources: currentMediaWithSources.map(({ occurrence, sourceUrl }) => ({
      occurrenceId: occurrence.occurrenceId,
      url: sourceUrl,
    })),
  });
  const active = structuredClone(blocked);
  active.activation = {
    ...active.activation,
    state: "active",
    activatedBy: "direct-task",
    activatedAt: ALLOWED_AT,
  };
  const selectorPreconditions = {
    contract: "verdify.lab-occurrence-export-selector-preconditions",
    schemaVersion: 1,
    aggregateExpectedSelectionSha256: SELECTED_SHA256,
    currentMedia: currentMedia.map(({ occurrenceId }, index) => ({
      occurrenceId,
      expectedSelectionSha256: MEDIA_SELECTED_SHA256[index],
    })),
  };
  const activeMediaById = new Map(active.currentMedia.map((record) => [record.occurrenceId, record]));
  const currentMediaRecords = currentMedia.map(({ occurrenceId }, index) => ({
    occurrenceId,
    captureStatus: "missing",
    requestProvenanceSha256: activeMediaById.get(occurrenceId).requestProvenanceSha256,
    candidate: null,
    expectedSelectionSha256: MEDIA_SELECTED_SHA256[index],
  }));
  return {
    graphs,
    currentMedia,
    manifest,
    manifestSha256,
    blocked,
    active,
    selectorPreconditions,
    currentMediaRecords,
  };
}

async function workspace(context) {
  const root = await mkdtemp(path.join(os.tmpdir(), "verdify-graph-batch-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "candidates"));
  return path.join(root, "candidates");
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

function rendererContract(render, overrides = {}) {
  return {
    contract: "verdify.lab-graph-renderer",
    schemaVersion: 3,
    sourceClass: "operator-owned-reporting-tier",
    anonymousAccess: false,
    reportingFeedSha256: reportingFeedEnvelopeSha256(REPORTING_FEED),
    reportingDatasourceIdentitySha256: DATASOURCE_IDENTITY_SHA256,
    abortCooperation: "settle-within-grace-after-abort",
    render,
    ...overrides,
  };
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

function assemblyInput(value, outputRoot, renderer, overrides = {}) {
  return {
    policy: value.active,
    manifest: value.manifest,
    manifestSha256: value.manifestSha256,
    reportingFeed: REPORTING_FEED,
    reportingDatasourceIdentity: DATASOURCE_IDENTITY,
    outputRoot,
    renderer,
    selectorPreconditions: value.selectorPreconditions,
    currentMediaRecords: value.currentMediaRecords,
    now: () => CAPTURED_AT,
    ...overrides,
  };
}

test("assembler emits deterministic canonical URL-free graph and complete selector-bound batch documents", async (context) => {
  const outputRoot = await workspace(context);
  const value = fixture();
  const png = await graphPng();
  const calls = [];
  const indexById = new Map(value.graphs.map(({ occurrenceId }, index) => [occurrenceId, index]));
  const renderer = rendererContract(async (options) => {
    calls.push(options);
    return indexById.get(options.request.occurrenceId) === 0
      ? response(png)
      : response(png, { status: 503 });
  });
  const assembled = await assembleGraphOccurrenceExportBatch(assemblyInput(value, outputRoot, renderer));

  assert.deepEqual(Object.keys(assembled), [
    "contract",
    "schemaVersion",
    "reportingDatasourceIdentitySha256",
    "graphResult",
    "graphResultSha256",
    "exportBatch",
    "exportBatchSha256",
  ]);
  assert.equal(assembled.contract, graphExportBatchContract.result.contract);
  assert.equal(assembled.schemaVersion, graphExportBatchContract.result.schemaVersion);
  assert.equal(assembled.reportingDatasourceIdentitySha256, DATASOURCE_IDENTITY_SHA256);
  assert.equal(assembled.graphResultSha256, digest(canonicalBytes(assembled.graphResult)));
  assert.equal(assembled.exportBatchSha256, digest(canonicalBytes(assembled.exportBatch)));
  assert.equal(assembled.graphResult.graphs.length, 143);
  assert.equal(assembled.exportBatch.graphs.length, 143);
  assert.equal(assembled.exportBatch.currentMedia.length, 2);
  assert.deepEqual(assembled.graphResult.graphs, assembled.exportBatch.graphs);
  assert.equal(new Set(assembled.exportBatch.graphs.map(({ occurrenceId }) => occurrenceId)).size, 143);
  assert.deepEqual(
    assembled.exportBatch.graphs.map(({ occurrenceId }) => occurrenceId),
    value.graphs.map(({ occurrenceId }) => occurrenceId),
  );
  assert.equal(assembled.exportBatch.policySha256, occurrenceExportPolicySha256(value.active));
  assert.equal(assembled.exportBatch.reportingFeed.sourceWatermark, REPORTING_FEED.sourceWatermark);
  assert.equal(assembled.exportBatch.reportingFeed.sourceWatermarkAt, REPORTING_FEED.sourceWatermarkAt);
  assert.equal(assembled.exportBatch.expectedSelectionSha256, SELECTED_SHA256);
  assert.deepEqual(
    assembled.exportBatch.currentMedia.map(({ expectedSelectionSha256 }) => expectedSelectionSha256),
    MEDIA_SELECTED_SHA256,
  );
  assert.match(assembled.exportBatch.batchId, /^batch_graph_[0-9a-f]{32}$/u);
  assert.equal(validateOccurrenceExportBatch(assembled.exportBatch, value.active, CAPTURED_AT).status, "fresh");
  assert.equal(calls.length, 143);
  assert.equal(calls.every(({ signal }) => signal.aborted), true);
  const legacy = calls.filter(({ request }) => LEGACY_DASHBOARDS.has(request.target.uid));
  const regular = calls.filter(({ request }) => !LEGACY_DASHBOARDS.has(request.target.uid));
  assert.equal(legacy.length, 40);
  assert.equal(regular.length, 103);
  assert.equal(legacy.every(({ request }) => (
    request.target.datasourceBinding.mode === "legacy-dashboard-dedicated-override"
    && request.target.datasourceBinding.identitySha256 === DATASOURCE_IDENTITY_SHA256
  )), true);
  assert.equal(regular.every(({ request }) => (
    request.target.datasourceBinding.mode === "reporting-tier-dedicated-default"
    && request.target.datasourceBinding.identitySha256 === DATASOURCE_IDENTITY_SHA256
  )), true);
  assert.doesNotMatch(JSON.stringify(assembled.graphResult), /https?:|graphs\.verdify\.ai|endpoint|authorization|cookie|secret/iu);
  assert.doesNotMatch(JSON.stringify(assembled), new RegExp(DATASOURCE_IDENTITY, "u"));

  const repeated = await assembleGraphOccurrenceExportBatch(assemblyInput(value, outputRoot, renderer));
  assert.deepEqual(repeated, assembled);
});

test("policy, datasource, renderer, media, and selector drift fail before a render call", async (context) => {
  const outputRoot = await workspace(context);
  const value = fixture();
  const png = await graphPng();
  let calls = 0;
  const renderer = rendererContract(async () => {
    calls += 1;
    return response(png);
  });

  await assert.rejects(assembleGraphOccurrenceExportBatch(assemblyInput(value, outputRoot, renderer, {
    policy: value.blocked,
  })), /not activated/u);
  for (const reportingDatasourceIdentity of [
    "P44368ADAD746BC27",
    "verdify-tsdb",
    "anonymous-graphs-source",
    "https://graphs.verdify.ai",
  ]) {
    await assert.rejects(assembleGraphOccurrenceExportBatch(assemblyInput(value, outputRoot, renderer, {
      reportingDatasourceIdentity,
    })), /datasource identity/u);
  }
  await assert.rejects(assembleGraphOccurrenceExportBatch(assemblyInput(value, outputRoot, rendererContract(
    renderer.render,
    { anonymousAccess: true },
  ))), /dedicated feed-bound abort-cooperative v3 contract/u);

  const reorderedSelectors = structuredClone(value.selectorPreconditions);
  reorderedSelectors.currentMedia.reverse();
  await assert.rejects(assembleGraphOccurrenceExportBatch(assemblyInput(value, outputRoot, renderer, {
    selectorPreconditions: reorderedSelectors,
  })), /manifest order/u);
  const wrongMediaPrecondition = structuredClone(value.currentMediaRecords);
  wrongMediaPrecondition[0].expectedSelectionSha256 = "e".repeat(64);
  await assert.rejects(assembleGraphOccurrenceExportBatch(assemblyInput(value, outputRoot, renderer, {
    currentMediaRecords: wrongMediaPrecondition,
  })), /policy- and selector-bound/u);
  const reorderedMedia = structuredClone(value.currentMediaRecords).reverse();
  await assert.rejects(assembleGraphOccurrenceExportBatch(assemblyInput(value, outputRoot, renderer, {
    currentMediaRecords: reorderedMedia,
  })), /manifest order/u);
  for (const relativePath of [
    `https://graphs.verdify.ai/current-media/${value.currentMediaRecords[0].occurrenceId}/${"f".repeat(64)}.png`,
    `current-media/${value.currentMediaRecords[0].occurrenceId}/../${"f".repeat(64)}.png`,
    `current-media/${value.currentMediaRecords[1].occurrenceId}/${"f".repeat(64)}.png`,
    `current-media/${value.currentMediaRecords[0].occurrenceId}/latest.png`,
  ]) {
    const pathDrift = structuredClone(value.currentMediaRecords);
    pathDrift[0] = {
      ...pathDrift[0],
      captureStatus: "success",
      candidate: {
        relativePath,
        mediaType: "image/png",
        capturedAt: CAPTURED_AT,
        requestProvenanceSha256: pathDrift[0].requestProvenanceSha256,
      },
    };
    await assert.rejects(assembleGraphOccurrenceExportBatch(assemblyInput(value, outputRoot, renderer, {
      currentMediaRecords: pathDrift,
    })), /current media candidate does not use the closed batch shape/u);
  }
  assert.equal(calls, 0);
});

test("mixed graph outcomes remain manifest-complete and camera records retain exact selector preconditions", async (context) => {
  const outputRoot = await workspace(context);
  const value = fixture();
  const png = await graphPng();
  const indexById = new Map(value.graphs.map(({ occurrenceId }, index) => [occurrenceId, index]));
  const currentMediaRecords = structuredClone(value.currentMediaRecords);
  currentMediaRecords[0] = {
    ...currentMediaRecords[0],
    captureStatus: "success",
    candidate: {
      relativePath: `current-media/${currentMediaRecords[0].occurrenceId}/${"f".repeat(64)}.png`,
      mediaType: "image/png",
      capturedAt: CAPTURED_AT,
      requestProvenanceSha256: currentMediaRecords[0].requestProvenanceSha256,
    },
  };
  const renderer = rendererContract(async ({ request }) => {
    const index = indexById.get(request.occurrenceId);
    if (index === 0) throw new Error("renderer details must not enter the batch");
    if (index === 1) return response(png, { status: 503 });
    if (index === 2) return response(Buffer.from("not-png"));
    if (index === 3) return response(png);
    return response(png, { status: 503 });
  });
  const assembled = await assembleGraphOccurrenceExportBatch(assemblyInput(value, outputRoot, renderer, {
    currentMediaRecords,
  }));

  assert.equal(assembled.exportBatch.graphs.length, 143);
  assert.deepEqual(
    assembled.exportBatch.graphs.slice(0, 4).map(({ probeStatus }) => probeStatus),
    ["missing", "http-error", "decode-error", "success"],
  );
  assert.equal(assembled.exportBatch.graphs.slice(4).every(({ probeStatus }) => probeStatus === "http-error"), true);
  assert.equal(assembled.exportBatch.currentMedia[0].captureStatus, "success");
  assert.equal(assembled.exportBatch.currentMedia[1].captureStatus, "missing");
  assert.deepEqual(
    assembled.exportBatch.currentMedia.map(({ expectedSelectionSha256 }) => expectedSelectionSha256),
    MEDIA_SELECTED_SHA256,
  );
  assert.equal(validateOccurrenceExportBatch(assembled.exportBatch, value.active, CAPTURED_AT).status, "fresh");
  assert.doesNotMatch(JSON.stringify(assembled.graphResult), /renderer details|https?:|endpoint/iu);
});

test("the assembly module has no default HTTP, environment, credential, store, or deployment binding", async () => {
  const source = await import("node:fs/promises").then(({ readFile }) => readFile(
    new URL("../scripts/lib/graph-export-batch.mjs", import.meta.url),
    "utf8",
  ));
  assert.doesNotMatch(source, /process\.env|fetch\s*\(|https?:\/\/|@aws-sdk|kubernetes|kubectl|secretKey|accessKey/iu);
  assert.equal(graphExportBatchContract.expectedGraphCount, 143);
  assert.equal(graphExportBatchContract.expectedCurrentMediaCount, 2);
  assert.deepEqual(graphExportProducerContract.legacyDatasourceDashboardUids, [...LEGACY_DASHBOARDS.keys()]);
});
