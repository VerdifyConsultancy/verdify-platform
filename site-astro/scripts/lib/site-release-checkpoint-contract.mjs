import { createHash } from "node:crypto";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const EVENT_ID_RE = /^evt_occurrence_site_[0-9a-f]{32}$/u;
const CHECKPOINT_KEYS = [
  "contract",
  "schemaVersion",
  "eventId",
  "eventSha256",
  "producerResultSha256",
  "occurrenceCallResultSha256",
  "sourceSnapshotManifestSha256",
  "sourceOccurrenceManifestSha256",
  "occurrencePolicySha256",
  "occurrenceStoreIdentitySha256",
  "occurrenceSelectionSha256",
  "occurrenceManifestSha256",
  "buildOperationSha256",
  "verificationOperationSha256",
  "siteStoreIdentitySha256",
  "expectedSiteSelectionSha256",
];
const MAX_CHECKPOINT_BYTES = 64 * 1024;

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function validateDigest(value, label) {
  if (typeof value !== "string" || !SHA256_RE.test(value)) {
    throw new Error(`${label} is invalid`);
  }
}

export function validateSiteReleaseCheckpointDocument(document, { eventId = null } = {}) {
  if (
    document === null
    || typeof document !== "object"
    || Array.isArray(document)
    || Object.getPrototypeOf(document) !== Object.prototype
    || Object.keys(document).join(",") !== CHECKPOINT_KEYS.join(",")
    || document.contract !== "verdify.lab-occurrence-site-publish-checkpoint"
    || document.schemaVersion !== 1
    || !EVENT_ID_RE.test(document.eventId ?? "")
    || (eventId !== null && document.eventId !== eventId)
  ) throw new Error("occurrence site checkpoint does not use the closed v1 contract");
  for (const key of CHECKPOINT_KEYS.slice(3, -1)) {
    validateDigest(document[key], `occurrence site checkpoint ${key}`);
  }
  if (document.expectedSiteSelectionSha256 !== null) {
    validateDigest(
      document.expectedSiteSelectionSha256,
      "occurrence site checkpoint selection precondition",
    );
  }
  return document;
}

export function siteReleaseCheckpointKey(eventId) {
  if (typeof eventId !== "string" || !EVENT_ID_RE.test(eventId)) {
    throw new Error("occurrence site checkpoint event ID is invalid");
  }
  return `checkpoints/sha256/${sha256(Buffer.from(eventId))}.json`;
}

export function parseSiteReleaseCheckpoint(value, { eventId = null } = {}) {
  if (
    value === null
    || typeof value !== "object"
    || !Buffer.isBuffer(value.bytes)
    || value.bytes.length < 1
    || value.bytes.length > MAX_CHECKPOINT_BYTES
  ) throw new Error("occurrence site checkpoint bytes are invalid");
  let document;
  try {
    document = JSON.parse(value.bytes.toString("utf8"));
  } catch {
    throw new Error("occurrence site checkpoint is not valid JSON");
  }
  validateSiteReleaseCheckpointDocument(document, { eventId });
  if (!canonicalBytes(document).equals(value.bytes)) {
    throw new Error("occurrence site checkpoint is not canonical");
  }
  return Object.freeze({
    document,
    bytes: value.bytes,
    sha256: sha256(value.bytes),
  });
}

export const siteReleaseCheckpointContract = Object.freeze({
  contract: "verdify.lab-occurrence-site-publish-checkpoint",
  schemaVersion: 1,
  key: "checkpoints/sha256/<sha256(eventId)>.json",
  maximumBytes: MAX_CHECKPOINT_BYTES,
  retentionSeconds: 14 * 24 * 60 * 60,
});
