import { createHash, randomUUID } from "node:crypto";

import {
  createReleaseStorageUsageState,
  executeReleaseStorageGcPlan,
  planReleaseStorageSafety,
  recordReleaseStorageUsage,
  releaseStorageSafetyContract,
} from "./release-storage-safety.mjs";
import { S3OccurrenceReleaseStore } from "./occurrence-release-store.mjs";
import {
  captureReleaseStorageS3Inventory,
  listReleaseStorageS3Inventory,
  planReleaseStorageS3InventoryReads,
  releaseStorageS3InventoryContract,
} from "./release-storage-s3-inventory.mjs";
import { S3ObjectStore } from "./s3-object-store.mjs";
import { S3SiteReleaseStore } from "./site-release-store.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const SAFE_ID_RE = /^[A-Za-z0-9_-]{8,128}$/u;
const MAX_COORDINATION_OBJECTS = 25_000;
const MAX_RESERVATION_BYTES = 2 * 1024;
const MAX_FENCE_BYTES = 32 * 1024;
const MAX_STATUS_BYTES = 64 * 1024;
const MAX_METRICS_BYTES = 32 * 1024;
const MAX_CAS_ATTEMPTS = 8;
const MAX_LEASE_SECONDS = 15 * 60;
const MIN_LEASE_SECONDS = 60;
const IDEMPOTENCY_HORIZON_MS = 14 * 24 * 60 * 60 * 1000;
const GC_CONFIRMATION_HORIZON_MS = 48 * 60 * 60 * 1000;
const ADAPTER_USAGE_LIMITS = releaseStorageSafetyContract.adapterOperation.usageLimits;
const RESERVATION_KEY_RE = /^usage\/(\d{4}-\d{2}-\d{2})\/reservations\/(inventory-list|inventory-read|gc-preflight|gc-mutation|publication)\/([0-9a-f]{64})\/((?:0|[1-9]\d*))-((?:0|[1-9]\d*))-((?:0|[1-9]\d*))-((?:0|[1-9]\d*))-([0-9a-f]{64})\.json$/u;
const CONFIRMATION_KEY_RE = /^gc\/confirmations\/(\d{4}-\d{2}-\d{2})\/([0-9a-f]{64})\/(site|occurrence)\/([0-9a-f]{64})\.json$/u;

// One event finalization performs at most eight bounded canonical reads/writes.
// This 2x envelope leaves room for four conditional retries while permitting
// hundreds of daily events under the tracker defaults. It replaces the prior
// unusable 512 MiB / 1,000-request reservation.
export const COORDINATION_FINALIZATION_USAGE = Object.freeze({
  writtenBytes: 256 * 1024,
  deletedBytes: 0,
  egressBytes: 256 * 1024,
  requests: 32,
});

const GC_PREFLIGHT_USAGE = Object.freeze({ ...ADAPTER_USAGE_LIMITS });
const GC_MUTATION_USAGE = Object.freeze({ ...ADAPTER_USAGE_LIMITS });
const RESERVATION_JOURNAL_USAGE = Object.freeze({
  writtenBytes: MAX_RESERVATION_BYTES,
  deletedBytes: 0,
  egressBytes: MAX_RESERVATION_BYTES,
  requests: 3,
});
const RESERVATION_KINDS = new Set([
  "inventory-list",
  "inventory-read",
  "gc-preflight",
  "gc-mutation",
  "publication",
]);

export class ReleaseStorageInventoryBudgetError extends Error {
  constructor(result) {
    super(`release storage inventory ${result.phase} blocked by its daily budget`);
    this.name = "ReleaseStorageInventoryBudgetError";
    this.result = result;
  }
}

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

function safeInteger(value, label, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < 0 || value > maximum) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function add(left, right, label) {
  return safeInteger(left + right, label);
}

function instant(value, label) {
  const parsed = Date.parse(value);
  if (
    typeof value !== "string"
    || !Number.isFinite(parsed)
    || new Date(parsed).toISOString() !== value
  ) throw new Error(`${label} is invalid`);
  return value;
}

