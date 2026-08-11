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
const COORDINATION_CLEANUP_HEADROOM = 1024;
const MAX_ADMITTED_COORDINATION_OBJECTS = MAX_COORDINATION_OBJECTS - COORDINATION_CLEANUP_HEADROOM;
const MAX_COORDINATION_HISTORY_DELETIONS_PER_RUN = 1000;
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
const RESERVATION_KEY_RE = /^usage\/(\d{4}-\d{2}-\d{2})\/reservations\/(inventory-list|inventory-read|gc-preflight|gc-mutation|publication|finalization)\/([0-9a-f]{64})\/((?:0|[1-9]\d*))-((?:0|[1-9]\d*))-((?:0|[1-9]\d*))-((?:0|[1-9]\d*))-([0-9a-f]{64})\.json$/u;
const CONFIRMATION_KEY_RE = /^gc\/confirmations\/(\d{4}-\d{2}-\d{2})\/([0-9a-f]{64})\/(site|occurrence)\/([0-9a-f]{64})\.json$/u;

// Terminal output uses three monotonic CAS writes (fail-closed metrics, status,
// terminal metrics). At eight attempts each that is 52 requests including
// verification; worst-case fence acquire/bind/release adds 23 and the durable
// reservation adds three. The 80-request, 2-MiB write, 3-MiB read envelope is
// therefore conservative even at maximum-size status/metrics/fence bodies.
export const COORDINATION_FINALIZATION_USAGE = Object.freeze({
  writtenBytes: 2 * 1024 * 1024,
  deletedBytes: 0,
  egressBytes: 3 * 1024 * 1024,
  requests: 80,
});
const COORDINATION_FINALIZATION_RETAINED_BYTES = MAX_STATUS_BYTES + MAX_METRICS_BYTES;

const GC_PREFLIGHT_USAGE = Object.freeze({ ...ADAPTER_USAGE_LIMITS });
const GC_MUTATION_USAGE = Object.freeze({ ...ADAPTER_USAGE_LIMITS });
const RESERVATION_JOURNAL_USAGE = Object.freeze({
  writtenBytes: MAX_RESERVATION_BYTES,
  deletedBytes: 0,
  egressBytes: MAX_RESERVATION_BYTES,
  requests: 3,
});
const MAX_COORDINATION_LIST_PAGES = 100;
const COORDINATION_LIST_USAGE = Object.freeze({
  writtenBytes: 0,
  deletedBytes: 0,
  egressBytes: 2 * 1024 * 1024 * MAX_COORDINATION_LIST_PAGES,
  requests: MAX_COORDINATION_LIST_PAGES,
});
const RESERVATION_KINDS = new Set([
  "inventory-list",
  "inventory-read",
  "gc-preflight",
  "gc-mutation",
  "publication",
  "finalization",
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
  return Object.freeze(addUsage(
    addUsage(operation, RESERVATION_JOURNAL_USAGE, label),
    {
      writtenBytes: 0,
      deletedBytes: 0,
      egressBytes: 2 * MAX_FENCE_BYTES,
      requests: 2,
    },
    `${label} fence reads`,
  ));
}

function inventoryBudgetResult(
  snapshot,
  delta,
  phase,
  observedAt,
  maximumCoordinationObjects = MAX_ADMITTED_COORDINATION_OBJECTS,
) {
  safeInteger(
    maximumCoordinationObjects,
    "release storage coordination admission limit",
    MAX_COORDINATION_OBJECTS,
  );
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
  if (projected.coordinationObjects > maximumCoordinationObjects) {
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
      coordinationObjects: maximumCoordinationObjects,
      physicalCoordinationObjects: MAX_COORDINATION_OBJECTS,
    }),
  });
}

function assertInventoryBudget(
  snapshot,
  delta,
  phase,
  observedAt,
  maximumCoordinationObjects = MAX_ADMITTED_COORDINATION_OBJECTS,
) {
  const result = inventoryBudgetResult(
    snapshot,
    delta,
    phase,
    observedAt,
    maximumCoordinationObjects,
  );
  if (result.status === "blocked") throw new ReleaseStorageInventoryBudgetError(result);
  return result;
}

