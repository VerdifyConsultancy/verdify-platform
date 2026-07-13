import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  createReleaseStorageUsageState,
  executeReleaseStorageGcPlan,
  parseReleaseStorageUsageState,
  planReleaseStorageSafety,
  recordReleaseStorageUsage,
  releaseStorageSafetyContract,
  serializeReleaseStorageUsageState,
} from "../scripts/lib/release-storage-safety.mjs";

const AS_OF = "2026-07-13T12:00:00.000Z";
const OLD = "2026-07-11T11:59:59.999Z";
const BOUNDARY = "2026-07-11T12:00:00.000Z";
const JUST_INSIDE = "2026-07-11T12:00:00.001Z";
const CAMERA_ID = "media_0123456789abcdef01234567";

function digest(label) {
  return createHash("sha256").update(label).digest("hex");
}

function immutable(namespace, kind, label, { bytes = 100, createdAt = OLD, references = [] } = {}) {
  const sha256 = digest(label);
  let key;
  if (kind === "blob") key = `blobs/sha256/${sha256}`;
  if (kind === "release") key = `releases/sha256/${sha256}.json`;
  if (kind === "manifest") key = `manifests/sha256/${sha256}.json`;
  if (kind === "generation") key = `occurrences/${CAMERA_ID}/generations/sha256/${sha256}.json`;
  if (kind === "event") key = namespace === "site"
    ? `events/sha256/${digest(`${label}-key`)}.json`
    : `occurrences/${CAMERA_ID}/events/sha256/${digest(`${label}-key`)}.json`;
  return {
    namespace,
    key,
    kind,
    sha256,
    bytes,
    createdAt,
    references: references.map((item) => item.key).sort(),
  };
}

function selector(namespace, selectorKind, current, rollback, label) {
  return {
    namespace,
    selectorKind,
    occurrenceId: selectorKind === "current-media" ? CAMERA_ID : null,
    key: selectorKind === "current-media" ? `occurrences/${CAMERA_ID}/selection.json` : "selection.json",
    sha256: digest(`${label}-selector`),
    etag: `etag-${label}`,
    bytes: 80,
    currentKey: current.key,
    rollbackKey: rollback?.key ?? null,
  };
}

