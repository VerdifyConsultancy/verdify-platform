import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { evaluateEventFreshness } from "../scripts/lib/occurrence-release.mjs";
import { S3OccurrenceReleaseStore } from "../scripts/lib/occurrence-release-store.mjs";
import {
  COORDINATION_FINALIZATION_USAGE,
  acquireReleaseStorageS3Lease,
  coordinateReleaseStorageS3Publication,
  loadReleaseStorageS3Usage,
  releaseReleaseStorageS3Lease,
  releaseStoragePassOneContract,
  releaseStorageS3CoordinatorContract,
  renderReleaseStorageS3Metrics,
  reserveReleaseStorageS3Usage,
} from "../scripts/lib/release-storage-s3-coordinator.mjs";
import { captureReleaseStorageS3Inventory } from "../scripts/lib/release-storage-s3-inventory.mjs";
import {
  ReleaseStorageS3ActivationProofError,
  proveReleaseStorageS3Activation,
} from "../scripts/lib/release-storage-s3-proof.mjs";
import { S3ObjectStore } from "../scripts/lib/s3-object-store.mjs";
import {
  S3SiteReleaseStore,
  siteContentIdentitySha256,
  siteReleasePayloadSha256,
} from "../scripts/lib/site-release-store.mjs";

const BUCKET = "verdify-lab-occurrences";
const SITE_PREFIX = "lab-stage/site-releases/v1";
const OCCURRENCE_BASE_PREFIX = "lab-stage";
const OCCURRENCE_PREFIX = `${OCCURRENCE_BASE_PREFIX}/occurrence-releases/v1`;
const COORDINATION_PREFIX = "lab-stage/coordination/v1";
const AS_OF = "2026-07-14T12:00:00.000Z";
const OLD = "2026-07-11T10:00:00.000Z";
const MEDIA_ID = "media_0123456789abcdef01234567";

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function missing() {
  const error = new Error("missing");
  error.name = "NoSuchKey";
  error.$metadata = { httpStatusCode: 404 };
  return error;
}

function precondition() {
  const error = new Error("precondition");
  error.name = "PreconditionFailed";
  error.$metadata = { httpStatusCode: 412 };
  return error;
}

function denied() {
  const error = new Error("denied");
  error.name = "AccessDenied";
  error.$metadata = { httpStatusCode: 403 };
  return error;
}

class FakeS3Client {
  constructor() {
    this.objects = new Map();
    this.commands = [];
    this.sequence = 0;
    this.pageSize = 3;
    this.deniedPrefix = null;
    this.failNextGetPrefix = null;
    this.deniedDeletePrefix = null;
  }

  identity(input) {
    return `${input.Bucket}/${input.Key}`;
  }

  seed(key, bytes, { lastModified = OLD, etag = null } = {}) {
    this.sequence += 1;
    this.objects.set(`${BUCKET}/${key}`, {
      bytes: Buffer.from(bytes),
      etag: etag ?? `"fixture-${this.sequence}"`,
      lastModified: new Date(lastModified),
    });
  }

  denied(input) {
    const key = input.Key ?? input.Prefix ?? "";
    return this.deniedPrefix !== null && key.startsWith(this.deniedPrefix);
  }

