import { createHash } from "node:crypto";

const GIB = 1024 ** 3;
const SHA256_RE = /^[0-9a-f]{64}$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/u;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/u;
const MEDIA_OCCURRENCE_ID_RE = /^media_[0-9a-f]{24}$/u;
const SAFE_KEY_RE = /^[A-Za-z0-9._/-]{1,1024}$/u;
const MAX_OBJECTS = 1_000_000;
const MAX_SELECTORS = 10_000;
const RECOVERY_GRACE_MS = 48 * 60 * 60 * 1000;
const EVENT_IDEMPOTENCY_HORIZON_MS = 14 * 24 * 60 * 60 * 1000;
const OPERATION_USAGE_LIMITS = Object.freeze({
  writtenBytes: 64 * 1024,
  deletedBytes: 0,
  egressBytes: 1024 * 1024,
  requests: 16,
});

const BUDGETS = Object.freeze({
  retainedBytes: 10 * GIB,
  writtenBytesPerDay: 5 * GIB,
  egressBytesPerDay: 10 * GIB,
  requestsPerDay: 25_000,
  warningFraction: 0.8,
});

const GC_KINDS = new Set(["release", "manifest", "generation", "blob", "event", "checkpoint"]);
const ALL_KINDS = new Set(GC_KINDS);

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

function safeInteger(value, label, { minimum = 0, maximum = Number.MAX_SAFE_INTEGER } = {}) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function addIntegers(left, right, label) {
  return safeInteger(left + right, label);
}

function sumIntegers(values, label) {
  return values.reduce((total, value) => addIntegers(total, value, label), 0);
}

