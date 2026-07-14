import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  draftBlockedOccurrenceExportPolicy,
  reportingFeedEnvelopeSha256,
} from "../scripts/lib/occurrence-export-contract.mjs";
import { reportingDatasourceIdentitySha256 } from "../scripts/lib/graph-export-producer.mjs";
import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
  staticOccurrenceManifest,
} from "../scripts/lib/occurrence-release.mjs";
import {
  REPORTING_DATASOURCE_IDENTITY,
  REPORTING_GATEWAY_ORIGIN,
  REPORTING_GRAPH_CONCURRENCY,
  REPORTING_PROJECTION_ORIGIN,
  REPORTING_WATERMARK_PATH,
  REPORTING_WATERMARK_SQL,
  REPORTING_WATERMARK_TIMEOUT_MS,
  createReportingTierRenderer,
  readReportingProjectionWatermark,
  runReportingOccurrenceProducerOnce,
} from "../scripts/lib/reporting-tier-runtime.mjs";

const SITE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(SITE_ROOT, "..");
const TARGETS_FILE = path.join(SITE_ROOT, "config/lab-stage-reporting-targets.json");
const DEPENDENCIES_FILE = path.join(SITE_ROOT, "config/lab-stage-reporting-dependencies.json");
const READINESS_SQL_FILE = path.join(
  REPO_ROOT,
  "deploy/k8s/overlays/lab-stage/reporting-tier/projection-readiness.sql",
);
const WATERMARK_AT = "2026-07-14T03:45:00Z";
const WATERMARK_DOCUMENT = Object.freeze({
  contract: "verdify.lab-reporting-projection-watermark",
  schemaVersion: 1,
  feedId: "lab-public-v1",
  sourceWatermark: "wm_stage_fixture_0001",
  sourceWatermarkAt: WATERMARK_AT,
  projectionReadOnly: true,
  trackAPrimaryCredential: false,
});
const REPORTING_FEED = Object.freeze({
  contract: "verdify.operator-public-reporting-feed",
  schemaVersion: 1,
  sourceId: "operator-public-reporting-feed-lab-stage",
  sourceClass: "public-reporting-projection",
  credentialClass: "reporting-read-only",
  direction: "one-way-read-only",
  sourceWatermark: WATERMARK_DOCUMENT.sourceWatermark,
  sourceWatermarkAt: WATERMARK_AT,
});

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function response(document, overrides = {}) {
  const bytes = Buffer.from(JSON.stringify(document));
  return new Response(bytes, {
    status: 200,
    headers: {
      "content-type": "application/json",
      "content-length": String(bytes.length),
    },
    ...overrides,
  });
}

async function targets() {
  return JSON.parse(await readFile(TARGETS_FILE, "utf8"));
}

function graphRequest(target) {
  return {
    contract: "verdify.lab-graph-render-request",
    schemaVersion: 3,
    occurrenceId: target.occurrenceId,
    occurrenceSha256: "a".repeat(64),
    reportingFeedSha256: reportingFeedEnvelopeSha256(REPORTING_FEED),
    target: {
      uid: target.uid,
      panelId: target.panelId,
      query: structuredClone(target.query),
      variables: structuredClone(target.variables),
      timeRange: { ...target.timeRange },
      datasourceBinding: {
        mode: "reporting-tier-dedicated-default",
        identitySha256: reportingDatasourceIdentitySha256(REPORTING_DATASOURCE_IDENTITY),
      },
    },
  };
}

function exactGraphUrl(target, index) {
  const url = new URL(`https://graphs.verdify.ai/d-solo/${target.uid}/fixture-${index}`);
  for (const [key, values] of Object.entries(target.query)) {
    for (const value of values) url.searchParams.append(key, value);
  }
  for (const [key, values] of Object.entries(target.variables)) {
    for (const value of values) url.searchParams.append(`var-${key}`, value);
  }
  return url.toString();
}

async function producerFixture() {
  const originalTargets = await targets();
  const discoveredGraphs = originalTargets.occurrences.map((target, index) => discoverGraphOccurrence({
    route: `/fixture/reporting-${String(index).padStart(3, "0")}`,
    ordinal: index,
    liveUrl: exactGraphUrl(target, index),
    title: `Reporting fixture ${index}`,
    renderCadenceSeconds: 900,
  }));
  const cameraSources = [
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080",
  ];
  const discoveredCurrentMedia = cameraSources.map((sourceUrl, index) => discoverCurrentMediaOccurrence({
    route: "/",
    ordinal: 1000 + index,
    sourceUrl,
    semanticRole: `Reporting fixture camera ${index + 1}`,
  }));
  const manifest = staticOccurrenceManifest({
    snapshotId: `sanitized-content-sha256:${"d".repeat(64)}`,
    discoveredGraphs,
    discoveredCurrentMedia,
  });
  const manifestSha256 = sha256(canonicalBytes(manifest));
  const blockedPolicy = draftBlockedOccurrenceExportPolicy({
    manifest,
    manifestSha256,
    policyVersion: "reporting-runtime-fixture-v1",
    approvedAt: "2026-07-14T03:40:00Z",
    cameraSources: discoveredCurrentMedia.map(({ occurrenceId }, index) => ({
      occurrenceId,
      url: cameraSources[index],
    })),
  });
  const policy = structuredClone(blockedPolicy);
  policy.activation = {
    ...policy.activation,
    state: "approved",
    approvedBy: "jason",
    approvedAt: "2026-07-14T03:41:00Z",
  };
  const reportingTargets = structuredClone(originalTargets);
  reportingTargets.sourceOccurrenceManifestSha256 = manifestSha256;
  reportingTargets.snapshotId = manifest.snapshotId;
  reportingTargets.occurrences = reportingTargets.occurrences.map((target, index) => ({
    ...target,
    occurrenceId: discoveredGraphs[index].occurrenceId,
  }));
  return { manifest, manifestSha256, policy, blockedPolicy, reportingTargets };
}

