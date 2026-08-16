// Trusted immutable production-snapshot activation resolver.
//
// `verify-production-output.mjs` has always required an activation-eligible,
// non-provisional build and has never had a producer. This module is that
// producer, and it is deliberately the narrowest one that can exist:
//
//   * The ONLY thing that can make a snapshot activation-eligible is a record in
//     `PRODUCTION_ACTIVATION_REGISTRY` below. That registry is a frozen module
//     constant compiled into the build. It is not read from the environment, a
//     CLI flag, a build argument, the snapshot payload, an object store, or any
//     other runtime input. Adding an entry is an explicit source change on
//     `main`; the immutable, digest-bound record is the activation mechanism.
//   * The registry ships EMPTY. Merging this contract changes no build's
//     verdict; every snapshot stays `provisional-only` / `activationEligible:
//     false` until a separate source change records an activation.
//   * An activation names exactly one snapshot by content. It binds the
//     sanitized content-manifest digest, the source-capture manifest digest,
//     the file count, the zero-finding public-output guard report digest, the
//     sanitization policy version, and the SHA-256 of the snapshot's own
//     attestation bytes. One flipped byte anywhere in the snapshot invalidates
//     it; an activation cannot be replayed onto a different capture.
//   * The snapshot must carry a canonical `activation.json` whose SHA-256 equals
//     the registry entry's `activationSha256` AND whose every field equals the
//     registry entry field-for-field. Editing the record breaks the digest;
//     editing the registry is a source diff.
//   * The record names its provenance in the open: the authoritative source
//     URI and capture instant, the activation actor, the permalink to the source
//     commit, the activation instant, the immutable GitHub release tag,
//     and the release asset digest.
//
// A fixture can never take this path (the fixture layout forbids
// `activation.json` and the fixture branch is mutually exclusive). The legacy
// provisional capture can never take this path either: it carries the
// `verdify.lab-stage-sanitized-snapshot` contract, whose verifier still
// hard-rejects its immutable v1 `approvalEligible !== false` wire verdict and
// normalizes it to `activationEligible: false`; relabelling it would change its
// attestation digest, which is pinned by its own release descriptor.