function inventory({
  asOf = AS_OF,
  siteComplete = true,
  occurrenceComplete = true,
  mutate = () => {},
} = {}) {
  const siteCurrentBlob = immutable("site", "blob", "site-current-blob", { bytes: 110 });
  const siteRollbackBlob = immutable("site", "blob", "site-rollback-blob", { bytes: 120 });
  const siteOrphanBlob = immutable("site", "blob", "site-orphan-blob", { bytes: 130 });
  const siteCurrent = immutable("site", "release", "site-current", {
    bytes: 210,
    references: [siteCurrentBlob],
  });
  const siteRollback = immutable("site", "release", "site-rollback", {
    bytes: 220,
    references: [siteRollbackBlob],
  });
  const siteOrphan = immutable("site", "release", "site-orphan", {
    bytes: 230,
    references: [siteCurrentBlob, siteOrphanBlob],
  });
  const siteEvent = immutable("site", "event", "site-event", { bytes: 40 });

  const graphBlob = immutable("occurrence", "blob", "graph-current-blob", { bytes: 140 });
  const graphRollbackBlob = immutable("occurrence", "blob", "graph-rollback-blob", { bytes: 150 });
  const cameraBlob = immutable("occurrence", "blob", "camera-current-blob", { bytes: 160 });
  const cameraRollbackBlob = immutable("occurrence", "blob", "camera-rollback-blob", { bytes: 170 });
  const orphanBlob = immutable("occurrence", "blob", "occurrence-orphan-blob", { bytes: 180 });
  const cameraCurrent = immutable("occurrence", "generation", "camera-current", {
    bytes: 260,
    references: [cameraBlob],
  });
  const cameraRollback = immutable("occurrence", "generation", "camera-rollback", {
    bytes: 270,
    references: [cameraRollbackBlob],
  });
  const cameraOrphan = immutable("occurrence", "generation", "camera-orphan", {
    bytes: 280,
    references: [orphanBlob],
  });
  const occurrenceCurrent = immutable("occurrence", "manifest", "occurrence-current", {
    bytes: 310,
    references: [cameraCurrent, graphBlob],
  });
  const occurrenceRollback = immutable("occurrence", "manifest", "occurrence-rollback", {
    bytes: 320,
    references: [cameraRollback, graphRollbackBlob],
  });
  const occurrenceOrphan = immutable("occurrence", "manifest", "occurrence-orphan", {
    bytes: 330,
    references: [graphBlob, orphanBlob],
  });
  const occurrenceEvent = immutable("occurrence", "event", "occurrence-event", { bytes: 50 });

  const value = {
    contract: "verdify.lab-release-storage-inventory",
    schemaVersion: 1,
    capturedAt: asOf,
    listings: {
      site: { complete: siteComplete, continuationToken: siteComplete ? null : "site-page-2" },
      occurrence: {
        complete: occurrenceComplete,
        continuationToken: occurrenceComplete ? null : "occurrence-page-2",
      },
    },
    selectors: [
      selector("site", "site", siteCurrent, siteRollback, "site"),
      selector("occurrence", "aggregate", occurrenceCurrent, occurrenceRollback, "aggregate"),
      selector("occurrence", "current-media", cameraCurrent, cameraRollback, "camera"),
    ],
    objects: [
      siteCurrentBlob,
      siteRollbackBlob,
      siteOrphanBlob,
      siteCurrent,
      siteRollback,
      siteOrphan,
      siteEvent,
      graphBlob,
      graphRollbackBlob,
      cameraBlob,
      cameraRollbackBlob,
      orphanBlob,
      cameraCurrent,
      cameraRollback,
      cameraOrphan,
      occurrenceCurrent,
      occurrenceRollback,
      occurrenceOrphan,
      occurrenceEvent,
    ],
  };
  mutate(value, {
    siteCurrentBlob,
    siteRollbackBlob,
    siteOrphanBlob,
    siteCurrent,
    siteRollback,
    siteOrphan,
    graphBlob,
    graphRollbackBlob,
    cameraBlob,
    cameraRollbackBlob,
    orphanBlob,
    cameraCurrent,
    cameraRollback,
    cameraOrphan,
    occurrenceCurrent,
    occurrenceRollback,
    occurrenceOrphan,
  });
  value.selectors.sort((left, right) => `${left.namespace}\u0000${left.key}`.localeCompare(`${right.namespace}\u0000${right.key}`));
  value.objects.sort((left, right) => `${left.namespace}\u0000${left.key}`.localeCompare(`${right.namespace}\u0000${right.key}`));
  return value;
}

function estimate(overrides = {}) {
  return {
    contract: "verdify.lab-release-storage-publication-estimate",
    schemaVersion: 1,
    retainedBytesAdded: 0,
    writtenBytes: 0,
    egressBytes: 0,
    requests: 0,
    ...overrides,
  };
}

function plan(options = {}) {
  return planReleaseStorageSafety({
    snapshot: options.snapshot ?? inventory(),
    usageState: options.usageState ?? createReleaseStorageUsageState(AS_OF),
    publication: options.publication ?? estimate(),
    asOf: options.asOf ?? AS_OF,
  });
}

function lease(value, overrides = {}) {
  return {
    contract: "verdify.lab-release-storage-gc-lease",
    schemaVersion: 1,
    leaseId: "gc_20260713_0001",
    fencingToken: 41,
    planSha256: value.sha256,
    issuedAt: AS_OF,
    expiresAt: "2026-07-13T12:05:00.000Z",
    ...overrides,
  };
}