test("projection watermark uses one closed read-only response and the fixed private route", async () => {
  const calls = [];
  const feed = await readReportingProjectionWatermark({
    fetchImpl: async (...args) => {
      calls.push(args);
      return response(WATERMARK_DOCUMENT);
    },
  });
  assert.deepEqual(feed, REPORTING_FEED);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], `${REPORTING_PROJECTION_ORIGIN}${REPORTING_WATERMARK_PATH}`);
  assert.deepEqual(calls[0][1], {
    method: "GET",
    redirect: "error",
    credentials: "omit",
    headers: { accept: "application/json" },
    signal: calls[0][1].signal,
  });
  assert.equal(calls[0][1].signal instanceof AbortSignal, true);
  assert.equal(calls[0][1].signal.aborted, false);
});

test("projection watermark fails closed on shape, authority, media type, and size drift", async () => {
  for (const makeResponse of [
    () => response({ ...WATERMARK_DOCUMENT, projectionReadOnly: false }),
    () => response({ ...WATERMARK_DOCUMENT, trackAPrimaryCredential: true }),
    () => response({ ...WATERMARK_DOCUMENT, extra: true }),
    () => response(WATERMARK_DOCUMENT, { headers: { "content-type": "text/plain" } }),
    () => response(WATERMARK_DOCUMENT, { headers: { "content-type": "application/json", "content-length": "5000" } }),
  ]) {
    await assert.rejects(
      readReportingProjectionWatermark({ fetchImpl: async () => makeResponse() }),
      /reporting projection/u,
    );
  }
});

test("projection watermark deadline aborts transport and cancels an unsettled body", async () => {
  let signal;
  let cancellations = 0;
  const startedAt = Date.now();
  await assert.rejects(
    readReportingProjectionWatermark({
      timeoutMs: 20,
      fetchImpl: async (_url, options) => {
        signal = options.signal;
        return {
          status: 200,
          redirected: false,
          headers: { get: (name) => name === "content-type" ? "application/json" : null },
          body: {
            cancel: async () => { cancellations += 1; },
            getReader: () => ({
              read: async () => new Promise(() => {}),
              cancel: async () => { cancellations += 1; },
              releaseLock: () => {},
            }),
          },
        };
      },
    }),
    /exceeded its deadline/u,
  );
  assert.equal(signal instanceof AbortSignal, true);
  assert.equal(signal.aborted, true);
  assert.equal(cancellations, 1);
  assert.equal(Date.now() - startedAt < 500, true);
  await assert.rejects(
    readReportingProjectionWatermark({ fetchImpl: async () => response(WATERMARK_DOCUMENT), timeoutMs: 0 }),
    /deadline is invalid/u,
  );
  await assert.rejects(
    readReportingProjectionWatermark({
      fetchImpl: async () => response(WATERMARK_DOCUMENT),
      timeoutMs: REPORTING_WATERMARK_TIMEOUT_MS + 1,
    }),
    /deadline is invalid/u,
  );
});

test("renderer permits one inventory-bound private PNG route with fixed render controls", async () => {
  const value = await targets();
  const target = value.occurrences[0];
  const calls = [];
  const pngBody = Buffer.from("fixture-png");
  const renderer = createReportingTierRenderer({
    targets: value,
    reportingFeed: REPORTING_FEED,
    fetchImpl: async (...args) => {
      calls.push(args);
      return new Response(pngBody, {
        status: 200,
        headers: { "content-type": "image/png", "content-length": String(pngBody.length) },
      });
    },
  });
  const result = await renderer.render({
    request: graphRequest(target),
    signal: new AbortController().signal,
  });
  assert.equal(calls.length, 1);
  const url = new URL(calls[0][0]);
  assert.equal(url.origin, REPORTING_GATEWAY_ORIGIN);
  assert.equal(url.pathname, `/render/d-solo/${target.uid}`);
  assert.equal(url.searchParams.get("panelId"), target.panelId);
  assert.equal(url.searchParams.get("width"), "1000");
  assert.equal(url.searchParams.get("height"), "400");
  assert.equal(url.searchParams.get("tz"), "America/Denver");
  assert.deepEqual(calls[0][1], {
    method: "GET",
    redirect: "error",
    credentials: "omit",
    headers: { accept: "image/png" },
    signal: calls[0][1].signal,
  });
  assert.equal(result.status, 200);
  assert.equal(result.contentType, "image/png");
  assert.equal(result.contentLength, pngBody.length);
  assert.equal(result.body instanceof ReadableStream, true);
});