  async send(command) {
    const name = command.constructor.name;
    const input = command.input;
    this.commands.push({ name, input: { ...input, Body: input.Body === undefined ? undefined : "<bytes>" } });
    if (this.denied(input)) throw denied();
    if (name === "PutObjectCommand") {
      const identity = this.identity(input);
      const current = this.objects.get(identity);
      if (input.IfNoneMatch === "*" && current !== undefined) throw precondition();
      if (input.IfMatch !== undefined && current?.etag !== input.IfMatch) throw precondition();
      const bytes = Buffer.from(command.input.Body);
      assert.equal(input.ContentLength, bytes.length);
      this.sequence += 1;
      const value = {
        bytes,
        etag: `"fixture-${this.sequence}"`,
        lastModified: new Date(AS_OF),
      };
      this.objects.set(identity, value);
      return { ETag: value.etag };
    }
    if (name === "GetObjectCommand") {
      if (this.failNextGetPrefix !== null && input.Key.startsWith(this.failNextGetPrefix)) {
        this.failNextGetPrefix = null;
        throw new Error("injected read failure");
      }
      const value = this.objects.get(this.identity(input));
      if (value === undefined) throw missing();
      return {
        ETag: value.etag,
        ContentLength: value.bytes.length,
        Body: (async function* body() {
          yield value.bytes;
        })(),
      };
    }
    if (name === "HeadObjectCommand") {
      const value = this.objects.get(this.identity(input));
      if (value === undefined) throw missing();
      return {
        ETag: value.etag,
        ContentLength: value.bytes.length,
        LastModified: value.lastModified,
      };
    }
    if (name === "DeleteObjectCommand") {
      if (this.deniedDeletePrefix !== null && input.Key.startsWith(this.deniedDeletePrefix)) {
        throw denied();
      }
      const identity = this.identity(input);
      const value = this.objects.get(identity);
      if (value === undefined) throw missing();
      if (value.etag !== input.IfMatch) throw precondition();
      this.objects.delete(identity);
      return {};
    }
    if (name === "ListObjectsV2Command") {
      const keys = [...this.objects.keys()]
        .map((identity) => identity.slice(`${input.Bucket}/`.length))
        .filter((key) => key.startsWith(input.Prefix))
        .sort();
      const offset = input.ContinuationToken === undefined ? 0 : Number(input.ContinuationToken);
      const page = keys.slice(offset, offset + this.pageSize);
      const next = offset + page.length;
      return {
        Contents: page.map((Key) => {
          const value = this.objects.get(`${input.Bucket}/${Key}`);
          return {
            Key,
            Size: value.bytes.length,
            LastModified: value.lastModified,
            ETag: value.etag,
          };
        }),
        IsTruncated: next < keys.length,
        ...(next < keys.length ? { NextContinuationToken: String(next) } : {}),
      };
    }
    throw new Error(`unexpected command ${name}`);
  }
}

function event(label, eventType = "reconciliation") {
  return {
    contract: "verdify.lab-release-trigger",
    schemaVersion: 1,
    eventId: `evt_${label}_fixture`,
    eventType,
    sourceId: "stage-reporting-feed",
    sourceWatermark: `watermark-${label}`,
    occurredAt: "2026-07-11T09:58:00.000Z",
    payloadSha256: "0".repeat(64),
  };
}

function siteManifest(label, blobBytes) {
  const file = {
    path: "index.html",
    sha256: sha256(blobBytes),
    bytes: blobBytes.length,
    mediaType: "text/html; charset=utf-8",
  };
  const sourceSnapshotManifestSha256 = sha256(`snapshot-${label}`);
  const policyVersion = "stage-policy-v1";
  const builderCommit = "a".repeat(40);
  const contentIdentitySha256 = siteContentIdentitySha256({
    sourceSnapshotManifestSha256,
    policyVersion,
    builderCommit,
    files: [file],
  });
  const trigger = event(`site_${label}`, "reconciliation");
  trigger.payloadSha256 = siteReleasePayloadSha256({
    sourceSnapshotManifestSha256,
    policyVersion,
    builderCommit,
    contentIdentitySha256,
  });
  const releasedAt = "2026-07-11T10:00:00.000Z";
  return {
    contract: "verdify.lab-site-release",
    schemaVersion: 1,
    sourceSnapshotManifestSha256,
    policyVersion,
    builderCommit,
    event: trigger,
    releasedAt,
    freshness: evaluateEventFreshness(trigger, releasedAt),
    contentIdentitySha256,
    fileCount: 1,
    totalBytes: blobBytes.length,
    files: [file],
  };
}