function safeText(value, label, maximum = 1024) {
  if (
    typeof value !== "string"
    || value.length < 1
    || value.length > maximum
    || /[\u0000-\u001f\u007f]/u.test(value)
  ) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function instant(value, label) {
  safeText(value, label, 32);
  const parsed = Date.parse(value);
  if (
    !ISO_INSTANT_RE.test(value)
    || !Number.isFinite(parsed)
    || new Date(parsed).toISOString() !== value
  ) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function digest(value, label) {
  if (typeof value !== "string" || !SHA256_RE.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function namespace(value) {
  if (!["site", "occurrence"].includes(value)) {
    throw new Error("release storage namespace is invalid");
  }
  return value;
}

function objectIdentity(namespaceValue, key) {
  return `${namespaceValue}\u0000${key}`;
}

function objectKey(value, namespaceValue, kind, label = "release storage object key") {
  safeText(value, label);
  if (
    !SAFE_KEY_RE.test(value)
    || value.startsWith("/")
    || value.includes("//")
    || value.split("/").includes("..")
  ) {
    throw new Error(`${label} is invalid`);
  }
  const keyedDigest = kind === "blob"
    ? /^blobs\/sha256\/([0-9a-f]{64})$/u.exec(value)
    : kind === "release"
      ? /^releases\/sha256\/([0-9a-f]{64})\.json$/u.exec(value)
      : kind === "manifest"
        ? /^manifests\/sha256\/([0-9a-f]{64})\.json$/u.exec(value)
        : kind === "generation"
          ? /^occurrences\/(media_[0-9a-f]{24})\/generations\/sha256\/([0-9a-f]{64})\.json$/u.exec(value)
          : kind === "event" && namespaceValue === "site"
            ? /^events\/sha256\/([0-9a-f]{64})\.json$/u.exec(value)
            : kind === "checkpoint" && namespaceValue === "site"
              ? /^checkpoints\/sha256\/([0-9a-f]{64})\.json$/u.exec(value)
              : kind === "event"
                ? /^(?:events\/sha256\/([0-9a-f]{64})|occurrences\/media_[0-9a-f]{24}\/events\/sha256\/([0-9a-f]{64}))\.json$/u.exec(value)
                : null;
  if (keyedDigest === null) throw new Error(`${label} does not match its object kind`);
  if (namespaceValue === "site" && !["release", "blob", "event", "checkpoint"].includes(kind)) {
    throw new Error("site release storage object kind is invalid");
  }
  if (namespaceValue === "occurrence" && !["manifest", "generation", "blob", "event"].includes(kind)) {
    throw new Error("occurrence release storage object kind is invalid");
  }
  return keyedDigest;
}

function matchedObjectDigest(match, kind) {
  if (kind === "generation") return match[2];
  if (kind === "event" && match[1] === undefined) return match[2];
  return match[1];
}

function selectorKey(value, selectorKind, occurrenceId) {
  safeText(value, "release storage selector key");
  const expected = selectorKind === "site" || selectorKind === "aggregate"
    ? "selection.json"
    : `occurrences/${occurrenceId}/selection.json`;
  if (value !== expected) throw new Error("release storage selector key is not canonical");
  return value;
}

function validateObject(raw) {
  if (!exactKeys(raw, ["namespace", "key", "kind", "sha256", "bytes", "createdAt", "references"])) {
    throw new Error("release storage object does not use the closed v1 inventory shape");
  }
  const namespaceValue = namespace(raw.namespace);
  if (!ALL_KINDS.has(raw.kind)) throw new Error("release storage object kind is invalid");
  const keyMatch = objectKey(raw.key, namespaceValue, raw.kind);
  digest(raw.sha256, "release storage object digest");
  if (matchedObjectDigest(keyMatch, raw.kind) !== raw.sha256) {
    throw new Error("release storage object key and digest differ");
  }
  safeInteger(raw.bytes, "release storage object byte count", { minimum: 1 });
  instant(raw.createdAt, "release storage object creation time");
  if (!Array.isArray(raw.references) || raw.references.length > MAX_OBJECTS) {
    throw new Error("release storage object references are invalid");
  }
  let prior = "";
  for (const reference of raw.references) {
    safeText(reference, "release storage object reference");
    if (!SAFE_KEY_RE.test(reference) || reference <= prior) {
      throw new Error("release storage object references are not strictly sorted");
    }
    prior = reference;
  }
  if (raw.kind === "blob" && raw.references.length !== 0) {
    throw new Error("release storage blob cannot reference another object");
  }
  if (["event", "checkpoint"].includes(raw.kind) && raw.references.length !== 0) {
    throw new Error("release storage idempotency tombstone cannot retain immutable payload objects");
  }
  return raw;
}

function validateSelector(raw) {
  if (!exactKeys(raw, [
    "namespace",
    "selectorKind",
    "occurrenceId",
    "key",
    "sha256",
    "etag",
    "bytes",
    "currentKey",
    "rollbackKey",
  ])) throw new Error("release storage selector does not use the closed v1 inventory shape");
  const namespaceValue = namespace(raw.namespace);
  if (!["site", "aggregate", "current-media"].includes(raw.selectorKind)) {
    throw new Error("release storage selector kind is invalid");
  }
  if (
    (raw.selectorKind === "site") !== (namespaceValue === "site")
    || (raw.selectorKind !== "site") !== (namespaceValue === "occurrence")
  ) throw new Error("release storage selector namespace is invalid");
  if (raw.selectorKind === "current-media") {
    if (typeof raw.occurrenceId !== "string" || !MEDIA_OCCURRENCE_ID_RE.test(raw.occurrenceId)) {
      throw new Error("release storage current-media selector identity is invalid");
    }
  } else if (raw.occurrenceId !== null) {
    throw new Error("release storage non-media selector has an occurrence identity");
  }
  selectorKey(raw.key, raw.selectorKind, raw.occurrenceId);
  digest(raw.sha256, "release storage selector digest");
  safeText(raw.etag, "release storage selector ETag", 512);
  safeInteger(raw.bytes, "release storage selector byte count", { minimum: 1 });
  safeText(raw.currentKey, "release storage current selector target");
  if (raw.rollbackKey !== null) safeText(raw.rollbackKey, "release storage rollback selector target");
  if (raw.currentKey === raw.rollbackKey) throw new Error("release storage selector targets are identical");
  return raw;
}

function targetKind(selectorKind) {
  if (selectorKind === "site") return "release";
  if (selectorKind === "aggregate") return "manifest";
  return "generation";
}

function validateSnapshot(snapshot) {
  if (!exactKeys(snapshot, ["contract", "schemaVersion", "capturedAt", "listings", "selectors", "objects"])) {
    throw new Error("release storage inventory does not use the closed v1 contract");
  }
  if (
    snapshot.contract !== "verdify.lab-release-storage-inventory"
    || snapshot.schemaVersion !== 1
  ) throw new Error("release storage inventory does not use the closed v1 contract");
  instant(snapshot.capturedAt, "release storage inventory capture time");
  if (!exactKeys(snapshot.listings, ["site", "occurrence"])) {
    throw new Error("release storage listing status is invalid");
  }
  for (const name of ["site", "occurrence"]) {
    const listing = snapshot.listings[name];
    if (
      !exactKeys(listing, ["complete", "continuationToken"])
      || typeof listing.complete !== "boolean"
      || (listing.complete ? listing.continuationToken !== null : typeof listing.continuationToken !== "string")
    ) throw new Error("release storage listing status is invalid");
    if (!listing.complete) safeText(listing.continuationToken, "release storage listing continuation token", 1024);
  }
  if (!Array.isArray(snapshot.selectors) || snapshot.selectors.length < 2 || snapshot.selectors.length > MAX_SELECTORS) {
    throw new Error("release storage selector inventory is invalid");
  }
  if (!Array.isArray(snapshot.objects) || snapshot.objects.length > MAX_OBJECTS) {
    throw new Error("release storage immutable object inventory is invalid");
  }
  const objects = new Map();
  let priorObject = "";
  for (const raw of snapshot.objects) {
    const object = validateObject(raw);
    const identity = objectIdentity(object.namespace, object.key);
    if (identity <= priorObject || objects.has(identity)) {
      throw new Error("release storage objects are not strictly sorted");
    }
    priorObject = identity;
    objects.set(identity, object);
  }
  const selectors = [];
  const selectorIds = new Set();
  let siteSelectors = 0;
  let aggregateSelectors = 0;
  const mediaSelectors = new Set();
  let priorSelector = "";
  for (const raw of snapshot.selectors) {
    const selector = validateSelector(raw);
    const identity = objectIdentity(selector.namespace, selector.key);
    if (identity <= priorSelector || selectorIds.has(identity)) {
      throw new Error("release storage selectors are not strictly sorted");
    }
    priorSelector = identity;
    selectorIds.add(identity);
    if (selector.selectorKind === "site") siteSelectors += 1;
    if (selector.selectorKind === "aggregate") aggregateSelectors += 1;
    if (selector.selectorKind === "current-media") mediaSelectors.add(selector.occurrenceId);
    for (const key of [selector.currentKey, selector.rollbackKey].filter(Boolean)) {
      const target = objects.get(objectIdentity(selector.namespace, key));
      if (target?.kind !== targetKind(selector.selectorKind)) {
        throw new Error("release storage selector target is missing or has the wrong kind");
      }
      if (
        selector.selectorKind === "current-media"
        && !key.startsWith(`occurrences/${selector.occurrenceId}/generations/`)
      ) throw new Error("current-media selector targets another occurrence");
    }
    selectors.push(selector);
  }
  if (siteSelectors !== 1 || aggregateSelectors !== 1) {
    throw new Error("release storage inventory requires one site and one aggregate selector");
  }
  for (const object of objects.values()) {
    if (object.kind === "generation") {
      const occurrenceId = object.key.split("/")[1];
      if (!mediaSelectors.has(occurrenceId)) {
        throw new Error("occurrence generation lacks its complete current-media selector inventory");
      }
    }
    for (const reference of object.references) {
      const target = objects.get(objectIdentity(object.namespace, reference));
      if (target === undefined) throw new Error("release storage object reference is missing");
      if (
        object.kind === "release" && target.kind !== "blob"
        || object.kind === "generation" && target.kind !== "blob"
        || object.kind === "manifest" && !["blob", "generation"].includes(target.kind)
      ) throw new Error("release storage object reference has the wrong kind");
    }
  }
  return { objects, selectors };
}

function validatePublication(publication) {
  if (!exactKeys(publication, [
    "contract",
    "schemaVersion",
    "retainedBytesAdded",
    "writtenBytes",
    "egressBytes",
    "requests",
  ]) || publication.contract !== "verdify.lab-release-storage-publication-estimate" || publication.schemaVersion !== 1) {
    throw new Error("release storage publication estimate does not use the closed v1 contract");
  }
  for (const [key, label] of [
    ["retainedBytesAdded", "publication retained byte count"],
    ["writtenBytes", "publication written byte count"],
    ["egressBytes", "publication egress byte count"],
    ["requests", "publication request count"],
  ]) safeInteger(publication[key], label);
  return publication;
}

function emptyCounters() {
  return { writtenBytes: 0, deletedBytes: 0, egressBytes: 0, requests: 0 };
}

function validateCounters(counters) {
  if (!exactKeys(counters, ["writtenBytes", "deletedBytes", "egressBytes", "requests"])) {
    throw new Error("release storage usage counters are invalid");
  }
  for (const [key, value] of Object.entries(counters)) {
    safeInteger(value, `release storage ${key} counter`);
  }
  return counters;
}

function validateUsageState(state) {
  if (!exactKeys(state, ["contract", "schemaVersion", "day", "updatedAt", "counters"])) {
    throw new Error("release storage usage state does not use the closed v1 contract");
  }
  if (
    state.contract !== "verdify.lab-release-storage-usage-state"
    || state.schemaVersion !== 1
    || typeof state.day !== "string"
    || !DAY_RE.test(state.day)
  ) throw new Error("release storage usage state does not use the closed v1 contract");
  instant(state.updatedAt, "release storage usage state update time");
  if (state.updatedAt.slice(0, 10) !== state.day) {
    throw new Error("release storage usage state day does not match its update time");
  }
  validateCounters(state.counters);
  return state;
}

function stateAsOf(state, asOf) {
  validateUsageState(state);
  instant(asOf, "release storage planning time");
  if (Date.parse(state.updatedAt) > Date.parse(asOf)) {
    throw new Error("release storage usage state is from the future");
  }
  if (state.day !== asOf.slice(0, 10)) {
    return {
      contract: state.contract,
      schemaVersion: state.schemaVersion,
      day: asOf.slice(0, 10),
      updatedAt: asOf,
      counters: emptyCounters(),
    };
  }
  return structuredClone(state);
}

export function createReleaseStorageUsageState(asOf) {
  instant(asOf, "release storage usage state creation time");
  return {
    contract: "verdify.lab-release-storage-usage-state",
    schemaVersion: 1,
    day: asOf.slice(0, 10),
    updatedAt: asOf,
    counters: emptyCounters(),
  };
}

export function serializeReleaseStorageUsageState(state) {
  validateUsageState(state);
  return canonicalBytes(state);
}

export function parseReleaseStorageUsageState(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 1 || bytes.length > 64 * 1024) {
    throw new Error("release storage usage state bytes are invalid");
  }
  let state;
  try {
    state = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("release storage usage state is not valid JSON");
  }
  validateUsageState(state);
  if (!canonicalBytes(state).equals(bytes)) throw new Error("release storage usage state is not canonical JSON");
  return state;
}

export function recordReleaseStorageUsage(state, delta, asOf) {
  if (!exactKeys(delta, ["writtenBytes", "deletedBytes", "egressBytes", "requests"])) {
    throw new Error("release storage usage delta is invalid");
  }
  validateCounters(delta);
  const current = stateAsOf(state, asOf);
  const counters = {};
  for (const key of Object.keys(current.counters)) {
    counters[key] = safeInteger(
      current.counters[key] + delta[key],
      `release storage accumulated ${key}`,
    );
  }
  return {
    ...current,
    updatedAt: asOf,
    counters,
  };
}

function retainedClosure(objects, roots) {
  const retained = new Set();
  const visiting = new Set();
  function visit(identity) {
    if (retained.has(identity)) return;
    if (visiting.has(identity)) throw new Error("release storage reference graph contains a cycle");
    const object = objects.get(identity);
    if (object === undefined) throw new Error("release storage retained object is missing");
    visiting.add(identity);
    for (const reference of object.references) {
      visit(objectIdentity(object.namespace, reference));
    }
    visiting.delete(identity);
    retained.add(identity);
  }
  for (const root of roots) visit(root);
  return retained;
}

function deletionOrder(candidates, objects) {
  const remainingReferences = new Map();
  const referencedBy = new Map();
  for (const identity of candidates) {
    const object = objects.get(identity);
    const references = object.references
      .map((key) => objectIdentity(object.namespace, key))
      .filter((target) => candidates.has(target));
    remainingReferences.set(identity, new Set(references));
    for (const target of references) {
      const parents = referencedBy.get(target) ?? new Set();
      parents.add(identity);
      referencedBy.set(target, parents);
    }
  }
  const ready = [...candidates].filter((identity) => !referencedBy.has(identity)).sort();
  const result = [];
  while (ready.length > 0) {
    const identity = ready.shift();
    result.push(identity);
    for (const target of remainingReferences.get(identity)) {
      const parents = referencedBy.get(target);
      parents.delete(identity);
      if (parents.size === 0) {
        referencedBy.delete(target);
        ready.push(target);
        ready.sort();
      }
    }
  }
  if (result.length !== candidates.size) throw new Error("release storage deletion graph contains a cycle");
  return result;
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

function multiplyIntegers(left, right, label) {
  return safeInteger(left * right, label);
}

function operationUsageUpperBound(operationCount, deletedBytes) {
  safeInteger(operationCount, "release storage GC operation count");
  safeInteger(deletedBytes, "release storage GC upper-bound deleted bytes");
  return {
    writtenBytes: multiplyIntegers(
      operationCount,
      OPERATION_USAGE_LIMITS.writtenBytes,
      "release storage GC upper-bound written bytes",
    ),
    deletedBytes,
    egressBytes: multiplyIntegers(
      operationCount,
      OPERATION_USAGE_LIMITS.egressBytes,
      "release storage GC upper-bound egress bytes",
    ),
    requests: multiplyIntegers(
      operationCount,
      OPERATION_USAGE_LIMITS.requests,
      "release storage GC upper-bound requests",
    ),
  };
}

function budgetEvaluation(projected, { inventoryComplete, gcComplete }) {
  const thresholds = [
    threshold("retainedBytes", projected.retainedBytes, BUDGETS.retainedBytes),
    threshold("writtenBytesPerDay", projected.writtenBytesPerDay, BUDGETS.writtenBytesPerDay),
    threshold("egressBytesPerDay", projected.egressBytesPerDay, BUDGETS.egressBytesPerDay),
    threshold("requestsPerDay", projected.requestsPerDay, BUDGETS.requestsPerDay),
  ];
  const reasons = [];
  if (!inventoryComplete) reasons.push("incomplete-listing");
  if (!gcComplete) reasons.push("gc-incomplete");
  reasons.push(...thresholds.filter((item) => item.status === "block").map((item) => `${item.name}-budget`));
  const decision = reasons.length > 0
    ? "block"
    : thresholds.some((item) => item.status === "warn") ? "warn" : "allow";
  if (decision === "warn") {
    reasons.push(...thresholds.filter((item) => item.status === "warn").map((item) => `${item.name}-warning`));
  }
  return {
    projected,
    thresholds,
    publication: {
      decision,
      reasons,
      preservesLastKnownGood: true,
    },
  };
}

function selectorPrecondition(selector) {
  return {
    namespace: selector.namespace,
    key: selector.key,
    sha256: selector.sha256,
    etag: selector.etag,
  };
}

export function planReleaseStorageSafety(input) {
  if (!exactKeys(input, ["snapshot", "usageState", "publication", "asOf"])) {
    throw new Error("release storage planning input does not use the closed v1 shape");
  }
  const { snapshot, usageState, publication, asOf } = input;
  instant(asOf, "release storage planning time");
  if (Date.parse(snapshot?.capturedAt ?? "") > Date.parse(asOf)) {
    throw new Error("release storage inventory is from the future");
  }
  const { objects, selectors } = validateSnapshot(snapshot);
  const estimate = validatePublication(publication);
  const state = stateAsOf(usageState, asOf);
  const complete = snapshot.listings.site.complete && snapshot.listings.occurrence.complete;
  const roots = [];
  for (const selector of selectors) {
    roots.push(objectIdentity(selector.namespace, selector.currentKey));
    if (selector.rollbackKey !== null) roots.push(objectIdentity(selector.namespace, selector.rollbackKey));
  }
  for (const [identity, object] of objects) {
    const age = Date.parse(asOf) - Date.parse(object.createdAt);
    if (age < 0) throw new Error("release storage object is from the future");
    const retentionHorizon = ["event", "checkpoint"].includes(object.kind)
      ? EVENT_IDEMPOTENCY_HORIZON_MS
      : RECOVERY_GRACE_MS;
    if (age <= retentionHorizon) roots.push(identity);
  }
  const retained = retainedClosure(objects, roots);
  const candidateIdentities = new Set();
  if (complete) {
    for (const [identity, object] of objects) {
      if (GC_KINDS.has(object.kind) && !retained.has(identity)) candidateIdentities.add(identity);
    }
  }
  const ordered = deletionOrder(candidateIdentities, objects);
  const deletions = ordered.map((identity) => {
    const object = objects.get(identity);
    return {
      namespace: object.namespace,
      key: object.key,
      kind: object.kind,
      sha256: object.sha256,
      bytes: object.bytes,
      createdAt: object.createdAt,
    };
  });
  const selectorBytes = sumIntegers(selectors.map((selector) => selector.bytes), "release storage selector bytes");
  const objectBytes = sumIntegers([...objects.values()].map((object) => object.bytes), "release storage object bytes");
  const observedRetainedBytes = addIntegers(selectorBytes, objectBytes, "release storage retained bytes");
  const plannedDeletedBytes = sumIntegers(
    deletions.map((object) => object.bytes),
    "release storage planned deleted bytes",
  );
  const retainedBytesAfterGc = observedRetainedBytes - plannedDeletedBytes;
  const operationCount = deletions.length === 0
    ? 1
    : safeInteger(1 + selectors.length + (3 * deletions.length), "release storage planned GC operations");
  const gcUsageUpperBound = operationUsageUpperBound(operationCount, plannedDeletedBytes);
  const projected = {
    retainedBytes: addIntegers(
      retainedBytesAfterGc,
      estimate.retainedBytesAdded,
      "release storage projected retained bytes",
    ),
    writtenBytesPerDay: addIntegers(
      addIntegers(state.counters.writtenBytes, estimate.writtenBytes, "release storage projected publication writes"),
      gcUsageUpperBound.writtenBytes,
      "release storage projected written bytes",
    ),
    egressBytesPerDay: addIntegers(
      addIntegers(state.counters.egressBytes, estimate.egressBytes, "release storage projected publication egress"),
      gcUsageUpperBound.egressBytes,
      "release storage projected egress bytes",
    ),
    requestsPerDay: addIntegers(
      addIntegers(state.counters.requests, estimate.requests, "release storage projected publication requests"),
      gcUsageUpperBound.requests,
      "release storage projected requests",
    ),
  };
  const budget = budgetEvaluation(projected, { inventoryComplete: complete, gcComplete: true });
  const document = {
    contract: "verdify.lab-release-storage-gc-plan",
    schemaVersion: 1,
    plannedAt: asOf,
    snapshotSha256: sha256(canonicalBytes(snapshot)),
    budgetsSha256: sha256(canonicalBytes(BUDGETS)),
    recoveryGraceSeconds: RECOVERY_GRACE_MS / 1000,
    eventIdempotencyHorizonSeconds: EVENT_IDEMPOTENCY_HORIZON_MS / 1000,
    selectors: selectors.map(selectorPrecondition),
    deletions,
    accounting: {
      inventoryComplete: complete,
      observedRetainedBytes,
      retainedBytesAfterGc,
      plannedDeletedBytes,
      currentDay: state.day,
      writtenBytesToday: state.counters.writtenBytes,
      deletedBytesToday: state.counters.deletedBytes,
      egressBytesToday: state.counters.egressBytes,
      requestsToday: state.counters.requests,
      publicationEstimate: {
        retainedBytesAdded: estimate.retainedBytesAdded,
        writtenBytes: estimate.writtenBytes,
        egressBytes: estimate.egressBytes,
        requests: estimate.requests,
      },
      gcUsageUpperBound,
      projected: budget.projected,
    },
    thresholds: budget.thresholds,
    publication: budget.publication,
  };
  return Object.freeze({ document, sha256: sha256(canonicalBytes(document)) });
}

function validatePlan(plan) {
  if (!exactKeys(plan, ["document", "sha256"]) || !exactKeys(plan.document, [
    "contract",
    "schemaVersion",
    "plannedAt",
    "snapshotSha256",
    "budgetsSha256",
    "recoveryGraceSeconds",
    "eventIdempotencyHorizonSeconds",
    "selectors",
    "deletions",
    "accounting",
    "thresholds",
    "publication",
  ])) throw new Error("release storage GC plan is invalid");
  const document = plan.document;
  if (
    document.contract !== "verdify.lab-release-storage-gc-plan"
    || document.schemaVersion !== 1
    || document.budgetsSha256 !== sha256(canonicalBytes(BUDGETS))
    || document.recoveryGraceSeconds !== RECOVERY_GRACE_MS / 1000
    || document.eventIdempotencyHorizonSeconds !== EVENT_IDEMPOTENCY_HORIZON_MS / 1000
    || sha256(canonicalBytes(document)) !== plan.sha256
  ) throw new Error("release storage GC plan identity is invalid");
  instant(document.plannedAt, "release storage GC plan time");
  digest(document.snapshotSha256, "release storage GC inventory digest");
  if (
    !Array.isArray(document.selectors)
    || !Array.isArray(document.deletions)
    || document.deletions.length > MAX_OBJECTS
  ) {
    throw new Error("release storage GC plan membership is invalid");
  }
  if (document.selectors.length < 2 || document.selectors.length > MAX_SELECTORS) {
    throw new Error("release storage GC selector preconditions are incomplete");
  }
  const selectorIds = new Set();
  let siteSelectors = 0;
  let aggregateSelectors = 0;
  let priorSelector = "";
  for (const selector of document.selectors) {
    if (!exactKeys(selector, ["namespace", "key", "sha256", "etag"])) {
      throw new Error("release storage GC selector precondition is invalid");
    }
    namespace(selector.namespace);
    safeText(selector.key, "release storage GC selector key");
    if (selector.namespace === "site") {
      if (selector.key !== "selection.json") throw new Error("release storage GC site selector key is invalid");
      siteSelectors += 1;
    } else if (selector.key === "selection.json") {
      aggregateSelectors += 1;
    } else if (!/^occurrences\/media_[0-9a-f]{24}\/selection\.json$/u.test(selector.key)) {
      throw new Error("release storage GC occurrence selector key is invalid");
    }
    digest(selector.sha256, "release storage GC selector digest");
    safeText(selector.etag, "release storage GC selector ETag", 512);
    const identity = objectIdentity(selector.namespace, selector.key);
    if (identity <= priorSelector || selectorIds.has(identity)) {
      throw new Error("release storage GC selector preconditions are not strictly sorted");
    }
    priorSelector = identity;
    selectorIds.add(identity);
  }
  if (siteSelectors !== 1 || aggregateSelectors !== 1) {
    throw new Error("release storage GC selector preconditions are incomplete");
  }
  let deletedBytes = 0;
  const deletionIds = new Set();
  for (const deletion of document.deletions) {
    if (!exactKeys(deletion, ["namespace", "key", "kind", "sha256", "bytes", "createdAt"])) {
      throw new Error("release storage GC deletion record is invalid");
    }
    const namespaceValue = namespace(deletion.namespace);
    if (!GC_KINDS.has(deletion.kind)) throw new Error("release storage GC deletion kind is invalid");
    const match = objectKey(deletion.key, namespaceValue, deletion.kind, "release storage GC deletion key");
    digest(deletion.sha256, "release storage GC deletion digest");
    const keyed = matchedObjectDigest(match, deletion.kind);
    if (keyed !== deletion.sha256) throw new Error("release storage GC deletion key and digest differ");
    const identity = objectIdentity(namespaceValue, deletion.key);
    if (deletionIds.has(identity)) throw new Error("release storage GC deletion is duplicated");
    deletionIds.add(identity);
    deletedBytes = addIntegers(
      deletedBytes,
      safeInteger(deletion.bytes, "release storage GC deletion byte count", { minimum: 1 }),
      "release storage GC deletion bytes",
    );
    instant(deletion.createdAt, "release storage GC deletion creation time");
    const deletionHorizon = deletion.kind === "event"
      ? EVENT_IDEMPOTENCY_HORIZON_MS
      : RECOVERY_GRACE_MS;
    if (Date.parse(document.plannedAt) - Date.parse(deletion.createdAt) <= deletionHorizon) {
      throw new Error("release storage GC plan deletes an object inside recovery grace");
    }
  }
  if (!exactKeys(document.accounting, [
    "inventoryComplete",
    "observedRetainedBytes",
    "retainedBytesAfterGc",
    "plannedDeletedBytes",
    "currentDay",
    "writtenBytesToday",
    "deletedBytesToday",
    "egressBytesToday",
    "requestsToday",
    "publicationEstimate",
    "gcUsageUpperBound",
    "projected",
  ]) || typeof document.accounting.inventoryComplete !== "boolean") {
    throw new Error("release storage GC accounting is invalid");
  }
  for (const key of [
    "observedRetainedBytes",
    "retainedBytesAfterGc",
    "plannedDeletedBytes",
    "writtenBytesToday",
    "deletedBytesToday",
    "egressBytesToday",
    "requestsToday",
  ]) safeInteger(document.accounting[key], `release storage GC ${key}`);
  if (typeof document.accounting.currentDay !== "string" || !DAY_RE.test(document.accounting.currentDay)) {
    throw new Error("release storage GC accounting day is invalid");
  }
  if (document.accounting.currentDay !== document.plannedAt.slice(0, 10)) {
    throw new Error("release storage GC accounting day differs from its plan time");
  }
  if (!document.accounting.inventoryComplete && document.deletions.length !== 0) {
    throw new Error("incomplete release storage inventory cannot authorize deletion");
  }
  const expectedOperationCount = document.deletions.length === 0
    ? 1
    : 1 + document.selectors.length + (3 * document.deletions.length);
  const expectedUpperBound = operationUsageUpperBound(expectedOperationCount, deletedBytes);
  if (
    !exactKeys(document.accounting.publicationEstimate, [
      "retainedBytesAdded",
      "writtenBytes",
      "egressBytes",
      "requests",
    ])
    || !exactKeys(document.accounting.gcUsageUpperBound, [
      "writtenBytes",
      "deletedBytes",
      "egressBytes",
      "requests",
    ])
  ) throw new Error("release storage GC resource bounds are invalid");
  for (const [key, value] of Object.entries(document.accounting.publicationEstimate)) {
    safeInteger(value, `release storage GC publication estimate ${key}`);
  }
  if (
    deletedBytes !== document.accounting.plannedDeletedBytes
    || document.accounting.observedRetainedBytes - deletedBytes !== document.accounting.retainedBytesAfterGc
    || JSON.stringify(document.accounting.gcUsageUpperBound) !== JSON.stringify(expectedUpperBound)
  ) {
    throw new Error("release storage GC deletion accounting differs from its plan");
  }
  if (!exactKeys(document.accounting.projected, [
    "retainedBytes",
    "writtenBytesPerDay",
    "egressBytesPerDay",
    "requestsPerDay",
  ])) throw new Error("release storage GC projected accounting is invalid");
  for (const [key, value] of Object.entries(document.accounting.projected)) {
    safeInteger(value, `release storage GC projected ${key}`);
  }
  const expectedProjected = {
    retainedBytes: addIntegers(
      document.accounting.retainedBytesAfterGc,
      document.accounting.publicationEstimate.retainedBytesAdded,
      "release storage GC expected retained projection",
    ),
    writtenBytesPerDay: addIntegers(
      addIntegers(
        document.accounting.writtenBytesToday,
        document.accounting.publicationEstimate.writtenBytes,
        "release storage GC expected publication writes",
      ),
      expectedUpperBound.writtenBytes,
      "release storage GC expected written projection",
    ),
    egressBytesPerDay: addIntegers(
      addIntegers(
        document.accounting.egressBytesToday,
        document.accounting.publicationEstimate.egressBytes,
        "release storage GC expected publication egress",
      ),
      expectedUpperBound.egressBytes,
      "release storage GC expected egress projection",
    ),
    requestsPerDay: addIntegers(
      addIntegers(
        document.accounting.requestsToday,
        document.accounting.publicationEstimate.requests,
        "release storage GC expected publication requests",
      ),
      expectedUpperBound.requests,
      "release storage GC expected request projection",
    ),
  };
  if (JSON.stringify(document.accounting.projected) !== JSON.stringify(expectedProjected)) {
    throw new Error("release storage GC projected accounting differs from its resource bounds");
  }
  const expectedBudget = budgetEvaluation(expectedProjected, {
    inventoryComplete: document.accounting.inventoryComplete,
    gcComplete: true,
  });
  if (JSON.stringify(document.thresholds) !== JSON.stringify(expectedBudget.thresholds)) {
    throw new Error("release storage GC thresholds differ from projected accounting");
  }
  if (!exactKeys(document.publication, ["decision", "reasons", "preservesLastKnownGood"])
    || !["allow", "warn", "block"].includes(document.publication.decision)
    || !Array.isArray(document.publication.reasons)
    || document.publication.preservesLastKnownGood !== true) {
    throw new Error("release storage GC publication decision is invalid");
  }
  if (JSON.stringify(document.publication) !== JSON.stringify(expectedBudget.publication)) {
    throw new Error("release storage GC publication decision differs from its thresholds");
  }
  return document;
}

function validateLease(lease, planSha256, currentTime) {
  if (!exactKeys(lease, [
    "contract",
    "schemaVersion",
    "leaseId",
    "fencingToken",
    "planSha256",
    "issuedAt",
    "expiresAt",
  ]) || lease.contract !== "verdify.lab-release-storage-gc-lease" || lease.schemaVersion !== 1) {
    throw new Error("release storage GC lease does not use the closed v1 contract");
  }
  safeText(lease.leaseId, "release storage GC lease ID", 256);
  safeInteger(lease.fencingToken, "release storage GC fencing token", { minimum: 1 });
  if (lease.planSha256 !== planSha256) throw new Error("release storage GC lease is bound to another plan");
  instant(lease.issuedAt, "release storage GC lease issue time");
  instant(lease.expiresAt, "release storage GC lease expiry time");
  instant(currentTime, "release storage GC execution time");
  if (
    Date.parse(lease.issuedAt) > Date.parse(currentTime)
    || Date.parse(currentTime) >= Date.parse(lease.expiresAt)
    || Date.parse(lease.issuedAt) >= Date.parse(lease.expiresAt)
  ) throw new Error("release storage GC lease is not current");
  return lease;
}

function validateAdapter(adapter) {
  if (
    !exactKeys(adapter, [
      "contract",
      "schemaVersion",
      "readFence",
      "readSelector",
      "statObject",
      "readDeletionConfirmation",
      "deleteObject",
    ])
    || adapter.contract !== "verdify.lab-release-storage-gc-delete-adapter"
    || adapter.schemaVersion !== 1
    || typeof adapter.readFence !== "function"
    || typeof adapter.readSelector !== "function"
    || typeof adapter.statObject !== "function"
    || typeof adapter.readDeletionConfirmation !== "function"
    || typeof adapter.deleteObject !== "function"
  ) throw new Error("release storage GC requires an explicitly injected deletion adapter");
  return adapter;
}

async function injectedCurrentInstant(currentInstant) {
  if (typeof currentInstant !== "function") {
    throw new Error("release storage GC requires injected current-instant evidence");
  }
  const evidence = await currentInstant();
  if (
    !exactKeys(evidence, ["contract", "schemaVersion", "instant"])
    || evidence.contract !== "verdify.lab-current-instant"
    || evidence.schemaVersion !== 1
  ) throw new Error("release storage GC current-instant evidence is invalid");
  return instant(evidence.instant, "release storage GC current-instant evidence");
}

function validateUsageDelta(usage, label, { deletedBytesMaximum = 0, requireRequest = true } = {}) {
  if (!exactKeys(usage, ["writtenBytes", "deletedBytes", "egressBytes", "requests"])) {
    throw new Error(`${label} usage does not use the closed v1 shape`);
  }
  safeInteger(usage.writtenBytes, `${label} written bytes`, {
    maximum: OPERATION_USAGE_LIMITS.writtenBytes,
  });
  safeInteger(usage.deletedBytes, `${label} deleted bytes`, { maximum: deletedBytesMaximum });
  safeInteger(usage.egressBytes, `${label} egress bytes`, {
    maximum: OPERATION_USAGE_LIMITS.egressBytes,
  });
  safeInteger(usage.requests, `${label} requests`, {
    minimum: requireRequest ? 1 : 0,
    maximum: OPERATION_USAGE_LIMITS.requests,
  });
  return usage;
}

function addUsage(left, right, label = "release storage GC accumulated usage") {
  return {
    writtenBytes: addIntegers(left.writtenBytes, right.writtenBytes, `${label} written bytes`),
    deletedBytes: addIntegers(left.deletedBytes, right.deletedBytes, `${label} deleted bytes`),
    egressBytes: addIntegers(left.egressBytes, right.egressBytes, `${label} egress bytes`),
    requests: addIntegers(left.requests, right.requests, `${label} requests`),
  };
}

function progressRecord(planSha256, updatedAt, confirmed = [], usage = emptyCounters()) {
  return {
    contract: "verdify.lab-release-storage-gc-progress",
    schemaVersion: 1,
    planSha256,
    updatedAt,
    confirmed,
    usage,
  };
}

function wrapProgress(document) {
  return Object.freeze({ document, sha256: sha256(canonicalBytes(document)) });
}

function validateProgress(progress, plan, document) {
  if (progress === null) return null;
  if (
    !exactKeys(progress, ["document", "sha256"])
    || !exactKeys(progress.document, [
      "contract",
      "schemaVersion",
      "planSha256",
      "updatedAt",
      "confirmed",
      "usage",
    ])
    || progress.document.contract !== "verdify.lab-release-storage-gc-progress"
    || progress.document.schemaVersion !== 1
    || progress.document.planSha256 !== plan.sha256
    || sha256(canonicalBytes(progress.document)) !== progress.sha256
  ) throw new Error("release storage GC progress identity is invalid");
  instant(progress.document.updatedAt, "release storage GC progress time");
  validateCounters(progress.document.usage);
  if (
    !Array.isArray(progress.document.confirmed)
    || progress.document.confirmed.length > document.deletions.length
  ) throw new Error("release storage GC progress membership is invalid");
  let confirmedBytes = 0;
  for (const [index, record] of progress.document.confirmed.entries()) {
    if (!exactKeys(record, [
      "namespace",
      "key",
      "kind",
      "sha256",
      "bytes",
      "createdAt",
      "confirmationSha256",
    ])) throw new Error("release storage GC confirmation is invalid");
    const expected = document.deletions[index];
    const { confirmationSha256, ...identity } = record;
    if (JSON.stringify(identity) !== JSON.stringify(expected)) {
      throw new Error("release storage GC progress is not an exact deletion prefix");
    }
    digest(confirmationSha256, "release storage GC deletion confirmation");
    confirmedBytes = addIntegers(confirmedBytes, record.bytes, "release storage GC confirmed bytes");
  }
  if (progress.document.usage.deletedBytes < confirmedBytes) {
    throw new Error("release storage GC progress undercounts confirmed deletions");
  }
  return structuredClone(progress);
}

export function serializeReleaseStorageGcProgress(progress) {
  if (!exactKeys(progress, ["document", "sha256"]) || sha256(canonicalBytes(progress.document)) !== progress.sha256) {
    throw new Error("release storage GC progress identity is invalid");
  }
  return canonicalBytes(progress);
}

export function parseReleaseStorageGcProgress(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 1 || bytes.length > 16 * 1024 * 1024) {
    throw new Error("release storage GC progress bytes are invalid");
  }
  let progress;
  try {
    progress = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("release storage GC progress is not valid JSON");
  }
  if (!canonicalBytes(progress).equals(bytes)) throw new Error("release storage GC progress is not canonical JSON");
  if (!exactKeys(progress, ["document", "sha256"]) || sha256(canonicalBytes(progress.document)) !== progress.sha256) {
    throw new Error("release storage GC progress identity is invalid");
  }
  return progress;
}

function confirmedBytes(progress) {
  return sumIntegers(
    progress.document.confirmed.map((record) => record.bytes),
    "release storage GC confirmed retained-byte reduction",
  );
}

function actualBudget(document, progress, gcComplete) {
  const publication = document.accounting.publicationEstimate;
  const usage = progress.document.usage;
  const projected = {
    retainedBytes: addIntegers(
      document.accounting.observedRetainedBytes - confirmedBytes(progress),
      publication.retainedBytesAdded,
      "release storage GC actual retained projection",
    ),
    writtenBytesPerDay: addIntegers(
      addIntegers(document.accounting.writtenBytesToday, publication.writtenBytes, "actual publication writes"),
      usage.writtenBytes,
      "release storage GC actual written projection",
    ),
    egressBytesPerDay: addIntegers(
      addIntegers(document.accounting.egressBytesToday, publication.egressBytes, "actual publication egress"),
      usage.egressBytes,
      "release storage GC actual egress projection",
    ),
    requestsPerDay: addIntegers(
      addIntegers(document.accounting.requestsToday, publication.requests, "actual publication requests"),
      usage.requests,
      "release storage GC actual request projection",
    ),
  };
  return budgetEvaluation(projected, {
    inventoryComplete: document.accounting.inventoryComplete,
    gcComplete,
  });
}

export class ReleaseStorageGcExecutionError extends Error {
  constructor(result) {
    super(`release storage GC execution interrupted at ${result.phase}: ${result.errorCode}`);
    this.name = "ReleaseStorageGcExecutionError";
    this.result = result;
  }
}

function interruption(context, errorCode, phase) {
  const result = {
    contract: "verdify.lab-release-storage-gc-interruption",
    schemaVersion: 1,
    status: "interrupted",
    planSha256: context.plan.sha256,
    phase,
    errorCode,
    progress: context.progress,
    budget: actualBudget(context.document, context.progress, false),
  };
  throw new ReleaseStorageGcExecutionError(result);
}

function updateProgress(context, { usage = null, confirmation = null, updatedAt = null } = {}) {
  const current = context.progress.document;
  const document = progressRecord(
    current.planSha256,
    updatedAt ?? current.updatedAt,
    confirmation === null ? current.confirmed : [...current.confirmed, confirmation],
    usage === null ? current.usage : addUsage(current.usage, usage),
  );
  context.progress = wrapProgress(document);
}

function operationUpperBound(deletedBytesMaximum) {
  return {
    ...OPERATION_USAGE_LIMITS,
    deletedBytes: deletedBytesMaximum,
  };
}

async function operation(context, phase, request, { deletedBytesMaximum = 0 } = {}) {
  addUsage(
    context.progress.document.usage,
    operationUpperBound(deletedBytesMaximum),
    "release storage GC operation usage headroom",
  );
  let response;
  try {
    response = await context.adapter[phase](request);
  } catch {
    updateProgress(context, { usage: operationUpperBound(deletedBytesMaximum) });
    interruption(context, "adapter-threw", phase);
  }
  if (!exactKeys(response, ["contract", "schemaVersion", "status", "value", "errorCode", "usage"])) {
    updateProgress(context, { usage: operationUpperBound(deletedBytesMaximum) });
    interruption(context, "invalid-adapter-result", phase);
  }
  let usage;
  try {
    if (
      response.contract !== "verdify.lab-release-storage-adapter-operation"
      || response.schemaVersion !== 1
      || !["ok", "error"].includes(response.status)
      || (response.status === "ok" ? response.errorCode !== null : response.value !== null)
      || (response.status === "error"
        && (typeof response.errorCode !== "string" || !/^[a-z][a-z0-9-]{0,63}$/u.test(response.errorCode)))
    ) throw new Error("invalid result envelope");
    usage = validateUsageDelta(response.usage, `release storage GC ${phase}`, { deletedBytesMaximum });
  } catch {
    updateProgress(context, { usage: operationUpperBound(deletedBytesMaximum) });
    interruption(context, "invalid-adapter-result", phase);
  }
  updateProgress(context, { usage });
  if (response.status === "error") interruption(context, response.errorCode, phase);
  return response.value;
}

function sameFence(actual, lease) {
  return exactKeys(actual, [
    "contract",
    "schemaVersion",
    "leaseId",
    "fencingToken",
    "planSha256",
    "issuedAt",
    "expiresAt",
  ]) && canonicalBytes(actual).equals(canonicalBytes(lease));
}

async function currentFence(context) {
  const actual = await operation(context, "readFence", { leaseId: context.lease.leaseId });
  if (!sameFence(actual, context.lease)) interruption(context, "stale-fence", "readFence");
  return actual;
}

export async function executeReleaseStorageGcPlan(input) {
  if (!exactKeys(input, ["plan", "adapter", "lease", "currentInstant", "progress"])) {
    throw new Error("release storage GC execution input does not use the closed v1 shape");
  }
  const { plan, adapter, lease, currentInstant, progress } = input;
  const document = validatePlan(plan);
  if (!document.accounting.inventoryComplete && document.deletions.length !== 0) {
    throw new Error("incomplete release storage inventory cannot authorize deletion during execution");
  }
  const injected = validateAdapter(adapter);
  const firstInstant = await injectedCurrentInstant(currentInstant);
  const fence = validateLease(lease, plan.sha256, firstInstant);
  if (Date.parse(fence.issuedAt) < Date.parse(document.plannedAt)) {
    throw new Error("release storage GC lease predates its plan");
  }
  const resumed = validateProgress(progress, plan, document);
  if (resumed !== null && Date.parse(resumed.document.updatedAt) > Date.parse(firstInstant)) {
    throw new Error("release storage GC progress is from the future");
  }
  const context = {
    plan,
    document,
    adapter: injected,
    lease: fence,
    currentInstant,
    progress: resumed ?? wrapProgress(progressRecord(plan.sha256, firstInstant)),
  };
  await currentFence(context);
  if (document.deletions.length === 0) {
    return {
      contract: "verdify.lab-release-storage-gc-result",
      schemaVersion: 1,
      status: "complete",
      planSha256: plan.sha256,
      deletedObjects: 0,
      progress: context.progress,
      usage: context.progress.document.usage,
      budget: actualBudget(document, context.progress, true),
    };
  }
  for (const confirmed of context.progress.document.confirmed) {
    const durable = await operation(context, "readDeletionConfirmation", {
      planSha256: plan.sha256,
      namespace: confirmed.namespace,
      key: confirmed.key,
      confirmationSha256: confirmed.confirmationSha256,
    });
    if (
      !exactKeys(durable, ["confirmed", "confirmationSha256"])
      || durable.confirmed !== true
      || durable.confirmationSha256 !== confirmed.confirmationSha256
    ) interruption(context, "deletion-confirmation-missing", "readDeletionConfirmation");
  }
  for (const expected of document.selectors) {
    const actual = await operation(context, "readSelector", {
      namespace: expected.namespace,
      key: expected.key,
    });
    if (
      !exactKeys(actual, ["sha256", "etag"])
      || actual.sha256 !== expected.sha256
      || actual.etag !== expected.etag
    ) interruption(context, "selector-changed", "readSelector");
  }
  const remaining = document.deletions.slice(context.progress.document.confirmed.length);
  for (const expected of remaining) {
    const actual = await operation(context, "statObject", {
      namespace: expected.namespace,
      key: expected.key,
    });
    if (
      !exactKeys(actual, ["sha256", "bytes", "createdAt"])
      || actual.sha256 !== expected.sha256
      || actual?.bytes !== expected.bytes
      || actual?.createdAt !== expected.createdAt
    ) interruption(context, "object-changed", "statObject");
  }
  for (const expected of remaining) {
    let now;
    try {
      now = await injectedCurrentInstant(currentInstant);
      validateLease(fence, plan.sha256, now);
    } catch {
      interruption(context, "lease-expired-or-time-invalid", "currentInstant");
    }
    updateProgress(context, { updatedAt: now });
    await currentFence(context);
    const deletedBytesBefore = context.progress.document.usage.deletedBytes;
    const result = await operation(context, "deleteObject", {
      namespace: expected.namespace,
      key: expected.key,
      expected: {
        sha256: expected.sha256,
        bytes: expected.bytes,
        createdAt: expected.createdAt,
      },
      selectorPreconditions: document.selectors,
      lease: fence,
    }, { deletedBytesMaximum: expected.bytes });
    const deletedBytesDelta = context.progress.document.usage.deletedBytes - deletedBytesBefore;
    if (
      !exactKeys(result, ["deleted", "confirmationSha256"])
      || result.deleted !== true
      || typeof result.confirmationSha256 !== "string"
      || !SHA256_RE.test(result.confirmationSha256)
      || deletedBytesDelta !== expected.bytes
    ) interruption(context, "deletion-unconfirmed", "deleteObject");
    updateProgress(context, {
      confirmation: {
        ...expected,
        confirmationSha256: result.confirmationSha256,
      },
    });
  }
  return {
    contract: "verdify.lab-release-storage-gc-result",
    schemaVersion: 1,
    status: "complete",
    planSha256: plan.sha256,
    deletedObjects: document.deletions.length,
    progress: context.progress,
    usage: context.progress.document.usage,
    budget: actualBudget(document, context.progress, true),
  };
}

export const releaseStorageSafetyContract = Object.freeze({
  budgets: BUDGETS,
  recoveryGraceSeconds: RECOVERY_GRACE_MS / 1000,
  eventIdempotencyHorizonSeconds: EVENT_IDEMPOTENCY_HORIZON_MS / 1000,
  inventory: Object.freeze({ contract: "verdify.lab-release-storage-inventory", schemaVersion: 1 }),
  publication: Object.freeze({ contract: "verdify.lab-release-storage-publication-estimate", schemaVersion: 1 }),
  usageState: Object.freeze({ contract: "verdify.lab-release-storage-usage-state", schemaVersion: 1 }),
  plan: Object.freeze({ contract: "verdify.lab-release-storage-gc-plan", schemaVersion: 1 }),
  progress: Object.freeze({ contract: "verdify.lab-release-storage-gc-progress", schemaVersion: 1 }),
  lease: Object.freeze({ contract: "verdify.lab-release-storage-gc-lease", schemaVersion: 1 }),
  currentInstant: Object.freeze({ contract: "verdify.lab-current-instant", schemaVersion: 1 }),
  adapter: Object.freeze({ contract: "verdify.lab-release-storage-gc-delete-adapter", schemaVersion: 1 }),
  adapterOperation: Object.freeze({
    contract: "verdify.lab-release-storage-adapter-operation",
    schemaVersion: 1,
    usageLimits: OPERATION_USAGE_LIMITS,
  }),
});