test("renderer rejects target drift before making a gateway request", async () => {
  const value = await targets();
  let calls = 0;
  const renderer = createReportingTierRenderer({
    targets: value,
    reportingFeed: REPORTING_FEED,
    fetchImpl: async () => { calls += 1; },
  });
  const request = graphRequest(value.occurrences[0]);
  request.target.query.panelId = ["999999"];
  await assert.rejects(
    renderer.render({ request, signal: new AbortController().signal }),
    /exact reporting target inventory/u,
  );
  assert.equal(calls, 0);
});

test("one-shot seam binds approval, exact manifest bytes, projection, and producer in order", async () => {
  const fixture = await producerFixture();
  const calls = [];
  const selectorPreconditionReader = Object.freeze({ marker: "injected-selector-reader" });
  const result = await runReportingOccurrenceProducerOnce({
    ...fixture,
    targets: fixture.reportingTargets,
    outputRoot: "/tmp/reporting-runtime-fixture",
    selectorPreconditionReader,
    fetchImpl: async (...args) => {
      calls.push(["fetch", ...args]);
      return response(WATERMARK_DOCUMENT);
    },
    produce: async (input) => {
      calls.push(["produce", input]);
      return { status: "fixture-produced" };
    },
  });
  assert.deepEqual(result, { status: "fixture-produced" });
  assert.deepEqual(calls.map(([kind]) => kind), ["fetch", "produce"]);
  assert.deepEqual(calls[1][1].reportingFeed, REPORTING_FEED);
  assert.equal(calls[1][1].reportingDatasourceIdentity, REPORTING_DATASOURCE_IDENTITY);
  assert.equal(calls[1][1].selectorPreconditionReader, selectorPreconditionReader);
  assert.equal(calls[1][1].renderer.anonymousAccess, false);
  assert.equal(typeof calls[1][1].cameraTransport, "function");
  assert.equal(calls[1][1].graphConcurrency, REPORTING_GRAPH_CONCURRENCY);
});

test("one-shot seam makes no request when approval or source binding is absent", async () => {
  const fixture = await producerFixture();
  for (const mutation of [
    (input) => { input.policy = fixture.blockedPolicy; },
    (input) => { input.manifestSha256 = "f".repeat(64); },
    (input) => { input.targets.sourceOccurrenceManifestSha256 = "e".repeat(64); },
  ]) {
    let requests = 0;
    const input = {
      ...fixture,
      targets: structuredClone(fixture.reportingTargets),
      outputRoot: "/tmp/reporting-runtime-fixture",
      selectorPreconditionReader: {},
      fetchImpl: async () => { requests += 1; },
      produce: async () => { throw new Error("producer must not run"); },
    };
    mutation(input);
    await assert.rejects(runReportingOccurrenceProducerOnce(input), /occurrence|reporting/u);
    assert.equal(requests, 0);
  }
});

test("projection bootstrap carries the exact runtime watermark query and no mutations", async () => {
  const sql = await readFile(READINESS_SQL_FILE, "utf8");
  const dependencies = JSON.parse(await readFile(DEPENDENCIES_FILE, "utf8"));
  const requiredRelations = sql.slice(
    sql.indexOf("WITH required_relations(name)"),
    sql.indexOf("), approved_relations(name)"),
  ).match(/'([a-z_][a-z0-9_]*)'::name/gu).map((entry) => entry.slice(1, entry.indexOf("'", 1)));
  const requiredFunctions = sql.slice(
    sql.indexOf("WITH required_functions(name)"),
    sql.indexOf("), actual_functions AS"),
  ).match(/'([a-z_][a-z0-9_]*)'::name/gu).map((entry) => entry.slice(1, entry.indexOf("'", 1)));
  assert.match(sql, /BEGIN TRANSACTION READ ONLY;/u);
  assert.match(sql, /ROLLBACK;\n$/u);
  assert.match(sql, /current_database\(\) = 'verdify_lab_reporting_stage'/u);
  assert.match(sql, /current_user = 'verdify_lab_reporting_reader'/u);
  assert.match(sql, /to_regclass\('lab_reporting\.source_watermark_v1'\)/u);
  assert.deepEqual(requiredRelations, dependencies.relations);
  assert.deepEqual(requiredFunctions, dependencies.callableProjectionFunctions);
  assert.match(sql, /no_missing_relations/u);
  assert.match(sql, /no_extra_relations/u);
  assert.match(sql, /no_missing_functions/u);
  assert.match(sql, /no_extra_functions/u);
  assert.match(sql, /count\(\*\) = 1 AS exactly_one/u);
  assert.match(sql, new RegExp(REPORTING_WATERMARK_SQL.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&"), "u"));
  assert.doesNotMatch(sql, /^\s*(?:CREATE|ALTER|DROP|GRANT|REVOKE|INSERT|UPDATE|DELETE|TRUNCATE)\b/imu);
});