function digest(value, label) {
  if (typeof value !== "string" || !SHA256_RE.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function canonicalDocument(bytes, label) {
  let document;
  try {
    document = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error(`${label} is not valid JSON`);
  }
  if (!canonicalBytes(document).equals(bytes)) {
    throw new Error(`${label} is not canonical JSON`);
  }
  return document;
}

function usage(value, label) {
  if (!exactKeys(value, ["writtenBytes", "deletedBytes", "egressBytes", "requests"])) {
    throw new Error(`${label} does not use the closed usage shape`);
  }
  for (const [key, amount] of Object.entries(value)) {
    safeInteger(amount, `${label} ${key}`);
  }
  return value;
}

function addUsage(left, right, label) {
  return {
    writtenBytes: add(left.writtenBytes, right.writtenBytes, `${label} written bytes`),
    deletedBytes: add(left.deletedBytes, right.deletedBytes, `${label} deleted bytes`),
    egressBytes: add(left.egressBytes, right.egressBytes, `${label} egress bytes`),
    requests: add(left.requests, right.requests, `${label} requests`),
  };
}

function inventoryReservationUsage(operation, label) {
  return Object.freeze(addUsage(operation, RESERVATION_JOURNAL_USAGE, label));
}

function inventoryBudgetResult(snapshot, delta, phase, observedAt) {
  const counters = snapshot.state.counters;
  const projected = {
    writtenBytes: add(counters.writtenBytes, delta.writtenBytes, "inventory projected written bytes"),
    deletedBytes: add(counters.deletedBytes, delta.deletedBytes, "inventory projected deleted bytes"),
    egressBytes: add(counters.egressBytes, delta.egressBytes, "inventory projected egress bytes"),
    requests: add(counters.requests, delta.requests, "inventory projected requests"),
    coordinationObjects: add(
      snapshot.inventoryEntries.length,
      1,
      "inventory projected coordination object count",
    ),
  };
  const limits = releaseStorageSafetyContract.budgets;
  const reasons = [];
  if (projected.writtenBytes >= limits.writtenBytesPerDay) reasons.push("writtenBytesPerDay-budget");
  if (projected.egressBytes >= limits.egressBytesPerDay) reasons.push("egressBytesPerDay-budget");
  if (projected.requests >= limits.requestsPerDay) reasons.push("requestsPerDay-budget");
  if (projected.coordinationObjects > MAX_COORDINATION_OBJECTS) {
    reasons.push("coordinationObjects-capacity");
  }
  return Object.freeze({
    contract: "verdify.lab-release-storage-inventory-budget-result",
    schemaVersion: 1,
    status: reasons.length === 0 ? "allow" : "blocked",
    phase,
    observedAt,
    day: observedAt.slice(0, 10),
    reasons: Object.freeze(reasons),
    projected: Object.freeze(projected),
    limits: Object.freeze({
      writtenBytes: limits.writtenBytesPerDay,
      egressBytes: limits.egressBytesPerDay,
      requests: limits.requestsPerDay,
      coordinationObjects: MAX_COORDINATION_OBJECTS,
    }),
  });
}

function assertInventoryBudget(snapshot, delta, phase, observedAt) {
  const result = inventoryBudgetResult(snapshot, delta, phase, observedAt);
  if (result.status === "blocked") throw new ReleaseStorageInventoryBudgetError(result);
  return result;
}

function applyReservationToUsageSnapshot(snapshot, reservation, observedAt) {
  if (snapshot.inventoryEntries.some(({ key }) => key === reservation.key)) return snapshot;
  return {
    ...snapshot,
    state: recordReleaseStorageUsage(snapshot.state, reservation.delta, observedAt),
    retainedBytes: add(
      snapshot.retainedBytes,
      reservation.retainedBytes,
      "release storage coordination retained reservation bytes",
    ),
    reservationCount: add(
      snapshot.reservationCount,
      1,
      "release storage coordination reservation count",
    ),
    inventoryEntries: [...snapshot.inventoryEntries, {
      key: reservation.key,
      bytes: reservation.retainedBytes,
      lastModified: observedAt,
      etag: reservation.etag,
    }],
  };
}

function assertReleaseStorageWriterTopology(siteStore, occurrenceStore, coordinationStore) {
  const stores = [siteStore?.objects, occurrenceStore?.objects, coordinationStore];
  if (
    !(siteStore instanceof S3SiteReleaseStore)
    || !(occurrenceStore instanceof S3OccurrenceReleaseStore)
    || stores.some((store) => (
      !(store instanceof S3ObjectStore)
      || store.accessMode !== "writer"
      || store.client === null
      || typeof store.client?.send !== "function"
    ))
  ) throw new Error("release storage coordinator requires three initialized S3 writer stores");
  const buckets = new Set(stores.map(({ bucket }) => bucket));
  const prefixes = stores.map(({ prefix }) => prefix);
  if (
    buckets.size !== 1
    || new Set(prefixes).size !== prefixes.length
    || prefixes.some((prefix, index) => prefixes.some(
      (other, otherIndex) => index !== otherIndex
        && (prefix.startsWith(`${other}/`) || other.startsWith(`${prefix}/`)),
    ))
  ) throw new Error("release storage coordinator requires three dedicated non-overlapping prefixes");
}

function multiplyUsage(value, count, label) {
  safeInteger(count, `${label} operation count`);
  return {
    writtenBytes: safeInteger(value.writtenBytes * count, `${label} written bytes`),
    deletedBytes: safeInteger(value.deletedBytes * count, `${label} deleted bytes`),
    egressBytes: safeInteger(value.egressBytes * count, `${label} egress bytes`),
    requests: safeInteger(value.requests * count, `${label} requests`),
  };
}

function gcPreflightReservation(plan) {
  const deletionCount = plan.document.deletions.length;
  const operationCount = deletionCount === 0
    ? 1
    : 1 + plan.document.selectors.length + (2 * deletionCount);
  return multiplyUsage(GC_PREFLIGHT_USAGE, operationCount, "release storage GC preflight reservation");
}

function reservationPayload({ kind, operationSha256, createdAt, delta }) {
  if (!RESERVATION_KINDS.has(kind)) throw new Error("release storage reservation kind is invalid");
  digest(operationSha256, "release storage reservation operation digest");
  instant(createdAt, "release storage reservation time");
  usage(delta, "release storage reservation delta");
  return {
    contract: "verdify.lab-release-storage-usage-reservation",
    schemaVersion: 1,
    kind,
    day: createdAt.slice(0, 10),
    operationSha256,
    createdAt,
    delta,
  };
}

function reservationDocument(input) {
  const payload = reservationPayload(input);
  const reservationId = sha256(canonicalBytes({
    contract: "verdify.lab-release-storage-usage-reservation-identity",
    schemaVersion: 1,
    kind: payload.kind,
    day: payload.day,
    operationSha256: payload.operationSha256,
    delta: payload.delta,
  }));
  return {
    document: { ...payload, reservationId },
    reservationId,
    key: `usage/${payload.day}/reservations/${payload.kind}/${payload.operationSha256}/${payload.delta.writtenBytes}-${payload.delta.deletedBytes}-${payload.delta.egressBytes}-${payload.delta.requests}-${reservationId}.json`,
  };
}

function parseReservationKey(key) {
  const match = RESERVATION_KEY_RE.exec(key);
  if (match === null) return null;
  const [, day, kind, operationSha256, written, deleted, egress, requests, reservationId] = match;
  const delta = {
    writtenBytes: safeInteger(Number(written), "release storage reservation-key written bytes"),
    deletedBytes: safeInteger(Number(deleted), "release storage reservation-key deleted bytes"),
    egressBytes: safeInteger(Number(egress), "release storage reservation-key egress bytes"),
    requests: safeInteger(Number(requests), "release storage reservation-key requests"),
  };
  const expected = sha256(canonicalBytes({
    contract: "verdify.lab-release-storage-usage-reservation-identity",
    schemaVersion: 1,
    kind,
    day,
    operationSha256,
    delta,
  }));
  if (reservationId !== expected) {
    throw new Error("release storage reservation key identity is invalid");
  }
  return { day, kind, operationSha256, delta, reservationId };
}

function validateReservation(document, key, asOf) {
  if (!exactKeys(document, [
    "contract",
    "schemaVersion",
    "kind",
    "day",
    "operationSha256",
    "createdAt",
    "delta",
    "reservationId",
  ])) throw new Error("release storage usage reservation does not use the closed v1 shape");
  const { reservationId, ...payload } = document;
  reservationPayload(payload);
  const expectedReservationId = sha256(canonicalBytes({
    contract: "verdify.lab-release-storage-usage-reservation-identity",
    schemaVersion: 1,
    kind: document.kind,
    day: document.day,
    operationSha256: document.operationSha256,
    delta: document.delta,
  }));
  if (
    document.contract !== "verdify.lab-release-storage-usage-reservation"
    || document.schemaVersion !== 1
    || document.day !== document.createdAt.slice(0, 10)
    || reservationId !== expectedReservationId
    || key !== reservationDocument(document).key
    || Date.parse(document.createdAt) > Date.parse(asOf)
  ) throw new Error("release storage usage reservation identity is invalid");
  return document;
}

async function readCanonical(store, key, maximumBytes, label, { missing = false } = {}) {
  const value = await store.read(key, { maximumBytes, label, missing });
  if (value === null) return null;
  return { ...value, document: canonicalDocument(value.bytes, label) };
}

async function putCanonicalAbsent(store, key, document, maximumBytes, label) {
  const bytes = canonicalBytes(document);
  if (bytes.length < 1 || bytes.length > maximumBytes) {
    throw new Error(`${label} exceeds its byte limit`);
  }
  let result;
  try {
    result = await store.putIfAbsent(key, bytes, { contentType: "application/json" });
  } catch (error) {
    const recovered = await readCanonical(store, key, maximumBytes, label, { missing: true }).catch(() => null);
    if (recovered !== null && recovered.bytes.equals(bytes)) return recovered;
    throw error;
  }
  if (!result.written) {
    const existing = await readCanonical(store, key, maximumBytes, label);
    if (!existing.bytes.equals(bytes)) throw new Error(`${label} collides with different bytes`);
    return existing;
  }
  const committed = await readCanonical(store, key, maximumBytes, label);
  if (!committed.bytes.equals(bytes)) throw new Error(`${label} changed after absent-only write`);
  return committed;
}

export async function reserveReleaseStorageS3Usage({
  coordinationStore,
  kind,
  operationSha256,
  createdAt,
  delta,
}) {
  if (!(coordinationStore instanceof S3ObjectStore) || coordinationStore.accessMode !== "writer") {
    throw new Error("release storage usage reservation requires a coordination writer");
  }
  const selected = reservationDocument({ kind, operationSha256, createdAt, delta });
  const existing = await readCanonical(
    coordinationStore,
    selected.key,
    MAX_RESERVATION_BYTES,
    "release storage usage reservation",
    { missing: true },
  );
  if (existing !== null) {
    validateReservation(existing.document, selected.key, createdAt);
    return Object.freeze({
      reservationId: selected.reservationId,
      key: selected.key,
      delta: structuredClone(delta),
      created: false,
      retainedBytes: existing.bytes.length,
      etag: existing.etag,
    });
  }
  const bytes = canonicalBytes(selected.document);
  if (bytes.length > MAX_RESERVATION_BYTES) {
    throw new Error("release storage usage reservation exceeds its byte limit");
  }
  let written = false;
  try {
    written = (await coordinationStore.putIfAbsent(selected.key, bytes, {
      contentType: "application/json",
    })).written;
  } catch (error) {
    const recovered = await readCanonical(
      coordinationStore,
      selected.key,
      MAX_RESERVATION_BYTES,
      "release storage usage reservation",
      { missing: true },
    ).catch(() => null);
    if (recovered === null) throw error;
  }
  const committed = await readCanonical(
    coordinationStore,
    selected.key,
    MAX_RESERVATION_BYTES,
    "release storage usage reservation",
  );
  validateReservation(committed.document, selected.key, createdAt);
  if (written && !committed.bytes.equals(bytes)) {
    throw new Error("release storage usage reservation changed after absent-only write");
  }
  return Object.freeze({
    reservationId: selected.reservationId,
    key: selected.key,
    delta: structuredClone(delta),
    // This call observed the key absent before a canonical committed value
    // appeared. Count it once in the caller's already-loaded usage snapshot,
    // including response-loss recovery and a same-identity racing writer.
    created: true,
    retainedBytes: committed.bytes.length,
    etag: committed.etag,
  });
}

async function coordinationInventory(store) {
  const entries = await store.listInventory("", { maximumObjects: MAX_COORDINATION_OBJECTS });
  for (const entry of entries) {
    if (
      entry.key === "fence.json"
      || entry.key === "status.json"
      || entry.key === "metrics/latest.prom"
      || parseReservationKey(entry.key) !== null
      || CONFIRMATION_KEY_RE.test(entry.key)
    ) continue;
    throw new Error("release storage coordination inventory contains bytes outside the closed root layout");
  }
  return entries;
}

export async function loadReleaseStorageS3Usage({ coordinationStore, asOf }) {
  if (!(coordinationStore instanceof S3ObjectStore)) {
    throw new Error("release storage usage requires an S3 coordination store");
  }
  instant(asOf, "release storage usage observation time");
  const entries = await coordinationInventory(coordinationStore);
  const dayPrefix = `usage/${asOf.slice(0, 10)}/reservations/`;
  let state = createReleaseStorageUsageState(asOf);
  for (const entry of entries.filter(({ key }) => key.startsWith(dayPrefix))) {
    const reservation = parseReservationKey(entry.key);
    if (reservation === null || reservation.day !== asOf.slice(0, 10)) {
      throw new Error("release storage usage reservation listing identity is invalid");
    }
    state = recordReleaseStorageUsage(state, reservation.delta, asOf);
  }
  const retainedBytes = entries.reduce(
    (total, entry) => add(total, entry.bytes, "release storage coordination retained bytes"),
    0,
  );
  return {
    state,
    retainedBytes,
    reservationCount: entries.filter(({ key }) => key.startsWith(dayPrefix)).length,
    inventoryEntries: entries,
  };
}

function wholeDayExpired(day, asOf, horizonMs) {
  const endOfDay = Date.parse(`${day}T23:59:59.999Z`);
  if (!Number.isFinite(endOfDay)) throw new Error("release storage coordination history day is invalid");
  return endOfDay < Date.parse(asOf) - horizonMs;
}

function planCoordinationHistory(entries, asOf) {
  const candidates = [];
  for (const entry of entries) {
    const reservation = parseReservationKey(entry.key);
    const confirmation = CONFIRMATION_KEY_RE.exec(entry.key);
    if (
      (reservation !== null && wholeDayExpired(reservation.day, asOf, IDEMPOTENCY_HORIZON_MS))
      || (confirmation !== null && wholeDayExpired(confirmation[1], asOf, GC_CONFIRMATION_HORIZON_MS))
    ) candidates.push(entry);
  }
  candidates.sort((left, right) => left.key.localeCompare(right.key));
  const deletedBytes = candidates.reduce(
    (total, entry) => add(total, entry.bytes, "release storage coordination history deleted bytes"),
    0,
  );
  const listPages = Math.max(1, Math.ceil(entries.length / 1000));
  // The safety plan's first non-mutation operation already reserves sixteen
  // requests and 1 MiB egress for the fence plus up to fifteen list pages.
  const extraObservationOperations = Math.max(
    0,
    Math.ceil((listPages + 1) / ADAPTER_USAGE_LIMITS.requests) - 1,
  );
  return Object.freeze({
    candidates: Object.freeze(candidates.map((entry) => Object.freeze({ ...entry }))),
    deletedBytes,
    usageUpperBound: Object.freeze({
      ...multiplyUsage(
        GC_MUTATION_USAGE,
        candidates.length,
        "release storage coordination history GC",
      ),
      deletedBytes,
    }),
    observationExtraUsage: Object.freeze(multiplyUsage(
      GC_PREFLIGHT_USAGE,
      extraObservationOperations,
      "release storage coordination history observation",
    )),
  });
}

function validateFence(document) {
  if (!exactKeys(document, [
    "contract",
    "schemaVersion",
    "fencingToken",
    "leaseId",
    "ownerSha256",
    "planSha256",
    "issuedAt",
    "expiresAt",
    "releasedAt",
  ])) throw new Error("release storage coordination fence does not use the closed v1 shape");
  if (
    document.contract !== "verdify.lab-release-storage-coordination-fence"
    || document.schemaVersion !== 1
    || !Number.isSafeInteger(document.fencingToken)
    || document.fencingToken < 1
    || typeof document.leaseId !== "string"
    || !/^lease_[0-9a-f]{32}$/u.test(document.leaseId)
  ) throw new Error("release storage coordination fence is invalid");
  digest(document.ownerSha256, "release storage coordination owner digest");
  digest(document.planSha256, "release storage coordination plan digest");
  instant(document.issuedAt, "release storage coordination fence issue time");
  instant(document.expiresAt, "release storage coordination fence expiry time");
  if (document.releasedAt !== null) instant(document.releasedAt, "release storage coordination release time");
  if (
    Date.parse(document.issuedAt) >= Date.parse(document.expiresAt)
    || (document.releasedAt !== null
      && (Date.parse(document.releasedAt) < Date.parse(document.issuedAt)
        || Date.parse(document.releasedAt) > Date.parse(document.expiresAt)))
  ) throw new Error("release storage coordination fence interval is invalid");
  return document;
}

async function readFence(store, { missing = true } = {}) {
  const value = await readCanonical(
    store,
    "fence.json",
    MAX_FENCE_BYTES,
    "release storage coordination fence",
    { missing },
  );
  if (value === null) return null;
  validateFence(value.document);
  return value;
}

function safetyLease(fence) {
  return {
    contract: "verdify.lab-release-storage-gc-lease",
    schemaVersion: 1,
    leaseId: fence.leaseId,
    fencingToken: fence.fencingToken,
    planSha256: fence.planSha256,
    issuedAt: fence.issuedAt,
    expiresAt: fence.expiresAt,
  };
}

async function writeFence(store, prior, document) {
  const bytes = canonicalBytes(document);
  const result = prior === null
    ? await store.putIfAbsent("fence.json", bytes, { contentType: "application/json" })
    : await store.putIfMatch("fence.json", bytes, prior.etag, { contentType: "application/json" });
  if (!result.written) return null;
  const committed = await readFence(store, { missing: false });
  if (!committed.bytes.equals(bytes)) throw new Error("release storage coordination fence changed after CAS");
  return committed;
}

export async function acquireReleaseStorageS3Lease({
  coordinationStore,
  planSha256,
  ownerIdentity,
  issuedAt,
  leaseSeconds = MAX_LEASE_SECONDS,
  nonce = randomUUID(),
}) {
  if (!(coordinationStore instanceof S3ObjectStore) || coordinationStore.accessMode !== "writer") {
    throw new Error("release storage lease requires a coordination writer");
  }
  digest(planSha256, "release storage lease plan digest");
  if (typeof ownerIdentity !== "string" || !SAFE_ID_RE.test(ownerIdentity)) {
    throw new Error("release storage lease owner identity is invalid");
  }
  if (typeof nonce !== "string" || !SAFE_ID_RE.test(nonce)) {
    throw new Error("release storage lease nonce is invalid");
  }
  instant(issuedAt, "release storage lease issue time");
  if (!Number.isSafeInteger(leaseSeconds) || leaseSeconds < MIN_LEASE_SECONDS || leaseSeconds > MAX_LEASE_SECONDS) {
    throw new Error("release storage lease duration is invalid");
  }
  const ownerSha256 = sha256(Buffer.from(ownerIdentity));
  const leaseId = `lease_${sha256(Buffer.from(`${ownerSha256}\u0000${planSha256}\u0000${nonce}`)).slice(0, 32)}`;
  const expiresAt = new Date(Date.parse(issuedAt) + (leaseSeconds * 1000)).toISOString();
  for (let attempt = 0; attempt < MAX_CAS_ATTEMPTS; attempt += 1) {
    const prior = await readFence(coordinationStore);
    if (prior !== null) {
      const active = prior.document.releasedAt === null
        && Date.parse(issuedAt) < Date.parse(prior.document.expiresAt);
      if (active) {
        if (
          prior.document.leaseId === leaseId
          && prior.document.ownerSha256 === ownerSha256
          && prior.document.planSha256 === planSha256
          && prior.document.issuedAt === issuedAt
        ) return { record: prior.document, etag: prior.etag, lease: safetyLease(prior.document) };
        throw new Error("another release storage publication lease is active");
      }
    }
    const document = {
      contract: "verdify.lab-release-storage-coordination-fence",
      schemaVersion: 1,
      fencingToken: (prior?.document.fencingToken ?? 0) + 1,
      leaseId,
      ownerSha256,
      planSha256,
      issuedAt,
      expiresAt,
      releasedAt: null,
    };
    validateFence(document);
    let committed;
    try {
      committed = await writeFence(coordinationStore, prior, document);
    } catch (error) {
      const recovered = await readFence(coordinationStore).catch(() => null);
      if (recovered?.bytes.equals(canonicalBytes(document))) committed = recovered;
      else throw error;
    }
    if (committed !== null) {
      return { record: committed.document, etag: committed.etag, lease: safetyLease(committed.document) };
    }
  }
  throw new Error("release storage lease CAS did not converge");
}

export async function releaseReleaseStorageS3Lease({ coordinationStore, acquired, releasedAt }) {
  instant(releasedAt, "release storage lease release time");
  const current = await readFence(coordinationStore, { missing: false });
  if (
    current.document.leaseId !== acquired.record.leaseId
    || current.document.fencingToken !== acquired.record.fencingToken
    || current.document.planSha256 !== acquired.record.planSha256
    || current.document.releasedAt !== null
    || Date.parse(releasedAt) >= Date.parse(current.document.expiresAt)
  ) throw new Error("release storage lease was lost before release");
  const document = { ...current.document, releasedAt };
  validateFence(document);
  const committed = await writeFence(coordinationStore, current, document);
  if (committed === null) throw new Error("release storage lease release CAS failed");
  return committed.document;
}

function adapterResult(value, delta, status = "ok", errorCode = null) {
  return {
    contract: "verdify.lab-release-storage-adapter-operation",
    schemaVersion: 1,
    status,
    value: status === "ok" ? value : null,
    errorCode: status === "ok" ? null : errorCode,
    usage: delta,
  };
}

function readDelta(bytes = 0, requests = 1) {
  return { writtenBytes: 0, deletedBytes: 0, egressBytes: bytes, requests };
}

function physicalKey(namespace, key, kind = null) {
  if (namespace === "occurrence" && (kind === "blob" || /^blobs\/sha256\/[0-9a-f]{64}$/u.test(key))) {
    return `${key}.png`;
  }
  return key;
}

function keyDigest(key, kind) {
  const match = kind === "generation"
    ? /\/sha256\/([0-9a-f]{64})\.json$/u.exec(key)
    : kind === "blob"
      ? /\/sha256\/([0-9a-f]{64})$/u.exec(key)
      : /\/sha256\/([0-9a-f]{64})\.json$/u.exec(key);
  if (match === null) throw new Error("release storage immutable key digest is invalid");
  return match[1];
}

function selectStore(namespace, siteStore, occurrenceStore) {
  if (namespace === "site") return siteStore.objects;
  if (namespace === "occurrence") return occurrenceStore.objects;
  throw new Error("release storage adapter namespace is invalid");
}

function confirmationKey(day, planSha256, namespace, key) {
  return `gc/confirmations/${day}/${planSha256}/${namespace}/${sha256(Buffer.from(key))}.json`;
}

function currentLeaseMatches(document, expected) {
  return document.releasedAt === null
    && document.leaseId === expected.leaseId
    && document.fencingToken === expected.fencingToken
    && document.planSha256 === expected.planSha256
    && document.issuedAt === expected.issuedAt
    && document.expiresAt === expected.expiresAt;
}

async function assertCurrentPublicationLease(coordinationStore, acquired, currentTime) {
  const current = await readFence(coordinationStore, { missing: false });
  if (
    !currentLeaseMatches(current.document, acquired.record)
    || Date.parse(current.document.issuedAt) > Date.parse(currentTime)
    || Date.parse(currentTime) >= Date.parse(current.document.expiresAt)
  ) throw new Error("release storage publication lease is no longer current");
  return current;
}

async function compactCoordinationHistory({
  coordinationStore,
  acquired,
  history,
  plannedAt,
  clock,
}) {
  let deletedBytes = 0;
  for (const entry of history.candidates) {
    const mutationAt = await currentForBudgetDay(
      clock,
      plannedAt,
      "release storage coordination history GC",
    );
    await reserveReleaseStorageS3Usage({
      coordinationStore,
      kind: "gc-mutation",
      operationSha256: sha256(canonicalBytes({
        contract: "verdify.lab-release-storage-coordination-history-gc-identity",
        schemaVersion: 1,
        keySha256: sha256(Buffer.from(entry.key)),
        etagSha256: sha256(Buffer.from(entry.etag)),
        bytes: entry.bytes,
      })),
      createdAt: mutationAt,
      delta: { ...GC_MUTATION_USAGE, deletedBytes: entry.bytes },
    });
    await assertCurrentPublicationLease(coordinationStore, acquired, mutationAt);
    const current = await coordinationStore.head(entry.key, {
      label: "release storage coordination history object",
    });
    if (
      current.etag !== entry.etag
      || current.bytes !== entry.bytes
      || current.lastModified !== entry.lastModified
    ) throw new Error("release storage coordination history changed before GC");
    if (!(await coordinationStore.deleteIfMatch(entry.key, current.etag)).deleted) {
      throw new Error("release storage coordination history CAS deletion failed");
    }
    if (await coordinationStore.head(entry.key, {
      missing: true,
      label: "release storage coordination history object",
    }) !== null) throw new Error("release storage coordination history deletion is unconfirmed");
    deletedBytes = add(deletedBytes, entry.bytes, "release storage coordination history deleted bytes");
  }
  if (deletedBytes !== history.deletedBytes) {
    throw new Error("release storage coordination history GC did not match its plan");
  }
  return Object.freeze({ deletedObjects: history.candidates.length, deletedBytes });
}

function createS3GcAdapter({
  siteStore,
  occurrenceStore,
  coordinationStore,
  lease,
  planSha256,
  plannedAt,
  mutationInstant,
}) {
  const etags = new Map();
  return {
    contract: "verdify.lab-release-storage-gc-delete-adapter",
    schemaVersion: 1,
    async readFence() {
      const value = await readFence(coordinationStore, { missing: false });
      const projected = currentLeaseMatches(value.document, lease)
        ? safetyLease(value.document)
        : null;
      return adapterResult(projected, readDelta(value.bytes.length));
    },
    async readSelector(request) {
      const store = selectStore(request.namespace, siteStore, occurrenceStore);
      const value = await store.read(request.key, {
        maximumBytes: 64 * 1024,
        label: "release storage selector",
      });
      return adapterResult(
        { sha256: sha256(value.bytes), etag: value.etag },
        readDelta(value.bytes.length),
      );
    },
    async statObject(request) {
      const store = selectStore(request.namespace, siteStore, occurrenceStore);
      const key = physicalKey(request.namespace, request.key);
      const value = await store.head(key, { label: "release storage immutable object" });
      etags.set(`${request.namespace}\u0000${request.key}`, value.etag);
      const kind = /^blobs\//u.test(request.key)
        ? "blob"
        : /generations\//u.test(request.key)
          ? "generation"
          : /^releases\//u.test(request.key)
            ? "release"
            : /^checkpoints\//u.test(request.key)
              ? "checkpoint"
              : "manifest";
      return adapterResult({
        sha256: keyDigest(request.key, kind),
        bytes: value.bytes,
        createdAt: value.lastModified,
      }, readDelta(1024));
    },
    async readDeletionConfirmation(request) {
      const key = confirmationKey(plannedAt.slice(0, 10), planSha256, request.namespace, request.key);
      const value = await readCanonical(
        coordinationStore,
        key,
        MAX_RESERVATION_BYTES,
        "release storage deletion confirmation",
        { missing: true },
      );
      return adapterResult(
        {
          confirmed: value !== null && sha256(value.bytes) === request.confirmationSha256,
          confirmationSha256: request.confirmationSha256,
        },
        readDelta(value?.bytes.length ?? 0),
      );
    },
    async deleteObject(request) {
      const mutationAt = await mutationInstant();
      const operationSha256 = sha256(canonicalBytes({
        contract: "verdify.lab-release-storage-gc-mutation-identity",
        schemaVersion: 1,
        planSha256,
        namespace: request.namespace,
        keySha256: sha256(Buffer.from(request.key)),
      }));
      await reserveReleaseStorageS3Usage({
        coordinationStore,
        kind: "gc-mutation",
        operationSha256,
        createdAt: mutationAt,
        delta: { ...GC_MUTATION_USAGE, deletedBytes: request.expected.bytes },
      });
      // Reservation I/O is itself bounded but can consume the remaining lease
      // interval. Re-observe time after it and immediately before any mutable
      // object or selector operation.
      const mutationAuthorizedAt = await mutationInstant();
      const fence = await readFence(coordinationStore, { missing: false });
      if (
        !currentLeaseMatches(fence.document, request.lease)
        || Date.parse(fence.document.issuedAt) > Date.parse(mutationAuthorizedAt)
        || Date.parse(mutationAuthorizedAt) >= Date.parse(fence.document.expiresAt)
      ) {
        return adapterResult(null, GC_MUTATION_USAGE, "error", "stale-fence");
      }
      for (const expected of request.selectorPreconditions) {
        const store = selectStore(expected.namespace, siteStore, occurrenceStore);
        const selected = await store.read(expected.key, {
          maximumBytes: 64 * 1024,
          label: "release storage selector",
        });
        if (sha256(selected.bytes) !== expected.sha256 || selected.etag !== expected.etag) {
          return adapterResult(null, GC_MUTATION_USAGE, "error", "selector-changed");
        }
      }
      const store = selectStore(request.namespace, siteStore, occurrenceStore);
      const kind = /^blobs\//u.test(request.key)
        ? "blob"
        : /generations\//u.test(request.key)
          ? "generation"
          : /^releases\//u.test(request.key)
            ? "release"
            : /^checkpoints\//u.test(request.key)
              ? "checkpoint"
              : "manifest";
      const key = physicalKey(request.namespace, request.key, kind);
      const current = await store.head(key, { label: "release storage immutable object" });
      const observedEtag = etags.get(`${request.namespace}\u0000${request.key}`);
      if (
        observedEtag === undefined
        || current.etag !== observedEtag
        || current.bytes !== request.expected.bytes
        || current.lastModified !== request.expected.createdAt
        || keyDigest(request.key, kind) !== request.expected.sha256
      ) return adapterResult(null, GC_MUTATION_USAGE, "error", "object-changed");
      const deleteAuthorizedAt = await mutationInstant();
      const deleteFence = await readFence(coordinationStore, { missing: false });
      if (
        !currentLeaseMatches(deleteFence.document, request.lease)
        || Date.parse(deleteFence.document.issuedAt) > Date.parse(deleteAuthorizedAt)
        || Date.parse(deleteAuthorizedAt) >= Date.parse(deleteFence.document.expiresAt)
      ) return adapterResult(null, GC_MUTATION_USAGE, "error", "stale-fence");
      if (!(await store.deleteIfMatch(key, current.etag)).deleted) {
        return adapterResult(null, GC_MUTATION_USAGE, "error", "object-changed");
      }
      if (await store.head(key, { missing: true, label: "release storage immutable object" }) !== null) {
        return adapterResult(null, GC_MUTATION_USAGE, "error", "deletion-unconfirmed");
      }
      const confirmation = {
        contract: "verdify.lab-release-storage-deletion-confirmation",
        schemaVersion: 1,
        planSha256,
        namespace: request.namespace,
        keySha256: sha256(Buffer.from(request.key)),
        objectSha256: request.expected.sha256,
        deletedBytes: request.expected.bytes,
        fencingToken: request.lease.fencingToken,
        confirmedAt: deleteAuthorizedAt,
      };
      const committed = await putCanonicalAbsent(
        coordinationStore,
        confirmationKey(plannedAt.slice(0, 10), planSha256, request.namespace, request.key),
        confirmation,
        MAX_RESERVATION_BYTES,
        "release storage deletion confirmation",
      );
      return adapterResult({
        deleted: true,
        confirmationSha256: sha256(committed.bytes),
      }, { ...GC_MUTATION_USAGE, deletedBytes: request.expected.bytes });
    },
  };
}

function effectivePublication(publication, coordinationRetainedBytes, history, reservationCount) {
  if (!exactKeys(publication, [
    "contract",
    "schemaVersion",
    "retainedBytesAdded",
    "writtenBytes",
    "egressBytes",
    "requests",
  ])) throw new Error("release storage publication estimate does not use the closed v1 shape");
  safeInteger(reservationCount, "release storage retained reservation count");
  const coordinationAfterGc = coordinationRetainedBytes - history.deletedBytes;
  if (coordinationAfterGc < 0) throw new Error("release storage coordination GC exceeds retained bytes");
  const retainedReservationBytes = safeInteger(
    reservationCount * MAX_RESERVATION_BYTES,
    "release storage retained reservation bytes",
  );
  const coordinationUsage = addUsage(
    history.usageUpperBound,
    history.observationExtraUsage,
    "release storage coordination GC usage",
  );
  return {
    ...publication,
    retainedBytesAdded: add(
      add(
        add(publication.retainedBytesAdded, coordinationAfterGc, "release storage total retained bytes"),
        retainedReservationBytes,
        "release storage reservation retained bytes",
      ),
      COORDINATION_FINALIZATION_USAGE.writtenBytes,
      "release storage finalization retained bytes",
    ),
    writtenBytes: add(
      add(publication.writtenBytes, coordinationUsage.writtenBytes, "release storage coordination writes"),
      COORDINATION_FINALIZATION_USAGE.writtenBytes,
      "release storage finalization written bytes",
    ),
    egressBytes: add(
      add(publication.egressBytes, coordinationUsage.egressBytes, "release storage coordination egress"),
      COORDINATION_FINALIZATION_USAGE.egressBytes,
      "release storage finalization egress bytes",
    ),
    requests: add(
      add(publication.requests, coordinationUsage.requests, "release storage coordination requests"),
      COORDINATION_FINALIZATION_USAGE.requests,
      "release storage finalization requests",
    ),
  };
}

function publisherReservation(publication) {
  return {
    writtenBytes: add(publication.writtenBytes, COORDINATION_FINALIZATION_USAGE.writtenBytes, "publication reservation writes"),
    deletedBytes: 0,
    egressBytes: add(publication.egressBytes, COORDINATION_FINALIZATION_USAGE.egressBytes, "publication reservation egress"),
    requests: add(publication.requests, COORDINATION_FINALIZATION_USAGE.requests, "publication reservation requests"),
  };
}

function validatePublisherResult(result, estimate) {
  if (!exactKeys(result, [
    "contract",
    "schemaVersion",
    "status",
    "siteReleaseSha256",
    "occurrenceManifestSha256",
    "usage",
  ]) || result.contract !== "verdify.lab-release-storage-publication-result" || result.schemaVersion !== 1) {
    throw new Error("release storage publisher result does not use the closed v1 shape");
  }
  if (!["published", "idempotent"].includes(result.status)) {
    throw new Error("release storage publisher status is invalid");
  }
  digest(result.siteReleaseSha256, "published site release digest");
  digest(result.occurrenceManifestSha256, "published occurrence manifest digest");
  usage(result.usage, "release storage publisher result usage");
  for (const key of ["writtenBytes", "egressBytes", "requests"]) {
    if (result.usage[key] > estimate[key]) {
      throw new Error("release storage publisher exceeded its pre-reserved resource bound");
    }
  }
  if (result.usage.deletedBytes !== 0) {
    throw new Error("release storage publisher cannot report deletion authority");
  }
  return result;
}

function statusDocument({
  state,
  observedAt,
  plan,
  usageSnapshot,
  fenceToken = null,
  leaseReleasedAt = null,
  gc = null,
  publication = null,
}) {
  const thresholds = plan.document.thresholds.map(
    ({ name, value, limit, ratio, status }) => ({ name, value, limit, ratio, status }),
  );
  return {
    contract: "verdify.lab-release-storage-coordinator-status",
    schemaVersion: 1,
    state,
    observedAt,
    planSha256: plan.sha256,
    inventorySha256: plan.document.snapshotSha256,
    fencingToken: fenceToken,
    leaseReleasedAt,
    publicationDecision: plan.document.publication.decision,
    publicationReasons: [...plan.document.publication.reasons],
    preservesLastKnownGood: true,
    retainedBytes: plan.document.accounting.projected.retainedBytes,
    plannedDeletedBytes: add(
      plan.document.accounting.plannedDeletedBytes,
      gc?.coordinationDeletedBytes ?? 0,
      "release storage status planned deleted bytes",
    ),
    deletedObjects: gc?.deletedObjects ?? 0,
    dailyUsage: structuredClone(usageSnapshot.state.counters),
    usageReservationCount: usageSnapshot.reservationCount,
    thresholds,
    selectedSiteReleaseSha256: publication?.siteReleaseSha256 ?? null,
    selectedOccurrenceManifestSha256: publication?.occurrenceManifestSha256 ?? null,
  };
}

export function renderReleaseStorageS3Metrics(status) {
  if (status?.contract !== "verdify.lab-release-storage-coordinator-status") {
    throw new Error("release storage metrics require a coordinator status document");
  }
  const threshold = Object.fromEntries(status.thresholds.map((item) => [item.name, item]));
  const lines = [
    "# HELP verdify_lab_release_storage_retained_bytes Immutable release and occurrence bytes retained.",
    "# TYPE verdify_lab_release_storage_retained_bytes gauge",
    `verdify_lab_release_storage_retained_bytes ${status.retainedBytes}`,
    "# HELP verdify_lab_release_storage_written_bytes_day Reserved write bytes for the current UTC day.",
    "# TYPE verdify_lab_release_storage_written_bytes_day gauge",
    `verdify_lab_release_storage_written_bytes_day ${status.dailyUsage.writtenBytes}`,
    "# HELP verdify_lab_release_storage_deleted_bytes_day Conservatively reserved deleted bytes for the current UTC day.",
    "# TYPE verdify_lab_release_storage_deleted_bytes_day gauge",
    `verdify_lab_release_storage_deleted_bytes_day ${status.dailyUsage.deletedBytes}`,
    "# HELP verdify_lab_release_storage_egress_bytes_day Reserved object-store egress bytes for the current UTC day.",
    "# TYPE verdify_lab_release_storage_egress_bytes_day gauge",
    `verdify_lab_release_storage_egress_bytes_day ${status.dailyUsage.egressBytes}`,
    "# HELP verdify_lab_release_storage_requests_day Reserved object-store requests for the current UTC day.",
    "# TYPE verdify_lab_release_storage_requests_day gauge",
    `verdify_lab_release_storage_requests_day ${status.dailyUsage.requests}`,
    "# HELP verdify_lab_release_storage_budget_ratio Current usage divided by its hard tracker budget.",
    "# TYPE verdify_lab_release_storage_budget_ratio gauge",
    `verdify_lab_release_storage_budget_ratio{resource="retained"} ${status.retainedBytes / threshold.retainedBytes.limit}`,
    `verdify_lab_release_storage_budget_ratio{resource="written_day"} ${status.dailyUsage.writtenBytes / threshold.writtenBytesPerDay.limit}`,
    `verdify_lab_release_storage_budget_ratio{resource="egress_day"} ${status.dailyUsage.egressBytes / threshold.egressBytesPerDay.limit}`,
    `verdify_lab_release_storage_budget_ratio{resource="requests_day"} ${status.dailyUsage.requests / threshold.requestsPerDay.limit}`,
    "# HELP verdify_lab_release_storage_publication_allowed Whether the coordinator budget gate permits publication.",
    "# TYPE verdify_lab_release_storage_publication_allowed gauge",
    `verdify_lab_release_storage_publication_allowed ${status.publicationDecision === "block" ? 0 : 1}`,
    "# HELP verdify_lab_release_storage_gc_deleted_objects Objects deleted by the latest fenced GC pass.",
    "# TYPE verdify_lab_release_storage_gc_deleted_objects gauge",
    `verdify_lab_release_storage_gc_deleted_objects ${status.deletedObjects}`,
    "",
  ];
  const rendered = lines.join("\n");
  if (Buffer.byteLength(rendered) > MAX_METRICS_BYTES) {
    throw new Error("release storage metrics exceed their byte limit");
  }
  return rendered;
}

async function writeMutable(store, key, bytes, maximumBytes, contentType) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 1 || bytes.length > maximumBytes) {
    throw new Error("release storage mutable coordination output exceeds its byte limit");
  }
  for (let attempt = 0; attempt < MAX_CAS_ATTEMPTS; attempt += 1) {
    const current = await store.read(key, {
      maximumBytes,
      label: "release storage mutable coordination output",
      missing: true,
    });
    const result = current === null
      ? await store.putIfAbsent(key, bytes, { contentType })
      : await store.putIfMatch(key, bytes, current.etag, { contentType });
    if (!result.written) continue;
    const committed = await store.read(key, {
      maximumBytes,
      label: "release storage mutable coordination output",
    });
    if (!committed.bytes.equals(bytes)) {
      throw new Error("release storage mutable coordination output changed after CAS");
    }
    return;
  }
  throw new Error("release storage mutable coordination output CAS did not converge");
}

