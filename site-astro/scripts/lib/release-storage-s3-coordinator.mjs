import { createHash, randomUUID } from "node:crypto";

import {
  createReleaseStorageUsageState,
  executeReleaseStorageGcPlan,
  planReleaseStorageSafety,
  recordReleaseStorageUsage,
  releaseStorageSafetyContract,
} from "./release-storage-safety.mjs";
import { captureReleaseStorageS3Inventory } from "./release-storage-s3-inventory.mjs";
import { S3ObjectStore } from "./s3-object-store.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const SAFE_ID_RE = /^[A-Za-z0-9_-]{8,128}$/u;
const MAX_COORDINATION_OBJECTS = 25_000;
const MAX_RESERVATION_BYTES = 16 * 1024;
const MAX_FENCE_BYTES = 32 * 1024;
const MAX_STATUS_BYTES = 64 * 1024;
const MAX_METRICS_BYTES = 32 * 1024;
const MAX_CAS_ATTEMPTS = 8;
const MAX_LEASE_SECONDS = 15 * 60;
const MIN_LEASE_SECONDS = 60;
const ADAPTER_USAGE_LIMITS = releaseStorageSafetyContract.adapterOperation.usageLimits;

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
const RESERVATION_KINDS = new Set(["gc-preflight", "gc-mutation", "publication"]);

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
    key: `usage/${payload.day}/reservations/${reservationId}.json`,
  };
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
    || key !== `usage/${document.day}/reservations/${reservationId}.json`
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
    return Object.freeze({ reservationId: selected.reservationId, delta: structuredClone(delta) });
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
  return Object.freeze({ reservationId: selected.reservationId, delta: structuredClone(delta) });
}

