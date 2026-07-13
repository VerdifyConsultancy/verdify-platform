import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cp, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { deflateSync } from "node:zlib";

import {
  loadCompilerOccurrenceBinding,
  verifyCompilerOccurrenceDiscovery,
  verifyCompleteSelectedOccurrenceEvidence,
} from "../scripts/compile-snapshot.mjs";
import {
  draftBlockedOccurrenceExportPolicy,
  occurrenceExportPolicySha256,
  staticOccurrenceDiscoveryProjection,
  staticOccurrenceDiscoverySha256,
} from "../scripts/lib/occurrence-export-contract.mjs";
import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
  currentMediaGenerationPayloadSha256,
  occurrenceReleasePayloadSha256,
  publishCurrentMediaGeneration,
  publishOccurrenceRelease,
  staticOccurrenceManifest,
} from "../scripts/lib/occurrence-release.mjs";
import { S3OccurrenceReleaseStore } from "../scripts/lib/occurrence-release-store.mjs";
import { verifySelectedEvidence } from "../scripts/verify-production-output.mjs";

const SITE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const S3_BUCKET = "verdify-lab-releases";
const S3_PREFIX = "compiler-reader-offline";
const S3_TYPED_PREFIX = `${S3_PREFIX}/occurrence-releases/v1`;
const S3_LOCATION = `s3://${S3_BUCKET}/${S3_PREFIX}`;

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

function pngChunk(type, data) {
  const typeBytes = Buffer.from(type);
  const result = Buffer.alloc(12 + data.length);
  result.writeUInt32BE(data.length, 0);
  typeBytes.copy(result, 4);
  data.copy(result, 8);
  result.writeUInt32BE(crc32(Buffer.concat([typeBytes, data])), 8 + data.length);
  return result;
}