const SHA256_RE = /^[0-9a-f]{64}$/;
const ACTIVATION_ID_RE = /^lab-production-snapshot-[0-9]{8}t[0-9]{4}z$/;
const RELEASE_TAG_RE = /^lab-production-snapshot-[0-9]{8}t[0-9]{4}z$/;
const UTC_INSTANT_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/;
const POLICY_VERSION_RE = /^verdify-public-output-production-v[1-9][0-9]{0,2}$/;
const SOURCE_ORIGIN_RE = /^s3:\/\/[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\/lab\/content$/;
const ACTIVATION_RECORD_URL_RE =
  /^https:\/\/github\.com\/VerdifyConsultancy\/verdify-platform\/commit\/[0-9a-f]{40}$/;

export const PRODUCTION_ACTIVATION_CONTRACT = "verdify.lab-production-snapshot-activation";
export const PRODUCTION_SNAPSHOT_CONTRACT = "verdify.lab-production-sanitized-snapshot";
export const PRODUCTION_RELEASE_CONTRACT = "verdify.lab-production-snapshot-release";
export const PRODUCTION_EVIDENCE_STATUS = "active-immutable";

// A production activation is created by a repository source change, not by a
// separate person or review role.
export const ACTIVATION_ACTORS = Object.freeze(["repository-change"]);

export const ACTIVATION_KEYS = Object.freeze([
  "contract",
  "schemaVersion",
  "activationId",
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
  "activationActor",
  "activationRecordUrl",
  "activatedAt",
  "releaseTag",
  "assetSha256",
]);

export const REGISTRY_ENTRY_KEYS = Object.freeze([...ACTIVATION_KEYS, "activationSha256"]);

// ---------------------------------------------------------------------------
// The activation registry.
//
// EMPTY BY DESIGN. An entry must bind the exact snapshot, carry its sanitization
// report, link the source commit in `activationRecordUrl`, and pin the published
// immutable release asset. The entry itself is the explicit activation record.
// ---------------------------------------------------------------------------
export const PRODUCTION_ACTIVATION_REGISTRY = Object.freeze([]);

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
 * entry. Structural only: it proves the document is a well-formed activation, not
 * that it is a trusted one.
 */
export function assertActivationShape(record, label) {
  if (!exactOrderedKeys(record, ACTIVATION_KEYS)) {
    throw new Error(`${label} does not use the closed production-activation v1 shape`);
  }
  if (record.contract !== PRODUCTION_ACTIVATION_CONTRACT || record.schemaVersion !== 1) {
    throw new Error(`${label} is not a production-activation v1 contract`);
  }
  if (typeof record.activationId !== "string" || !ACTIVATION_ID_RE.test(record.activationId)) {
    throw new Error(`${label} has an invalid activation id`);
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
  if (!isCanonicalUtcInstant(record.sourceCapturedAt) || !isCanonicalUtcInstant(record.activatedAt)) {
    throw new Error(`${label} has a non-canonical UTC instant`);
  }
  if (Date.parse(record.activatedAt) < Date.parse(record.sourceCapturedAt)) {
    throw new Error(`${label} was active before its source was captured`);
  }
  if (typeof record.activationActor !== "string" || !ACTIVATION_ACTORS.includes(record.activationActor)) {
    throw new Error(`${label} does not name the repository-change activation actor`);
  }
  if (typeof record.activationRecordUrl !== "string" || !ACTIVATION_RECORD_URL_RE.test(record.activationRecordUrl)) {
    throw new Error(`${label} does not link its source commit in this repository`);
  }
  if (typeof record.releaseTag !== "string" || !RELEASE_TAG_RE.test(record.releaseTag)) {
    throw new Error(`${label} does not name an immutable production release tag`);
  }
  return record;
}

function assertRegistryEntry(entry, index) {
  const label = `production activation registry entry ${index}`;
  if (!exactKeySet(entry, REGISTRY_ENTRY_KEYS)) {
    throw new Error(`${label} does not use the closed registry-entry shape`);
  }
  if (typeof entry.activationSha256 !== "string" || !SHA256_RE.test(entry.activationSha256)) {
    throw new Error(`${label} has an invalid activationSha256`);
  }
  const record = {};
  for (const key of ACTIVATION_KEYS) record[key] = entry[key];
  assertActivationShape(record, label);
  return entry;
}

/**
 * Fail loudly at import time rather than silently accepting a half-written
 * activation. A malformed registry breaks every Lab build, which is the correct
 * failure mode for the file that decides what production evidence is.
 */
export function assertRegistryIntegrity(registry) {
  if (!Array.isArray(registry)) throw new Error("production activation registry must be an array");
  const seen = new Set();
  registry.forEach((entry, index) => {
    assertRegistryEntry(entry, index);
    for (const key of ["activationId", "activationSha256", "sanitizedManifestSha256", "snapshotAttestationSha256"]) {
      const unique = `${key}:${entry[key]}`;
      if (seen.has(unique)) throw new Error(`production activation registry repeats ${key}`);
      seen.add(unique);
    }
  });
  return registry;
}

assertRegistryIntegrity(PRODUCTION_ACTIVATION_REGISTRY);

/**
 * Resolve a snapshot's on-disk activation record against a registry.
 *
 * @param {Buffer} activationBytes raw `activation.json` bytes
 * @param {object} bindings values independently recomputed from the snapshot
 * @param {ReadonlyArray<object>} registry trusted activations
 */
export function verifyProductionActivation(activationBytes, bindings, registry) {
  assertRegistryIntegrity(registry);
  const text = activationBytes.toString("utf8");
  let record;
  try {
    record = JSON.parse(text);
  } catch {
    throw new Error("production snapshot activation is not valid JSON");
  }
  assertActivationShape(record, "production snapshot activation");
  if (`${JSON.stringify(record, null, 2)}\n` !== text) {
    throw new Error("production snapshot activation must be canonical JSON");
  }

  const digest = bindings.activationDigest;
  if (typeof digest !== "string" || !SHA256_RE.test(digest)) {
    throw new Error("production snapshot activation digest was not computed");
  }

  const matches = registry.filter((entry) => entry.activationId === record.activationId);
  if (matches.length !== 1) {
    throw new Error("production snapshot activation is not in the validated activation registry");
  }
  const entry = matches[0];
  if (entry.activationSha256 !== digest) {
    throw new Error("production snapshot activation bytes do not match their registered activation digest");
  }
  for (const key of ACTIVATION_KEYS) {
    if (record[key] !== entry[key]) {
      throw new Error(`production snapshot activation disagrees with its registry entry: ${key}`);
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
      throw new Error(`production snapshot activation does not bind the supplied snapshot: ${key}`);
    }
  }
  return record;
}

// The fields of an activation that may be published.
//
// `verifySnapshot`'s result is spread into `build.sanitization`, which the
// compiler writes to `dist/static-build.json` — a file served at the public site
// root (it is the deployment's readiness-probe path and `verify-live-occurrences.mjs`
// fetches it over HTTP). Everything here therefore appears on lab.verdify.ai.
//
// `sourceOrigin` is deliberately EXCLUDED: it names the private Lab content
// bucket, which is infrastructure detail, not public evidence. It stays in the
// snapshot's `activation.json` and in the validated registry entry, where auditors
// can read it. Every content digest a reader needs to verify the claim is
// already published in the attestation.
const PUBLISHED_ACTIVATION_KEYS = Object.freeze([
  "contract",
  "schemaVersion",
  "activationId",
  "sourceCapturedAt",
  "occurrenceSelectionPolicySha256",
  "activationActor",
  "activationRecordUrl",
  "activatedAt",
  "releaseTag",
  "assetSha256",
]);

export function publishedActivationIdentity(record) {
  return Object.freeze(Object.fromEntries(PUBLISHED_ACTIVATION_KEYS.map((key) => [key, record[key]])));
}
