import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import { deflateSync } from "node:zlib";

import {
  canonicalEvidenceBlobUrl,
  LIVE_OCCURRENCE_ATTESTED_ORIGIN,
  normalizeLiveOccurrenceOrigin,
  normalizeLiveOccurrenceTransportOrigin,
  normalizeSha256,
  validateLiveOccurrenceDocuments,
} from "../scripts/lib/live-occurrence-acceptance.mjs";
import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
} from "../scripts/lib/occurrence-release.mjs";
import { verifyLiveOccurrences } from "../scripts/verify-live-occurrences.mjs";

const execFileAsync = promisify(execFile);
const SITE_ROOT = path.resolve(import.meta.dirname, "..");
const CLI = path.join(SITE_ROOT, "scripts/verify-live-occurrences.mjs");
const IMMUTABLE_CACHE = "public, max-age=31536000, immutable";
const ATTESTED_ORIGIN = LIVE_OCCURRENCE_ATTESTED_ORIGIN;

const CRC_TABLE = Array.from({ length: 256 }, (_, value) => {
  let crc = value;
  for (let bit = 0; bit < 8; bit += 1) crc = (crc & 1) === 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  return crc >>> 0;
});

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const typeBytes = Buffer.from(type);
  const result = Buffer.alloc(12 + data.length);
  result.writeUInt32BE(data.length, 0);
  typeBytes.copy(result, 4);
  data.copy(result, 8);
  result.writeUInt32BE(crc32(Buffer.concat([typeBytes, data])), 8 + data.length);
  return result;
}

function png(red, green, blue) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(2, 0);
  header.writeUInt32BE(1, 4);
  header[8] = 8;
  header[9] = 6;
  const scanline = Buffer.from([0, red, green, blue, 255, red, green, blue, 255]);
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(scanline)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function selectedFallback(bytes) {
  const digest = sha256(bytes);
  return {
    publicPath: `/evidence/blobs/sha256/${digest}.png`,
    sha256: digest,
    decodedSha256: "d".repeat(64),
    decodedBytes: 8,
    bytes: bytes.length,
    mediaType: "image/png",
    width: 2,
    height: 1,
    capturedAt: "2026-07-14T00:00:00Z",
    verifiedAt: "2026-07-14T00:00:01Z",
    policyVersion: "lab-stage-occurrence-export-v1",
  };
}

function fixture(origin) {
  const graphBytes = png(20, 80, 40);
  const cameraOneBytes = png(80, 20, 40);
  const cameraTwoBytes = png(40, 20, 80);
  const graphFallback = selectedFallback(graphBytes);
  const cameraOneFallback = selectedFallback(cameraOneBytes);
  const cameraTwoFallback = selectedFallback(cameraTwoBytes);
  const selectedManifestSha256 = "a".repeat(64);
  const graphs = Array.from({ length: 143 }, (_, index) => {
    const discovered = discoverGraphOccurrence({
      route: `/evidence/live-graph-${index}`,
      ordinal: index,
      liveUrl: `https://graphs.verdify.ai/d-solo/site-home/public?panelId=${index + 1}&from=now-24h&to=now&var-zone=all`,
      title: `Live graph ${index}`,
      renderCadenceSeconds: 600,
    });
    return {
      ...discovered,
      selected: {
        ...discovered,
        staleAfterSeconds: Math.max(discovered.renderCadenceSeconds * 2, 1800),
        probeStatus: "success",
        state: "verified",
        fallback: graphFallback,
      },
    };
  });
  const currentMedia = [cameraOneFallback, cameraTwoFallback].map((fallback, index) => {
    const discovered = discoverCurrentMediaOccurrence({
      route: "/greenhouse/cameras",
      ordinal: index,
      sourceUrl: `https://api.verdify.ai/api/v1/public/cameras/greenhouse_${index + 1}/latest.jpg?h=1080`,
      semanticRole: `Current greenhouse view ${index + 1}`,
      captureCadenceSeconds: 300,
    });
    return {
      ...discovered,
      selected: {
        ...discovered,
        policySha256: "b".repeat(64),
        requestProvenanceSha256: sha256(`approved-camera-request-${index}`),
        staleAfterSeconds: Math.max(discovered.captureCadenceSeconds * 2, 900),
        captureStatus: "selected-generation",
        state: "verified",
        fallback,
        pointer: {
          selectionSha256: sha256(`camera-selection-${index}`),
          generation: 1,
          currentGenerationSha256: sha256(`camera-generation-${index}`),
          previousGenerationSha256: null,
        },
      },
    };
  });
  const occurrenceManifest = {
    contract: "verdify.lab-static-occurrence-manifest",
    schemaVersion: 1,
    snapshotId: "fixture-live-occurrence-snapshot",
    selectedManifestSha256,
    graphs,
    currentMedia,
  };
  const occurrenceManifestBytes = canonicalBytes(occurrenceManifest);
  const assets = new Map([
    [graphFallback.publicPath, graphBytes],
    [cameraOneFallback.publicPath, cameraOneBytes],
    [cameraTwoFallback.publicPath, cameraTwoBytes],
  ]);
  const build = {
    contract: "verdify.lab-astro-stage-build",
    schemaVersion: 1,
    siteOrigin: origin,
    stageGlobalNoindex: true,
    snapshotId: occurrenceManifest.snapshotId,
    grafanaOccurrenceCount: 143,
    currentMediaOccurrenceCount: 2,
    cameraOccurrenceCount: 2,
    cameraLocalFallbackCount: 2,
    selectedOccurrenceManifestSha256: `sha256:${selectedManifestSha256}`,
    occurrenceManifestDigest: `sha256:${sha256(occurrenceManifestBytes)}`,
    materializedOccurrenceBlobCount: assets.size,
  };
  return { build, occurrenceManifest, occurrenceManifestBytes, assets };
}

