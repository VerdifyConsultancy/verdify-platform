import { createHash } from "node:crypto";

import { S3ObjectStore } from "./s3-object-store.mjs";

const NONCE_RE = /^[A-Za-z0-9_-]{8,128}$/u;
const MAX_PROOF_BYTES = 4096;
const ROLES = Object.freeze(["site", "occurrence", "coordination"]);

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function strictInstant(value) {
  const parsed = Date.parse(value);
  if (
    typeof value !== "string"
    || !Number.isFinite(parsed)
    || new Date(parsed).toISOString() !== value
  ) throw new Error("release storage proof time is invalid");
  return value;
}

function stores(input) {
  const selected = {
    site: input.siteObjects,
    occurrence: input.occurrenceObjects,
    coordination: input.coordinationObjects,
  };
  if (ROLES.some((role) => !(selected[role] instanceof S3ObjectStore))) {
    throw new Error("release storage activation proof requires three initialized S3 object stores");
  }
  if (ROLES.some((role) => selected[role].accessMode !== "writer")) {
    throw new Error("release storage activation proof requires writer stores");
  }
  const buckets = new Set(ROLES.map((role) => selected[role].bucket));
  const prefixes = ROLES.map((role) => selected[role].prefix);
  if (
    buckets.size !== 1
    || new Set(prefixes).size !== ROLES.length
    || prefixes.some((prefix, index) => prefixes.some(
      (other, otherIndex) => index !== otherIndex
        && (prefix.startsWith(`${other}/`) || other.startsWith(`${prefix}/`)),
    ))
  ) throw new Error("release storage activation proof requires three dedicated non-overlapping prefixes");
  return selected;
}

function emptyEvidence() {
  return {
    created: false,
    read: false,
    head: false,
    existingKeyCreateRejected: false,
    existingKeyPreserved: false,
    conditionalWrite: false,
    staleWriteRejected: false,
    staleDeleteRejected: false,
    deleted: false,
    absentAfterDelete: false,
    cleanupAttempted: false,
    cleanupComplete: false,
  };
}

async function cleanupProbe(store, key, ownedBodies, evidence) {
  evidence.cleanupAttempted = true;
  try {
    const current = await store.read(key, {
      missing: true,
      maximumBytes: MAX_PROOF_BYTES,
      label: "release storage activation proof object",
    });
    if (current !== null) {
      // A failed absent-only create may have collided with unrelated bytes. Only
      // the exact bounded probe body is owned by this proof and safe to remove.
      if (!ownedBodies.some((bytes) => current.bytes.equals(bytes))) return;
      const deleted = await store.deleteIfMatch(key, current.etag);
      if (!deleted.deleted) return;
    }
    evidence.cleanupComplete = await store.head(key, {
      missing: true,
      label: "release storage activation proof object",
    }) === null;
  } catch {
    evidence.cleanupComplete = false;
  }
}

async function probePrefix(store, role, nonce, probedAt) {
  const evidence = emptyEvidence();
  const key = `activation-proof/${sha256(Buffer.from(`${nonce}\u0000${role}`))}.json`;
  const bytes = canonicalBytes({
    contract: "verdify.lab-release-storage-activation-probe",
    schemaVersion: 1,
    role,
    nonceSha256: sha256(Buffer.from(nonce)),
    probedAt,
  });
  const conditionalBytes = canonicalBytes({
    contract: "verdify.lab-release-storage-activation-probe",
    schemaVersion: 1,
    role,
    nonceSha256: sha256(Buffer.from(nonce)),
    probedAt,
    phase: "conditional-replacement",
  });
  const absentOnlyBytes = canonicalBytes({
    contract: "verdify.lab-release-storage-activation-probe",
    schemaVersion: 1,
    role,
    nonceSha256: sha256(Buffer.from(nonce)),
    probedAt,
    phase: "must-not-replace-existing-key",
  });
  if (
    bytes.length > MAX_PROOF_BYTES
    || conditionalBytes.length > MAX_PROOF_BYTES
    || absentOnlyBytes.length > MAX_PROOF_BYTES
  ) {
    throw new Error("release storage activation proof body exceeds its bound");
  }
  let etag = null;
  try {
    const created = await store.putIfAbsent(key, bytes, {
      contentType: "application/json",
    });
    evidence.created = created.written === true;
    etag = created.etag;
    if (!evidence.created || typeof etag !== "string") return evidence;
    const read = await store.read(key, {
      maximumBytes: MAX_PROOF_BYTES,
      label: "release storage activation proof object",
    });
    evidence.read = read.bytes.equals(bytes) && read.etag === etag;
    const head = await store.head(key, {
      label: "release storage activation proof object",
    });
    evidence.head = head.bytes === bytes.length && head.etag === etag;
    const absentOnly = await store.putIfAbsent(key, absentOnlyBytes, {
      contentType: "application/json",
    });
    evidence.existingKeyCreateRejected = absentOnly.written === false;
    const preserved = await store.read(key, {
      maximumBytes: MAX_PROOF_BYTES,
      label: "release storage activation proof absent-only object",
    });
    evidence.existingKeyPreserved = preserved.bytes.equals(bytes) && preserved.etag === etag;
    if (!evidence.existingKeyCreateRejected || !evidence.existingKeyPreserved) return evidence;
    const replaced = await store.putIfMatch(key, conditionalBytes, etag, {
      contentType: "application/json",
    });
    evidence.conditionalWrite = replaced.written === true
      && typeof replaced.etag === "string"
      && replaced.etag !== etag;
    if (!evidence.conditionalWrite) return evidence;
    const staleWrite = await store.putIfMatch(key, bytes, etag, {
      contentType: "application/json",
    });
    evidence.staleWriteRejected = staleWrite.written === false;
    const staleDelete = await store.deleteIfMatch(key, etag);
    evidence.staleDeleteRejected = staleDelete.deleted === false;
    const selected = await store.read(key, {
      maximumBytes: MAX_PROOF_BYTES,
      label: "release storage activation proof conditional object",
    });
    if (
      !selected.bytes.equals(conditionalBytes)
      || selected.etag !== replaced.etag
    ) return evidence;
    const deleted = await store.deleteIfMatch(key, replaced.etag);
    evidence.deleted = deleted.deleted === true;
    evidence.absentAfterDelete = evidence.deleted && await store.head(key, {
      missing: true,
      label: "release storage activation proof object",
    }) === null;
    return evidence;
  } catch {
    return evidence;
  } finally {
    await cleanupProbe(store, key, [bytes, absentOnlyBytes, conditionalBytes], evidence);
  }
}