function fakeAdapter(value, fence, overrides = {}) {
  const selectors = new Map(value.document.selectors.map((item) => [`${item.namespace}:${item.key}`, item]));
  const objects = new Map(value.document.deletions.map((item) => [`${item.namespace}:${item.key}`, item]));
  const calls = { fence: 0, selector: 0, stat: 0, delete: 0, deleted: [] };
  return {
    calls,
    adapter: {
      contract: "verdify.lab-release-storage-gc-delete-adapter",
      schemaVersion: 1,
      async readFence() {
        calls.fence += 1;
        return overrides.fence ?? fence;
      },
      async readSelector(request) {
        calls.selector += 1;
        const selected = selectors.get(`${request.namespace}:${request.key}`);
        return overrides.selector ?? { sha256: selected.sha256, etag: selected.etag };
      },
      async statObject(request) {
        calls.stat += 1;
        const object = objects.get(`${request.namespace}:${request.key}`);
        return overrides.object ?? {
          sha256: object.sha256,
          bytes: object.bytes,
          createdAt: object.createdAt,
        };
      },
      async deleteObject(request) {
        calls.delete += 1;
        assert.deepEqual(request.selectorPreconditions, value.document.selectors);
        assert.deepEqual(request.lease, fence);
        calls.deleted.push(`${request.namespace}:${request.key}`);
        return { deleted: true };
      },
    },
  };
}

test("GC reachability keeps current and rollback releases/generations while ordering old orphan cleanup", () => {
  const value = plan();
  const keys = value.document.deletions.map((item) => item.key);
  assert.deepEqual(keys.sort(), [
    `blobs/sha256/${digest("occurrence-orphan-blob")}`,
    `blobs/sha256/${digest("site-orphan-blob")}`,
    `manifests/sha256/${digest("occurrence-orphan")}.json`,
    `occurrences/${CAMERA_ID}/generations/sha256/${digest("camera-orphan")}.json`,
    `releases/sha256/${digest("site-orphan")}.json`,
  ].sort());
  assert.ok(!keys.includes(`blobs/sha256/${digest("site-current-blob")}`), "shared selected site blob is retained");
  assert.ok(!keys.includes(`blobs/sha256/${digest("graph-current-blob")}`), "shared selected graph blob is retained");

  const orderedKeys = value.document.deletions.map((item) => item.key);
  assert.ok(
    orderedKeys.indexOf(`releases/sha256/${digest("site-orphan")}.json`)
      < orderedKeys.indexOf(`blobs/sha256/${digest("site-orphan-blob")}`),
    "a referencing release is deleted before its exclusive blob",
  );
  assert.ok(
    orderedKeys.indexOf(`occurrences/${CAMERA_ID}/generations/sha256/${digest("camera-orphan")}.json`)
      < orderedKeys.indexOf(`blobs/sha256/${digest("occurrence-orphan-blob")}`),
    "generation and manifest references are deleted before their shared orphan blob",
  );
  assert.equal(value.document.publication.decision, "allow");
  assert.equal(value.document.publication.preservesLastKnownGood, true);
});

test("objects become eligible only strictly after the 48-hour recovery boundary", () => {
  const boundarySnapshot = inventory({
    mutate(value, records) {
      records.siteOrphan.createdAt = BOUNDARY;
      records.siteOrphanBlob.createdAt = OLD;
      records.cameraOrphan.createdAt = JUST_INSIDE;
      records.occurrenceOrphan.createdAt = OLD;
      records.orphanBlob.createdAt = OLD;
      value.objects = value.objects;
    },
  });
  const value = plan({ snapshot: boundarySnapshot });
  const keys = value.document.deletions.map((item) => item.key);
  assert.ok(!keys.includes(`releases/sha256/${digest("site-orphan")}.json`));
  assert.ok(!keys.includes(`blobs/sha256/${digest("site-orphan-blob")}`), "boundary release keeps its old child blob");
  assert.ok(!keys.includes(`occurrences/${CAMERA_ID}/generations/sha256/${digest("camera-orphan")}.json`));
  assert.ok(keys.includes(`manifests/sha256/${digest("occurrence-orphan")}.json`));
});

