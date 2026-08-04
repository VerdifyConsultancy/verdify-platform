// Trusted immutable production-snapshot approval resolver.
//
// `verify-production-output.mjs` has always required an approval-eligible,
// non-provisional build and has never had a producer. This module is that
// producer, and it is deliberately the narrowest one that can exist:
//
//   * The ONLY thing that can make a snapshot approval-eligible is a record in
//     `PRODUCTION_APPROVAL_REGISTRY` below. That registry is a frozen module
//     constant compiled into the build. It is not read from the environment, a
//     CLI flag, a build argument, the snapshot payload, an object store, or any
//     other runtime input. Adding an entry is a reviewed source change on
//     `main` — that review IS the approval gate.
//   * The registry ships EMPTY. Merging this contract changes no build's
//     verdict; every snapshot stays `provisional-only` / `approvalEligible:
//     false` until a separate, separately reviewed PR records an approval.
//   * An approval names exactly one snapshot by content. It binds the
//     sanitized content-manifest digest, the source-capture manifest digest,
//     the file count, the zero-finding public-output guard report digest, the
//     sanitization policy version, and the SHA-256 of the snapshot's own
//     attestation bytes. One flipped byte anywhere in the snapshot invalidates
//     it; an approval cannot be replayed onto a different capture.
//   * The snapshot must carry a canonical `approval.json` whose SHA-256 equals
//     the registry entry's `approvalSha256` AND whose every field equals the
//     registry entry field-for-field. Editing the record breaks the digest;
//     editing the registry is a source diff.
//   * The record names its provenance in the open: the authoritative source
//     URI and capture instant, the approver, the permalink to the recorded
//     human decision, the approval instant, the immutable GitHub release tag,
//     and the release asset digest.
//
// A fixture can never take this path (the fixture layout forbids
// `approval.json` and the fixture branch is mutually exclusive). The legacy
// provisional capture can never take this path either: it carries the
// `verdify.lab-stage-sanitized-snapshot` contract, whose verifier still
// hard-rejects `approvalEligible !== false`, and relabelling it would change
// its attestation digest, which is pinned by its own release descriptor.