function occurrenceManifest(label, graphBlobSha256, generationSha256) {
  const trigger = event(`occurrence_${label}`);
  const publishedAt = "2026-07-11T10:00:00.000Z";
  return {
    contract: "verdify.lab-specialist-occurrence-release",
    schemaVersion: 2,
    event: trigger,
    policyVersion: "stage-policy-v1",
    policySha256: "b".repeat(64),
    sourceSnapshotManifestSha256: "c".repeat(64),
    publishedAt,
    freshness: evaluateEventFreshness(trigger, publishedAt),
    occurrences: {
      graphs: [{ fallback: { sha256: graphBlobSha256 } }],
      currentMedia: [{
        occurrenceId: MEDIA_ID,
        pointer: { generationSha256 },
      }],
    },
  };
}

function occurrenceSelection(current, previous) {
  return {
    contract: "verdify.lab-occurrence-selection",
    schemaVersion: 1,
    generation: 2,
    current: { manifestSha256: current, eventId: "evt_occurrence_current_fixture" },
    previous: { manifestSha256: previous, eventId: "evt_occurrence_rollback_fixture" },
    selectedAt: "2026-07-11T10:00:00.000Z",
    reason: "publish",
  };
}

function mediaGeneration(label, blobSha256) {
  return {
    contract: "verdify.lab-current-media-generation",
    schemaVersion: 3,
    occurrenceId: MEDIA_ID,
    label,
    fallback: { sha256: blobSha256 },
  };
}

function mediaSelection(current, currentBlob, previous, previousBlob) {
  return {
    contract: "verdify.lab-current-media-selection",
    schemaVersion: 1,
    occurrenceId: MEDIA_ID,
    generation: 2,
    current: { generationSha256: current, blobSha256: currentBlob },
    previous: { generationSha256: previous, blobSha256: previousBlob },
    selectedAt: "2026-07-11T10:00:00.000Z",
    reason: "publish",
  };
}

async function fixture({ includeOrphans = true } = {}) {
  const client = new FakeS3Client();
  const siteStore = await new S3SiteReleaseStore(
    `s3://${BUCKET}/${SITE_PREFIX}`,
    { client },
  ).initialize();
  const occurrenceStore = await new S3OccurrenceReleaseStore(
    `s3://${BUCKET}/${OCCURRENCE_BASE_PREFIX}`,
    { client },
  ).initialize();
  const coordinationStore = await new S3ObjectStore({
    bucket: BUCKET,
    prefix: COORDINATION_PREFIX,
    accessMode: "writer",
    client,
  }).initialize();

  const site = {};
  for (const label of ["current", "rollback", ...(includeOrphans ? ["orphan"] : [])]) {
    const blobBytes = Buffer.from(`<!doctype html><title>${label}</title>\n`);
    const manifest = siteManifest(label, blobBytes);
    const releaseSha256 = sha256(canonicalBytes(manifest));
    client.seed(`${SITE_PREFIX}/blobs/sha256/${sha256(blobBytes)}`, blobBytes);
    client.seed(`${SITE_PREFIX}/releases/sha256/${releaseSha256}.json`, canonicalBytes(manifest));
    site[label] = { blobSha256: sha256(blobBytes), releaseSha256 };
  }
  client.seed(`${SITE_PREFIX}/selection.json`, canonicalBytes({
    contract: "verdify.lab-site-release-selection",
    schemaVersion: 1,
    generation: 2,
    current: { releaseSha256: site.current.releaseSha256, eventId: "evt_site_current_fixture" },
    previous: { releaseSha256: site.rollback.releaseSha256, eventId: "evt_site_rollback_fixture" },
    selectedAt: "2026-07-11T10:00:00.000Z",
    reason: "publish",
  }));

  const occurrence = {};
  for (const label of ["current", "rollback", ...(includeOrphans ? ["orphan"] : [])]) {
    const graphBytes = Buffer.from(`graph-${label}`);
    const mediaBytes = Buffer.from(`media-${label}`);
    const graphBlobSha256 = sha256(graphBytes);
    const mediaBlobSha256 = sha256(mediaBytes);
    const generation = mediaGeneration(label, mediaBlobSha256);
    const generationSha256 = sha256(canonicalBytes(generation));
    const manifest = occurrenceManifest(label, graphBlobSha256, generationSha256);
    const manifestSha256 = sha256(canonicalBytes(manifest));
    client.seed(`${OCCURRENCE_PREFIX}/blobs/sha256/${graphBlobSha256}.png`, graphBytes);
    client.seed(`${OCCURRENCE_PREFIX}/blobs/sha256/${mediaBlobSha256}.png`, mediaBytes);
    client.seed(
      `${OCCURRENCE_PREFIX}/occurrences/${MEDIA_ID}/generations/sha256/${generationSha256}.json`,
      canonicalBytes(generation),
    );
    client.seed(`${OCCURRENCE_PREFIX}/manifests/sha256/${manifestSha256}.json`, canonicalBytes(manifest));
    occurrence[label] = { graphBlobSha256, mediaBlobSha256, generationSha256, manifestSha256 };
  }
  client.seed(
    `${OCCURRENCE_PREFIX}/selection.json`,
    canonicalBytes(occurrenceSelection(occurrence.current.manifestSha256, occurrence.rollback.manifestSha256)),
  );
  client.seed(
    `${OCCURRENCE_PREFIX}/occurrences/${MEDIA_ID}/selection.json`,
    canonicalBytes(mediaSelection(
      occurrence.current.generationSha256,
      occurrence.current.mediaBlobSha256,
      occurrence.rollback.generationSha256,
      occurrence.rollback.mediaBlobSha256,
    )),
  );
  return { client, siteStore, occurrenceStore, coordinationStore, site, occurrence };
}

