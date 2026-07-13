import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";

import {
  discoverGraphOccurrence,
  evaluateEventFreshness,
  loadSelectedCurrentMediaGeneration,
  loadSelectedOccurrenceRelease,
  materializeOccurrenceBlobs,
  occurrenceReleasePayloadSha256,
  publishOccurrenceRelease,
} from "../scripts/lib/occurrence-release.mjs";
import {
  LocalOccurrenceReleaseStore,
  S3OccurrenceReleaseStore,
  createOccurrenceReleaseStore,
  occurrenceReleaseStoreContract,
  parseOccurrenceReleaseStoreLocation,
} from "../scripts/lib/occurrence-release-store.mjs";

const BUCKET = "verdify-lab-releases";
const BASE_PREFIX = "lab-stage/releases";
const TYPED_PREFIX = `${BASE_PREFIX}/occurrence-releases/v1`;
const LOCATION = `s3://${BUCKET}/${BASE_PREFIX}`;
const MEDIA_ID = `media_${"1".repeat(24)}`;

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function missing() {
  const error = new Error("object is absent");
  error.name = "NoSuchKey";
  error.$metadata = { httpStatusCode: 404 };
  return error;
}

function precondition() {
  const error = new Error("conditional write did not match");
  error.name = "PreconditionFailed";
  error.$metadata = { httpStatusCode: 412 };
  return error;
}

class FakeS3Client {
  constructor() {
    this.objects = new Map();
    this.commands = [];
    this.sequence = 0;
    this.afterPutError = null;
    this.beforePut = null;
  }

  identity(input) {
    return `${input.Bucket}/${input.Key}`;
  }

  seed(key, bytes, etag = null) {
    this.sequence += 1;
    this.objects.set(`${BUCKET}/${key}`, {
      bytes: Buffer.from(bytes),
      etag: etag ?? `"fake-${this.sequence}"`,
    });
  }

  async send(command) {
    const name = command.constructor.name;
    const input = command.input;
    this.commands.push({
      name,
      input: {
        ...input,
        ...(Buffer.isBuffer(input.Body) ? { Body: Buffer.from(input.Body) } : {}),
      },
    });
    if (name === "GetObjectCommand") {
      const value = this.objects.get(this.identity(input));
      if (value === undefined) throw missing();
      return {
        ETag: value.etag,
        ContentLength: value.bytes.length,
        Body: (async function* body() {
          for (let offset = 0; offset < value.bytes.length; offset += 5) {
            yield value.bytes.subarray(offset, offset + 5);
          }
        })(),
      };
    }
    if (name === "PutObjectCommand") {
      if (this.beforePut !== null) await this.beforePut(input, this);
      const identity = this.identity(input);
      const current = this.objects.get(identity);
      if (input.IfNoneMatch === "*" && current !== undefined) throw precondition();
      if (input.IfMatch !== undefined && current?.etag !== input.IfMatch) throw precondition();
      const bytes = Buffer.from(input.Body);
      assert.equal(input.ContentLength, bytes.length);
      this.sequence += 1;
      const etag = `"fake-${this.sequence}"`;
      this.objects.set(identity, { bytes, etag });
      if (this.afterPutError !== null) {
        const error = this.afterPutError;
        this.afterPutError = null;
        throw error;
      }
      return { ETag: etag };
    }
    if (name === "ListObjectsV2Command") {
      return { Contents: [], IsTruncated: false };
    }
    throw new Error(`unexpected command ${name}`);
  }
}