async function persistStatus(coordinationStore, status) {
  const bytes = canonicalBytes(status);
  await writeMutable(coordinationStore, "status.json", bytes, MAX_STATUS_BYTES, "application/json");
  await writeMutable(
    coordinationStore,
    "metrics/latest.prom",
    Buffer.from(renderReleaseStorageS3Metrics(status)),
    MAX_METRICS_BYTES,
    "text/plain; version=0.0.4; charset=utf-8",
  );
}

async function currentFrom(clock) {
  if (typeof clock !== "function") throw new Error("release storage coordinator requires an injected clock");
  return instant(await clock(), "release storage coordinator time");
}

async function currentForBudgetDay(clock, plannedAt, label) {
  const current = await currentFrom(clock);
  if (current.slice(0, 10) !== plannedAt.slice(0, 10)) {
    throw new Error(`${label} crossed the planned UTC budget day`);
  }
  return current;
}

export async function coordinateReleaseStorageS3Publication({
  siteStore,
  occurrenceStore,
  coordinationStore,
  publication,
  eventIdentitySha256,
  ownerIdentity,
  clock,
  publisher,
  checkpoint = async () => {},
  leaseSeconds = MAX_LEASE_SECONDS,
  leaseNonce = randomUUID(),
  inventoryNonce = randomUUID(),
}) {
  digest(eventIdentitySha256, "release storage event identity digest");
  if (!(coordinationStore instanceof S3ObjectStore) || coordinationStore.accessMode !== "writer") {
    throw new Error("release storage coordinator requires an S3 coordination writer");
  }
  if (typeof publisher !== "function" || typeof checkpoint !== "function") {
    throw new Error("release storage coordinator operations are invalid");
  }
  if (typeof inventoryNonce !== "string" || !SAFE_ID_RE.test(inventoryNonce)) {
    throw new Error("release storage inventory nonce is invalid");
  }
  assertReleaseStorageWriterTopology(siteStore, occurrenceStore, coordinationStore);
  const plannedAt = await currentFrom(clock);
  let usageBefore = await loadReleaseStorageS3Usage({ coordinationStore, asOf: plannedAt });
  const listing = await listReleaseStorageS3Inventory({
    siteStore,
    occurrenceStore,
    async beforePage({ namespace, pageNumber }) {
      const reservationTime = await currentForBudgetDay(
        clock,
        plannedAt,
        "release storage inventory listing",
      );
      const delta = inventoryReservationUsage(
        releaseStorageS3InventoryContract.listing.pageReservationUsage,
        "release storage inventory listing reservation",
      );
      assertInventoryBudget(usageBefore, delta, "listing", reservationTime);
      const reservation = await reserveReleaseStorageS3Usage({
        coordinationStore,
        kind: "inventory-list",
        operationSha256: sha256(canonicalBytes({
          contract: "verdify.lab-release-storage-inventory-list-page-identity",
          schemaVersion: 1,
          eventIdentitySha256,
          inventoryNonce,
          namespace,
          pageNumber,
        })),
        createdAt: reservationTime,
        delta,
      });
      usageBefore = applyReservationToUsageSnapshot(
        usageBefore,
        reservation,
        reservationTime,
      );
      await checkpoint(Object.freeze({
        phase: "after-inventory-list-page-reservation",
        namespace,
        pageNumber,
        reservationId: reservation.reservationId,
      }));
    },
  });
  const readPlan = planReleaseStorageS3InventoryReads(listing);
  const readReservationTime = await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage inventory exact-read reservation",
  );
  const readDelta = inventoryReservationUsage(
    readPlan.usage,
    "release storage inventory exact-read reservation",
  );
  assertInventoryBudget(usageBefore, readDelta, "exact-read", readReservationTime);
  const readReservation = await reserveReleaseStorageS3Usage({
    coordinationStore,
    kind: "inventory-read",
    operationSha256: sha256(canonicalBytes({
      contract: "verdify.lab-release-storage-inventory-read-identity",
      schemaVersion: 1,
      eventIdentitySha256,
      inventoryNonce,
      listingSha256: listing.sha256,
    })),
    createdAt: readReservationTime,
    delta: readDelta,
  });
  usageBefore = applyReservationToUsageSnapshot(
    usageBefore,
    readReservation,
    readReservationTime,
  );
  await checkpoint(Object.freeze({
    phase: "after-inventory-read-reservation",
    listingSha256: listing.sha256,
    canonicalObjectCount: readPlan.canonicalObjectCount,
    reservationId: readReservation.reservationId,
  }));
  await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage inventory exact reads",
  );
  const inventory = await captureReleaseStorageS3Inventory({
    siteStore,
    occurrenceStore,
    capturedAt: plannedAt,
    listing,
  });
  const planAt = await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage safety planning",
  );
  const coordinationHistory = planCoordinationHistory(usageBefore.inventoryEntries, planAt);
  const preliminaryEffective = effectivePublication(
    publication,
    usageBefore.retainedBytes,
    coordinationHistory,
    coordinationHistory.candidates.length + 2,
  );
  const preliminaryPlan = planReleaseStorageSafety({
    snapshot: inventory,
    usageState: usageBefore.state,
    publication: preliminaryEffective,
    asOf: planAt,
  });
  const retainedJournalCount = 2
    + (2 * preliminaryPlan.document.deletions.length)
    + coordinationHistory.candidates.length;
  const effective = effectivePublication(
    publication,
    usageBefore.retainedBytes,
    coordinationHistory,
    retainedJournalCount,
  );
  const plan = planReleaseStorageSafety({
    snapshot: inventory,
    usageState: usageBefore.state,
    publication: effective,
    asOf: planAt,
  });
  if (JSON.stringify(plan.document.deletions) !== JSON.stringify(preliminaryPlan.document.deletions)) {
    throw new Error("release storage GC membership changed during retained-journal accounting");
  }
  if (plan.document.publication.decision === "block") {
    return statusDocument({
      state: "blocked",
      observedAt: planAt,
      plan,
      usageSnapshot: usageBefore,
    });
  }
  const leaseIssuedAt = await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage lease acquisition",
  );
  const acquired = await acquireReleaseStorageS3Lease({
    coordinationStore,
    planSha256: plan.sha256,
    ownerIdentity,
    issuedAt: leaseIssuedAt,
    leaseSeconds,
    nonce: leaseNonce,
  });
  const gcReservationTime = await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage GC preflight",
  );
  const gcPreflight = addUsage(
    gcPreflightReservation(plan),
    coordinationHistory.observationExtraUsage,
    "release storage complete GC preflight reservation",
  );
  await reserveReleaseStorageS3Usage({
    coordinationStore,
    kind: "gc-preflight",
    operationSha256: sha256(canonicalBytes({
      contract: "verdify.lab-release-storage-gc-preflight-identity",
      schemaVersion: 1,
      planSha256: plan.sha256,
      fencingToken: acquired.record.fencingToken,
    })),
    createdAt: gcReservationTime,
    delta: gcPreflight,
  });
  const mutationInstant = () => currentForBudgetDay(
    clock,
    plannedAt,
    "release storage GC mutation",
  );
  const gc = await executeReleaseStorageGcPlan({
    plan,
    adapter: createS3GcAdapter({
      siteStore,
      occurrenceStore,
      coordinationStore,
      lease: acquired.record,
      planSha256: plan.sha256,
      plannedAt,
      mutationInstant,
    }),
    lease: acquired.lease,
    currentInstant: async () => ({
      contract: "verdify.lab-current-instant",
      schemaVersion: 1,
      instant: await currentForBudgetDay(
        clock,
        plannedAt,
        "release storage GC execution",
      ),
    }),
    progress: null,
  });
  const coordinationGc = await compactCoordinationHistory({
    coordinationStore,
    acquired,
    history: coordinationHistory,
    plannedAt,
    clock,
  });
  const totalGcUsageUpperBound = addUsage(
    addUsage(
      plan.document.accounting.gcUsageUpperBound,
      coordinationHistory.usageUpperBound,
      "release storage site and coordination GC upper bound",
    ),
    coordinationHistory.observationExtraUsage,
    "release storage complete GC upper bound",
  );
  await checkpoint(Object.freeze({
    phase: "after-gc",
    planSha256: plan.sha256,
    gcUsageUpperBound: Object.freeze(totalGcUsageUpperBound),
  }));
  const publicationTime = await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage publication",
  );
  await reserveReleaseStorageS3Usage({
    coordinationStore,
    kind: "publication",
    operationSha256: sha256(canonicalBytes({
      contract: "verdify.lab-release-storage-publication-identity",
      schemaVersion: 1,
      eventIdentitySha256,
    })),
    createdAt: publicationTime,
    delta: publisherReservation(publication),
  });
  await assertCurrentPublicationLease(
    coordinationStore,
    acquired,
    await currentForBudgetDay(
      clock,
      plannedAt,
      "release storage publication fence check",
    ),
  );
  const published = validatePublisherResult(await publisher(Object.freeze({
    contract: "verdify.lab-release-storage-publication-authority",
    schemaVersion: 1,
    eventIdentitySha256,
    planSha256: plan.sha256,
    fencingToken: acquired.record.fencingToken,
    leaseId: acquired.record.leaseId,
    expiresAt: acquired.record.expiresAt,
  })), publication);
  await assertCurrentPublicationLease(
    coordinationStore,
    acquired,
    await currentForBudgetDay(
      clock,
      plannedAt,
      "release storage post-publication fence check",
    ),
  );
  await checkpoint(Object.freeze({ phase: "after-publisher", planSha256: plan.sha256 }));
  await assertCurrentPublicationLease(
    coordinationStore,
    acquired,
    await currentForBudgetDay(
      clock,
      plannedAt,
      "release storage terminal-status fence check",
    ),
  );
  const usageObservedAt = await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage terminal usage observation",
  );
  const usageAfter = await loadReleaseStorageS3Usage({
    coordinationStore,
    asOf: usageObservedAt,
  });
  const releasedAt = await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage terminal lease release",
  );
  await assertCurrentPublicationLease(coordinationStore, acquired, releasedAt);
  await releaseReleaseStorageS3Lease({
    coordinationStore,
    acquired,
    releasedAt,
  });
  const status = statusDocument({
    state: "complete",
    observedAt: releasedAt,
    plan,
    usageSnapshot: usageAfter,
    fenceToken: acquired.record.fencingToken,
    leaseReleasedAt: releasedAt,
    gc: Object.freeze({
      ...gc,
      deletedObjects: gc.deletedObjects + coordinationGc.deletedObjects,
      coordinationDeletedBytes: coordinationGc.deletedBytes,
    }),
    publication: published,
  });
  await persistStatus(coordinationStore, status);
  return status;
}