function fixturePng() {
  const width = 320;
  const height = 180;
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 6;
  const scanlines = Buffer.alloc((1 + (width * 4)) * height);
  for (let row = 0; row < height; row += 1) {
    const start = row * (1 + (width * 4));
    scanlines[start] = 0;
    for (let column = 0; column < width; column += 1) {
      const pixel = start + 1 + (column * 4);
      scanlines[pixel] = 20;
      scanlines[pixel + 1] = 80;
      scanlines[pixel + 2] = 40;
      scanlines[pixel + 3] = 255;
    }
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk("IHDR", header),
    pngChunk("IDAT", deflateSync(scanlines)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function missingS3Object() {
  const error = new Error("object is absent");
  error.name = "NoSuchKey";
  error.$metadata = { httpStatusCode: 404 };
  return error;
}

class FakeReadOnlyS3Client {
  constructor() {
    this.objects = new Map();
    this.commands = [];
  }

  seed(key, bytes) {
    this.objects.set(`${S3_BUCKET}/${key}`, Buffer.from(bytes));
  }

  async send(command) {
    const name = command.constructor.name;
    const input = command.input;
    this.commands.push({ name, bucket: input.Bucket, key: input.Key });
    if (name !== "GetObjectCommand") throw new Error(`unexpected compiler store command ${name}`);
    const bytes = this.objects.get(`${input.Bucket}/${input.Key}`);
    if (bytes === undefined) throw missingS3Object();
    return {
      ETag: `"fake-${sha256(bytes).slice(0, 16)}"`,
      ContentLength: bytes.length,
      Body: (async function* body() {
        for (let offset = 0; offset < bytes.length; offset += 1024) {
          yield bytes.subarray(offset, offset + 1024);
        }
      })(),
    };
  }
}

async function seedFakeS3FromLocalStore(client, storeRoot) {
  const pending = [[storeRoot, ""]];
  while (pending.length > 0) {
    const [directory, prefix] = pending.pop();
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) pending.push([absolute, relative]);
      else if (entry.isFile()) client.seed(`${S3_TYPED_PREFIX}/${relative}`, await readFile(absolute));
      else throw new Error("local occurrence fixture contains a special file");
    }
  }
}

function discoveredOccurrences(sourceSnapshotManifestSha256) {
  const graphs = Array.from({ length: 143 }, (_, index) => discoverGraphOccurrence({
    route: `/evidence/graph-${index + 1}`,
    ordinal: 0,
    liveUrl: `https://graphs.verdify.ai/d-solo/site-home/public?panelId=${index + 1}&from=now-24h&to=now`,
    title: `Graph ${index + 1}`,
  }));
  const mediaSources = Array.from({ length: 2 }, (_, index) => ({
    route: `/greenhouse/camera-${index + 1}`,
    ordinal: 0,
    sourceUrl: `https://api.verdify.ai/api/v1/public/cameras/greenhouse_${index + 1}/latest.jpg?h=1080`,
    semanticRole: `Current greenhouse view ${index + 1}`,
  }));
  const currentMedia = mediaSources.map((source) => discoverCurrentMediaOccurrence(source));
  const manifest = staticOccurrenceManifest({
    snapshotId: `snapshot-sha256:${sourceSnapshotManifestSha256}`,
    discoveredGraphs: graphs,
    discoveredCurrentMedia: currentMedia,
  });
  return { graphs, currentMedia, mediaSources, manifest };
}

async function selectedReleaseFixture(context) {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-compiler-occurrence-binding-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  const sourceRoot = path.join(root, "source");
  const storeRoot = path.join(root, "store");
  await Promise.all([mkdir(sourceRoot), mkdir(storeRoot)]);

  const sourceSnapshotManifestSha256 = sha256("compiler-binding-snapshot");
  const discovered = discoveredOccurrences(sourceSnapshotManifestSha256);
  const blockedPolicy = draftBlockedOccurrenceExportPolicy({
    manifest: discovered.manifest,
    manifestSha256: staticOccurrenceDiscoverySha256(discovered.manifest),
    policyVersion: "compiler-occurrence-export-v1",
    approvedAt: "2026-07-13T17:00:00Z",
    cameraSources: discovered.mediaSources.map(({ sourceUrl: url }, index) => ({
      occurrenceId: discovered.currentMedia[index].occurrenceId,
      url,
    })),
  });
  const policy = {
    ...blockedPolicy,
    activation: {
      ...blockedPolicy.activation,
      state: "approved",
      approvedBy: "jason",
      approvedAt: "2026-07-13T17:00:00Z",
    },
  };
  const policyPath = path.join(root, "occurrence-policy.json");
  const blockedPolicyPath = path.join(root, "blocked-occurrence-policy.json");
  await writeFile(policyPath, canonicalBytes(policy));
  await writeFile(blockedPolicyPath, canonicalBytes(blockedPolicy));
  const policySha256 = occurrenceExportPolicySha256(policy);
  const request = {
    sourceSnapshotManifestSha256,
    policyVersion: policy.policyVersion,
    policySha256,
    graphs: [],
    currentMedia: [],
  };
  const event = {
    contract: "verdify.lab-release-trigger",
    schemaVersion: 1,
    eventId: "evt_compiler_binding_0001",
    eventType: "planner-completed",
    sourceId: "compiler-occurrence-binding-test",
    sourceWatermark: "wm_compiler_binding_0001",
    occurredAt: "2026-07-13T17:00:00Z",
    payloadSha256: occurrenceReleasePayloadSha256(request),
  };
  const published = await publishOccurrenceRelease({
    storeRoot,
    sourceRoot,
    event,
    ...request,
    publishedAt: "2026-07-13T17:01:00Z",
  });

  return {
    root,
    storeRoot,
    policy,
    policyPath,
    blockedPolicyPath,
    policySha256,
    published,
    discovered,
    snapshot: {
      manifestDigest: `sha256:${sourceSnapshotManifestSha256}`,
      sanitization: { policyVersion: "separate-snapshot-sanitization-policy" },
    },
  };
}

async function writePolicy(root, name, policy) {
  const file = path.join(root, name);
  await writeFile(file, canonicalBytes(policy));
  return file;
}

test("compiler binds a selected store release to the snapshot and exact occurrence policy", async (context) => {
  const fixture = await selectedReleaseFixture(context);
  const binding = await loadCompilerOccurrenceBinding({
    snapshot: fixture.snapshot,
    occurrenceStore: fixture.storeRoot,
    occurrencePolicy: fixture.policyPath,
  });

  assert.equal(binding.release.selection.current.manifestSha256, fixture.published.manifestSha256);
  assert.equal(binding.release.current.sourceSnapshotManifestSha256, fixture.policy.sourceSnapshotManifestSha256);
  assert.equal(binding.release.current.policyVersion, fixture.policy.policyVersion);
  assert.equal(binding.release.current.policySha256, fixture.policySha256);
  assert.doesNotThrow(() => verifyCompilerOccurrenceDiscovery(binding, fixture.discovered.manifest));
  const incompleteServedManifest = staticOccurrenceManifest({
    snapshotId: fixture.discovered.manifest.snapshotId,
    selectedManifestSha256: binding.release.selection.current.manifestSha256,
    discoveredGraphs: fixture.discovered.graphs,
    discoveredCurrentMedia: fixture.discovered.currentMedia,
    selectedManifest: binding.release.current,
  });
  assert.throws(
    () => verifyCompleteSelectedOccurrenceEvidence(
      binding.release,
      incompleteServedManifest,
      binding.policy,
      binding.policySha256,
    ),
    /complete graph fallback coverage/,
  );
});

test("compiler keeps absent-store behavior pending and requires a policy for any supplied store", async (context) => {
  const fixture = await selectedReleaseFixture(context);
  assert.deepEqual(await loadCompilerOccurrenceBinding({
    snapshot: fixture.snapshot,
    occurrenceStore: "",
    occurrencePolicy: "",
  }), {
    release: { selection: null, current: null },
    policy: null,
    policySha256: null,
  });
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: fixture.snapshot,
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: "",
    }),
    /LAB_OCCURRENCE_POLICY must name the exact policy/,
  );
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: fixture.snapshot,
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: fixture.blockedPolicyPath,
    }),
    /policy is not approved for compiler use/,
  );
});