async function assertInventoryBudgetOrFinalize({
  snapshot,
  delta,
  phase,
  observedAt,
  maximumCoordinationObjects = MAX_ADMITTED_COORDINATION_OBJECTS,
  finalizeBudgetBlock,
}) {
  if (typeof finalizeBudgetBlock !== "function") {
    throw new Error("release storage inventory budget finalizer is invalid");
  }
  try {
    return assertInventoryBudget(
      snapshot,
      delta,
      phase,
      observedAt,
      maximumCoordinationObjects,
    );
  } catch (error) {
    if (!(error instanceof ReleaseStorageInventoryBudgetError)) throw error;
    await finalizeBudgetBlock(error, snapshot);
    throw error;
  }
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
  let committed = null;
  try {
    written = (await coordinationStore.putIfAbsent(selected.key, bytes, {
      contentType: "application/json",
    })).written;
  } catch (error) {
    committed = await readCanonical(
      coordinationStore,
      selected.key,
      MAX_RESERVATION_BYTES,
      "release storage usage reservation",
      { missing: true },
    ).catch(() => null);
    if (committed === null) throw error;
  }
  committed ??= await readCanonical(
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
  const listed = await store.listInventory("", {
    maximumObjects: MAX_COORDINATION_OBJECTS,
    includePageCount: true,
  });
  const entries = listed.objects;
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
  return { entries, pageCount: listed.pageCount };
}

export async function loadReleaseStorageS3Usage({ coordinationStore, asOf }) {
  if (!(coordinationStore instanceof S3ObjectStore)) {
    throw new Error("release storage usage requires an S3 coordination store");
  }
  instant(asOf, "release storage usage observation time");
  const inventory = await coordinationInventory(coordinationStore);
  const entries = inventory.entries;
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
    inventoryPageCount: inventory.pageCount,
  };
}

function wholeDayExpired(day, asOf, horizonMs) {
  const endOfDay = Date.parse(`${day}T23:59:59.999Z`);
  if (!Number.isFinite(endOfDay)) throw new Error("release storage coordination history day is invalid");
  return endOfDay < Date.parse(asOf) - horizonMs;
}