const CRC_TABLE = Array.from({ length: 256 }, (_, value) => {
  let crc = value;
  for (let bit = 0; bit < 8; bit += 1) {
    crc = (crc & 1) === 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  }
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

function png(r, g, b) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(2, 0);
  header.writeUInt32BE(1, 4);
  header[8] = 8;
  header[9] = 6;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.from([0, r, g, b, 255, r, g, b, 255]))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function aggregateSelection(manifestSha256, generation = 1, previous = null) {
  return {
    contract: "verdify.lab-occurrence-selection",
    schemaVersion: 1,
    generation,
    current: {
      manifestSha256,
      eventId: `evt_aggregate_${String(generation).padStart(4, "0")}`,
    },
    previous,
    selectedAt: `2026-07-12T12:${String(generation).padStart(2, "0")}:00Z`,
    reason: "publish",
  };
}

function mediaSelection(generationSha256, blobSha256, generation = 1, previous = null) {
  return {
    contract: "verdify.lab-current-media-selection",
    schemaVersion: 1,
    occurrenceId: MEDIA_ID,
    generation,
    current: { generationSha256, blobSha256 },
    previous,
    selectedAt: `2026-07-12T12:${String(generation).padStart(2, "0")}:00Z`,
    reason: "publish",
  };
}

function event(eventId, eventType = "reconciliation") {
  return {
    contract: "verdify.lab-release-trigger",
    schemaVersion: 1,
    eventId,
    eventType,
    sourceId: "offline-test-source",
    sourceWatermark: "offline-test-watermark",
    occurredAt: "2026-07-12T12:00:00Z",
    payloadSha256: "a".repeat(64),
  };
}

function storeEventIntent(store, contract, eventId) {
  return {
    contract,
    eventId,
    storeIdentitySha256: store.identity.sha256,
  };
}

function aggregateManifest(eventId = "evt_aggregate_0001") {
  const releaseEvent = event(eventId);
  const publishedAt = "2026-07-12T12:01:00Z";
  return {
    contract: "verdify.lab-specialist-occurrence-release",
    schemaVersion: 2,
    event: releaseEvent,
    policyVersion: "offline-policy-v1",
    policySha256: "b".repeat(64),
    sourceSnapshotManifestSha256: "c".repeat(64),
    publishedAt,
    freshness: evaluateEventFreshness(releaseEvent, publishedAt),
    occurrences: { graphs: [], currentMedia: [] },
  };
}

function mediaGeneration(blob) {
  return {
    contract: "verdify.lab-current-media-generation",
    schemaVersion: 3,
    occurrenceId: MEDIA_ID,
    sourceProvenanceSha256: "d".repeat(64),
    policySha256: "e".repeat(64),
    requestProvenanceSha256: "f".repeat(64),
    event: event("evt_media_adapter_0001", "current-media-updated"),
    policyVersion: "offline-policy-v1",
    publishedAt: "2026-07-12T12:01:00Z",
    fallback: {
      publicPath: `/evidence/blobs/sha256/${blob.sha256}.png`,
      sha256: blob.sha256,
      decodedSha256: blob.decodedSha256,
      decodedBytes: blob.decodedBytes,
      bytes: blob.bytes,
      mediaType: "image/png",
      width: blob.width,
      height: blob.height,
      capturedAt: "2026-07-12T12:00:00Z",
      verifiedAt: "2026-07-12T12:00:30Z",
      policyVersion: "offline-policy-v1",
    },
  };
}

function fallbackRecord(blob) {
  return {
    publicPath: `/evidence/blobs/sha256/${blob.sha256}.png`,
    sha256: blob.sha256,
    decodedSha256: blob.decodedSha256,
    decodedBytes: blob.decodedBytes,
    bytes: blob.bytes,
    mediaType: "image/png",
    width: blob.width,
    height: blob.height,
    capturedAt: "2026-07-12T12:00:00Z",
    verifiedAt: "2026-07-12T12:00:30Z",
    policyVersion: "offline-policy-v1",
  };
}

function graphOccurrence(ordinal, blob) {
  const discovered = discoverGraphOccurrence({
    route: `/evidence/materialize-${ordinal}`,
    ordinal,
    liveUrl: `https://graphs.verdify.ai/d-solo/site-home/public?panelId=${ordinal + 1}&from=now-24h&to=now`,
    title: `Materialization graph ${ordinal}`,
    renderCadenceSeconds: 600,
  });
  return {
    ...discovered,
    staleAfterSeconds: 1800,
    probeStatus: "success",
    state: "verified",
    fallback: fallbackRecord(blob),
  };
}

function twoGraphManifest(blobs) {
  const releaseEvent = event("evt_materialize_s3_0001");
  const publishedAt = "2026-07-12T12:01:00Z";
  return {
    contract: "verdify.lab-specialist-occurrence-release",
    schemaVersion: 2,
    event: releaseEvent,
    policyVersion: "offline-policy-v1",
    policySha256: "b".repeat(64),
    sourceSnapshotManifestSha256: "c".repeat(64),
    publishedAt,
    freshness: evaluateEventFreshness(releaseEvent, publishedAt),
    occurrences: {
      graphs: blobs.map((blob, ordinal) => graphOccurrence(ordinal, blob)),
      currentMedia: [],
    },
  };
}

test("occurrence store factory is strict and binds a distinct typed namespace", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-occurrence-store-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  assert.deepEqual(parseOccurrenceReleaseStoreLocation(LOCATION), {
    kind: "s3",
    bucket: BUCKET,
    prefix: TYPED_PREFIX,
  });
  assert.deepEqual(parseOccurrenceReleaseStoreLocation(root), {
    kind: "local",
    root,
  });
  for (const invalid of [
    "s3://verdify-lab-releases",
    "s3://Verdify/releases",
    "s3://verdify-lab-releases/",
    "s3://verdify-lab-releases/lab//occurrences",
    "s3://verdify-lab-releases/lab/../occurrences",
    "https://verdify.invalid/releases",
    "file:///tmp/releases",
    "//remote/releases",
    "relative\\windows",
    `s3://verdify-lab-releases/${Array.from({ length: 4 }, () => "a".repeat(220)).join("/")}`,
  ]) {
    assert.throws(() => parseOccurrenceReleaseStoreLocation(invalid), /invalid/);
  }

  const local = createOccurrenceReleaseStore(root);
  const client = new FakeS3Client();
  const s3 = createOccurrenceReleaseStore(LOCATION, { client });
  assert.ok(local instanceof LocalOccurrenceReleaseStore);
  assert.ok(s3 instanceof S3OccurrenceReleaseStore);
  assert.notEqual(local.identity.sha256, s3.identity.sha256);
  assert.equal(s3.identity.document.namespace, occurrenceReleaseStoreContract.namespace);
  assert.equal(s3.identity.document.prefix, TYPED_PREFIX);
  assert.equal(client.commands.length, 0, "construction performs no client operation");
  let factoryCalls = 0;
  const lazy = createOccurrenceReleaseStore(LOCATION, {
    clientFactory: () => {
      factoryCalls += 1;
      return client;
    },
  });
  assert.ok(lazy instanceof S3OccurrenceReleaseStore);
  assert.equal(factoryCalls, 0, "the injected client factory stays lazy before initialize");
  const oversizedTypedPrefix = `${Array.from({ length: 4 }, () => "a".repeat(220)).join("/")}/occurrence-releases/v1`;
  assert.throws(
    () => new S3OccurrenceReleaseStore({
      kind: "s3",
      bucket: BUCKET,
      prefix: oversizedTypedPrefix,
    }, { client }),
    /location is invalid/,
  );
});