test("blocked compiler policy prevents injected S3 store and client construction", async (context) => {
  const fixture = await selectedReleaseFixture(context);
  let storeFactoryCalls = 0;
  let clientFactoryCalls = 0;
  let clientInvocationCalls = 0;
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: fixture.snapshot,
      occurrenceStore: S3_LOCATION,
      occurrencePolicy: fixture.blockedPolicyPath,
      occurrenceStoreFactory: (location) => {
        storeFactoryCalls += 1;
        return new S3OccurrenceReleaseStore(location, {
          clientFactory: () => {
            clientFactoryCalls += 1;
            return {
              send: async () => {
                clientInvocationCalls += 1;
                throw new Error("blocked policy invoked its client");
              },
            };
          },
        });
      },
    }),
    /policy is not approved for compiler use/,
  );
  assert.equal(storeFactoryCalls, 0);
  assert.equal(clientFactoryCalls, 0);
  assert.equal(clientInvocationCalls, 0);
});

test("compiler rejects snapshot, discovery, policy-version, and canonical-policy drift", async (context) => {
  const fixture = await selectedReleaseFixture(context);
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: { manifestDigest: `sha256:${sha256("different-snapshot")}` },
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: fixture.policyPath,
    }),
    /exact snapshot manifest/,
  );
  const differentPolicySnapshotPath = await writePolicy(fixture.root, "different-policy-snapshot.json", {
    ...fixture.policy,
    sourceSnapshotManifestSha256: sha256("different-policy-snapshot"),
  });
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: fixture.snapshot,
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: differentPolicySnapshotPath,
    }),
    /exact snapshot manifest/,
  );
  const differentVersionPath = await writePolicy(fixture.root, "different-version.json", {
    ...fixture.policy,
    policyVersion: "compiler-occurrence-export-v2",
  });
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: fixture.snapshot,
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: differentVersionPath,
    }),
    /export policy version/,
  );
  const differentBytesPath = await writePolicy(fixture.root, "different-bytes.json", {
    ...fixture.policy,
    reviewedAt: "2026-07-13T16:59:59Z",
  });
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: fixture.snapshot,
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: differentBytesPath,
    }),
    /exact occurrence export policy bytes/,
  );

  const binding = await loadCompilerOccurrenceBinding({
    snapshot: fixture.snapshot,
    occurrenceStore: fixture.storeRoot,
    occurrencePolicy: fixture.policyPath,
  });
  const changedDiscovery = {
    ...fixture.discovered.manifest,
    graphs: fixture.discovered.manifest.graphs.slice(1),
  };
  assert.throws(
    () => verifyCompilerOccurrenceDiscovery(binding, changedDiscovery),
    /stable discovery manifest/,
  );
});

