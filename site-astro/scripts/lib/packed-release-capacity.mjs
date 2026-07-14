import { createHash } from "node:crypto";

const GIB = 1024 ** 3;
const SCENARIO_DAYS = 16;
const EVENTS_PER_DAY = 96;
const CADENCE_MINUTES = 15;
const TOTAL_EVENTS = SCENARIO_DAYS * EVENTS_PER_DAY;
const OCCURRENCE_RETENTION_MINUTES = 48 * 60;
const RECEIPT_RETENTION_MINUTES = 14 * 24 * 60;
const RESERVATION_RETENTION_DAYS = 14;
const CONFIRMATION_RETENTION_DAY_BUCKETS = 3;
const PACKS_PER_EVENT = 2;
const CONFIRMATIONS_PER_EVENT = 4;
const PUBLICATION_REQUESTS_PER_EVENT = 128;
const DELETION_CANDIDATES_PER_EVENT = 8;
const REQUESTS_PER_DELETION = 7;
const DAILY_AUDIT_REQUESTS = 8;
const FIXED_CONTROL_OBJECTS = 5;

const BUDGETS = Object.freeze({
  retainedObjects: 25_000,
  retainedBytes: 10 * GIB,
  writtenBytesPerDay: 5 * GIB,
  egressBytesPerDay: 10 * GIB,
  requestsPerDay: 25_000,
  warningFraction: 0.8,
});

const BYTE_MODEL_KEYS = [
  "occurrencePackBytes",
  "sitePackBytes",
  "eventReceiptBytes",
  "attemptReservationBytes",
  "deletionConfirmationBytes",
  "selectedRootBytes",
  "inventoryRootBytes",
  "fenceBytes",
  "statusBytes",
  "metricsBytes",
  "publicationAdditionalWrittenBytes",
  "publicationEgressBytes",
  "deletionAdditionalWrittenBytes",
  "deletionEgressBytes",
  "dailyAuditEgressBytes",
];

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).join(",") === keys.join(",");
}

function safeInteger(value, label, { minimum = 0 } = {}) {
  if (!Number.isSafeInteger(value) || value < minimum) throw new Error(`${label} is invalid`);
  return value;
}

function add(left, right, label) {
  return safeInteger(left + right, label);
}

function multiply(left, right, label) {
  return safeInteger(left * right, label);
}

function sum(values, label) {
  return values.reduce((total, value) => add(total, value, label), 0);
}

function validateByteModel(value) {
  if (!exactKeys(value, BYTE_MODEL_KEYS)) {
    throw new Error("packed release byte model does not use the closed v1 shape");
  }
  for (const key of BYTE_MODEL_KEYS) {
    safeInteger(value[key], `packed release byte model ${key}`, {
      minimum: key.endsWith("AdditionalWrittenBytes") || key.endsWith("EgressBytes") ? 0 : 1,
    });
  }
  return value;
}

function threshold(name, value, limit) {
  const ratio = value / limit;
  return {
    name,
    value,
    limit,
    ratio,
    status: ratio >= 1 ? "block" : ratio >= BUDGETS.warningFraction ? "warn" : "ok",
  };
}

function events() {
  return Array.from({ length: TOTAL_EVENTS }, (_, index) => ({
    index,
    minute: index * CADENCE_MINUTES,
    day: Math.floor(index / EVENTS_PER_DAY),
  }));
}