test("local adapter preserves aggregate, per-camera, event, and PNG layouts", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-occurrence-local-"));
  const output = path.join(root, "output");
  const storeRoot = path.join(root, "store");
  await mkdir(storeRoot);
  await mkdir(output);
  t.after(() => rm(root, { recursive: true, force: true }));
  const store = await new LocalOccurrenceReleaseStore(storeRoot).initialize({ create: true });

  const manifest = aggregateManifest();
  const manifestSha256 = await store.publishAggregateManifest(manifest);
  await store.publishAggregateManifest(manifest);
  const intent = storeEventIntent(store, "offline.aggregate.intent", manifest.event.eventId);
  await store.publishAggregateEventIntent(manifest.event.eventId, intent);
  const storedManifest = await store.readAggregateManifest(manifestSha256);
  assert.deepEqual(storedManifest.document, manifest);
  assert.equal(storedManifest.storeIdentitySha256, store.identity.sha256);
  const storedIntent = await store.readAggregateEventIntent(manifest.event.eventId);
  assert.deepEqual(storedIntent.document, intent);
  assert.equal(storedIntent.storeIdentitySha256, store.identity.sha256);
  const selected = aggregateSelection(manifestSha256);
  const selectionSha256 = await store.writeAggregateSelection(selected, null);
  assert.equal((await store.readAggregateSelection()).sha256, selectionSha256);
  const concurrent = await Promise.allSettled([
    store.writeAggregateSelection(
      aggregateSelection("d".repeat(64), 2, selected.current),
      selectionSha256,
    ),
    store.writeAggregateSelection(
      aggregateSelection("e".repeat(64), 2, selected.current),
      selectionSha256,
    ),
  ]);
  assert.equal(concurrent.filter((result) => result.status === "fulfilled").length, 1);
  assert.equal(concurrent.filter((result) => result.status === "rejected").length, 1);

  const image = png(20, 80, 40);
  const imageSha256 = sha256(image);
  const blob = await store.publishPngBlob(image, imageSha256);
  await store.publishPngBlob(image, imageSha256);
  const generation = mediaGeneration(blob);
  const generationSha256 = await store.publishCurrentMediaGeneration(MEDIA_ID, generation);
  const mediaIntent = storeEventIntent(store, "offline.media.intent", generation.event.eventId);
  await store.publishCurrentMediaEventIntent(MEDIA_ID, generation.event.eventId, mediaIntent);
  const mediaSelected = mediaSelection(generationSha256, imageSha256);
  const mediaSelectionSha256 = await store.writeCurrentMediaSelection(MEDIA_ID, mediaSelected, null);
  assert.equal((await store.readCurrentMediaSelection(MEDIA_ID)).sha256, mediaSelectionSha256);
  const storedGeneration = await store.readCurrentMediaGeneration(MEDIA_ID, generationSha256);
  assert.deepEqual(storedGeneration.document, generation);
  assert.equal(storedGeneration.storeIdentitySha256, store.identity.sha256);
  assert.deepEqual(
    (await store.readCurrentMediaEventIntent(MEDIA_ID, generation.event.eventId)).document,
    mediaIntent,
  );

  const target = path.join(output, `${imageSha256}.png`);
  assert.equal(
    (await store.materializePngBlob(imageSha256, target, { maximumBytes: image.length })).created,
    true,
  );
  assert.deepEqual(await readFile(target), image);
  assert.equal(
    (await store.materializePngBlob(imageSha256, target, { maximumBytes: image.length })).created,
    false,
  );
  assert.deepEqual(await readdir(output), [`${imageSha256}.png`]);
});