function estimate(overrides = {}) {
  return {
    contract: "verdify.lab-release-storage-publication-estimate",
    schemaVersion: 1,
    retainedBytesAdded: 1024,
    writtenBytes: 2048,
    egressBytes: 4096,
    requests: 5,
    ...overrides,
  };
}

function publisherResult(overrides = {}) {
  return {
    contract: "verdify.lab-release-storage-publication-result",
    schemaVersion: 1,
    status: "published",
    siteReleaseSha256: "d".repeat(64),
    occurrenceManifestSha256: "e".repeat(64),
    usage: { writtenBytes: 1024, deletedBytes: 0, egressBytes: 2048, requests: 3 },
    ...overrides,
  };
}

function clock(start = AS_OF) {
  let value = Date.parse(start);
  return async () => {
    const result = new Date(value).toISOString();
    value += 1000;
    return result;
  };
}

test("complete S3 inventory covers site and nested occurrence roots and rejects unknown bytes", async () => {
  const value = await fixture();
  const inventory = await captureReleaseStorageS3Inventory({
    siteStore: value.siteStore,
    occurrenceStore: value.occurrenceStore,
    capturedAt: AS_OF,
  });
  assert.equal(inventory.listings.site.complete, true);
  assert.equal(inventory.listings.occurrence.complete, true);
  assert.equal(inventory.selectors.length, 3);
  assert.ok(inventory.objects.some((object) => (
    object.kind === "generation"
    && object.key.includes(`occurrences/${MEDIA_ID}/generations/sha256/`)
  )));
  assert.ok(inventory.objects.some((object) => (
    object.namespace === "occurrence"
    && object.kind === "blob"
    && object.key === `blobs/sha256/${value.occurrence.current.graphBlobSha256}`
  )));

  value.client.seed(`${OCCURRENCE_PREFIX}/unclassified.bin`, Buffer.from("unknown"));
  await assert.rejects(
    captureReleaseStorageS3Inventory({
      siteStore: value.siteStore,
      occurrenceStore: value.occurrenceStore,
      capturedAt: AS_OF,
    }),
    /outside the closed nested-root layout/u,
  );
});