function planCoordinationHistory(entries, asOf, pageCount) {
  safeInteger(pageCount, "release storage coordination inventory page count", MAX_COORDINATION_OBJECTS);
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
  const selectedCandidates = candidates.slice(0, MAX_COORDINATION_HISTORY_DELETIONS_PER_RUN);
  const deletedBytes = selectedCandidates.reduce(
    (total, entry) => add(total, entry.bytes, "release storage coordination history deleted bytes"),
    0,
  );
  // `pageCount` is observed rather than inferred so short S3 pages remain
  // explicit evidence. Each coordination inventory call is separately
  // pre-reserved at its maximum page envelope by the coordinator.
  const listPages = pageCount;
  return Object.freeze({
    candidates: Object.freeze(selectedCandidates.map((entry) => Object.freeze({ ...entry }))),
    deletedBytes,
    usageUpperBound: Object.freeze({
      ...multiplyUsage(
        GC_MUTATION_USAGE,
        selectedCandidates.length,
        "release storage coordination history GC",
      ),
      deletedBytes,
    }),
    observedListPages: listPages,
    observationExtraUsage: Object.freeze({
      writtenBytes: 0,
      deletedBytes: 0,
      egressBytes: 0,
      requests: 0,
    }),
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

export async function releaseReleaseStorageS3Lease({
  coordinationStore,
  acquired,
  releasedAt = null,
  clock = null,
  plannedAt = null,
}) {
  if ((clock === null) !== (plannedAt === null)) {
    throw new Error("release storage lease release clock binding is invalid");
  }
  if (clock === null) instant(releasedAt, "release storage lease release time");
  const current = await readFence(coordinationStore, { missing: false });
  const selectedReleasedAt = clock === null
    ? releasedAt
    : await currentForBudgetDay(
        clock,
        plannedAt,
        "release storage lease release",
      );
  if (
    current.document.leaseId !== acquired.record.leaseId
    || current.document.fencingToken !== acquired.record.fencingToken
    || current.document.planSha256 !== acquired.record.planSha256
    || current.document.releasedAt !== null
    || Date.parse(selectedReleasedAt) >= Date.parse(current.document.expiresAt)
  ) throw new Error("release storage lease was lost before release");
  const document = { ...current.document, releasedAt: selectedReleasedAt };
  validateFence(document);
  const committed = await writeFence(coordinationStore, current, document);
  if (committed === null) throw new Error("release storage lease release CAS failed");
  return committed.document;
}

async function bindReleaseStorageS3LeasePlan({
  coordinationStore,
  acquired,
  planSha256,
  clock,
  plannedAt,
}) {
  digest(planSha256, "release storage bound plan digest");
  const current = await readFence(coordinationStore, { missing: false });
  const boundAt = await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage lease plan binding",
  );
  if (
    !currentLeaseMatches(current.document, acquired.record)
    || Date.parse(current.document.issuedAt) > Date.parse(boundAt)
    || Date.parse(boundAt) >= Date.parse(current.document.expiresAt)
  ) throw new Error("release storage publication lease is no longer current");
  const document = { ...current.document, planSha256 };
  validateFence(document);
  const committed = await writeFence(coordinationStore, current, document);
  if (committed === null) throw new Error("release storage lease plan binding CAS failed");
  return {
    record: committed.document,
    etag: committed.etag,
    lease: safetyLease(committed.document),
  };
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

async function assertCurrentPublicationLease(
  coordinationStore,
  acquired,
  { clock, plannedAt, label },
) {
  const current = await readFence(coordinationStore, { missing: false });
  const currentTime = await currentForBudgetDay(clock, plannedAt, label);
  if (
    !currentLeaseMatches(current.document, acquired.record)
    || Date.parse(current.document.issuedAt) > Date.parse(currentTime)
    || Date.parse(currentTime) >= Date.parse(current.document.expiresAt)
  ) throw new Error("release storage publication lease is no longer current");
  return { current, currentTime };
}

async function reserveCoordinationInventoryObservation({
  coordinationStore,
  acquired,
  clock,
  plannedAt,
  eventIdentitySha256,
  inventoryNonce,
  phase,
}) {
  const { currentTime: createdAt } = await assertCurrentPublicationLease(
    coordinationStore,
    acquired,
    {
      clock,
      plannedAt,
      label: `release storage coordination inventory ${phase}`,
    },
  );
  return reserveReleaseStorageS3Usage({
    coordinationStore,
    kind: "inventory-list",
    operationSha256: sha256(canonicalBytes({
      contract: "verdify.lab-release-storage-coordination-inventory-identity",
      schemaVersion: 1,
      eventIdentitySha256,
      inventoryNonce,
      fencingToken: acquired.record.fencingToken,
      phase,
    })),
    createdAt,
    delta: inventoryReservationUsage(
      COORDINATION_LIST_USAGE,
      "release storage coordination inventory reservation",
    ),
  });
}

async function compactCoordinationHistory({
  coordinationStore,
  acquired,
  history,
  usageSnapshot,
  plannedAt,
  clock,
  finalizeBudgetBlock,
}) {
  let deletedBytes = 0;
  if (history.candidates.length > 0) {
    const { currentTime: reservationAt } = await assertCurrentPublicationLease(
      coordinationStore,
      acquired,
      {
        clock,
        plannedAt,
        label: "release storage coordination history GC admission",
      },
    );
    const mutationDelta = inventoryReservationUsage(
      { ...history.usageUpperBound, deletedBytes: history.deletedBytes },
      "release storage coordination history GC reservation",
    );
    const cleanupTransactionDelta = addUsage(
      mutationDelta,
      inventoryReservationUsage(
        COORDINATION_LIST_USAGE,
        "release storage post-history-GC coordination inventory reservation",
      ),
      "release storage complete coordination history cleanup transaction",
    );
    await assertInventoryBudgetOrFinalize({
      snapshot: usageSnapshot,
      delta: cleanupTransactionDelta,
      phase: "coordination-history-gc",
      observedAt: reservationAt,
      maximumCoordinationObjects: MAX_COORDINATION_OBJECTS,
      finalizeBudgetBlock,
    });
    await reserveReleaseStorageS3Usage({
      coordinationStore,
      kind: "gc-mutation",
      operationSha256: sha256(canonicalBytes({
        contract: "verdify.lab-release-storage-coordination-history-gc-batch-identity",
        schemaVersion: 1,
        fencingToken: acquired.record.fencingToken,
        candidates: history.candidates.map(({ key, etag, bytes }) => ({
          keySha256: sha256(Buffer.from(key)),
          etagSha256: sha256(Buffer.from(etag)),
          bytes,
        })),
      })),
      createdAt: reservationAt,
      delta: mutationDelta,
    });
    await assertCurrentPublicationLease(coordinationStore, acquired, {
      clock,
      plannedAt,
      label: "release storage coordination history GC post-reservation",
    });
  }
  for (const entry of history.candidates) {
    const current = await coordinationStore.head(entry.key, {
      label: "release storage coordination history object",
    });
    if (
      current.etag !== entry.etag
      || current.bytes !== entry.bytes
      || current.lastModified !== entry.lastModified
    ) throw new Error("release storage coordination history changed before GC");
    await assertCurrentPublicationLease(coordinationStore, acquired, {
      clock,
      plannedAt,
      label: "release storage coordination history GC deletion",
    });
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
        fencingToken: request.lease.fencingToken,
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
      const fence = await readFence(coordinationStore, { missing: false });
      const mutationAuthorizedAt = await mutationInstant();
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
      const deleteFence = await readFence(coordinationStore, { missing: false });
      const deleteAuthorizedAt = await mutationInstant();
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
  const nextAdmissionUsage = inventoryReservationUsage(
    COORDINATION_LIST_USAGE,
    "release storage next-cycle coordination inventory headroom",
  );
  const publicationUsage = publisherReservation(publication);
  return {
    ...publication,
    retainedBytesAdded: add(
      add(
        add(publication.retainedBytesAdded, coordinationAfterGc, "release storage total retained bytes"),
        retainedReservationBytes,
        "release storage reservation retained bytes",
      ),
      COORDINATION_FINALIZATION_RETAINED_BYTES,
      "release storage finalization retained bytes",
    ),
    writtenBytes: add(
      add(
        add(publicationUsage.writtenBytes, coordinationUsage.writtenBytes, "release storage coordination writes"),
        nextAdmissionUsage.writtenBytes,
        "release storage next admission writes",
      ),
      COORDINATION_FINALIZATION_USAGE.writtenBytes,
      "release storage finalization written bytes",
    ),
    egressBytes: add(
      add(
        add(publicationUsage.egressBytes, coordinationUsage.egressBytes, "release storage coordination egress"),
        nextAdmissionUsage.egressBytes,
        "release storage next admission egress",
      ),
      COORDINATION_FINALIZATION_USAGE.egressBytes,
      "release storage finalization egress bytes",
    ),
    requests: add(
      add(
        add(publicationUsage.requests, coordinationUsage.requests, "release storage coordination requests"),
        nextAdmissionUsage.requests,
        "release storage next admission requests",
      ),
      COORDINATION_FINALIZATION_USAGE.requests,
      "release storage finalization requests",
    ),
  };
}

function publisherReservation(publication) {
  return inventoryReservationUsage({
    writtenBytes: publication.writtenBytes,
    deletedBytes: 0,
    egressBytes: publication.egressBytes,
    requests: publication.requests,
  }, "release storage publication reservation");
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

function coordinatorThreshold(name, value, limit) {
  const ratio = value / limit;
  return {
    name,
    value,
    limit,
    ratio,
    status: ratio >= 1
      ? "block"
      : ratio >= releaseStorageSafetyContract.budgets.warningFraction ? "warn" : "ok",
  };
}

function unknownCoordinatorThreshold(name, limit) {
  return {
    name,
    value: null,
    limit,
    ratio: null,
    status: "unknown",
  };
}

function inventoryBudgetStatusDocument({
  error,
  observedAt,
  usageSnapshot,
  acquired,
  leaseReleasedAt,
  coordinationGc,
}) {
  const limits = releaseStorageSafetyContract.budgets;
  const counters = usageSnapshot.state.counters;
  return {
    contract: "verdify.lab-release-storage-coordinator-status",
    schemaVersion: 1,
    state: "blocked",
    observedAt,
    planSha256: null,
    inventorySha256: null,
    fencingToken: acquired.record.fencingToken,
    leaseReleasedAt,
    publicationDecision: "block",
    publicationReasons: [`${error.result.phase}-admission`, ...error.result.reasons],
    preservesLastKnownGood: true,
    retainedBytes: null,
    retainedBytesKnown: false,
    plannedDeletedBytes: coordinationGc.deletedBytes,
    deletedObjects: coordinationGc.deletedObjects,
    dailyUsage: structuredClone(counters),
    usageReservationCount: usageSnapshot.reservationCount,
    thresholds: [
      unknownCoordinatorThreshold("retainedBytes", limits.retainedBytes),
      coordinatorThreshold("writtenBytesPerDay", counters.writtenBytes, limits.writtenBytesPerDay),
      coordinatorThreshold("egressBytesPerDay", counters.egressBytes, limits.egressBytesPerDay),
      coordinatorThreshold("requestsPerDay", counters.requests, limits.requestsPerDay),
    ],
    selectedSiteReleaseSha256: null,
    selectedOccurrenceManifestSha256: null,
  };
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
    retainedBytesKnown: true,
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

export function renderReleaseStorageS3Metrics(status, { terminal = true } = {}) {
  if (status?.contract !== "verdify.lab-release-storage-coordinator-status") {
    throw new Error("release storage metrics require a coordinator status document");
  }
  if (typeof status.retainedBytesKnown !== "boolean") {
    throw new Error("release storage metrics retained-byte knownness is invalid");
  }
  const threshold = Object.fromEntries(status.thresholds.map((item) => [item.name, item]));
  const retainedBytes = status.retainedBytesKnown ? status.retainedBytes : "NaN";
  const retainedRatio = status.retainedBytesKnown
    ? status.retainedBytes / threshold.retainedBytes.limit
    : "NaN";
  const lines = [
    "# HELP verdify_lab_release_storage_retained_bytes Immutable release and occurrence bytes retained.",
    "# TYPE verdify_lab_release_storage_retained_bytes gauge",
    `verdify_lab_release_storage_retained_bytes ${retainedBytes}`,
    "# HELP verdify_lab_release_storage_retained_bytes_known Whether retained bytes include complete release-root inventory.",
    "# TYPE verdify_lab_release_storage_retained_bytes_known gauge",
    `verdify_lab_release_storage_retained_bytes_known ${status.retainedBytesKnown ? 1 : 0}`,
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
    `verdify_lab_release_storage_budget_ratio{resource="retained"} ${retainedRatio}`,
    `verdify_lab_release_storage_budget_ratio{resource="written_day"} ${status.dailyUsage.writtenBytes / threshold.writtenBytesPerDay.limit}`,
    `verdify_lab_release_storage_budget_ratio{resource="egress_day"} ${status.dailyUsage.egressBytes / threshold.egressBytesPerDay.limit}`,
    `verdify_lab_release_storage_budget_ratio{resource="requests_day"} ${status.dailyUsage.requests / threshold.requestsPerDay.limit}`,
    "# HELP verdify_lab_release_storage_publication_allowed Whether the coordinator budget gate permits publication.",
    "# TYPE verdify_lab_release_storage_publication_allowed gauge",
    `verdify_lab_release_storage_publication_allowed ${terminal && status.publicationDecision !== "block" ? 1 : 0}`,
    "# HELP verdify_lab_release_storage_fencing_token Monotonic token ordering terminal coordinator output.",
    "# TYPE verdify_lab_release_storage_fencing_token gauge",
    `verdify_lab_release_storage_fencing_token ${status.fencingToken}`,
    "# HELP verdify_lab_release_storage_terminal_output_complete Whether metrics match the accepted status for this token.",
    "# TYPE verdify_lab_release_storage_terminal_output_complete gauge",
    `verdify_lab_release_storage_terminal_output_complete ${terminal ? 1 : 0}`,
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

function statusOutputOrder(bytes) {
  const document = canonicalDocument(bytes, "release storage coordinator status");
  if (
    document.contract !== "verdify.lab-release-storage-coordinator-status"
    || document.schemaVersion !== 1
    || !Number.isSafeInteger(document.fencingToken)
    || document.fencingToken < 1
  ) throw new Error("release storage coordinator status fencing token is invalid");
  return { fencingToken: document.fencingToken, phase: 1 };
}

function metricsOutputOrder(bytes) {
  const rendered = bytes.toString("utf8");
  const token = /^verdify_lab_release_storage_fencing_token (\d+)$/mu.exec(rendered);
  const terminal = /^verdify_lab_release_storage_terminal_output_complete ([01])$/mu.exec(rendered);
  if (token === null || terminal === null) {
    throw new Error("release storage metrics fencing metadata is invalid");
  }
  const fencingToken = safeInteger(Number(token[1]), "release storage metrics fencing token");
  if (fencingToken < 1) throw new Error("release storage metrics fencing token is invalid");
  return {
    fencingToken,
    phase: Number(terminal[1]),
  };
}

function compareOutputOrder(left, right) {
  return left.fencingToken === right.fencingToken
    ? left.phase - right.phase
    : left.fencingToken - right.fencingToken;
}

async function writeMonotonicMutable(
  store,
  key,
  bytes,
  maximumBytes,
  contentType,
  outputOrder,
) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 1 || bytes.length > maximumBytes) {
    throw new Error("release storage mutable coordination output exceeds its byte limit");
  }
  const candidateOrder = outputOrder(bytes);
  for (let attempt = 0; attempt < MAX_CAS_ATTEMPTS; attempt += 1) {
    const current = await store.read(key, {
      maximumBytes,
      label: "release storage mutable coordination output",
      missing: true,
    });
    if (current !== null) {
      const ordering = compareOutputOrder(candidateOrder, outputOrder(current.bytes));
      if (ordering < 0) return Object.freeze({ accepted: false, reason: "newer-output-exists" });
      if (ordering === 0) {
        if (!current.bytes.equals(bytes)) {
          throw new Error("release storage mutable coordination output conflicts at one fencing order");
        }
        return Object.freeze({ accepted: true, reason: "idempotent" });
      }
    }
    const result = current === null
      ? await store.putIfAbsent(key, bytes, { contentType })
      : await store.putIfMatch(key, bytes, current.etag, { contentType });
    if (!result.written) continue;
    const committed = await store.read(key, {
      maximumBytes,
      label: "release storage mutable coordination output",
    });
    if (!committed.bytes.equals(bytes)) {
      if (compareOutputOrder(outputOrder(committed.bytes), candidateOrder) > 0) {
        return Object.freeze({ accepted: false, reason: "newer-output-exists" });
      }
      throw new Error("release storage mutable coordination output changed after CAS");
    }
    return Object.freeze({ accepted: true, reason: "written" });
  }
  throw new Error("release storage mutable coordination output CAS did not converge");
}

async function persistStatus(coordinationStore, status) {
  const statusBytes = canonicalBytes(status);
  const pendingMetrics = Buffer.from(renderReleaseStorageS3Metrics(status, { terminal: false }));
  await writeMonotonicMutable(
    coordinationStore,
    "metrics/latest.prom",
    pendingMetrics,
    MAX_METRICS_BYTES,
    "text/plain; version=0.0.4; charset=utf-8",
    metricsOutputOrder,
  );
  const accepted = await writeMonotonicMutable(
    coordinationStore,
    "status.json",
    statusBytes,
    MAX_STATUS_BYTES,
    "application/json",
    statusOutputOrder,
  );
  if (!accepted.accepted) return accepted;
  const current = await coordinationStore.read("status.json", {
    maximumBytes: MAX_STATUS_BYTES,
    label: "release storage coordinator status",
  });
  if (!current.bytes.equals(statusBytes)) {
    return Object.freeze({ accepted: false, reason: "newer-output-exists" });
  }
  return writeMonotonicMutable(
    coordinationStore,
    "metrics/latest.prom",
    Buffer.from(renderReleaseStorageS3Metrics(status)),
    MAX_METRICS_BYTES,
    "text/plain; version=0.0.4; charset=utf-8",
    metricsOutputOrder,
  );
}

async function finalizeInventoryBudgetBlock({
  error,
  coordinationStore,
  acquired,
  usageSnapshot,
  clock,
  plannedAt,
  coordinationGc,
}) {
  let observedAt = error.result.observedAt;
  let leaseReleasedAt = null;
  try {
    const released = await releaseReleaseStorageS3Lease({
      coordinationStore,
      acquired,
      clock,
      plannedAt,
    });
    observedAt = released.releasedAt;
    leaseReleasedAt = released.releasedAt;
  } catch {
    // If the fence expired or a later token won, retain the truthful null
    // release marker. Monotonic output ordering prevents this token from
    // replacing any already-persisted later attempt.
  }
  const status = inventoryBudgetStatusDocument({
    error,
    observedAt,
    usageSnapshot,
    acquired,
    leaseReleasedAt,
    coordinationGc,
  });
  await persistStatus(coordinationStore, status);
  return status;
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
  const admissionIssuedAt = await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage admission lease acquisition",
  );
  let acquired = await acquireReleaseStorageS3Lease({
    coordinationStore,
    planSha256: eventIdentitySha256,
    ownerIdentity,
    issuedAt: admissionIssuedAt,
    leaseSeconds,
    nonce: leaseNonce,
  });
  const { currentTime: finalizationReservedAt } = await assertCurrentPublicationLease(
    coordinationStore,
    acquired,
    {
      clock,
      plannedAt,
      label: "release storage attempt finalization admission",
    },
  );
  await reserveReleaseStorageS3Usage({
    coordinationStore,
    kind: "finalization",
    operationSha256: sha256(canonicalBytes({
      contract: "verdify.lab-release-storage-attempt-finalization-identity",
      schemaVersion: 1,
      eventIdentitySha256,
      fencingToken: acquired.record.fencingToken,
    })),
    createdAt: finalizationReservedAt,
    delta: COORDINATION_FINALIZATION_USAGE,
  });
  let coordinationGc = Object.freeze({ deletedObjects: 0, deletedBytes: 0 });
  const finalizeBudgetBlock = (error, usageSnapshot) => finalizeInventoryBudgetBlock({
    error,
    coordinationStore,
    acquired,
    usageSnapshot,
    clock,
    plannedAt,
    coordinationGc,
  });
  await reserveCoordinationInventoryObservation({
    coordinationStore,
    acquired,
    clock,
    plannedAt,
    eventIdentitySha256,
    inventoryNonce,
    phase: "initial",
  });
  let usageBefore = await loadReleaseStorageS3Usage({ coordinationStore, asOf: plannedAt });
  const historyAt = await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage coordination history planning",
  );
  const earlyCoordinationHistory = planCoordinationHistory(
    usageBefore.inventoryEntries,
    historyAt,
    usageBefore.inventoryPageCount,
  );
  coordinationGc = await compactCoordinationHistory({
    coordinationStore,
    acquired,
    history: earlyCoordinationHistory,
    usageSnapshot: usageBefore,
    plannedAt,
    clock,
    finalizeBudgetBlock,
  });
  if (coordinationGc.deletedObjects > 0) {
    await reserveCoordinationInventoryObservation({
      coordinationStore,
      acquired,
      clock,
      plannedAt,
      eventIdentitySha256,
      inventoryNonce,
      phase: "post-history-gc",
    });
    usageBefore = await loadReleaseStorageS3Usage({ coordinationStore, asOf: plannedAt });
  }
  const listing = await listReleaseStorageS3Inventory({
    siteStore,
    occurrenceStore,
    async beforePage({ namespace, pageNumber }) {
      const { currentTime: reservationTime } = await assertCurrentPublicationLease(
        coordinationStore,
        acquired,
        {
          clock,
          plannedAt,
          label: "release storage inventory listing admission",
        },
      );
      const delta = inventoryReservationUsage(
        releaseStorageS3InventoryContract.listing.pageReservationUsage,
        "release storage inventory listing reservation",
      );
      await assertInventoryBudgetOrFinalize({
        snapshot: usageBefore,
        delta,
        phase: "listing",
        observedAt: reservationTime,
        finalizeBudgetBlock,
      });
      const reservation = await reserveReleaseStorageS3Usage({
        coordinationStore,
        kind: "inventory-list",
        operationSha256: sha256(canonicalBytes({
          contract: "verdify.lab-release-storage-inventory-list-page-identity",
          schemaVersion: 1,
          eventIdentitySha256,
          inventoryNonce,
          fencingToken: acquired.record.fencingToken,
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
      await assertCurrentPublicationLease(coordinationStore, acquired, {
        clock,
        plannedAt,
        label: "release storage inventory listing post-reservation",
      });
      await checkpoint(Object.freeze({
        phase: "after-inventory-list-page-reservation",
        namespace,
        pageNumber,
        reservationId: reservation.reservationId,
      }));
    },
  });
  const readPlan = planReleaseStorageS3InventoryReads(listing);
  const { currentTime: readReservationTime } = await assertCurrentPublicationLease(
    coordinationStore,
    acquired,
    {
      clock,
      plannedAt,
      label: "release storage inventory exact-read admission",
    },
  );
  const readDelta = inventoryReservationUsage(
    readPlan.usage,
    "release storage inventory exact-read reservation",
  );
  await assertInventoryBudgetOrFinalize({
    snapshot: usageBefore,
    delta: readDelta,
    phase: "exact-read",
    observedAt: readReservationTime,
    finalizeBudgetBlock,
  });
  const readReservation = await reserveReleaseStorageS3Usage({
    coordinationStore,
    kind: "inventory-read",
    operationSha256: sha256(canonicalBytes({
      contract: "verdify.lab-release-storage-inventory-read-identity",
      schemaVersion: 1,
      eventIdentitySha256,
      inventoryNonce,
      fencingToken: acquired.record.fencingToken,
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
  await assertCurrentPublicationLease(coordinationStore, acquired, {
    clock,
    plannedAt,
    label: "release storage inventory exact-read post-reservation",
  });
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
  const coordinationHistory = Object.freeze({
    candidates: Object.freeze([]),
    deletedBytes: 0,
    usageUpperBound: Object.freeze({
      writtenBytes: 0,
      deletedBytes: 0,
      egressBytes: 0,
      requests: 0,
    }),
    observationExtraUsage: Object.freeze({
      writtenBytes: 0,
      deletedBytes: 0,
      egressBytes: 0,
      requests: 0,
    }),
  });
  const preliminaryEffective = effectivePublication(
    publication,
    usageBefore.retainedBytes,
    coordinationHistory,
    coordinationHistory.candidates.length + 3,
  );
  const preliminaryPlan = planReleaseStorageSafety({
    snapshot: inventory,
    usageState: usageBefore.state,
    publication: preliminaryEffective,
    asOf: planAt,
  });
  const retainedJournalCount = 3
    + (2 * preliminaryPlan.document.deletions.length)
    + (coordinationHistory.candidates.length > 0 ? 1 : 0);
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
  acquired = await bindReleaseStorageS3LeasePlan({
    coordinationStore,
    acquired,
    planSha256: plan.sha256,
    clock,
    plannedAt,
  });
  await checkpoint(Object.freeze({
    phase: "after-plan-bound",
    planSha256: plan.sha256,
    fencingToken: acquired.record.fencingToken,
  }));
  if (plan.document.publication.decision === "block") {
    await assertCurrentPublicationLease(
      coordinationStore,
      acquired,
      {
        clock,
        plannedAt,
        label: "release storage blocked terminal fence check",
      },
    );
    await assertCurrentPublicationLease(
      coordinationStore,
      acquired,
      {
        clock,
        plannedAt,
        label: "release storage blocked terminal post-reservation fence check",
      },
    );
    const released = await releaseReleaseStorageS3Lease({
      coordinationStore,
      acquired,
      clock,
      plannedAt,
    });
    const releasedAt = released.releasedAt;
    const status = statusDocument({
      state: "blocked",
      observedAt: releasedAt,
      plan,
      usageSnapshot: usageBefore,
      fenceToken: acquired.record.fencingToken,
      leaseReleasedAt: releasedAt,
      gc: Object.freeze({
        deletedObjects: coordinationGc.deletedObjects,
        coordinationDeletedBytes: coordinationGc.deletedBytes,
      }),
    });
    await persistStatus(coordinationStore, status);
    return status;
  }
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
      fencingToken: acquired.record.fencingToken,
    })),
    createdAt: publicationTime,
    delta: publisherReservation(publication),
  });
  await assertCurrentPublicationLease(coordinationStore, acquired, {
    clock,
    plannedAt,
    label: "release storage publication fence check",
  });
  const published = validatePublisherResult(await publisher(Object.freeze({
    contract: "verdify.lab-release-storage-publication-authority",
    schemaVersion: 1,
    eventIdentitySha256,
    planSha256: plan.sha256,
    fencingToken: acquired.record.fencingToken,
    leaseId: acquired.record.leaseId,
    expiresAt: acquired.record.expiresAt,
  })), publication);
  await assertCurrentPublicationLease(coordinationStore, acquired, {
    clock,
    plannedAt,
    label: "release storage post-publication fence check",
  });
  await checkpoint(Object.freeze({ phase: "after-publisher", planSha256: plan.sha256 }));
  await assertCurrentPublicationLease(coordinationStore, acquired, {
    clock,
    plannedAt,
    label: "release storage terminal-status fence check",
  });
  const usageObservedAt = await currentForBudgetDay(
    clock,
    plannedAt,
    "release storage terminal usage observation",
  );
  await reserveCoordinationInventoryObservation({
    coordinationStore,
    acquired,
    clock,
    plannedAt,
    eventIdentitySha256,
    inventoryNonce,
    phase: "terminal",
  });
  const usageAfter = await loadReleaseStorageS3Usage({
    coordinationStore,
    asOf: usageObservedAt,
  });
  await assertCurrentPublicationLease(
    coordinationStore,
    acquired,
    {
      clock,
      plannedAt,
      label: "release storage terminal lease release",
    },
  );
  const released = await releaseReleaseStorageS3Lease({
    coordinationStore,
    acquired,
    clock,
    plannedAt,
  });
  const terminalReleasedAt = released.releasedAt;
  await checkpoint(Object.freeze({
    phase: "after-release",
    planSha256: plan.sha256,
    fencingToken: acquired.record.fencingToken,
  }));
  const status = statusDocument({
    state: "complete",
    observedAt: terminalReleasedAt,
    plan,
    usageSnapshot: usageAfter,
    fenceToken: acquired.record.fencingToken,
    leaseReleasedAt: terminalReleasedAt,
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
  activationGate: Object.freeze({
    status: "blocked",
    reason: "per-object-143-plus-2-format-exceeds-active-capacity",
    requestedFullPublicationsPerDay: 96,
    occurrenceObjectsPerPublication: 148,
    retainedSamplesAtStrict48Hours: 193,
    occurrencePayloadObjectsAtRecoveryHorizon: 28_564,
    occurrenceObjectsLowerBound: 32_887,
    combinedObjectsLowerBound: 63_946,
    maximumInventoryObjects: releaseStorageS3InventoryContract.maximumObjects,
    minimumWriteRequestsPerDay: 29_088,
    canonicalInventoryReadsPerDay: 212_736,
    requestsPerDayBudget: releaseStorageSafetyContract.budgets.requestsPerDay,
    prerequisite: "deterministic-occurrence-and-site-packs-with-selected-root-inventory",
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
    maximumAdmittedObjects: MAX_ADMITTED_COORDINATION_OBJECTS,
    cleanupHeadroomObjects: COORDINATION_CLEANUP_HEADROOM,
    maximumHistoryDeletionsPerRun: MAX_COORDINATION_HISTORY_DELETIONS_PER_RUN,
  }),
  finalizationUsage: COORDINATION_FINALIZATION_USAGE,
  coordinationInventoryUsage: Object.freeze(inventoryReservationUsage(
    COORDINATION_LIST_USAGE,
    "release storage coordination inventory contract",
  )),
  inventory: Object.freeze({
    listingPageUsage: releaseStorageS3InventoryContract.listing.pageReservationUsage,
    reservationJournalUsage: RESERVATION_JOURNAL_USAGE,
    checkpoint: releaseStorageS3InventoryContract.checkpoint,
  }),
  gcPreflightUsage: GC_PREFLIGHT_USAGE,
  gcMutationUsage: GC_MUTATION_USAGE,
  budgets: releaseStorageSafetyContract.budgets,
  activationGate: releaseStoragePassOneContract.activationGate,
  status: Object.freeze({
    contract: "verdify.lab-release-storage-coordinator-status",
    schemaVersion: 1,
    ordering: "monotonic-fencing-token",
    metricsTransition: "fail-closed-pending-before-status-before-terminal",
  }),
});