test("materialization removes its wx target when the file-handle close fails", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-occurrence-close-"));
  const storeRoot = path.join(root, "store");
  const output = path.join(root, "output");
  await mkdir(storeRoot);
  await mkdir(output);
  t.after(() => rm(root, { recursive: true, force: true }));
  const store = await new LocalOccurrenceReleaseStore(storeRoot).initialize({ create: true });
  const image = png(45, 60, 75);
  const imageSha256 = sha256(image);
  await store.publishPngBlob(image, imageSha256);
  const target = path.join(output, `${imageSha256}.png`);
  let closeCalls = 0;
  await assert.rejects(
    store.materializePngBlob(imageSha256, target, {
      maximumBytes: image.length,
      fileOperations: {
        open: async (...args) => {
          const handle = await open(...args);
          return {
            stat: (...values) => handle.stat(...values),
            writeFile: (...values) => handle.writeFile(...values),
            sync: (...values) => handle.sync(...values),
            close: async () => {
              closeCalls += 1;
              await handle.close().catch(() => {});
              throw new Error("injected materialization close failure");
            },
          };
        },
      },
    }),
    /injected materialization close failure/,
  );
  assert.ok(closeCalls >= 1);
  await assert.rejects(readFile(target), (error) => error.code === "ENOENT");
  assert.deepEqual(await readdir(output), []);
});