test("distributed lease uses monotonic fencing, hashes owner identity, and rejects an active peer", async () => {
  const value = await fixture({ includeOrphans: false });
  const marker = "stage-publisher-marker";
  const first = await acquireReleaseStorageS3Lease({
    coordinationStore: value.coordinationStore,
    planSha256: "1".repeat(64),
    ownerIdentity: marker,
    issuedAt: AS_OF,
    nonce: "lease_nonce_0001",
  });
  assert.equal(first.record.fencingToken, 1);
  assert.doesNotMatch(value.client.objects.get(`${BUCKET}/${COORDINATION_PREFIX}/fence.json`).bytes.toString(), new RegExp(marker, "u"));
  await assert.rejects(
    acquireReleaseStorageS3Lease({
      coordinationStore: value.coordinationStore,
      planSha256: "2".repeat(64),
      ownerIdentity: "another-stage-publisher",
      issuedAt: "2026-07-14T12:01:00.000Z",
      nonce: "lease_nonce_0002",
    }),
    /another release storage publication lease is active/u,
  );
  await releaseReleaseStorageS3Lease({
    coordinationStore: value.coordinationStore,
    acquired: first,
    releasedAt: "2026-07-14T12:02:00.000Z",
  });
  const second = await acquireReleaseStorageS3Lease({
    coordinationStore: value.coordinationStore,
    planSha256: "2".repeat(64),
    ownerIdentity: "another-stage-publisher",
    issuedAt: "2026-07-14T12:03:00.000Z",
    nonce: "lease_nonce_0002",
  });
  assert.equal(second.record.fencingToken, 2);
});

test("usage reservations are immutable, idempotent, and never decrease daily counters", async () => {
  const value = await fixture({ includeOrphans: false });
  const input = {
    coordinationStore: value.coordinationStore,
    kind: "publication",
    operationSha256: "3".repeat(64),
    createdAt: AS_OF,
    delta: { writtenBytes: 10, deletedBytes: 0, egressBytes: 20, requests: 2 },
  };
  const first = await reserveReleaseStorageS3Usage(input);
  const retry = await reserveReleaseStorageS3Usage({
    ...input,
    createdAt: "2026-07-14T12:01:00.000Z",
  });
  assert.equal(retry.reservationId, first.reservationId);
  const loaded = await loadReleaseStorageS3Usage({
    coordinationStore: value.coordinationStore,
    asOf: "2026-07-14T12:02:00.000Z",
  });
  assert.deepEqual(loaded.state.counters, {
    writtenBytes: 10,
    deletedBytes: 0,
    egressBytes: 20,
    requests: 2,
  });
  assert.equal(loaded.reservationCount, 1);
});