test("an incomplete namespace listing blocks publication and emits no deletion plan", () => {
  const value = plan({ snapshot: inventory({ occurrenceComplete: false }) });
  assert.deepEqual(value.document.deletions, []);
  assert.equal(value.document.accounting.plannedDeletedBytes, 0);
  assert.equal(value.document.publication.decision, "block");
  assert.deepEqual(value.document.publication.reasons, ["incomplete-listing"]);
  assert.equal(value.document.publication.preservesLastKnownGood, true);
});

test("default budget thresholds warn at exactly 80 percent and block at exactly 100 percent", () => {
  assert.deepEqual(releaseStorageSafetyContract.budgets, {
    retainedBytes: 10 * 1024 ** 3,
    writtenBytesPerDay: 5 * 1024 ** 3,
    egressBytesPerDay: 10 * 1024 ** 3,
    requestsPerDay: 25_000,
    warningFraction: 0.8,
  });
  const warning = plan({
    publication: estimate({ writtenBytes: releaseStorageSafetyContract.budgets.writtenBytesPerDay * 0.8 }),
  });
  assert.equal(warning.document.publication.decision, "warn");
  assert.deepEqual(warning.document.publication.reasons, ["writtenBytesPerDay-warning"]);
  assert.equal(
    warning.document.thresholds.find((item) => item.name === "writtenBytesPerDay").status,
    "warn",
  );

  const blocked = plan({
    publication: estimate({ writtenBytes: releaseStorageSafetyContract.budgets.writtenBytesPerDay }),
  });
  assert.equal(blocked.document.publication.decision, "block");
  assert.deepEqual(blocked.document.publication.reasons, ["writtenBytesPerDay-budget"]);
  assert.equal(blocked.document.publication.preservesLastKnownGood, true);

  const baseline = plan();
  const retainedBlock = plan({
    publication: estimate({
      retainedBytesAdded: releaseStorageSafetyContract.budgets.retainedBytes
        - baseline.document.accounting.retainedBytesAfterGc,
    }),
  });
  assert.equal(retainedBlock.document.publication.decision, "block");
  assert.ok(retainedBlock.document.publication.reasons.includes("retainedBytes-budget"));

  const egressBlock = plan({
    publication: estimate({ egressBytes: releaseStorageSafetyContract.budgets.egressBytesPerDay }),
  });
  assert.equal(egressBlock.document.publication.decision, "block");
  assert.ok(egressBlock.document.publication.reasons.includes("egressBytesPerDay-budget"));

  const requestState = createReleaseStorageUsageState(AS_OF);
  requestState.counters.requests = releaseStorageSafetyContract.budgets.requestsPerDay
    - baseline.document.accounting.plannedGcRequests;
  const requestBlock = plan({ usageState: requestState });
  assert.equal(requestBlock.document.publication.decision, "block");
  assert.ok(requestBlock.document.publication.reasons.includes("requestsPerDay-budget"));
});

test("canonical usage state survives restart, accumulates measured work, and rolls over by UTC day", () => {
  const initial = createReleaseStorageUsageState("2026-07-13T01:00:00.000Z");
  const first = recordReleaseStorageUsage(initial, {
    writtenBytes: 10,
    deletedBytes: 20,
    egressBytes: 30,
    requests: 4,
  }, "2026-07-13T02:00:00.000Z");
  const serialized = serializeReleaseStorageUsageState(first);
  assert.deepEqual(parseReleaseStorageUsageState(serialized), first);
  assert.throws(
    () => parseReleaseStorageUsageState(Buffer.from(JSON.stringify(first))),
    /not canonical JSON/u,
  );
  const restarted = recordReleaseStorageUsage(parseReleaseStorageUsageState(serialized), {
    writtenBytes: 1,
    deletedBytes: 2,
    egressBytes: 3,
    requests: 1,
  }, "2026-07-13T03:00:00.000Z");
  assert.deepEqual(restarted.counters, {
    writtenBytes: 11,
    deletedBytes: 22,
    egressBytes: 33,
    requests: 5,
  });
  const nextDay = recordReleaseStorageUsage(restarted, {
    writtenBytes: 1,
    deletedBytes: 2,
    egressBytes: 3,
    requests: 1,
  }, "2026-07-14T00:00:00.000Z");
  assert.deepEqual(nextDay.counters, {
    writtenBytes: 1,
    deletedBytes: 2,
    egressBytes: 3,
    requests: 1,
  });
});