test("staging and directory-sync failures clean private state and retry monotonically", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-occurrence-sync-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const storeRoot = path.join(root, "store");
  await mkdir(storeRoot);
  const store = await new LocalOccurrenceReleaseStore(storeRoot).initialize({ create: true });
  const image = png(25, 50, 75);
  const imageSha256 = sha256(image);
  await store.publishPngBlob(image, imageSha256);

  const stagedOutput = path.join(root, "staged-output");
  await mkdir(stagedOutput);
  const stagedTarget = path.join(stagedOutput, `${imageSha256}.png`);
  await assert.rejects(
    store.materializePngBlob(imageSha256, stagedTarget, {
      maximumBytes: image.length,
      fileOperations: {
        open: async (...args) => {
          const handle = await open(...args);
          return {
            stat: (...values) => handle.stat(...values),
            writeFile: (...values) => handle.writeFile(...values),
            sync: async () => {
              throw new Error("injected staged file sync failure");
            },
            close: (...values) => handle.close(...values),
          };
        },
      },
    }),
    /injected staged file sync failure/,
  );
  assert.deepEqual(await readdir(stagedOutput), []);
  assert.equal(
    (await store.materializePngBlob(imageSha256, stagedTarget, { maximumBytes: image.length })).created,
    true,
  );

  const directorySyncOutput = path.join(root, "directory-sync-output");
  await mkdir(directorySyncOutput);
  const directorySyncTarget = path.join(directorySyncOutput, `${imageSha256}.png`);
  await assert.rejects(
    store.materializePngBlob(imageSha256, directorySyncTarget, {
      maximumBytes: image.length,
      fileOperations: {
        syncDirectory: async () => {
          throw new Error("injected destination directory sync failure");
        },
      },
    }),
    /injected destination directory sync failure/,
  );
  assert.deepEqual(await readFile(directorySyncTarget), image);
  assert.deepEqual(await readdir(directorySyncOutput), [`${imageSha256}.png`]);
  assert.equal(
    (await store.materializePngBlob(
      imageSha256,
      directorySyncTarget,
      { maximumBytes: image.length },
    )).created,
    false,
  );
});

test("a conflicting target arriving at commit is never overwritten or deleted", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-occurrence-conflict-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const storeRoot = path.join(root, "store");
  const output = path.join(root, "output");
  await mkdir(storeRoot);
  await mkdir(output);
  const store = await new LocalOccurrenceReleaseStore(storeRoot).initialize({ create: true });
  const expected = png(10, 20, 30);
  const expectedSha256 = sha256(expected);
  const foreign = png(90, 80, 70);
  await store.publishPngBlob(expected, expectedSha256);
  const target = path.join(output, `${expectedSha256}.png`);
  await assert.rejects(
    store.materializePngBlob(expectedSha256, target, {
      maximumBytes: expected.length,
      fileOperations: {
        link: async (_source, destination) => {
          await writeFile(destination, foreign, { flag: "wx" });
          const error = new Error("injected destination arrival");
          error.code = "EEXIST";
          throw error;
        },
      },
    }),
    /conflicts with staged blob/,
  );
  assert.deepEqual(await readFile(target), foreign);
  assert.deepEqual(await readdir(output), [`${expectedSha256}.png`]);
});

