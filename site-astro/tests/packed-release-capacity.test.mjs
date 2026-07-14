import assert from "node:assert/strict";
import test from "node:test";

import {
  packedReleaseCapacityContract,
  simulatePackedReleaseCapacity,
} from "../scripts/lib/packed-release-capacity.mjs";

const MIB = 1024 ** 2;
const GIB = 1024 ** 3;

function byteModel(overrides = {}) {
  return {
    occurrencePackBytes: 1 * MIB,
    sitePackBytes: 2 * MIB,
    eventReceiptBytes: 1024,
    attemptReservationBytes: 2048,
    deletionConfirmationBytes: 1024,
    selectedRootBytes: 4096,
    inventoryRootBytes: 8192,
    fenceBytes: 2048,
    statusBytes: 4096,
    metricsBytes: 2048,
    publicationAdditionalWrittenBytes: 4096,
    publicationEgressBytes: 4 * MIB,
    deletionAdditionalWrittenBytes: 1024,
    deletionEgressBytes: 2048,
    dailyAuditEgressBytes: 8 * MIB,
    ...overrides,
  };
}

test("the exact 16-day 1,536-event simulation proves 4,328 objects and 17,672 daily requests", () => {
  const proof = simulatePackedReleaseCapacity(byteModel());
  assert.equal(proof.document.scenario.days, 16);
  assert.equal(proof.document.scenario.eventsPerDay, 96);
  assert.equal(proof.document.scenario.totalEvents, 1_536);
  assert.deepEqual(proof.document.retainedObjects, {
    releasePacks: 386,
    eventReceipts: 1_345,
    attemptReservations: 1_440,
    deletionConfirmations: 1_152,
    fixedControlRoots: 5,
    total: 4_328,
  });
  assert.deepEqual(proof.document.dailyRequests, {
    publications: 96,
    publicationRequests: 12_288,
    deletionCandidates: 768,
    deletionRequests: 5_376,
    auditRequests: 8,
    total: 17_672,
  });
  assert.equal(proof.document.retainedObjects.total, packedReleaseCapacityContract.expected.retainedObjects);
  assert.equal(proof.document.dailyRequests.total, packedReleaseCapacityContract.expected.requestsPerDay);
  assert.equal(proof.document.decision, "allow");
  assert.equal(proof.document.thresholds.every(({ status }) => status === "ok"), true);
  assert.match(proof.sha256, /^[0-9a-f]{64}$/);
});

test("actual byte parameters drive allow, warning, and hard block gates", () => {
  const allowed = simulatePackedReleaseCapacity(byteModel());
  assert.equal(allowed.document.bytes.model.occurrencePackBytes, 1 * MIB);
  assert.equal(allowed.document.bytes.retained < 10 * GIB, true);
  assert.equal(allowed.document.bytes.writtenPerDay < 5 * GIB, true);
  assert.equal(allowed.document.bytes.egressPerDay < 10 * GIB, true);
  assert.equal(allowed.document.decision, "allow");

  const warning = simulatePackedReleaseCapacity(byteModel({
    occurrencePackBytes: 44 * MIB,
    sitePackBytes: 1 * MIB,
  }));
  assert.equal(warning.document.decision, "warn");
  assert.equal(
    warning.document.thresholds.some(({ name, status }) => name === "retainedBytes" && status === "warn"),
    true,
  );
  assert.equal(
    warning.document.thresholds.some(({ name, status }) => name === "writtenBytesPerDay" && status === "warn"),
    true,
  );

  const blocked = simulatePackedReleaseCapacity(byteModel({
    occurrencePackBytes: 53 * MIB,
    sitePackBytes: 1 * MIB,
  }));
  assert.equal(blocked.document.decision, "block");
  assert.equal(
    blocked.document.thresholds.some(({ name, status }) => name === "retainedBytes" && status === "block"),
    true,
  );
  assert.equal(
    blocked.document.thresholds.some(({ name, status }) => name === "writtenBytesPerDay" && status === "block"),
    true,
  );

  const egressBlocked = simulatePackedReleaseCapacity(byteModel({ dailyAuditEgressBytes: 10 * GIB }));
  assert.equal(egressBlocked.document.decision, "block");
  assert.equal(
    egressBlocked.document.thresholds.find(({ name }) => name === "egressBytesPerDay").status,
    "block",
  );
});

test("byte modeling fails closed on missing, reordered, negative, or overflowing parameters", () => {
  const missing = byteModel();
  delete missing.metricsBytes;
  assert.throws(() => simulatePackedReleaseCapacity(missing), /closed v1 shape/);

  const reordered = { sitePackBytes: 2 * MIB, occurrencePackBytes: 1 * MIB };
  for (const [key, value] of Object.entries(byteModel())) {
    if (!(key in reordered)) reordered[key] = value;
  }
  assert.throws(() => simulatePackedReleaseCapacity(reordered), /closed v1 shape/);
  assert.throws(
    () => simulatePackedReleaseCapacity(byteModel({ eventReceiptBytes: -1 })),
    /eventReceiptBytes is invalid/,
  );
  assert.throws(
    () => simulatePackedReleaseCapacity(byteModel({ occurrencePackBytes: Number.MAX_SAFE_INTEGER })),
    /byte count|bytes is invalid/,
  );
});