export const releaseStoragePassOneContract = Object.freeze({
  readiness: Object.freeze({
    mutating: false,
    command: "node scripts/verify-release-storage-s3.mjs contract",
    claim: "names-only-source-contract",
  }),
  activationProof: Object.freeze({
    mutating: true,
    command: "node scripts/verify-release-storage-s3.mjs activation-proof --acknowledge-stage-mutation",
    claim: "bounded-create-read-head-stale-conditional-delete",
  }),
  configMap: "verdify-lab-occurrence-store-metadata",
  readerSecret: "verdify-lab-occurrence-store-reader",
  writerSecret: "verdify-lab-occurrence-store-writer",
  readerEnvironmentNames: Object.freeze([
    "LAB_RELEASE_STORE",
    "LAB_OCCURRENCE_STORE",
    "LAB_S3_ENDPOINT_URL",
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
  ]),
  writerEnvironmentNames: Object.freeze([
    "LAB_RELEASE_STORE",
    "LAB_OCCURRENCE_STORE",
    "LAB_RELEASE_COORDINATION_STORE",
    "LAB_S3_ENDPOINT_URL",
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
  ]),
  locationConstraints: Object.freeze({
    site: "dedicated-s3-prefix",
    occurrence: "dedicated-s3-base-prefix-with-typed-occurrence-releases-v1-child",
    coordination: "dedicated-non-overlapping-s3-prefix",
  }),
});