test("S3 adapter covers both selector families and immutable object families offline", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-occurrence-s3-"));
  const output = path.join(root, "output");
  await mkdir(output);
  t.after(() => rm(root, { recursive: true, force: true }));
  const client = new FakeS3Client();
  const store = await new S3OccurrenceReleaseStore(LOCATION, { client }).initialize();

  const image = png(90, 30, 10);
  const imageSha256 = sha256(image);
  const blob = await store.publishPngBlob(image, imageSha256);
  await store.publishPngBlob(image, imageSha256);
  const manifest = aggregateManifest();
  const manifestSha256 = await store.publishAggregateManifest(manifest);
  assert.equal(
    (await store.readAggregateManifest(manifestSha256)).storeIdentitySha256,
    store.identity.sha256,
  );
  await store.publishAggregateEventIntent(
    manifest.event.eventId,
    storeEventIntent(store, "offline.aggregate.intent", manifest.event.eventId),
  );
  const firstSelection = aggregateSelection(manifestSha256);
  const firstSelectionSha256 = await store.writeAggregateSelection(firstSelection, null);

  const secondManifest = aggregateManifest("evt_aggregate_0002");
  const secondManifestSha256 = await store.publishAggregateManifest(secondManifest);
  const secondSelection = aggregateSelection(
    secondManifestSha256,
    2,
    firstSelection.current,
  );
  const immediatelyRead = await store.readAggregateSelection();
  const secondSelectionSha256 = await store.writeAggregateSelection(
    secondSelection,
    firstSelectionSha256,
  );
  const aggregatePut = client.commands
    .filter((command) => command.name === "PutObjectCommand" && command.input.Key.endsWith("selection.json"))
    .at(-1);
  assert.equal(aggregatePut.input.IfMatch, immediatelyRead.etag);
  assert.equal((await store.readAggregateSelection()).sha256, secondSelectionSha256);

  const generation = mediaGeneration(blob);
  const generationSha256 = await store.publishCurrentMediaGeneration(MEDIA_ID, generation);
  await store.publishCurrentMediaEventIntent(
    MEDIA_ID,
    generation.event.eventId,
    storeEventIntent(store, "offline.media.intent", generation.event.eventId),
  );
  const firstMediaSelection = mediaSelection(generationSha256, imageSha256);
  const firstMediaSelectionSha256 = await store.writeCurrentMediaSelection(
    MEDIA_ID,
    firstMediaSelection,
    null,
  );
  assert.equal(
    (await store.readCurrentMediaSelection(MEDIA_ID)).sha256,
    firstMediaSelectionSha256,
  );
  assert.deepEqual(
    (await store.readCurrentMediaGeneration(MEDIA_ID, generationSha256)).document,
    generation,
  );
  assert.equal(
    (await store.readCurrentMediaGeneration(MEDIA_ID, generationSha256)).storeIdentitySha256,
    store.identity.sha256,
  );
  assert.equal(
    (await store.readCurrentMediaEventIntent(MEDIA_ID, generation.event.eventId)).document.eventId,
    generation.event.eventId,
  );

  const immutablePuts = client.commands.filter(
    (command) => command.name === "PutObjectCommand" && !command.input.Key.endsWith("selection.json"),
  );
  assert.ok(immutablePuts.length >= 5);
  assert.ok(immutablePuts.every((command) => command.input.IfNoneMatch === "*"));
  assert.ok(immutablePuts.every((command) => command.input.Key.startsWith(`${TYPED_PREFIX}/`)));

  const target = path.join(output, `${imageSha256}.png`);
  await store.materializePngBlob(imageSha256, target, { maximumBytes: image.length });
  assert.deepEqual(await readFile(target), image);
});