test("execution requires an explicit current fence and accounts confirmed deletions and requests", async () => {
  const value = plan();
  const token = lease(value);
  const fake = fakeAdapter(value, token);
  const result = await executeReleaseStorageGcPlan({
    plan: value,
    adapter: fake.adapter,
    lease: token,
    asOf: AS_OF,
  });
  assert.equal(result.deletedObjects, value.document.deletions.length);
  assert.equal(result.usage.deletedBytes, value.document.accounting.plannedDeletedBytes);
  assert.equal(result.usage.requests, value.document.accounting.plannedGcRequests);
  assert.equal(fake.calls.delete, value.document.deletions.length);
  assert.equal(fake.calls.selector, value.document.selectors.length);
  assert.equal(fake.calls.stat, value.document.deletions.length);
  assert.equal(fake.calls.fence, value.document.deletions.length + 1);
});

test("a stale fencing token stops before object inspection or deletion", async () => {
  const value = plan();
  const token = lease(value);
  const fake = fakeAdapter(value, token, {
    fence: { ...token, fencingToken: token.fencingToken + 1 },
  });
  await assert.rejects(
    executeReleaseStorageGcPlan({ plan: value, adapter: fake.adapter, lease: token, asOf: AS_OF }),
    /fencing token is stale/u,
  );
  assert.equal(fake.calls.selector, 0);
  assert.equal(fake.calls.stat, 0);
  assert.equal(fake.calls.delete, 0);
});

test("selector or object conflicts are fully preflighted before any deletion", async () => {
  const value = plan();
  const token = lease(value);
  const selectorConflict = fakeAdapter(value, token, {
    selector: { sha256: digest("changed-selector"), etag: "changed" },
  });
  await assert.rejects(
    executeReleaseStorageGcPlan({ plan: value, adapter: selectorConflict.adapter, lease: token, asOf: AS_OF }),
    /selector changed/u,
  );
  assert.equal(selectorConflict.calls.stat, 0);
  assert.equal(selectorConflict.calls.delete, 0);

  const objectConflict = fakeAdapter(value, token, {
    object: { sha256: digest("changed-object"), bytes: 1, createdAt: OLD },
  });
  await assert.rejects(
    executeReleaseStorageGcPlan({ plan: value, adapter: objectConflict.adapter, lease: token, asOf: AS_OF }),
    /object changed/u,
  );
  assert.equal(objectConflict.calls.delete, 0);
});

test("invalid inventory or plan state cannot reach an injected deletion adapter", async () => {
  assert.throws(
    () => plan({
      snapshot: inventory({
        mutate(_value, records) {
          records.siteOrphan.references.push(`blobs/sha256/${digest("missing")}`);
          records.siteOrphan.references.sort();
        },
      }),
    }),
    /reference is missing/u,
  );

  const value = plan();
  const token = lease(value);
  const fake = fakeAdapter(value, token);
  const tampered = structuredClone(value);
  tampered.document.deletions[0].bytes += 1;
  await assert.rejects(
    executeReleaseStorageGcPlan({ plan: tampered, adapter: fake.adapter, lease: token, asOf: AS_OF }),
    /plan identity is invalid/u,
  );
  assert.equal(fake.calls.fence, 0);
  assert.equal(fake.calls.delete, 0);

  await assert.rejects(
    executeReleaseStorageGcPlan({ plan: value, adapter: null, lease: token, asOf: AS_OF }),
    /explicitly injected deletion adapter/u,
  );
});