function rebind(value) {
  value.occurrenceManifestBytes = canonicalBytes(value.occurrenceManifest);
  value.build.occurrenceManifestDigest = `sha256:${sha256(value.occurrenceManifestBytes)}`;
  return value;
}

function addUniqueGraphAssets(value, count) {
  for (let index = 0; index < count; index += 1) {
    const bytes = png(index + 1, index + 2, index + 3);
    const fallback = selectedFallback(bytes);
    value.occurrenceManifest.graphs[index].selected.fallback = fallback;
    value.assets.set(fallback.publicPath, bytes);
  }
  value.build.materializedOccurrenceBlobCount = value.assets.size;
  rebind(value);
}

function validate(value, origin) {
  return validateLiveOccurrenceDocuments({
    origin,
    build: value.build,
    occurrenceManifest: value.occurrenceManifest,
    occurrenceManifestBytes: value.occurrenceManifestBytes,
  });
}

async function localServer(context) {
  const state = {
    fixture: null,
    requests: [],
    blobMode: "ok",
    affectedPath: null,
    documentRedirect: false,
    activeBlobs: 0,
    maximumActiveBlobs: 0,
  };
  const server = createServer((request, response) => {
    const pathname = new URL(request.url, "http://fixture.invalid").pathname;
    state.requests.push({
      method: request.method,
      pathname,
      acceptEncoding: request.headers["accept-encoding"],
      host: request.headers.host,
      authorization: request.headers.authorization,
      cookie: request.headers.cookie,
      origin: request.headers.origin,
      referer: request.headers.referer,
    });
    if (state.documentRedirect && pathname === "/static-build.json") {
      response.writeHead(302, { Location: "/redirect-target" });
      response.end();
      return;
    }
    if (pathname === "/redirect-target") {
      response.writeHead(500);
      response.end("redirect followed");
      return;
    }
    if (pathname === "/static-build.json") {
      const bytes = canonicalBytes(state.fixture.build);
      response.writeHead(200, { "Content-Type": "application/json", "Content-Length": bytes.length });
      response.end(bytes);
      return;
    }
    if (pathname === "/occurrence-manifest.json") {
      const bytes = state.fixture.occurrenceManifestBytes;
      response.writeHead(200, { "Content-Type": "application/json", "Content-Length": bytes.length });
      response.end(bytes);
      return;
    }
    const selected = state.fixture.assets.get(pathname);
    if (selected) {
      if (pathname === state.affectedPath && state.blobMode === "redirect") {
        response.writeHead(302, { Location: "/redirect-target" });
        response.end();
        return;
      }
      const contentType = pathname === state.affectedPath && state.blobMode === "mime"
        ? "image/jpeg"
        : "image/png";
      const cacheControl = pathname === state.affectedPath && state.blobMode === "cache"
        ? "no-cache"
        : IMMUTABLE_CACHE;
      let bytes = selected;
      if (pathname === state.affectedPath && state.blobMode === "digest") {
        bytes = Buffer.from(selected);
        bytes[bytes.length - 5] ^= 1;
      }
      if (pathname === state.affectedPath && ["bytes", "stream-overflow"].includes(state.blobMode)) {
        bytes = Buffer.concat([selected, Buffer.from([0])]);
      }
      if (pathname === state.affectedPath && state.blobMode === "signature") {
        bytes = Buffer.from(selected);
        bytes[0] ^= 1;
      }
      const headers = {
        "Content-Type": contentType,
        "Cache-Control": cacheControl,
        ...(pathname === state.affectedPath && state.blobMode === "stream-overflow"
          ? {}
          : { "Content-Length": bytes.length }),
        ...(pathname === state.affectedPath && state.blobMode === "encoding"
          ? { "Content-Encoding": "gzip" }
          : {}),
      };
      const send = () => {
        response.writeHead(200, headers);
        response.end(bytes);
        state.activeBlobs -= 1;
      };
      state.activeBlobs += 1;
      state.maximumActiveBlobs = Math.max(state.maximumActiveBlobs, state.activeBlobs);
      if (state.blobMode === "delay") setTimeout(send, 40);
      else send();
      return;
    }
    response.writeHead(404);
    response.end("missing");
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  context.after(() => new Promise((resolve) => {
    server.close(resolve);
    server.closeAllConnections();
  }));
  const address = server.address();
  const transportOrigin = `http://127.0.0.1:${address.port}`;
  state.fixture = fixture(ATTESTED_ORIGIN);
  state.affectedPath = [...state.fixture.assets.keys()][0];
  return { transportOrigin, state };
}

function publicFixtureFetch(transportOrigin, requests) {
  return async (url, options) => {
    requests.push({ url, options });
    const canonical = new URL(url);
    const response = await fetch(new URL(canonical.pathname, `${transportOrigin}/`), options);
    return {
      status: response.status,
      redirected: response.redirected,
      url,
      headers: response.headers,
      body: response.body,
    };
  };
}

test("digest and origin normalization preserve the raw/prefixed selection contract", () => {
  const digest = "a".repeat(64);
  assert.equal(normalizeSha256(digest), digest);
  assert.equal(normalizeSha256(`sha256:${digest}`), digest);
  assert.throws(() => normalizeSha256(`SHA256:${digest}`), /canonical/u);
  assert.throws(() => normalizeSha256("A".repeat(64)), /canonical/u);
  assert.equal(normalizeLiveOccurrenceOrigin("https://lab-stage.verdify.ai/"), "https://lab-stage.verdify.ai");
  assert.throws(() => normalizeLiveOccurrenceOrigin("https://lab-stage.verdify.ai/path"), /origin/u);
  assert.throws(() => normalizeLiveOccurrenceOrigin("https://user@example.com/"), /origin/u);
  assert.equal(normalizeLiveOccurrenceTransportOrigin("http://127.0.0.1:18080"), "http://127.0.0.1:18080");
  assert.equal(normalizeLiveOccurrenceTransportOrigin("https://127.42.7.9:18443"), "https://127.42.7.9:18443");
  assert.equal(normalizeLiveOccurrenceTransportOrigin("http://[::1]:18080"), "http://[::1]:18080");
  assert.throws(() => normalizeLiveOccurrenceTransportOrigin("http://127.0.0.1:18080/."), /transport origin/u);
});

test("pure document acceptance requires complete 143+2 selected evidence and canonical unique blobs", () => {
  const origin = "https://lab-stage.verdify.ai";
  const value = fixture(origin);
  const result = validate(value, origin);
  assert.equal(result.selectedManifestSha256, `sha256:${"a".repeat(64)}`);
  assert.deepEqual(result.counts, {
    graphs: 143,
    currentMedia: 2,
    occurrences: 145,
    materializedBlobs: 3,
    blobBytes: [...value.assets.values()].reduce((total, bytes) => total + bytes.length, 0),
  });
  assert.equal(result.assets.length, 3);
  for (const asset of result.assets) {
    assert.equal(asset.url, new URL(asset.publicPath, `${origin}/`).href);
    assert.equal(canonicalEvidenceBlobUrl(origin, value.occurrenceManifest.graphs[0].selected.fallback).startsWith(origin), true);
  }
});

for (const [label, mutate, pattern] of [
  ["a pending graph", (value) => { value.occurrenceManifest.graphs[72].selected = null; }, /graph occurrence .* pending/u],
  ["a pending camera", (value) => { value.occurrenceManifest.currentMedia[1].selected = null; }, /camera occurrence .* pending/u],
  ["a missing graph", (value) => { value.occurrenceManifest.graphs.pop(); }, /exactly 143 graph/u],
  ["a duplicate occurrence ID", (value) => { value.occurrenceManifest.graphs[1] = structuredClone(value.occurrenceManifest.graphs[0]); }, /duplicate occurrence ID/u],
  ["a noncanonical occurrence ID", (value) => {
    value.occurrenceManifest.graphs[0].occurrenceId = "graph_000";
    value.occurrenceManifest.graphs[0].selected.occurrenceId = "graph_000";
  }, /static graph occurrence|invalid/u],
  ["a misbound selected identity", (value) => { value.occurrenceManifest.graphs[0].selected.occurrenceId = value.occurrenceManifest.graphs[1].occurrenceId; }, /not bound to its discovery identity/u],
  ["misbound selected camera provenance", (value) => { value.occurrenceManifest.currentMedia[0].selected.sourceProvenanceSha256 = "f".repeat(64); }, /not bound to its discovery identity/u],
  ["a noncanonical path", (value) => { value.occurrenceManifest.graphs[0].selected.fallback.publicPath = "/evidence/blobs/sha256/../image.png"; }, /canonical evidence blob path/u],
  ["a foreign origin path", (value) => { value.occurrenceManifest.graphs[0].selected.fallback.publicPath = `https://other.invalid/evidence/blobs/sha256/${value.occurrenceManifest.graphs[0].selected.fallback.sha256}.png`; }, /canonical evidence blob path/u],
  ["conflicting metadata for one blob digest", (value) => {
    const graphFallback = value.occurrenceManifest.graphs[0].selected.fallback;
    value.occurrenceManifest.currentMedia[0].selected.fallback = {
      ...value.occurrenceManifest.currentMedia[0].selected.fallback,
      publicPath: graphFallback.publicPath,
      sha256: graphFallback.sha256,
      bytes: graphFallback.bytes,
      decodedSha256: "e".repeat(64),
    };
    value.build.materializedOccurrenceBlobCount = 2;
  }, /conflicting selected metadata/u],
  ["a materialized-count mismatch", (value) => { value.build.materializedOccurrenceBlobCount += 1; }, /materialized occurrence blob count/u],
  ["a selection mismatch", (value) => { value.build.selectedOccurrenceManifestSha256 = `sha256:${"b".repeat(64)}`; }, /select different releases/u],
  ["a missing stage noindex binding", (value) => { value.build.stageGlobalNoindex = false; }, /stage noindex binding/u],
  ["a raw build selection", (value) => { value.build.selectedOccurrenceManifestSha256 = "a".repeat(64); }, /prefixed\/raw representations/u],
  ["a prefixed manifest selection", (value) => { value.occurrenceManifest.selectedManifestSha256 = `sha256:${"a".repeat(64)}`; }, /prefixed\/raw representations/u],
  ["an aggregate byte overflow", (value) => {
    for (let index = 0; index < 33; index += 1) {
      const digest = sha256(`oversized-fixture-${index}`);
      value.occurrenceManifest.graphs[index].selected.fallback = {
        ...value.occurrenceManifest.graphs[index].selected.fallback,
        publicPath: `/evidence/blobs/sha256/${digest}.png`,
        sha256: digest,
        bytes: 32 * 1024 * 1024,
      };
    }
    value.build.materializedOccurrenceBlobCount = 36;
  }, /aggregate live-acceptance byte bound/u],
]) {
  test(`pure document acceptance rejects ${label}`, () => {
    const origin = "https://lab-stage.verdify.ai";
    const value = fixture(origin);
    mutate(value);
    rebind(value);
    assert.throws(() => validate(value, origin), pattern);
  });
}

test("pure document acceptance binds the exact served occurrence-manifest bytes", () => {
  const origin = "https://lab-stage.verdify.ai";
  const value = fixture(origin);
  value.occurrenceManifestBytes = Buffer.concat([value.occurrenceManifestBytes, Buffer.from(" ")]);
  assert.throws(() => validate(value, origin), /manifest bytes/u);
});

test("public-default verifier requests only canonical attested-origin paths", async (context) => {
  const { transportOrigin, state } = await localServer(context);
  const requests = [];
  const result = await verifyLiveOccurrences({
    origin: ATTESTED_ORIGIN,
    fetchImpl: publicFixtureFetch(transportOrigin, requests),
  });
  assert.equal(result.origin, ATTESTED_ORIGIN);
  assert.equal(requests.length, 5);
  assert.equal(requests.every(({ url }) => new URL(url).origin === ATTESTED_ORIGIN), true);
  assert.equal(requests.every(({ options }) => options.credentials === "omit"), true);
  assert.equal(requests.every(({ options }) => options.referrerPolicy === "no-referrer"), true);
  assert.equal(requests.every(({ options }) => !Object.hasOwn(options.headers, "Host")), true);
  assert.equal(requests.every(({ options }) => !Object.hasOwn(options.headers, "Origin")), true);
  assert.equal(requests.every(({ options }) => !Object.hasOwn(options.headers, "Authorization")), true);
  assert.equal(requests.every(({ options }) => !Object.hasOwn(options.headers, "Cookie")), true);
  assert.equal(state.requests.every(({ acceptEncoding }) => acceptEncoding === "identity"), true);
});

test("explicit internal transport accepts 143 graphs, two cameras, and every canonical immutable PNG", async (context) => {
  const { transportOrigin, state } = await localServer(context);
  const result = await verifyLiveOccurrences({ origin: ATTESTED_ORIGIN, transportOrigin });
  assert.equal(result.contract, "verdify.lab-live-occurrence-acceptance");
  assert.equal(result.origin, ATTESTED_ORIGIN);
  assert.deepEqual(result.counts, {
    graphs: 143,
    currentMedia: 2,
    occurrences: 145,
    materializedBlobs: 3,
    blobBytes: [...state.fixture.assets.values()].reduce((total, bytes) => total + bytes.length, 0),
  });
  assert.equal(result.blobs.length, 3);
  assert.equal(result.totalBlobBytes, [...state.fixture.assets.values()].reduce((total, value) => total + value.length, 0));
  assert.equal(state.requests.filter(({ pathname }) => state.fixture.assets.has(pathname)).length, 3);
  assert.equal(state.requests.every(({ method }) => method === "GET"), true);
  assert.equal(state.requests.every(({ acceptEncoding }) => acceptEncoding === "identity"), true);
  assert.deepEqual([...new Set(state.requests.map(({ host }) => host))], [new URL(transportOrigin).host]);
  assert.equal(state.requests.every(({ authorization, cookie, origin, referer }) => (
    authorization === undefined
    && cookie === undefined
    && origin === undefined
    && referer === undefined
  )), true);
});

test("executable verifier emits the canonical acceptance report against a local fixture", async (context) => {
  const { transportOrigin } = await localServer(context);
  const { stdout, stderr } = await execFileAsync(process.execPath, [
    CLI,
    "--origin",
    ATTESTED_ORIGIN,
    "--transport-origin",
    transportOrigin,
  ], {
    cwd: SITE_ROOT,
    timeout: 30_000,
  });
  assert.equal(stderr, "");
  const report = JSON.parse(stdout);
  assert.equal(report.contract, "verdify.lab-live-occurrence-acceptance");
  assert.equal(report.origin, ATTESTED_ORIGIN);
  assert.equal(report.counts.occurrences, 145);
});

test("live verifier rejects an attested-origin mismatch before accepting transported documents", async (context) => {
  const { transportOrigin, state } = await localServer(context);
  state.fixture.build.siteOrigin = "https://other.invalid";
  await assert.rejects(
    () => verifyLiveOccurrences({ origin: ATTESTED_ORIGIN, transportOrigin }),
    /live origin/u,
  );

  let requests = 0;
  await assert.rejects(
    () => verifyLiveOccurrences({
      origin: "https://other.invalid",
      transportOrigin,
      fetchImpl: async () => {
        requests += 1;
        throw new Error("must not request");
      },
    }),
    /bound to https:\/\/lab-stage\.verdify\.ai/u,
  );
  assert.equal(requests, 0);
});

for (const transportOrigin of [
  "ftp://127.0.0.1:18080",
  "http://user@127.0.0.1:18080",
  "http://127.0.0.1:18080/path",
  "http://127.0.0.1:18080?query=1",
  "http://127.0.0.1:18080#fragment",
  "http://localhost:18080",
  "http://pod.internal:18080",
  "http://10.0.0.1:18080",
  "http://172.16.0.1:18080",
  "http://192.168.1.1:18080",
  "http://8.8.8.8:18080",
  "http://[::2]:18080",
  "http://[fd00::1]:18080",
  "http://[::ffff:127.0.0.1]:18080",
]) {
  test(`live verifier rejects unsafe explicit transport ${transportOrigin}`, async () => {
    let requests = 0;
    await assert.rejects(
      () => verifyLiveOccurrences({
        origin: ATTESTED_ORIGIN,
        transportOrigin,
        fetchImpl: async () => {
          requests += 1;
          throw new Error("must not request");
        },
      }),
      /transport origin/u,
    );
    assert.equal(requests, 0);
  });
}

for (const [mode, pattern] of [
  ["redirect", /unredirected HTTP 200/u],
  ["mime", /wrong MIME type/u],
  ["cache", /immutable caching/u],
  ["digest", /selected SHA-256/u],
  ["bytes", /Content-Length differs|byte bound/u],
  ["signature", /PNG signature/u],
  ["stream-overflow", /byte bound while streaming/u],
  ["encoding", /unsupported content encoding/u],
]) {
  test(`bounded live verifier rejects blob ${mode} drift`, async (context) => {
    const { transportOrigin, state } = await localServer(context);
    state.blobMode = mode;
    await assert.rejects(
      () => verifyLiveOccurrences({ origin: ATTESTED_ORIGIN, transportOrigin }),
      pattern,
    );
    if (mode === "redirect") {
      assert.equal(state.requests.some(({ pathname }) => pathname === "/redirect-target"), false);
    }
  });
}

test("Content-Length rejection cancels the unread blob response body", async (context) => {
  const { transportOrigin } = await localServer(context);
  let cancellations = 0;
  const fetchImpl = async (url, options) => {
    if (!new URL(url).pathname.startsWith("/evidence/blobs/sha256/")) {
      return fetch(url, options);
    }
    return {
      status: 200,
      redirected: false,
      url,
      headers: new Headers({
        "Content-Type": "image/png",
        "Cache-Control": IMMUTABLE_CACHE,
        "Content-Length": "1",
      }),
      body: new ReadableStream({
        cancel() {
          cancellations += 1;
        },
      }),
    };
  };
  await assert.rejects(
    () => verifyLiveOccurrences({
      origin: ATTESTED_ORIGIN,
      transportOrigin,
      fetchImpl,
      concurrency: 1,
    }),
    /Content-Length differs/u,
  );
  assert.equal(cancellations, 1);
});

test("live blob requests never exceed the fixed concurrency ceiling", async (context) => {
  const { transportOrigin, state } = await localServer(context);
  addUniqueGraphAssets(state.fixture, 8);
  state.blobMode = "delay";
  const report = await verifyLiveOccurrences({
    origin: ATTESTED_ORIGIN,
    transportOrigin,
    concurrency: 4,
  });
  assert.equal(report.counts.materializedBlobs, 11);
  assert.equal(state.maximumActiveBlobs, 4);
});

test("bounded live verifier refuses document redirects without following them", async (context) => {
  const { transportOrigin, state } = await localServer(context);
  state.documentRedirect = true;
  await assert.rejects(
    () => verifyLiveOccurrences({ origin: ATTESTED_ORIGIN, transportOrigin }),
    /unredirected HTTP 200/u,
  );
  assert.equal(state.requests.some(({ pathname }) => pathname === "/redirect-target"), false);
});