test("S3 two-blob materialization pre-stages sources and partial commit retries monotonically", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-occurrence-s3-materialize-"));
  const output = path.join(root, "output");
  await mkdir(output);
  t.after(() => rm(root, { recursive: true, force: true }));
  const client = new FakeS3Client();
  const store = await new S3OccurrenceReleaseStore(LOCATION, { client }).initialize();
  const firstBytes = png(15, 30, 45);
  const secondBytes = png(90, 75, 60);
  const blobs = [
    await store.publishPngBlob(firstBytes, sha256(firstBytes)),
    await store.publishPngBlob(secondBytes, sha256(secondBytes)),
  ];
  const manifest = twoGraphManifest(blobs);
  const ordered = [...blobs].sort((left, right) => left.sha256.localeCompare(right.sha256));
  const failedBlob = ordered[1];
  const failedKey = `${TYPED_PREFIX}/blobs/sha256/${failedBlob.sha256}.png`;
  client.seed(failedKey, Buffer.from("invalid PNG bytes"));

  await assert.rejects(
    materializeOccurrenceBlobs(store, manifest, output),
    /digest mismatch|cannot be decoded|signature/,
  );
  const targetDirectory = path.join(output, "evidence", "blobs", "sha256");
  assert.deepEqual(await readdir(targetDirectory), []);

  client.seed(failedKey, failedBlob.body);
  const commitStagedPngBlob = store.commitStagedPngBlob.bind(store);
  let commitCalls = 0;
  store.commitStagedPngBlob = async (...args) => {
    commitCalls += 1;
    if (commitCalls === 2) throw new Error("injected second destination commit failure");
    return commitStagedPngBlob(...args);
  };
  await assert.rejects(
    materializeOccurrenceBlobs(store, manifest, output),
    /injected second destination commit failure/,
  );
  store.commitStagedPngBlob = commitStagedPngBlob;
  assert.deepEqual(await readdir(targetDirectory), [`${ordered[0].sha256}.png`]);
  assert.deepEqual(
    await readFile(path.join(targetDirectory, `${ordered[0].sha256}.png`)),
    ordered[0].body,
  );

  assert.equal(await materializeOccurrenceBlobs(store, manifest, output), 2);
  assert.deepEqual(
    (await readdir(targetDirectory)).sort(),
    ordered.map((blob) => `${blob.sha256}.png`),
  );
  for (const blob of ordered) {
    assert.deepEqual(
      await readFile(path.join(targetDirectory, `${blob.sha256}.png`)),
      blob.body,
    );
  }
  assert.equal(await materializeOccurrenceBlobs(store, manifest, output), 2);
  for (const blob of ordered) {
    assert.deepEqual(
      await readFile(path.join(targetDirectory, `${blob.sha256}.png`)),
      blob.body,
    );
  }
});

test("S3 immutable writes recover exact committed bytes and reject collisions and bounds", async () => {
  const client = new FakeS3Client();
  const store = await new S3OccurrenceReleaseStore(LOCATION, { client }).initialize();
  await assert.rejects(
    store.publishAggregateEventIntent("evt_wrong_store_0001", {
      contract: "offline.aggregate.intent",
      eventId: "evt_wrong_store_0001",
      storeIdentitySha256: "0".repeat(64),
    }),
    /canonical store identity/,
  );
  assert.equal(client.commands.length, 0);
  await assert.rejects(
    store.writeCurrentMediaSelection(MEDIA_ID, {
      ...mediaSelection("1".repeat(64), "2".repeat(64)),
      occurrenceId: `media_${"9".repeat(24)}`,
    }, null),
    /does not match its occurrence identity/,
  );
  assert.equal(client.commands.length, 0);
  const intent = storeEventIntent(store, "offline.aggregate.intent", "evt_recovery_0001");
  client.afterPutError = new Error("response was unavailable after commit");
  await store.publishAggregateEventIntent(intent.eventId, intent);
  assert.deepEqual((await store.readAggregateEventIntent(intent.eventId)).document, intent);

  const manifest = aggregateManifest("evt_collision_0001");
  const manifestBytes = canonicalBytes(manifest);
  const manifestSha256 = sha256(manifestBytes);
  client.seed(
    `${TYPED_PREFIX}/manifests/sha256/${manifestSha256}.json`,
    Buffer.from(`${"x".repeat(manifestBytes.length - 1)}\n`),
  );
  await assert.rejects(
    store.publishAggregateManifest(manifest),
    /content-addressed occurrence manifest collision/,
  );

  const image = png(10, 20, 30);
  const imageSha256 = sha256(image);
  client.seed(`${TYPED_PREFIX}/blobs/sha256/${imageSha256}.png`, image);
  await assert.rejects(
    store.readPngBlob(imageSha256, { maximumBytes: image.length - 1 }),
    /exceeds its byte limit/,
  );
  const commandsBefore = client.commands.length;
  await assert.rejects(
    store.publishPngBlob(Buffer.alloc(0), imageSha256),
    /byte limit/,
  );
  assert.equal(client.commands.length, commandsBefore);
});