test("selected builds retain a stable discovery hash and require 143 graph plus 2 camera fallbacks", async (context) => {
  const fixture = await selectedReleaseFixture(context);
  const manifestSha256 = "a".repeat(64);
  const fallbackSha256 = "b".repeat(64);
  const fallback = {
    sha256: fallbackSha256,
    decodedSha256: "c".repeat(64),
    decodedBytes: 320 * 180 * 4,
    bytes: 1000,
    mediaType: "image/png",
    width: 320,
    height: 180,
    capturedAt: "2026-07-13T17:00:00Z",
    verifiedAt: "2026-07-13T17:00:30Z",
    policyVersion: fixture.policy.policyVersion,
    publicPath: `/evidence/blobs/sha256/${fallbackSha256}.png`,
  };
  const requestByMedia = new Map(fixture.policy.currentMedia.map((occurrence) => [
    occurrence.occurrenceId,
    occurrence.requestProvenanceSha256,
  ]));
  const selectedManifest = {
    occurrences: {
      graphs: fixture.discovered.graphs.map((occurrence) => ({
        ...occurrence,
        staleAfterSeconds: Math.max(occurrence.renderCadenceSeconds * 2, 1800),
        probeStatus: "success",
        state: "verified",
        fallback,
      })),
      currentMedia: fixture.discovered.currentMedia.map((occurrence) => ({
        ...occurrence,
        policySha256: fixture.policySha256,
        requestProvenanceSha256: requestByMedia.get(occurrence.occurrenceId),
        staleAfterSeconds: Math.max(occurrence.captureCadenceSeconds * 2, 900),
        captureStatus: "selected-generation",
        state: "verified",
        fallback,
        pointer: {
          selectionSha256: "d".repeat(64),
          generation: 1,
          currentGenerationSha256: "e".repeat(64),
          previousGenerationSha256: null,
        },
      })),
    },
  };
  const servedFor = (selected) => staticOccurrenceManifest({
    snapshotId: fixture.discovered.manifest.snapshotId,
    selectedManifestSha256: manifestSha256,
    discoveredGraphs: fixture.discovered.graphs,
    discoveredCurrentMedia: fixture.discovered.currentMedia,
    selectedManifest: selected,
  });
  const servedManifest = servedFor(selectedManifest);
  const release = {
    selection: { current: { manifestSha256 } },
    current: selectedManifest,
  };
  assert.equal(staticOccurrenceDiscoverySha256(servedManifest), fixture.policy.sourceOccurrenceManifestSha256);
  assert.deepEqual(staticOccurrenceDiscoveryProjection(servedManifest), fixture.discovered.manifest);
  assert.doesNotThrow(() => verifyCompleteSelectedOccurrenceEvidence(
    release,
    servedManifest,
    fixture.policy,
    fixture.policySha256,
  ));
  assert.equal(servedManifest.graphs.filter((occurrence) => occurrence.selected?.fallback).length, 143);
  assert.equal(servedManifest.currentMedia.filter((occurrence) => occurrence.selected?.fallback).length, 2);

  const build = {
    selectedOccurrenceManifestSha256: `sha256:${manifestSha256}`,
    materializedOccurrenceBlobCount: 1,
  };
  assert.doesNotThrow(() => verifySelectedEvidence(build, servedManifest));
  assert.throws(
    () => verifySelectedEvidence({ ...build, selectedOccurrenceManifestSha256: manifestSha256 }, servedManifest),
    /no selected immutable occurrence manifest/,
  );
  assert.throws(
    () => verifySelectedEvidence(build, { ...servedManifest, selectedManifestSha256: "d".repeat(64) }),
    /select different releases/,
  );

  const incompleteSelected = {
    occurrences: {
      ...selectedManifest.occurrences,
      currentMedia: selectedManifest.occurrences.currentMedia.map((occurrence, index) => (
        index === 0 ? { ...occurrence, fallback: null } : occurrence
      )),
    },
  };
  const incompleteRelease = { ...release, current: incompleteSelected };
  assert.throws(
    () => verifyCompleteSelectedOccurrenceEvidence(
      incompleteRelease,
      servedFor(incompleteSelected),
      fixture.policy,
      fixture.policySha256,
    ),
    /approved current-media fallback coverage/,
  );

  const wrongProvenanceSelected = {
    occurrences: {
      ...selectedManifest.occurrences,
      currentMedia: selectedManifest.occurrences.currentMedia.map((occurrence, index) => (
        index === 0 ? { ...occurrence, requestProvenanceSha256: "f".repeat(64) } : occurrence
      )),
    },
  };
  assert.throws(
    () => verifyCompleteSelectedOccurrenceEvidence(
      { ...release, current: wrongProvenanceSelected },
      servedFor(wrongProvenanceSelected),
      fixture.policy,
      fixture.policySha256,
    ),
    /request provenance differs/,
  );

  const undersizedSelected = {
    occurrences: {
      ...selectedManifest.occurrences,
      graphs: selectedManifest.occurrences.graphs.map((occurrence, index) => (
        index === 0 ? { ...occurrence, fallback: { ...occurrence.fallback, width: 2, height: 1 } } : occurrence
      )),
    },
  };
  assert.throws(
    () => verifyCompleteSelectedOccurrenceEvidence(
      { ...release, current: undersizedSelected },
      servedFor(undersizedSelected),
      fixture.policy,
      fixture.policySha256,
    ),
    /graph fallback violates the approved image bounds/,
  );
});

