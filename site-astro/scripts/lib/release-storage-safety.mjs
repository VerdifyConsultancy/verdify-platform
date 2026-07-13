import { createHash } from "node:crypto";

const GIB = 1024 ** 3;
const SHA256_RE = /^[0-9a-f]{64}$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/u;
const MEDIA_OCCURRENCE_ID_RE = /^media_[0-9a-f]{24}$/u;
const SAFE_KEY_RE = /^[A-Za-z0-9._/-]{1,1024}$/u;
const MAX_OBJECTS = 1_000_000;
const MAX_SELECTORS = 10_000;
const RECOVERY_GRACE_MS = 48 * 60 * 60 * 1000;

const BUDGETS = Object.freeze({
  retainedBytes: 10 * GIB,
  writtenBytesPerDay: 5 * GIB,
  egressBytesPerDay: 10 * GIB,
  requestsPerDay: 25_000,
  warningFraction: 0.8,
});

const GC_KINDS = new Set(["release", "manifest", "generation", "blob"]);
const ALL_KINDS = new Set([...GC_KINDS, "event"]);

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
  if (!ISO_INSTANT_RE.test(value) || !Number.isFinite(Date.parse(value))) {
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
            ? /^events\/sha256\/[0-9a-f]{64}\.json$/u.exec(value)
            : kind === "event"
              ? /^(?:events\/sha256\/[0-9a-f]{64}|occurrences\/media_[0-9a-f]{24}\/events\/sha256\/[0-9a-f]{64})\.json$/u.exec(value)
              : null;
  if (keyedDigest === null) throw new Error(`${label} does not match its object kind`);
  if (namespaceValue === "site" && !["release", "blob", "event"].includes(kind)) {
    throw new Error("site release storage object kind is invalid");
  }
  if (namespaceValue === "occurrence" && !["manifest", "generation", "blob", "event"].includes(kind)) {
    throw new Error("occurrence release storage object kind is invalid");
  }
  return keyedDigest;
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
  if (raw.kind !== "event") {
    const keyed = raw.kind === "generation" ? keyMatch[2] : keyMatch[1];
    if (keyed !== raw.sha256) throw new Error("release storage object key and digest differ");
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
  if (raw.kind === "event" && raw.references.length !== 0) {
    throw new Error("permanent release storage event cannot retain immutable payload objects");
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
    if (object.kind === "event" || age <= RECOVERY_GRACE_MS) roots.push(identity);
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
  const gcRequests = deletions.length === 0
    ? 0
    : safeInteger(1 + selectors.length + (3 * deletions.length), "release storage planned GC requests");
  const projected = {
    retainedBytes: addIntegers(
      retainedBytesAfterGc,
      estimate.retainedBytesAdded,
      "release storage projected retained bytes",
    ),
    writtenBytesPerDay: addIntegers(
      state.counters.writtenBytes,
      estimate.writtenBytes,
      "release storage projected written bytes",
    ),
    egressBytesPerDay: addIntegers(
      state.counters.egressBytes,
      estimate.egressBytes,
      "release storage projected egress bytes",
    ),
    requestsPerDay: addIntegers(
      addIntegers(state.counters.requests, estimate.requests, "release storage projected publication requests"),
      gcRequests,
      "release storage projected requests",
    ),
  };
  const thresholds = [
    threshold("retainedBytes", projected.retainedBytes, BUDGETS.retainedBytes),
    threshold("writtenBytesPerDay", projected.writtenBytesPerDay, BUDGETS.writtenBytesPerDay),
    threshold("egressBytesPerDay", projected.egressBytesPerDay, BUDGETS.egressBytesPerDay),
    threshold("requestsPerDay", projected.requestsPerDay, BUDGETS.requestsPerDay),
  ];
  const reasons = [];
  if (!complete) reasons.push("incomplete-listing");
  reasons.push(...thresholds.filter((item) => item.status === "block").map((item) => `${item.name}-budget`));
  let decision = reasons.length > 0 ? "block" : thresholds.some((item) => item.status === "warn") ? "warn" : "allow";
  if (decision === "warn") {
    reasons.push(...thresholds.filter((item) => item.status === "warn").map((item) => `${item.name}-warning`));
  }
  const document = {
    contract: "verdify.lab-release-storage-gc-plan",
    schemaVersion: 1,
    plannedAt: asOf,
    snapshotSha256: sha256(canonicalBytes(snapshot)),
    budgetsSha256: sha256(canonicalBytes(BUDGETS)),
    recoveryGraceSeconds: RECOVERY_GRACE_MS / 1000,
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
      plannedGcRequests: gcRequests,
      projected,
    },
    thresholds,
    publication: {
      decision,
      reasons,
      preservesLastKnownGood: true,
    },
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
    const keyed = deletion.kind === "generation" ? match[2] : match[1];
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
    if (Date.parse(document.plannedAt) - Date.parse(deletion.createdAt) <= RECOVERY_GRACE_MS) {
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
    "plannedGcRequests",
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
    "plannedGcRequests",
  ]) safeInteger(document.accounting[key], `release storage GC ${key}`);
  if (typeof document.accounting.currentDay !== "string" || !DAY_RE.test(document.accounting.currentDay)) {
    throw new Error("release storage GC accounting day is invalid");
  }
  if (document.accounting.currentDay !== document.plannedAt.slice(0, 10)) {
    throw new Error("release storage GC accounting day differs from its plan time");
  }
  const expectedGcRequests = document.deletions.length === 0
    ? 0
    : 1 + document.selectors.length + (3 * document.deletions.length);
  if (
    deletedBytes !== document.accounting.plannedDeletedBytes
    || document.accounting.observedRetainedBytes - deletedBytes !== document.accounting.retainedBytesAfterGc
    || document.accounting.plannedGcRequests !== expectedGcRequests
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
  if (
    document.accounting.projected.retainedBytes < document.accounting.retainedBytesAfterGc
    || document.accounting.projected.writtenBytesPerDay < document.accounting.writtenBytesToday
    || document.accounting.projected.egressBytesPerDay < document.accounting.egressBytesToday
    || document.accounting.projected.requestsPerDay < addIntegers(
      document.accounting.requestsToday,
      document.accounting.plannedGcRequests,
      "release storage GC minimum projected requests",
    )
  ) throw new Error("release storage GC projected accounting regresses measured usage");
  const expectedThresholds = [
    threshold("retainedBytes", document.accounting.projected.retainedBytes, BUDGETS.retainedBytes),
    threshold("writtenBytesPerDay", document.accounting.projected.writtenBytesPerDay, BUDGETS.writtenBytesPerDay),
    threshold("egressBytesPerDay", document.accounting.projected.egressBytesPerDay, BUDGETS.egressBytesPerDay),
    threshold("requestsPerDay", document.accounting.projected.requestsPerDay, BUDGETS.requestsPerDay),
  ];
  if (JSON.stringify(document.thresholds) !== JSON.stringify(expectedThresholds)) {
    throw new Error("release storage GC thresholds differ from projected accounting");
  }
  if (!exactKeys(document.publication, ["decision", "reasons", "preservesLastKnownGood"])
    || !["allow", "warn", "block"].includes(document.publication.decision)
    || !Array.isArray(document.publication.reasons)
    || document.publication.preservesLastKnownGood !== true) {
    throw new Error("release storage GC publication decision is invalid");
  }
  const expectedReasons = [];
  if (!document.accounting.inventoryComplete) expectedReasons.push("incomplete-listing");
  expectedReasons.push(...expectedThresholds
    .filter((item) => item.status === "block")
    .map((item) => `${item.name}-budget`));
  const expectedDecision = expectedReasons.length > 0
    ? "block"
    : expectedThresholds.some((item) => item.status === "warn") ? "warn" : "allow";
  if (expectedDecision === "warn") {
    expectedReasons.push(...expectedThresholds
      .filter((item) => item.status === "warn")
      .map((item) => `${item.name}-warning`));
  }
  if (
    document.publication.decision !== expectedDecision
    || JSON.stringify(document.publication.reasons) !== JSON.stringify(expectedReasons)
  ) throw new Error("release storage GC publication decision differs from its thresholds");
  return document;
}

function validateLease(lease, planSha256, asOf) {
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
  instant(asOf, "release storage GC execution time");
  if (
    Date.parse(lease.issuedAt) > Date.parse(asOf)
    || Date.parse(asOf) >= Date.parse(lease.expiresAt)
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
      "deleteObject",
    ])
    || adapter.contract !== "verdify.lab-release-storage-gc-delete-adapter"
    || adapter.schemaVersion !== 1
    || typeof adapter.readFence !== "function"
    || typeof adapter.readSelector !== "function"
    || typeof adapter.statObject !== "function"
    || typeof adapter.deleteObject !== "function"
  ) throw new Error("release storage GC requires an explicitly injected deletion adapter");
  return adapter;
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

async function currentFence(adapter, lease) {
  const actual = await adapter.readFence({ leaseId: lease.leaseId });
  if (!sameFence(actual, lease)) throw new Error("release storage GC fencing token is stale");
  return actual;
}

export async function executeReleaseStorageGcPlan(input) {
  if (!exactKeys(input, ["plan", "adapter", "lease", "asOf"])) {
    throw new Error("release storage GC execution input does not use the closed v1 shape");
  }
  const { plan, adapter, lease, asOf } = input;
  const document = validatePlan(plan);
  if (document.deletions.length === 0) {
    return {
      contract: "verdify.lab-release-storage-gc-result",
      schemaVersion: 1,
      planSha256: plan.sha256,
      deletedObjects: 0,
      usage: emptyCounters(),
    };
  }
  const injected = validateAdapter(adapter);
  const fence = validateLease(lease, plan.sha256, asOf);
  if (Date.parse(fence.issuedAt) < Date.parse(document.plannedAt)) {
    throw new Error("release storage GC lease predates its plan");
  }
  let requests = 0;
  await currentFence(injected, fence);
  requests += 1;
  for (const expected of document.selectors) {
    const actual = await injected.readSelector({ namespace: expected.namespace, key: expected.key });
    requests += 1;
    if (actual?.sha256 !== expected.sha256 || actual?.etag !== expected.etag) {
      throw new Error("release storage selector changed after GC planning");
    }
  }
  for (const expected of document.deletions) {
    const actual = await injected.statObject({ namespace: expected.namespace, key: expected.key });
    requests += 1;
    if (
      actual?.sha256 !== expected.sha256
      || actual?.bytes !== expected.bytes
      || actual?.createdAt !== expected.createdAt
    ) throw new Error("release storage object changed after GC planning");
  }
  let deletedBytes = 0;
  for (const expected of document.deletions) {
    await currentFence(injected, fence);
    requests += 1;
    const result = await injected.deleteObject({
      namespace: expected.namespace,
      key: expected.key,
      expected: {
        sha256: expected.sha256,
        bytes: expected.bytes,
        createdAt: expected.createdAt,
      },
      selectorPreconditions: document.selectors,
      lease: fence,
    });
    requests += 1;
    if (!exactKeys(result, ["deleted"]) || result.deleted !== true) {
      throw new Error("release storage GC deletion was not confirmed");
    }
    deletedBytes = addIntegers(deletedBytes, expected.bytes, "release storage GC confirmed deleted bytes");
  }
  return {
    contract: "verdify.lab-release-storage-gc-result",
    schemaVersion: 1,
    planSha256: plan.sha256,
    deletedObjects: document.deletions.length,
    usage: {
      writtenBytes: 0,
      deletedBytes,
      egressBytes: 0,
      requests,
    },
  };
}

export const releaseStorageSafetyContract = Object.freeze({
  budgets: BUDGETS,
  recoveryGraceSeconds: RECOVERY_GRACE_MS / 1000,
  inventory: Object.freeze({ contract: "verdify.lab-release-storage-inventory", schemaVersion: 1 }),
  publication: Object.freeze({ contract: "verdify.lab-release-storage-publication-estimate", schemaVersion: 1 }),
  usageState: Object.freeze({ contract: "verdify.lab-release-storage-usage-state", schemaVersion: 1 }),
  plan: Object.freeze({ contract: "verdify.lab-release-storage-gc-plan", schemaVersion: 1 }),
  lease: Object.freeze({ contract: "verdify.lab-release-storage-gc-lease", schemaVersion: 1 }),
  adapter: Object.freeze({ contract: "verdify.lab-release-storage-gc-delete-adapter", schemaVersion: 1 }),
});