test("S3 selectors reject stale and racing writers using the immediately read ETag", async () => {
  const client = new FakeS3Client();
  const store = await new S3OccurrenceReleaseStore(LOCATION, { client }).initialize();
  const first = aggregateSelection("1".repeat(64));
  const firstSha256 = await store.writeAggregateSelection(first, null);
  const second = aggregateSelection("2".repeat(64), 2, first.current);
  await assert.rejects(
    store.writeAggregateSelection(second, "f".repeat(64)),
    /precondition failed/,
  );
  client.beforePut = async (input, fake) => {
    if (input.IfMatch === undefined) return;
    fake.beforePut = null;
    const current = fake.objects.get(`${input.Bucket}/${input.Key}`);
    fake.seed(input.Key, current.bytes, '"competing-writer"');
  };
  await assert.rejects(
    store.writeAggregateSelection(second, firstSha256),
    /precondition failed/,
  );
  assert.equal((await store.readAggregateSelection()).document.generation, 1);
});

test("S3 selector writes recover exact bytes after a committed response failure", async () => {
  const client = new FakeS3Client();
  const store = await new S3OccurrenceReleaseStore(LOCATION, { client }).initialize();
  const first = aggregateSelection("1".repeat(64));
  client.afterPutError = new Error("initial selector response was unavailable after commit");
  const firstSha256 = await store.writeAggregateSelection(first, null);
  assert.equal((await store.readAggregateSelection()).sha256, firstSha256);

  const second = aggregateSelection("2".repeat(64), 2, first.current);
  client.afterPutError = new Error("CAS selector response was unavailable after commit");
  const secondSha256 = await store.writeAggregateSelection(second, firstSha256);
  const selected = await store.readAggregateSelection();
  assert.equal(selected.sha256, secondSha256);
  assert.deepEqual(selected.document, second);
});

test("high-level readers consume an explicitly injected S3 adapter without enabling CLI mutation", async () => {
  const client = new FakeS3Client();
  const store = await new S3OccurrenceReleaseStore(LOCATION, { client }).initialize();
  const manifest = aggregateManifest();
  const manifestSha256 = await store.publishAggregateManifest(manifest);
  await store.writeAggregateSelection(aggregateSelection(manifestSha256), null);
  const selected = await loadSelectedOccurrenceRelease(store);
  assert.equal(selected.current.event.eventId, manifest.event.eventId);
  assert.match(store.identity.sha256, /^[0-9a-f]{64}$/u);

  const image = png(80, 40, 20);
  const blob = await store.publishPngBlob(image, sha256(image));
  const generation = mediaGeneration(blob);
  const generationSha256 = await store.publishCurrentMediaGeneration(MEDIA_ID, generation);
  await store.writeCurrentMediaSelection(
    MEDIA_ID,
    mediaSelection(generationSha256, blob.sha256),
    null,
  );
  const selectedMedia = await loadSelectedCurrentMediaGeneration(store, MEDIA_ID);
  assert.equal(selectedMedia.current.fallback.sha256, blob.sha256);

  assert.throws(
    () => createOccurrenceReleaseStore("s3://verdify-lab-releases"),
    /invalid/,
  );
  await assert.rejects(
    loadSelectedOccurrenceRelease(LOCATION),
    /explicitly constructed adapter; CLI S3 mutation is disabled/,
  );
  const request = {
    storeRoot: LOCATION,
    sourceRoot: "/offline/not-read",
    event: null,
    sourceSnapshotManifestSha256: "c".repeat(64),
    policyVersion: "offline-policy-v1",
    policySha256: "b".repeat(64),
    publishedAt: "2026-07-12T12:01:00Z",
    graphs: [],
    currentMedia: [],
    expectedSelectionSha256: null,
  };
  request.event = {
    ...event("evt_cli_s3_disabled_0001"),
    payloadSha256: occurrenceReleasePayloadSha256(request),
  };
  await assert.rejects(
    publishOccurrenceRelease(request),
    /explicitly constructed adapter; CLI S3 mutation is disabled/,
  );
});