test("compiler builds and materializes a selected 143 plus 2 release through one fake S3 reader", async (context) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-selected-compiler-integration-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  const projectRoot = path.join(root, "site-astro");
  await mkdir(projectRoot);
  await mkdir(path.join(projectRoot, "vendor"));
  await Promise.all([
    cp(path.join(SITE_ROOT, "scripts"), path.join(projectRoot, "scripts"), { recursive: true }),
    cp(path.join(SITE_ROOT, "vendor", "compat-public"), path.join(projectRoot, "vendor", "compat-public"), { recursive: true }),
    cp(path.join(SITE_ROOT, "vendor", "site-shell"), path.join(projectRoot, "vendor", "site-shell"), { recursive: true }),
    cp(path.join(SITE_ROOT, "tests", "fixtures", "snapshot"), path.join(projectRoot, "snapshot"), { recursive: true }),
    symlink(path.join(SITE_ROOT, "node_modules"), path.join(projectRoot, "node_modules"), "dir"),
  ]);

  const snapshotRoot = path.join(projectRoot, "snapshot");
  const contentRoot = path.join(snapshotRoot, "content");
  const manifestPath = path.join(snapshotRoot, "manifests", "content.json");
  const snapshotManifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const indexPath = path.join(contentRoot, "index.md");
  const indexSource = await readFile(indexPath, "utf8");
  const completeOccurrenceSource = indexSource
    .replace(
      "https://api.verdify.ai/api/v1/public/cameras/cam-public-fixture/latest.png",
      "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    )
    + `\n<img src="https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080" alt="Current greenhouse view 2">\n`
    + Array.from({ length: 141 }, (_, index) => (
      `<iframe src="https://graphs.verdify.ai/d-solo/site-complete/graph?orgId=1&amp;panelId=${index + 1}&amp;from=now-24h&amp;to=now" width="100%" height="320" title="Complete graph ${index + 1}"></iframe>`
    )).join("\n")
    + "\n";
  await writeFile(indexPath, completeOccurrenceSource);
  snapshotManifest.files["index.md"] = sha256(completeOccurrenceSource);
  const fillerWrites = [];
  for (let index = 0; index < 179; index += 1) {
    const relative = `static/video/integration-${String(index).padStart(3, "0")}.ts`;
    const bytes = Buffer.from(`video-${index}\n`);
    snapshotManifest.files[relative] = sha256(bytes);
    fillerWrites.push([path.join(contentRoot, relative), bytes]);
  }
  for (let index = 0; index < 240; index += 1) {
    const relative = `static/integration/filler-${String(index).padStart(3, "0")}.txt`;
    const bytes = Buffer.from(`filler-${index}\n`);
    snapshotManifest.files[relative] = sha256(bytes);
    fillerWrites.push([path.join(contentRoot, relative), bytes]);
  }
  await Promise.all([
    mkdir(path.join(contentRoot, "static", "video"), { recursive: true }),
    mkdir(path.join(contentRoot, "static", "integration"), { recursive: true }),
  ]);
  await Promise.all(fillerWrites.map(([file, bytes]) => writeFile(file, bytes)));
  const snapshotManifestBytes = canonicalBytes(snapshotManifest);
  await writeFile(manifestPath, snapshotManifestBytes);
  await rm(path.join(snapshotRoot, "manifests", "synthetic-fixture.json"));
  const guardReport = {
    findings: [],
    missing_roots: [],
    roots: [{ identity: sha256("selected-compiler-integration-content"), label: "content" }],
    routes: [],
    schema_version: 2,
  };
  const guardBytes = canonicalBytes(guardReport);
  await mkdir(path.join(snapshotRoot, "evidence"));
  await writeFile(path.join(snapshotRoot, "evidence", "public-output-guard.json"), guardBytes);
  await writeFile(path.join(snapshotRoot, "attestation.json"), canonicalBytes({
    contract: "verdify.lab-stage-sanitized-snapshot",
    schemaVersion: 1,
    evidenceStatus: "provisional-only",
    approvalEligible: false,
    sourceManifestSha256: "05d4373ebf59bef3a7899c5e94514971d663fd7264db09b2b5cb26fec78410b1",
    sanitizedManifestSha256: sha256(snapshotManifestBytes),
    sourceFileCount: 429,
    sanitizedFileCount: 429,
    policyVersion: "verdify-public-output-stage-v1",
    guardReportSha256: sha256(guardBytes),
    guardSchemaVersion: 2,
    guardFindings: 0,
    transformations: {
      changedFiles: 8,
      textRedactionFiles: 3,
      invalidValueRepairFiles: 3,
      pngReencodeFiles: 3,
      hlsFilesPreserved: 179,
    },
  }));

  function run(script, environment) {
    const result = spawnSync(process.execPath, [path.join(projectRoot, script)], {
      cwd: projectRoot,
      env: environment,
      encoding: "utf8",
      timeout: 120_000,
    });
    assert.equal(result.status, 0, `${script} failed:\n${result.stderr}`);
  }

  const baseEnvironment = {
    ...process.env,
    LAB_SNAPSHOT: snapshotRoot,
    ALLOW_SYNTHETIC_FIXTURE: "false",
    SITE_ORIGIN: "https://lab-stage.verdify.ai",
    STAGE_GLOBAL_NOINDEX: "true",
  };
  delete baseEnvironment.LAB_OCCURRENCE_STORE;
  delete baseEnvironment.LAB_OCCURRENCE_POLICY;
  run("scripts/prepare-site-shell.mjs", baseEnvironment);
  run("scripts/compile-snapshot.mjs", baseEnvironment);

  const discoveryManifest = JSON.parse(await readFile(
    path.join(projectRoot, ".generated", "public", "occurrence-manifest.json"),
    "utf8",
  ));
  const discoveryBuild = JSON.parse(await readFile(
    path.join(projectRoot, ".generated", "build.json"),
    "utf8",
  ));
  assert.equal(discoveryManifest.graphs.length, 143);
  assert.equal(discoveryManifest.currentMedia.length, 2);

  const cameraUrls = [
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080",
  ];
  const blockedPolicy = draftBlockedOccurrenceExportPolicy({
    manifest: discoveryManifest,
    manifestSha256: staticOccurrenceDiscoverySha256(discoveryManifest),
    policyVersion: "selected-compiler-integration-v1",
    approvedAt: "2026-07-13T18:00:00Z",
    cameraSources: discoveryManifest.currentMedia.map((occurrence, index) => ({
      occurrenceId: occurrence.occurrenceId,
      url: cameraUrls[index],
    })),
  });
  const policy = {
    ...blockedPolicy,
    activation: {
      ...blockedPolicy.activation,
      state: "approved",
      approvedBy: "jason",
      approvedAt: "2026-07-13T18:00:00Z",
    },
  };
  const policyPath = path.join(root, "approved-policy.json");
  await writeFile(policyPath, canonicalBytes(policy));
  const policySha256 = occurrenceExportPolicySha256(policy);
  const sourceSnapshotManifestSha256 = discoveryBuild.snapshotManifestDigest.slice("sha256:".length);
  const storeRoot = path.join(root, "store");
  const sourceRoot = path.join(root, "source");
  await Promise.all([mkdir(storeRoot), mkdir(sourceRoot)]);
  const imageBytes = fixturePng();
  const imageSha256 = sha256(imageBytes);
  await writeFile(path.join(sourceRoot, "fallback.png"), imageBytes);
  const candidate = {
    relativePath: "fallback.png",
    expectedSha256: imageSha256,
    verifiedAt: "2026-07-13T18:00:30Z",
    capturedAt: "2026-07-13T18:00:00Z",
  };
  const requestByMedia = new Map(policy.currentMedia.map((occurrence) => [
    occurrence.occurrenceId,
    occurrence.requestProvenanceSha256,
  ]));
  for (const [index, occurrence] of discoveryManifest.currentMedia.entries()) {
    const requestProvenanceSha256 = requestByMedia.get(occurrence.occurrenceId);
    const mediaCandidate = { ...candidate, requestProvenanceSha256 };
    const payload = {
      policyVersion: policy.policyVersion,
      policySha256,
      requestProvenanceSha256,
      occurrence: { ...occurrence, selected: undefined },
      candidate: mediaCandidate,
    };
    delete payload.occurrence.selected;
    await publishCurrentMediaGeneration({
      storeRoot,
      sourceRoot,
      event: {
        contract: "verdify.lab-release-trigger",
        schemaVersion: 1,
        eventId: `evt_selected_compiler_media_000${index + 1}`,
        eventType: "current-media-updated",
        sourceId: "selected-compiler-integration",
        sourceWatermark: `wm_selected_compiler_media_000${index + 1}`,
        occurredAt: "2026-07-13T18:00:00Z",
        payloadSha256: currentMediaGenerationPayloadSha256(payload),
      },
      ...payload,
      publishedAt: "2026-07-13T18:01:00Z",
    });
  }

  const graphs = discoveryManifest.graphs.map((occurrence) => ({
    route: occurrence.route,
    ordinal: occurrence.ordinal,
    liveUrl: occurrence.liveUrl,
    title: occurrence.semanticRole,
    renderCadenceSeconds: occurrence.renderCadenceSeconds,
    probeStatus: "success",
    candidate,
  }));
  const currentMedia = discoveryManifest.currentMedia.map((occurrence) => {
    const { selected: _selected, ...discovered } = occurrence;
    return {
      discovered,
      requestProvenanceSha256: requestByMedia.get(occurrence.occurrenceId),
    };
  });
  const releasePayload = {
    sourceSnapshotManifestSha256,
    policyVersion: policy.policyVersion,
    policySha256,
    graphs,
    currentMedia,
  };
  const published = await publishOccurrenceRelease({
    storeRoot,
    sourceRoot,
    event: {
      contract: "verdify.lab-release-trigger",
      schemaVersion: 1,
      eventId: "evt_selected_compiler_release_0001",
      eventType: "planner-completed",
      sourceId: "selected-compiler-integration",
      sourceWatermark: "wm_selected_compiler_release_0001",
      occurredAt: "2026-07-13T18:00:00Z",
      payloadSha256: occurrenceReleasePayloadSha256(releasePayload),
    },
    ...releasePayload,
    publishedAt: "2026-07-13T18:01:00Z",
  });

  const client = new FakeReadOnlyS3Client();
  await seedFakeS3FromLocalStore(client, storeRoot);
  const environmentNames = [
    "LAB_SNAPSHOT",
    "ALLOW_SYNTHETIC_FIXTURE",
    "SITE_ORIGIN",
    "STAGE_GLOBAL_NOINDEX",
    "LAB_OCCURRENCE_STORE",
    "LAB_OCCURRENCE_POLICY",
  ];
  const previousEnvironment = new Map(environmentNames.map((name) => [name, process.env[name]]));
  let storeFactoryCalls = 0;
  let clientFactoryCalls = 0;
  try {
    Object.assign(process.env, {
      LAB_SNAPSHOT: snapshotRoot,
      ALLOW_SYNTHETIC_FIXTURE: "false",
      SITE_ORIGIN: "https://lab-stage.verdify.ai",
      STAGE_GLOBAL_NOINDEX: "true",
      LAB_OCCURRENCE_STORE: S3_LOCATION,
      LAB_OCCURRENCE_POLICY: policyPath,
    });
    const compilerModule = await import(pathToFileURL(path.join(projectRoot, "scripts", "compile-snapshot.mjs")));
    const storeModule = await import(pathToFileURL(path.join(projectRoot, "scripts", "lib", "occurrence-release-store.mjs")));
    await compilerModule.main({
      occurrenceStoreFactory: (location) => {
        storeFactoryCalls += 1;
        return new storeModule.S3OccurrenceReleaseStore(location, {
          clientFactory: () => {
            clientFactoryCalls += 1;
            return client;
          },
        });
      },
    });
  } finally {
    for (const [name, value] of previousEnvironment) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
  const selectedBuild = JSON.parse(await readFile(path.join(projectRoot, ".generated", "build.json"), "utf8"));
  const selectedManifest = JSON.parse(await readFile(
    path.join(projectRoot, ".generated", "public", "occurrence-manifest.json"),
    "utf8",
  ));
  assert.equal(selectedBuild.selectedOccurrenceManifestSha256, `sha256:${published.manifestSha256}`);
  assert.equal(selectedManifest.selectedManifestSha256, published.manifestSha256);
  assert.equal(selectedBuild.materializedOccurrenceBlobCount, 1);
  assert.equal(selectedManifest.graphs.filter((occurrence) => occurrence.selected?.fallback).length, 143);
  assert.equal(selectedManifest.currentMedia.filter((occurrence) => occurrence.selected?.fallback).length, 2);
  assert.equal(storeFactoryCalls, 1);
  assert.equal(clientFactoryCalls, 1);
  assert.ok(client.commands.length > 0);
  assert.ok(client.commands.every(({ name }) => name === "GetObjectCommand"));
  assert.ok(client.commands.every(({ key }) => key.startsWith(`${S3_TYPED_PREFIX}/`)));
  assert.equal((await readFile(
    path.join(projectRoot, ".generated", "public", "evidence", "blobs", "sha256", `${imageSha256}.png`),
  )).compare(imageBytes), 0);
});