export function simulatePackedReleaseCapacity(byteModelInput) {
  const byteModel = validateByteModel(byteModelInput);
  const allEvents = events();
  const asOf = allEvents.at(-1);
  const occurrenceEvents = allEvents.filter(({ minute }) => (
    asOf.minute - minute <= OCCURRENCE_RETENTION_MINUTES
  ));
  const receiptEvents = allEvents.filter(({ minute }) => (
    asOf.minute - minute <= RECEIPT_RETENTION_MINUTES
  ));
  const reservationFirstDay = Math.floor(
    (asOf.minute - RESERVATION_RETENTION_DAYS * 24 * 60) / (24 * 60),
  );
  const reservationEvents = allEvents.filter(({ day }) => day >= reservationFirstDay);
  const confirmationFirstDay = asOf.day - CONFIRMATION_RETENTION_DAY_BUCKETS + 1;
  const confirmationEvents = allEvents.filter(({ day }) => day >= confirmationFirstDay);
  const currentDayEvents = allEvents.filter(({ day }) => day === asOf.day);

  const retainedObjects = {
    releasePacks: multiply(occurrenceEvents.length, PACKS_PER_EVENT, "retained release pack count"),
    eventReceipts: receiptEvents.length,
    attemptReservations: reservationEvents.length,
    deletionConfirmations: multiply(
      confirmationEvents.length,
      CONFIRMATIONS_PER_EVENT,
      "retained deletion confirmation count",
    ),
    fixedControlRoots: FIXED_CONTROL_OBJECTS,
    total: 0,
  };
  retainedObjects.total = sum([
    retainedObjects.releasePacks,
    retainedObjects.eventReceipts,
    retainedObjects.attemptReservations,
    retainedObjects.deletionConfirmations,
    retainedObjects.fixedControlRoots,
  ], "retained object count");

  const dailyRequests = {
    publications: currentDayEvents.length,
    publicationRequests: multiply(
      currentDayEvents.length,
      PUBLICATION_REQUESTS_PER_EVENT,
      "daily publication request count",
    ),
    deletionCandidates: multiply(
      currentDayEvents.length,
      DELETION_CANDIDATES_PER_EVENT,
      "daily deletion candidate count",
    ),
    deletionRequests: 0,
    auditRequests: DAILY_AUDIT_REQUESTS,
    total: 0,
  };
  dailyRequests.deletionRequests = multiply(
    dailyRequests.deletionCandidates,
    REQUESTS_PER_DELETION,
    "daily deletion request count",
  );
  dailyRequests.total = sum([
    dailyRequests.publicationRequests,
    dailyRequests.deletionRequests,
    dailyRequests.auditRequests,
  ], "daily request count");

  const packBytes = add(
    byteModel.occurrencePackBytes,
    byteModel.sitePackBytes,
    "paired release pack byte count",
  );
  const fixedControlBytes = sum([
    byteModel.selectedRootBytes,
    byteModel.inventoryRootBytes,
    byteModel.fenceBytes,
    byteModel.statusBytes,
    byteModel.metricsBytes,
  ], "fixed control byte count");
  const retainedBytes = sum([
    multiply(occurrenceEvents.length, packBytes, "retained pack bytes"),
    multiply(receiptEvents.length, byteModel.eventReceiptBytes, "retained event receipt bytes"),
    multiply(reservationEvents.length, byteModel.attemptReservationBytes, "retained reservation bytes"),
    multiply(
      retainedObjects.deletionConfirmations,
      byteModel.deletionConfirmationBytes,
      "retained deletion confirmation bytes",
    ),
    fixedControlBytes,
  ], "retained byte count");
  const publicationWrittenBytes = sum([
    packBytes,
    byteModel.eventReceiptBytes,
    byteModel.attemptReservationBytes,
    byteModel.publicationAdditionalWrittenBytes,
  ], "per-publication written bytes");
  const dailyConfirmationCount = multiply(
    currentDayEvents.length,
    CONFIRMATIONS_PER_EVENT,
    "daily deletion confirmation count",
  );
  const writtenBytesPerDay = sum([
    multiply(currentDayEvents.length, publicationWrittenBytes, "daily publication written bytes"),
    multiply(
      dailyConfirmationCount,
      byteModel.deletionConfirmationBytes,
      "daily confirmation written bytes",
    ),
    multiply(
      dailyRequests.deletionCandidates,
      byteModel.deletionAdditionalWrittenBytes,
      "daily deletion additional written bytes",
    ),
  ], "daily written byte count");
  const egressBytesPerDay = sum([
    multiply(
      currentDayEvents.length,
      byteModel.publicationEgressBytes,
      "daily publication egress bytes",
    ),
    multiply(
      dailyRequests.deletionCandidates,
      byteModel.deletionEgressBytes,
      "daily deletion egress bytes",
    ),
    byteModel.dailyAuditEgressBytes,
  ], "daily egress byte count");

  const thresholds = [
    threshold("retainedObjects", retainedObjects.total, BUDGETS.retainedObjects),
    threshold("retainedBytes", retainedBytes, BUDGETS.retainedBytes),
    threshold("writtenBytesPerDay", writtenBytesPerDay, BUDGETS.writtenBytesPerDay),
    threshold("egressBytesPerDay", egressBytesPerDay, BUDGETS.egressBytesPerDay),
    threshold("requestsPerDay", dailyRequests.total, BUDGETS.requestsPerDay),
  ];
  const blocked = thresholds.filter(({ status }) => status === "block");
  const warnings = thresholds.filter(({ status }) => status === "warn");
  const decision = blocked.length > 0 ? "block" : warnings.length > 0 ? "warn" : "allow";
  const document = {
    contract: "verdify.lab-packed-release-capacity-proof",
    schemaVersion: 1,
    scenario: {
      days: SCENARIO_DAYS,
      cadenceMinutes: CADENCE_MINUTES,
      eventsPerDay: EVENTS_PER_DAY,
      totalEvents: allEvents.length,
      occurrenceRetentionHours: OCCURRENCE_RETENTION_MINUTES / 60,
      receiptRetentionDays: RECEIPT_RETENTION_MINUTES / (24 * 60),
      reservationRetentionDayBuckets: RESERVATION_RETENTION_DAYS + 1,
      confirmationRetentionDayBuckets: CONFIRMATION_RETENTION_DAY_BUCKETS,
    },
    retainedObjects,
    dailyRequests,
    bytes: {
      model: byteModel,
      retained: retainedBytes,
      writtenPerDay: writtenBytesPerDay,
      egressPerDay: egressBytesPerDay,
    },
    budgets: BUDGETS,
    thresholds,
    decision,
    reasons: (blocked.length > 0 ? blocked : warnings).map(({ name, status }) => `${name}-${status}`),
  };
  return Object.freeze({ document, sha256: sha256(canonicalBytes(document)) });
}

export const packedReleaseCapacityContract = Object.freeze({
  days: SCENARIO_DAYS,
  eventsPerDay: EVENTS_PER_DAY,
  totalEvents: TOTAL_EVENTS,
  budgets: BUDGETS,
  expected: Object.freeze({
    retainedObjects: 4_328,
    requestsPerDay: 17_672,
  }),
  byteModelKeys: Object.freeze([...BYTE_MODEL_KEYS]),
});
