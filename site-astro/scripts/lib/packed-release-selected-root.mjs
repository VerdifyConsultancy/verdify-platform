import { createHash } from "node:crypto";

import { validateDeterministicReleasePackReference } from "./deterministic-release-pack.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const EVENT_ID_RE = /^evt_[A-Za-z0-9_-]{8,128}$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/u;
const MAX_ROOT_BYTES = 64 * 1024;

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

function eventId(value) {
  if (typeof value !== "string" || !EVENT_ID_RE.test(value)) {
    throw new Error("packed release pair event ID is invalid");
  }
  return value;
}

function instant(value) {
  if (
    typeof value !== "string"
    || !ISO_INSTANT_RE.test(value)
    || !Number.isFinite(Date.parse(value))
    || new Date(Date.parse(value)).toISOString() !== value
  ) {
    throw new Error("packed release selected time is invalid");
  }
  return value;
}

function pairIdentityBytes(value) {
  return canonicalBytes({
    contract: value.contract,
    schemaVersion: value.schemaVersion,
    eventId: value.eventId,
    occurrencePack: value.occurrencePack,
    sitePack: value.sitePack,
  });
}

export function validatePackedReleasePair(value) {
  if (!exactKeys(value, [
    "contract",
    "schemaVersion",
    "eventId",
    "occurrencePack",
    "sitePack",
    "pairSha256",
  ])) {
    throw new Error("packed release pair does not use the closed v1 schema");
  }
  if (
    value.contract !== "verdify.lab-packed-release-pair"
    || value.schemaVersion !== 1
  ) {
    throw new Error("packed release pair does not use the closed v1 schema");
  }
  eventId(value.eventId);
  validateDeterministicReleasePackReference(value.occurrencePack);
  validateDeterministicReleasePackReference(value.sitePack);
  if (value.occurrencePack.kind !== "occurrence" || value.sitePack.kind !== "site") {
    throw new Error("packed release pair does not bind one occurrence pack and one site pack");
  }
  if (
    typeof value.pairSha256 !== "string"
    || !SHA256_RE.test(value.pairSha256)
    || value.pairSha256 !== sha256(pairIdentityBytes(value))
  ) {
    throw new Error("packed release pair digest is invalid");
  }
  return value;
}

export function createPackedReleasePair(input) {
  if (!exactKeys(input, ["eventId", "occurrencePack", "sitePack"])) {
    throw new Error("packed release pair input is invalid");
  }
  const { eventId: selectedEventId, occurrencePack, sitePack } = input;
  const value = {
    contract: "verdify.lab-packed-release-pair",
    schemaVersion: 1,
    eventId: eventId(selectedEventId),
    occurrencePack,
    sitePack,
  };
  return validatePackedReleasePair({
    ...value,
    pairSha256: sha256(pairIdentityBytes(value)),
  });
}

export function validatePackedReleaseSelectedRoot(value) {
  if (!exactKeys(value, [
    "contract",
    "schemaVersion",
    "generation",
    "current",
    "rollback",
    "selectedAt",
    "reason",
  ])) {
    throw new Error("packed release selected root does not use the closed v1 schema");
  }
  if (
    value.contract !== "verdify.lab-packed-release-selected-root"
    || value.schemaVersion !== 1
  ) {
    throw new Error("packed release selected root does not use the closed v1 schema");
  }
  safeInteger(value.generation, "packed release selected-root generation", { minimum: 1 });
  validatePackedReleasePair(value.current);
  if (value.rollback !== null) {
    validatePackedReleasePair(value.rollback);
    if (value.current.pairSha256 === value.rollback.pairSha256) {
      throw new Error("packed release current and rollback pairs are identical");
    }
  }
  instant(value.selectedAt);
  if (!["publish", "rollback"].includes(value.reason)) {
    throw new Error("packed release selected-root reason is invalid");
  }
  if (value.reason === "rollback" && value.rollback === null) {
    throw new Error("packed release rollback selection requires both generations");
  }
  const bytes = canonicalBytes(value);
  if (bytes.length > MAX_ROOT_BYTES) throw new Error("packed release selected root exceeds its byte limit");
  return value;
}

export function createPackedReleaseSelectedRoot(input) {
  if (!exactKeys(input, ["generation", "current", "rollback", "selectedAt", "reason"])) {
    throw new Error("packed release selected-root input is invalid");
  }
  const document = validatePackedReleaseSelectedRoot({
    contract: "verdify.lab-packed-release-selected-root",
    schemaVersion: 1,
    generation: input.generation,
    current: input.current,
    rollback: input.rollback,
    selectedAt: input.selectedAt,
    reason: input.reason,
  });
  const bytes = canonicalBytes(document);
  return { document, bytes, sha256: sha256(bytes) };
}

export function serializePackedReleaseSelectedRoot(value) {
  const document = value?.document ?? value;
  validatePackedReleaseSelectedRoot(document);
  const bytes = canonicalBytes(document);
  if (value?.sha256 !== undefined && value.sha256 !== sha256(bytes)) {
    throw new Error("packed release selected-root wrapper digest is invalid");
  }
  return bytes;
}

export function parsePackedReleaseSelectedRoot(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 1 || bytes.length > MAX_ROOT_BYTES) {
    throw new Error("packed release selected-root bytes are invalid");
  }
  let document;
  try {
    document = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("packed release selected root is not valid JSON");
  }
  if (!canonicalBytes(document).equals(bytes)) {
    throw new Error("packed release selected root is not canonical JSON");
  }
  validatePackedReleaseSelectedRoot(document);
  return { document, bytes: Buffer.from(bytes), sha256: sha256(bytes) };
}

export const packedReleaseSelectedRootContract = Object.freeze({
  contract: "verdify.lab-packed-release-selected-root",
  schemaVersion: 1,
  key: "selected-root.json",
  maxBytes: MAX_ROOT_BYTES,
  selectionUnit: "one-occurrence-pack-plus-one-site-pack",
});