export const releaseStorageS3CoordinatorContract = Object.freeze({
  leaseSeconds: Object.freeze({ minimum: MIN_LEASE_SECONDS, maximum: MAX_LEASE_SECONDS }),
  retention: Object.freeze({
    eventIdempotencySeconds: releaseStorageSafetyContract.eventIdempotencyHorizonSeconds,
    reservationIdempotencySeconds: IDEMPOTENCY_HORIZON_MS / 1000,
    deletionConfirmationSeconds: GC_CONFIRMATION_HORIZON_MS / 1000,
    maximumInventoryObjects: MAX_COORDINATION_OBJECTS,
  }),
  finalizationUsage: COORDINATION_FINALIZATION_USAGE,
  inventory: Object.freeze({
    listingPageUsage: releaseStorageS3InventoryContract.listing.pageReservationUsage,
    reservationJournalUsage: RESERVATION_JOURNAL_USAGE,
    checkpoint: releaseStorageS3InventoryContract.checkpoint,
  }),
  gcPreflightUsage: GC_PREFLIGHT_USAGE,
  gcMutationUsage: GC_MUTATION_USAGE,
  budgets: releaseStorageSafetyContract.budgets,
  status: Object.freeze({ contract: "verdify.lab-release-storage-coordinator-status", schemaVersion: 1 }),
});