test("coordinator performs fenced 48-hour GC, reserves before mutations, and emits names-safe status metrics", async () => {
  const value = await fixture();
  let authority = null;
  const status = await coordinateReleaseStorageS3Publication({
    siteStore: value.siteStore,
    occurrenceStore: value.occurrenceStore,
    coordinationStore: value.coordinationStore,
    publication: estimate(),
    eventIdentitySha256: "4".repeat(64),
    ownerIdentity: "stage-producer-pod-0001",
    clock: clock(),
    leaseNonce: "coordinator_nonce_0001",
    async publisher(selected) {
      authority = selected;
      return publisherResult();
    },
  });
  assert.equal(status.state, "complete");
  assert.ok(status.deletedObjects >= 5);
  assert.equal(authority.fencingToken, status.fencingToken);
  assert.equal(
    value.client.objects.has(`${BUCKET}/${SITE_PREFIX}/releases/sha256/${value.site.orphan.releaseSha256}.json`),
    false,
  );
  assert.equal(
    value.client.objects.has(`${BUCKET}/${OCCURRENCE_PREFIX}/manifests/sha256/${value.occurrence.orphan.manifestSha256}.json`),
    false,
  );
  const loaded = await loadReleaseStorageS3Usage({
    coordinationStore: value.coordinationStore,
    asOf: "2026-07-14T12:10:00.000Z",
  });
  assert.ok(loaded.reservationCount >= status.deletedObjects + 2);
  assert.ok(loaded.state.counters.deletedBytes > 0);
  const metrics = renderReleaseStorageS3Metrics(status);
  assert.match(metrics, /verdify_lab_release_storage_requests_day/u);
  assert.doesNotMatch(metrics, /stage-producer-pod|verdify-lab-occurrences|lab-stage\//u);
  assert.equal(COORDINATION_FINALIZATION_USAGE.egressBytes, 256 * 1024);
  assert.equal(COORDINATION_FINALIZATION_USAGE.requests, 32);
  assert.ok(COORDINATION_FINALIZATION_USAGE.egressBytes < 512 * 1024 * 1024);
  assert.ok(COORDINATION_FINALIZATION_USAGE.requests < 1000);
});

async function exactNextRequestBlock(value, nextAt) {
  const loaded = await loadReleaseStorageS3Usage({
    coordinationStore: value.coordinationStore,
    asOf: nextAt,
  });
  const requests = releaseStorageS3CoordinatorContract.budgets.requestsPerDay
    - loaded.state.counters.requests
    - COORDINATION_FINALIZATION_USAGE.requests
    - releaseStorageS3CoordinatorContract.gcPreflightUsage.requests;
  assert.ok(requests >= 0);
  let publisherCalls = 0;
  const blocked = await coordinateReleaseStorageS3Publication({
    siteStore: value.siteStore,
    occurrenceStore: value.occurrenceStore,
    coordinationStore: value.coordinationStore,
    publication: estimate({ retainedBytesAdded: 0, writtenBytes: 0, egressBytes: 0, requests }),
    eventIdentitySha256: "9".repeat(64),
    ownerIdentity: "stage-producer-pod-next",
    clock: clock(nextAt),
    leaseNonce: "coordinator_nonce_next",
    async publisher() {
      publisherCalls += 1;
      return publisherResult();
    },
  });
  assert.equal(blocked.state, "blocked");
  assert.equal(blocked.publicationDecision, "block");
  assert.ok(blocked.publicationReasons.includes("requestsPerDay-budget"));
  assert.equal(publisherCalls, 0);
}

test("a crash after the publisher retains its pre-reservation and the next coordinator blocks at 100 percent", async () => {
  const value = await fixture({ includeOrphans: false });
  let publisherCalls = 0;
  await assert.rejects(
    coordinateReleaseStorageS3Publication({
      siteStore: value.siteStore,
      occurrenceStore: value.occurrenceStore,
      coordinationStore: value.coordinationStore,
      publication: estimate(),
      eventIdentitySha256: "5".repeat(64),
      ownerIdentity: "stage-producer-pod-crash-publish",
      clock: clock(),
      leaseNonce: "coordinator_nonce_crash_publish",
      async publisher() {
        publisherCalls += 1;
        return publisherResult();
      },
      async checkpoint({ phase }) {
        if (phase === "after-publisher") throw new Error("simulated crash after publisher");
      },
    }),
    /simulated crash after publisher/u,
  );
  assert.equal(publisherCalls, 1);
  const usageAfterCrash = await loadReleaseStorageS3Usage({
    coordinationStore: value.coordinationStore,
    asOf: "2026-07-14T12:10:00.000Z",
  });
  assert.ok(usageAfterCrash.state.counters.writtenBytes >= COORDINATION_FINALIZATION_USAGE.writtenBytes);
  await exactNextRequestBlock(value, "2026-07-14T12:20:00.000Z");
});

test("a crash after GC retains every pre-mutation reservation and the next coordinator blocks at 100 percent", async () => {
  const value = await fixture();
  let publisherCalls = 0;
  await assert.rejects(
    coordinateReleaseStorageS3Publication({
      siteStore: value.siteStore,
      occurrenceStore: value.occurrenceStore,
      coordinationStore: value.coordinationStore,
      publication: estimate(),
      eventIdentitySha256: "6".repeat(64),
      ownerIdentity: "stage-producer-pod-crash-gc",
      clock: clock(),
      leaseNonce: "coordinator_nonce_crash_gc",
      async publisher() {
        publisherCalls += 1;
        return publisherResult();
      },
      async checkpoint({ phase }) {
        if (phase === "after-gc") throw new Error("simulated crash after GC");
      },
    }),
    /simulated crash after GC/u,
  );
  assert.equal(publisherCalls, 0);
  const usageAfterCrash = await loadReleaseStorageS3Usage({
    coordinationStore: value.coordinationStore,
    asOf: "2026-07-14T12:10:00.000Z",
  });
  assert.ok(usageAfterCrash.state.counters.deletedBytes > 0);
  assert.ok(usageAfterCrash.reservationCount > 1);
  await exactNextRequestBlock(value, "2026-07-14T12:20:00.000Z");
});

test("an expired fence blocks the publisher after its conservative reservation", async () => {
  const value = await fixture({ includeOrphans: false });
  let now = AS_OF;
  let publisherCalls = 0;
  await assert.rejects(
    coordinateReleaseStorageS3Publication({
      siteStore: value.siteStore,
      occurrenceStore: value.occurrenceStore,
      coordinationStore: value.coordinationStore,
      publication: estimate(),
      eventIdentitySha256: "7".repeat(64),
      ownerIdentity: "stage-producer-pod-expired",
      clock: async () => now,
      leaseSeconds: 60,
      leaseNonce: "coordinator_nonce_expired",
      async publisher() {
        publisherCalls += 1;
        return publisherResult();
      },
      async checkpoint({ phase }) {
        if (phase === "after-gc") now = "2026-07-14T12:01:01.000Z";
      },
    }),
    /publication lease is no longer current/u,
  );
  assert.equal(publisherCalls, 0);
  const usage = await loadReleaseStorageS3Usage({
    coordinationStore: value.coordinationStore,
    asOf: "2026-07-14T12:02:00.000Z",
  });
  assert.ok(usage.state.counters.writtenBytes >= COORDINATION_FINALIZATION_USAGE.writtenBytes);
});

test("activation proof mutates and cleans all three prefixes and fails closed when one prefix is denied", async () => {
  const value = await fixture({ includeOrphans: false });
  const result = await proveReleaseStorageS3Activation({
    siteObjects: value.siteStore.objects,
    occurrenceObjects: value.occurrenceStore.objects,
    coordinationObjects: value.coordinationStore,
    nonce: "activation_nonce_0001",
    probedAt: AS_OF,
  });
  assert.equal(result.dedicatedPrefixesVerified, true);
  assert.equal(result.boundedCreateReadHeadDelete, true);
  assert.equal(result.cleanupComplete, true);
  for (const evidence of Object.values(result.prefixes)) {
    assert.deepEqual(evidence, {
      created: true,
      read: true,
      head: true,
      deleted: true,
      absentAfterDelete: true,
      cleanupAttempted: true,
      cleanupComplete: true,
    });
  }
  assert.equal(
    [...value.client.objects.keys()].some((key) => key.includes("activation-proof/")),
    false,
  );

  value.client.deniedPrefix = OCCURRENCE_PREFIX;
  await assert.rejects(
    proveReleaseStorageS3Activation({
      siteObjects: value.siteStore.objects,
      occurrenceObjects: value.occurrenceStore.objects,
      coordinationObjects: value.coordinationStore,
      nonce: "activation_nonce_0002",
      probedAt: AS_OF,
    }),
    (error) => {
      assert.ok(error instanceof ReleaseStorageS3ActivationProofError);
      assert.equal(error.result.dedicatedPrefixesVerified, false);
      assert.equal(error.result.prefixes.occurrence.created, false);
      assert.equal(error.result.prefixes.site.cleanupComplete, true);
      assert.equal(error.result.prefixes.coordination.cleanupComplete, true);
      return true;
    },
  );
  assert.equal(
    [...value.client.objects.keys()].some((key) => key.includes("activation-proof/")),
    false,
  );
});

test("activation proof cleans every created key after an intermediate read failure", async () => {
  const value = await fixture({ includeOrphans: false });
  value.client.failNextGetPrefix = `${SITE_PREFIX}/activation-proof/`;
  await assert.rejects(
    proveReleaseStorageS3Activation({
      siteObjects: value.siteStore.objects,
      occurrenceObjects: value.occurrenceStore.objects,
      coordinationObjects: value.coordinationStore,
      nonce: "activation_nonce_read_failure",
      probedAt: AS_OF,
    }),
    (error) => {
      assert.ok(error instanceof ReleaseStorageS3ActivationProofError);
      assert.equal(error.result.status, "failed");
      assert.equal(error.result.dedicatedPrefixesVerified, false);
      assert.equal(error.result.prefixes.site.created, true);
      assert.equal(error.result.prefixes.site.read, false);
      for (const evidence of Object.values(error.result.prefixes)) {
        assert.equal(evidence.cleanupAttempted, true);
        assert.equal(evidence.cleanupComplete, true);
      }
      return true;
    },
  );
  assert.equal(
    [...value.client.objects.keys()].some((key) => key.includes("activation-proof/")),
    false,
  );
});

test("activation proof cannot report success when final cleanup is denied", async () => {
  const value = await fixture({ includeOrphans: false });
  value.client.deniedDeletePrefix = `${OCCURRENCE_PREFIX}/activation-proof/`;
  await assert.rejects(
    proveReleaseStorageS3Activation({
      siteObjects: value.siteStore.objects,
      occurrenceObjects: value.occurrenceStore.objects,
      coordinationObjects: value.coordinationStore,
      nonce: "activation_nonce_cleanup_denied",
      probedAt: AS_OF,
    }),
    (error) => {
      assert.ok(error instanceof ReleaseStorageS3ActivationProofError);
      assert.equal(error.result.status, "failed");
      assert.equal(error.result.cleanupComplete, false);
      assert.equal(error.result.dedicatedPrefixesVerified, false);
      assert.equal(error.result.prefixes.occurrence.cleanupAttempted, true);
      assert.equal(error.result.prefixes.occurrence.cleanupComplete, false);
      return true;
    },
  );
  assert.equal(
    [...value.client.objects.keys()].filter((key) => key.includes("activation-proof/")).length,
    1,
  );
});

test("Pass 1 contract separates names-only readiness from explicit mutating activation", () => {
  assert.equal(releaseStoragePassOneContract.readiness.mutating, false);
  assert.equal(releaseStoragePassOneContract.activationProof.mutating, true);
  assert.match(releaseStoragePassOneContract.activationProof.command, /acknowledge-stage-mutation/u);
  assert.equal(releaseStoragePassOneContract.readerSecret, "verdify-lab-occurrence-store-reader");
  assert.equal(releaseStoragePassOneContract.writerSecret, "verdify-lab-occurrence-store-writer");
  assert.ok(releaseStoragePassOneContract.writerEnvironmentNames.includes("LAB_RELEASE_COORDINATION_STORE"));
  assert.ok(!releaseStoragePassOneContract.readerEnvironmentNames.includes("LAB_RELEASE_COORDINATION_STORE"));
  assert.doesNotMatch(JSON.stringify(releaseStoragePassOneContract), /s3:\/\/|AWS_SECRET_ACCESS_KEY":"/u);
});