const SHA256_RE = /^[0-9a-f]{64}$/;
const APPROVAL_ID_RE = /^lab-production-snapshot-[0-9]{8}t[0-9]{4}z$/;
const RELEASE_TAG_RE = /^lab-production-snapshot-[0-9]{8}t[0-9]{4}z$/;
const UTC_INSTANT_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/;
const POLICY_VERSION_RE = /^verdify-public-output-production-v[1-9][0-9]{0,2}$/;
const SOURCE_ORIGIN_RE = /^s3:\/\/[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\/lab\/content$/;
const APPROVAL_RECORD_URL_RE =
  /^https:\/\/github\.com\/VerdifyConsultancy\/verdify-platform\/issues\/[1-9][0-9]{0,9}#issuecomment-[1-9][0-9]{0,18}$/;

export const PRODUCTION_APPROVAL_CONTRACT = "verdify.lab-production-snapshot-approval";
export const PRODUCTION_SNAPSHOT_CONTRACT = "verdify.lab-production-sanitized-snapshot";
export const PRODUCTION_RELEASE_CONTRACT = "verdify.lab-production-snapshot-release";
export const PRODUCTION_EVIDENCE_STATUS = "approved-immutable";

// The recorded approval authority for Verdify Lab production evidence. An
// approval signed by anyone else is rejected even if every digest matches.
export const APPROVAL_AUTHORITIES = Object.freeze(["jvallery"]);

export const APPROVAL_KEYS = Object.freeze([
  "contract",
  "schemaVersion",
  "approvalId",
  "snapshotAttestationSha256",
  "sanitizedManifestSha256",
  "sourceManifestSha256",
  "sanitizedFileCount",
  "sourceFileCount",
  "policyVersion",
  "guardReportSha256",
  "sourceOrigin",
  "sourceCapturedAt",
  "occurrenceSelectionPolicySha256",
  "approver",
  "approvalRecordUrl",
  "approvedAt",
  "releaseTag",
  "assetSha256",
]);

export const REGISTRY_ENTRY_KEYS = Object.freeze([...APPROVAL_KEYS, "approvalSha256"]);

// ---------------------------------------------------------------------------
// The approval registry.
//
// EMPTY BY DESIGN. Do not add an entry to make a build pass. An entry may only
// be added by a PR that (a) links the permalink in `approvalRecordUrl` to a
// comment in which the named approver explicitly approves that exact snapshot,
// (b) carries the sanitization report for the capture, and (c) pins the
// published immutable release asset. Reviewers: an entry here is the whole
// approval. Read it, do not skim it.
// ---------------------------------------------------------------------------
export const PRODUCTION_APPROVAL_REGISTRY = Object.freeze([]);

function isPlainObject(value) {
  return (
    value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
  );
}

function exactOrderedKeys(value, keys) {
  return isPlainObject(value) && Object.keys(value).join(",") === keys.join(",");
}

function exactKeySet(value, keys) {
  return isPlainObject(value) && [...Object.keys(value)].sort().join(",") === [...keys].sort().join(",");
}

function isCanonicalUtcInstant(value) {
  if (typeof value !== "string" || !UTC_INSTANT_RE.test(value)) return false;
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().replace(/\.000Z$/, "Z") === value;
}

function isBoundedCount(value, maximum) {
  return Number.isSafeInteger(value) && value >= 0 && value <= maximum;
}

const MAX_SNAPSHOT_FILES = 10_000;

/**
 * Field-level shape validation shared by the on-disk record and every registry
 * entry. Structural only: it proves the document is a well-formed approval, not
 * that it is a trusted one.
 */
export function assertApprovalShape(record, label) {
  if (!exactOrderedKeys(record, APPROVAL_KEYS)) {
    throw new Error(`${label} does not use the closed production-approval v1 shape`);
  }
  if (record.contract !== PRODUCTION_APPROVAL_CONTRACT || record.schemaVersion !== 1) {
    throw new Error(`${label} is not a production-approval v1 contract`);
  }
  if (typeof record.approvalId !== "string" || !APPROVAL_ID_RE.test(record.approvalId)) {
    throw new Error(`${label} has an invalid approval id`);
  }
  for (const key of [
    "snapshotAttestationSha256",
    "sanitizedManifestSha256",
    "sourceManifestSha256",
    "guardReportSha256",
    "occurrenceSelectionPolicySha256",
    "assetSha256",
  ]) {
    if (typeof record[key] !== "string" || !SHA256_RE.test(record[key])) {
      throw new Error(`${label} has an invalid ${key}`);
    }
  }
  if (
    !isBoundedCount(record.sanitizedFileCount, MAX_SNAPSHOT_FILES)
    || record.sanitizedFileCount < 1
    || !isBoundedCount(record.sourceFileCount, MAX_SNAPSHOT_FILES)
    || record.sourceFileCount < record.sanitizedFileCount
  ) {
    throw new Error(`${label} has an invalid file-count binding`);
  }
  if (typeof record.policyVersion !== "string" || !POLICY_VERSION_RE.test(record.policyVersion)) {
    throw new Error(`${label} does not name a production public-output policy version`);
  }
  if (typeof record.sourceOrigin !== "string" || !SOURCE_ORIGIN_RE.test(record.sourceOrigin)) {
    throw new Error(`${label} does not name the authoritative Lab content source`);
  }
  if (!isCanonicalUtcInstant(record.sourceCapturedAt) || !isCanonicalUtcInstant(record.approvedAt)) {
    throw new Error(`${label} has a non-canonical UTC instant`);
  }
  if (Date.parse(record.approvedAt) < Date.parse(record.sourceCapturedAt)) {
    throw new Error(`${label} was approved before its source was captured`);
  }
  if (typeof record.approver !== "string" || !APPROVAL_AUTHORITIES.includes(record.approver)) {
    throw new Error(`${label} is not signed by a recorded approval authority`);
  }
  if (typeof record.approvalRecordUrl !== "string" || !APPROVAL_RECORD_URL_RE.test(record.approvalRecordUrl)) {
    throw new Error(`${label} does not link a recorded approval comment in this repository`);
  }
  if (typeof record.releaseTag !== "string" || !RELEASE_TAG_RE.test(record.releaseTag)) {
    throw new Error(`${label} does not name an immutable production release tag`);
  }
  return record;
}

function assertRegistryEntry(entry, index) {
  const label = `production approval registry entry ${index}`;
  if (!exactKeySet(entry, REGISTRY_ENTRY_KEYS)) {
    throw new Error(`${label} does not use the closed registry-entry shape`);
  }
  if (typeof entry.approvalSha256 !== "string" || !SHA256_RE.test(entry.approvalSha256)) {
    throw new Error(`${label} has an invalid approvalSha256`);
  }
  const record = {};
  for (const key of APPROVAL_KEYS) record[key] = entry[key];
  assertApprovalShape(record, label);
  return entry;
}

/**
 * Fail loudly at import time rather than silently accepting a half-written
 * approval. A malformed registry breaks every Lab build, which is the correct
 * failure mode for the file that decides what production evidence is.
 */
export function assertRegistryIntegrity(registry) {
  if (!Array.isArray(registry)) throw new Error("production approval registry must be an array");
  const seen = new Set();
  registry.forEach((entry, index) => {
    assertRegistryEntry(entry, index);
    for (const key of ["approvalId", "approvalSha256", "sanitizedManifestSha256", "snapshotAttestationSha256"]) {
      const unique = `${key}:${entry[key]}`;
      if (seen.has(unique)) throw new Error(`production approval registry repeats ${key}`);
      seen.add(unique);
    }
  });
  return registry;
}

assertRegistryIntegrity(PRODUCTION_APPROVAL_REGISTRY);

/**
 * Resolve a snapshot's on-disk approval record against a registry.
 *
 * @param {Buffer} approvalBytes raw `approval.json` bytes
 * @param {object} bindings values independently recomputed from the snapshot
 * @param {ReadonlyArray<object>} registry trusted approvals
 */
export function verifyProductionApproval(approvalBytes, bindings, registry) {
  assertRegistryIntegrity(registry);
  const text = approvalBytes.toString("utf8");
  let record;
  try {
    record = JSON.parse(text);
  } catch {
    throw new Error("production snapshot approval is not valid JSON");
  }
  assertApprovalShape(record, "production snapshot approval");
  if (`${JSON.stringify(record, null, 2)}\n` !== text) {
    throw new Error("production snapshot approval must be canonical JSON");
  }

  const digest = bindings.approvalDigest;
  if (typeof digest !== "string" || !SHA256_RE.test(digest)) {
    throw new Error("production snapshot approval digest was not computed");
  }

  const matches = registry.filter((entry) => entry.approvalId === record.approvalId);
  if (matches.length !== 1) {
    throw new Error("production snapshot approval is not in the reviewed approval registry");
  }
  const entry = matches[0];
  if (entry.approvalSha256 !== digest) {
    throw new Error("production snapshot approval bytes do not match their registered approval digest");
  }
  for (const key of APPROVAL_KEYS) {
    if (record[key] !== entry[key]) {
      throw new Error(`production snapshot approval disagrees with its registry entry: ${key}`);
    }
  }

  // Independently recomputed from the snapshot on disk, never read from the
  // record being validated.
  const observed = {
    snapshotAttestationSha256: bindings.attestationSha256,
    sanitizedManifestSha256: bindings.sanitizedManifestSha256,
    sourceManifestSha256: bindings.sourceManifestSha256,
    sanitizedFileCount: bindings.sanitizedFileCount,
    sourceFileCount: bindings.sourceFileCount,
    policyVersion: bindings.policyVersion,
    guardReportSha256: bindings.guardReportSha256,
  };
  for (const [key, value] of Object.entries(observed)) {
    if (record[key] !== value) {
      throw new Error(`production snapshot approval does not bind the supplied snapshot: ${key}`);
    }
  }
  return record;
}

// The fields of an approval that may be published.
//
// `verifySnapshot`'s result is spread into `build.sanitization`, which the
// compiler writes to `dist/static-build.json` — a file served at the public site
// root (it is the deployment's readiness-probe path and `verify-live-occurrences.mjs`
// fetches it over HTTP). Everything here therefore appears on lab.verdify.ai.
//
// `sourceOrigin` is deliberately EXCLUDED: it names the private Lab content
// bucket, which is infrastructure detail, not public evidence. It stays in the
// snapshot's `approval.json` and in the reviewed registry entry, where auditors
// can read it. Every content digest a reader needs to verify the claim is
// already published in the attestation.
const PUBLISHED_APPROVAL_KEYS = Object.freeze([
  "contract",
  "schemaVersion",
  "approvalId",
  "sourceCapturedAt",
  "occurrenceSelectionPolicySha256",
  "approver",
  "approvalRecordUrl",
  "approvedAt",
  "releaseTag",
  "assetSha256",
]);

export function publishedApprovalIdentity(record) {
  return Object.freeze(Object.fromEntries(PUBLISHED_APPROVAL_KEYS.map((key) => [key, record[key]])));
}