function verified(evidence) {
  return evidence.created
    && evidence.read
    && evidence.head
    && evidence.existingKeyCreateRejected
    && evidence.existingKeyPreserved
    && evidence.conditionalWrite
    && evidence.staleWriteRejected
    && evidence.staleDeleteRejected
    && evidence.deleted
    && evidence.absentAfterDelete
    && evidence.cleanupAttempted
    && evidence.cleanupComplete;
}

export class ReleaseStorageS3ActivationProofError extends Error {
  constructor(result) {
    super("release storage activation proof did not verify every dedicated prefix");
    this.name = "ReleaseStorageS3ActivationProofError";
    this.result = result;
  }
}

export async function proveReleaseStorageS3Activation({
  siteObjects,
  occurrenceObjects,
  coordinationObjects,
  nonce,
  probedAt,
}) {
  if (typeof nonce !== "string" || !NONCE_RE.test(nonce)) {
    throw new Error("release storage activation proof nonce is invalid");
  }
  strictInstant(probedAt);
  const selected = stores({ siteObjects, occurrenceObjects, coordinationObjects });
  const results = await Promise.all(ROLES.map(
    (role) => probePrefix(selected[role], role, nonce, probedAt),
  ));
  const prefixes = Object.fromEntries(ROLES.map((role, index) => [role, results[index]]));
  const result = {
    contract: "verdify.lab-release-storage-activation-proof",
    schemaVersion: 1,
    status: results.every(verified) ? "verified" : "failed",
    probedAt,
    boundedCreateReadHeadDelete: results.every((evidence) => (
      evidence.created && evidence.read && evidence.head && evidence.deleted
    )),
    staleConditionalOperationsRejected: results.every((evidence) => (
      evidence.existingKeyCreateRejected
      && evidence.existingKeyPreserved
      && evidence.conditionalWrite
      && evidence.staleWriteRejected
      && evidence.staleDeleteRejected
    )),
    absentOnlySemanticsVerified: results.every((evidence) => (
      evidence.existingKeyCreateRejected && evidence.existingKeyPreserved
    )),
    cleanupComplete: results.every((evidence) => evidence.cleanupComplete),
    dedicatedPrefixesVerified: results.every(verified),
    prefixes,
  };
  if (!result.dedicatedPrefixesVerified) {
    throw new ReleaseStorageS3ActivationProofError(result);
  }
  return result;
}

export const releaseStorageS3ActivationProofContract = Object.freeze({
  mutating: true,
  maximumObjectBytes: MAX_PROOF_BYTES,
  conditionalSemantics: Object.freeze([
    "existing-key-create-rejected",
    "existing-key-preserved",
    "current-write-succeeds",
    "stale-write-rejected",
    "stale-delete-rejected",
    "current-delete-succeeds",
  ]),
  roles: ROLES,
  contract: "verdify.lab-release-storage-activation-proof",
  schemaVersion: 1,
});