async function coordinationInventory(store) {
  const entries = await store.listInventory("", { maximumObjects: MAX_COORDINATION_OBJECTS });
  for (const entry of entries) {
    if (
      entry.key === "fence.json"
      || entry.key === "status.json"
      || entry.key === "metrics/latest.prom"
      || /^usage\/\d{4}-\d{2}-\d{2}\/reservations\/[0-9a-f]{64}\.json$/u.test(entry.key)
      || /^gc\/confirmations\/[0-9a-f]{64}\/(?:site|occurrence)\/[0-9a-f]{64}\.json$/u.test(entry.key)
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
    const value = await readCanonical(
      coordinationStore,
      entry.key,
      MAX_RESERVATION_BYTES,
      "release storage usage reservation",
    );
    if (value.etag !== entry.etag || value.bytes.length !== entry.bytes) {
      throw new Error("release storage usage reservation changed during complete inventory");
    }
    const reservation = validateReservation(value.document, entry.key, asOf);
    state = recordReleaseStorageUsage(state, reservation.delta, asOf);
  }
  const retainedBytes = entries.reduce(
    (total, entry) => add(total, entry.bytes, "release storage coordination retained bytes"),
    0,
  );
  return { state, retainedBytes, reservationCount: entries.filter(({ key }) => key.startsWith(dayPrefix)).length };
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

function confirmationKey(planSha256, namespace, key) {
  return `gc/confirmations/${planSha256}/${namespace}/${sha256(Buffer.from(key))}.json`;
}

function currentLeaseMatches(document, expected) {
  return document.releasedAt === null
    && document.leaseId === expected.leaseId
    && document.fencingToken === expected.fencingToken
    && document.planSha256 === expected.planSha256
    && document.issuedAt === expected.issuedAt
    && document.expiresAt === expected.expiresAt;
}

function createS3GcAdapter({
  siteStore,
  occurrenceStore,
  coordinationStore,
  lease,
  planSha256,
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
            : "manifest";
      return adapterResult({
        sha256: keyDigest(request.key, kind),
        bytes: value.bytes,
        createdAt: value.lastModified,
      }, readDelta(1024));
    },
    async readDeletionConfirmation(request) {
      const key = confirmationKey(planSha256, request.namespace, request.key);
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
        createdAt: await mutationInstant(),
        delta: { ...GC_MUTATION_USAGE, deletedBytes: request.expected.bytes },
      });
      const fence = await readFence(coordinationStore, { missing: false });
      if (!currentLeaseMatches(fence.document, request.lease)) {
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
        confirmedAt: await mutationInstant(),
      };
      const committed = await putCanonicalAbsent(
        coordinationStore,
        confirmationKey(planSha256, request.namespace, request.key),
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

function effectivePublication(publication, coordinationRetainedBytes) {
  if (!exactKeys(publication, [
    "contract",
    "schemaVersion",
    "retainedBytesAdded",
    "writtenBytes",
    "egressBytes",
    "requests",
  ])) throw new Error("release storage publication estimate does not use the closed v1 shape");
  return {
    ...publication,
    retainedBytesAdded: add(
      add(publication.retainedBytesAdded, coordinationRetainedBytes, "release storage total retained bytes"),
      COORDINATION_FINALIZATION_USAGE.writtenBytes,
      "release storage finalization retained bytes",
    ),
    writtenBytes: add(
      publication.writtenBytes,
      COORDINATION_FINALIZATION_USAGE.writtenBytes,
      "release storage finalization written bytes",
    ),
    egressBytes: add(
      publication.egressBytes,
      COORDINATION_FINALIZATION_USAGE.egressBytes,
      "release storage finalization egress bytes",
    ),
    requests: add(
      publication.requests,
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

function statusDocument({ state, observedAt, plan, usageSnapshot, fenceToken = null, gc = null, publication = null }) {
  const thresholds = plan.document.thresholds.map(({ name, limit, status }) => ({ name, limit, status }));
  return {
    contract: "verdify.lab-release-storage-coordinator-status",
    schemaVersion: 1,
    state,
    observedAt,
    planSha256: plan.sha256,
    inventorySha256: plan.document.snapshotSha256,
    fencingToken: fenceToken,
    publicationDecision: plan.document.publication.decision,
    publicationReasons: [...plan.document.publication.reasons],
    preservesLastKnownGood: true,
    retainedBytes: plan.document.accounting.observedRetainedBytes,
    plannedDeletedBytes: plan.document.accounting.plannedDeletedBytes,
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
}) {
  digest(eventIdentitySha256, "release storage event identity digest");
  if (!(coordinationStore instanceof S3ObjectStore) || coordinationStore.accessMode !== "writer") {
    throw new Error("release storage coordinator requires an S3 coordination writer");
  }
  if (typeof publisher !== "function" || typeof checkpoint !== "function") {
    throw new Error("release storage coordinator operations are invalid");
  }
  const plannedAt = await currentFrom(clock);
  const inventory = await captureReleaseStorageS3Inventory({
    siteStore,
    occurrenceStore,
    capturedAt: plannedAt,
  });
  const usageBefore = await loadReleaseStorageS3Usage({ coordinationStore, asOf: plannedAt });
  const effective = effectivePublication(publication, usageBefore.retainedBytes);
  const plan = planReleaseStorageSafety({
    snapshot: inventory,
    usageState: usageBefore.state,
    publication: effective,
    asOf: plannedAt,
  });
  if (plan.document.publication.decision === "block") {
    return statusDocument({
      state: "blocked",
      observedAt: plannedAt,
      plan,
      usageSnapshot: usageBefore,
    });
  }
  const acquired = await acquireReleaseStorageS3Lease({
    coordinationStore,
    planSha256: plan.sha256,
    ownerIdentity,
    issuedAt: plannedAt,
    leaseSeconds,
    nonce: leaseNonce,
  });
  const gcReservationTime = await currentFrom(clock);
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
    delta: GC_PREFLIGHT_USAGE,
  });
  const mutationInstant = () => currentFrom(clock);
  const gc = await executeReleaseStorageGcPlan({
    plan,
    adapter: createS3GcAdapter({
      siteStore,
      occurrenceStore,
      coordinationStore,
      lease: acquired.record,
      planSha256: plan.sha256,
      mutationInstant,
    }),
    lease: acquired.lease,
    currentInstant: async () => ({
      contract: "verdify.lab-current-instant",
      schemaVersion: 1,
      instant: await currentFrom(clock),
    }),
    progress: null,
  });
  await checkpoint(Object.freeze({ phase: "after-gc", planSha256: plan.sha256 }));
  const publicationTime = await currentFrom(clock);
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
  const published = validatePublisherResult(await publisher(Object.freeze({
    contract: "verdify.lab-release-storage-publication-authority",
    schemaVersion: 1,
    eventIdentitySha256,
    planSha256: plan.sha256,
    fencingToken: acquired.record.fencingToken,
    leaseId: acquired.record.leaseId,
    expiresAt: acquired.record.expiresAt,
  })), publication);
  await checkpoint(Object.freeze({ phase: "after-publisher", planSha256: plan.sha256 }));
  const observedAt = await currentFrom(clock);
  const usageAfter = await loadReleaseStorageS3Usage({ coordinationStore, asOf: observedAt });
  const status = statusDocument({
    state: "complete",
    observedAt,
    plan,
    usageSnapshot: usageAfter,
    fenceToken: acquired.record.fencingToken,
    gc,
    publication: published,
  });
  await persistStatus(coordinationStore, status);
  await releaseReleaseStorageS3Lease({
    coordinationStore,
    acquired,
    releasedAt: await currentFrom(clock),
  });
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
    claim: "bounded-create-read-head-delete",
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
  finalizationUsage: COORDINATION_FINALIZATION_USAGE,
  gcPreflightUsage: GC_PREFLIGHT_USAGE,
  gcMutationUsage: GC_MUTATION_USAGE,
  budgets: releaseStorageSafetyContract.budgets,
  status: Object.freeze({ contract: "verdify.lab-release-storage-coordinator-status", schemaVersion: 1 }),
});
