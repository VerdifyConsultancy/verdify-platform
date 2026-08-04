import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, opendir, readFile, readdir, realpath } from "node:fs/promises";
import path from "node:path";

import {
  PRODUCTION_APPROVAL_REGISTRY,
  PRODUCTION_EVIDENCE_STATUS,
  PRODUCTION_SNAPSHOT_CONTRACT,
  publishedApprovalIdentity,
  verifyProductionApproval,
} from "./production-approval.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/;
const STAGE_SNAPSHOT_CONTRACT = "verdify.lab-stage-sanitized-snapshot";
const MANDATORY_APPROVAL_BOUNDARY = "approved immutable filesystem/object-store snapshot attestation";
const MAX_FILES = 10_000;
const MAX_FILE_BYTES = 128 * 1024 * 1024;
const MAX_TOTAL_BYTES = 1024 * 1024 * 1024;
const MAX_DEPTH = 24;
const SOURCE_MANIFEST_SHA256 = "05d4373ebf59bef3a7899c5e94514971d663fd7264db09b2b5cb26fec78410b1";
const ATTESTATION_KEYS = [
  "contract",
  "schemaVersion",
  "evidenceStatus",
  "approvalEligible",
  "sourceManifestSha256",
  "sanitizedManifestSha256",
  "sourceFileCount",
  "sanitizedFileCount",
  "policyVersion",
  "guardReportSha256",
  "guardSchemaVersion",
  "guardFindings",
  "transformations",
];
const TRANSFORMATION_KEYS = [
  "changedFiles",
  "textRedactionFiles",
  "invalidValueRepairFiles",
  "pngReencodeFiles",
  "hlsFilesPreserved",
];

export async function sha256File(file) {
  const hash = createHash("sha256");
  await new Promise((resolve, reject) => {
    const stream = createReadStream(file);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("error", reject);
    stream.on("end", resolve);
  });
  return hash.digest("hex");
}

export async function sha256Bytes(file) {
  const bytes = await readFile(file);
  return {
    bytes,
    digest: createHash("sha256").update(bytes).digest("hex"),
  };
}

function safeManifestPath(value) {
  if (typeof value !== "string" || value.length === 0 || value.length > 4096 || value.includes("\\")) {
    throw new Error("snapshot manifest contains an invalid path");
  }
  const normalized = path.posix.normalize(value);
  if (normalized !== value || normalized.startsWith("/") || normalized === ".." || normalized.startsWith("../")) {
    throw new Error("snapshot manifest path escapes the content root");
  }
  return normalized;
}

async function inventoryTree(root) {
  const rootStat = await lstat(root);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) {
    throw new Error("snapshot content root must be a real directory");
  }
  if ((await realpath(root)) !== path.resolve(root)) {
    throw new Error("snapshot content root must not resolve through a symlink");
  }

  const files = new Map();
  const caseFolded = new Set();
  const pending = [{ absolute: root, relative: "", depth: 0 }];
  let totalBytes = 0;
  while (pending.length > 0) {
    const directory = pending.pop();
    if (directory.depth > MAX_DEPTH) throw new Error(`snapshot depth exceeds ${MAX_DEPTH}`);
    const handle = await opendir(directory.absolute);
    for await (const entry of handle) {
      const relative = directory.relative ? `${directory.relative}/${entry.name}` : entry.name;
      safeManifestPath(relative);
      const folded = relative.normalize("NFC").toLocaleLowerCase("en-US");
      if (caseFolded.has(folded)) throw new Error(`snapshot has a case-folded collision: ${relative}`);
      caseFolded.add(folded);
      const absolute = path.join(root, ...relative.split("/"));
      const metadata = await lstat(absolute, { bigint: true });
      if (metadata.isSymbolicLink()) throw new Error(`snapshot contains a symlink: ${relative}`);
      if (metadata.isDirectory()) {
        pending.push({ absolute, relative, depth: directory.depth + 1 });
        continue;
      }
      if (!metadata.isFile()) throw new Error(`snapshot contains a non-regular file: ${relative}`);
      if (metadata.nlink !== 1n) throw new Error(`snapshot contains a hardlinked file: ${relative}`);
      const size = Number(metadata.size);
      if (!Number.isSafeInteger(size) || size > MAX_FILE_BYTES) {
        throw new Error(`snapshot file exceeds ${MAX_FILE_BYTES} bytes: ${relative}`);
      }
      totalBytes += size;
      if (totalBytes > MAX_TOTAL_BYTES) throw new Error(`snapshot exceeds ${MAX_TOTAL_BYTES} bytes`);
      files.set(relative, { absolute, size });
      if (files.size > MAX_FILES) throw new Error(`snapshot contains more than ${MAX_FILES} files`);
    }
  }
  return files;
}

async function readBoundedRegularFile(file, maximumBytes) {
  const metadata = await lstat(file);
  if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.nlink !== 1 || metadata.size > maximumBytes) {
    throw new Error("snapshot metadata must be a bounded single-link regular file");
  }
  return readFile(file);
}

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).join(",") === keys.join(",");
}

async function verifySyntheticFixture(root) {
  const markerPath = path.join(root, "manifests", "synthetic-fixture.json");
  const bytes = await readBoundedRegularFile(markerPath, 1024);
  const expected = '{\n  "contract": "verdify.lab-stage-synthetic-fixture",\n  "schemaVersion": 1\n}\n';
  if (bytes.toString("utf8") !== expected) throw new Error("synthetic fixture marker is missing or noncanonical");
  return {
    fixtureOnly: true,
    sourceManifestSha256: null,
    sanitizedManifestSha256: null,
    policyVersion: "synthetic-fixture-only",
    guardReportSha256: null,
    transformations: null,
  };
}

export async function verifySanitizationAttestation(root, manifestDigest, inventory) {
  const bytes = await readBoundedRegularFile(path.join(root, "attestation.json"), 64 * 1024);
  let attestation;
  try {
    attestation = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("sanitized snapshot attestation is not valid JSON");
  }
  if (!exactKeys(attestation, ATTESTATION_KEYS) || !exactKeys(attestation.transformations, TRANSFORMATION_KEYS)) {
    throw new Error("sanitized snapshot attestation does not use the closed v1 shape");
  }
  if (`${JSON.stringify(attestation, null, 2)}\n` !== bytes.toString("utf8")) {
    throw new Error("sanitized snapshot attestation must be canonical JSON");
  }
  if (
    attestation.contract !== STAGE_SNAPSHOT_CONTRACT
    || attestation.schemaVersion !== 1
    || attestation.evidenceStatus !== "provisional-only"
    || attestation.approvalEligible !== false
    || attestation.sourceManifestSha256 !== SOURCE_MANIFEST_SHA256
    || attestation.sanitizedManifestSha256 !== manifestDigest
    || attestation.sourceFileCount !== 429
    || attestation.sanitizedFileCount !== 429
    || inventory.size !== attestation.sanitizedFileCount
    || attestation.policyVersion !== "verdify-public-output-stage-v1"
    || !SHA256_RE.test(attestation.guardReportSha256)
    || attestation.guardSchemaVersion !== 2
    || attestation.guardFindings !== 0
  ) {
    throw new Error("sanitized snapshot attestation does not match the stage release policy");
  }
  for (const key of TRANSFORMATION_KEYS) {
    const value = attestation.transformations[key];
    if (!Number.isSafeInteger(value) || value < 0 || value > attestation.sourceFileCount) {
      throw new Error(`sanitized snapshot transformation count is out of bounds: ${key}`);
    }
  }
  const expectedTransformations = {
    changedFiles: 8,
    textRedactionFiles: 3,
    invalidValueRepairFiles: 3,
    pngReencodeFiles: 3,
    hlsFilesPreserved: 179,
  };
  if (JSON.stringify(attestation.transformations) !== JSON.stringify(expectedTransformations)) {
    throw new Error("sanitized snapshot transformation counts do not match the reviewed release");
  }
  const actualHlsFiles = [...inventory.keys()].filter((relative) => relative.startsWith("static/video/")).length;
  if (attestation.transformations.hlsFilesPreserved !== actualHlsFiles) {
    throw new Error("sanitized snapshot HLS preservation count does not match the content tree");
  }
  return { ...attestation, fixtureOnly: false };
}

/**
 * Verify a PRODUCTION sanitized snapshot: the closed v1 attestation shape with
 * the production contract, plus a registry-trusted immutable approval record.
 *
 * Unlike the stage verifier this carries no hard-coded content pins — the pins
 * live in the reviewed approval registry entry, so one code path serves every
 * future approved capture without ever loosening.
 *
 * `registry` is a required argument with no default. The only production caller
 * is `verifySnapshot`, which passes the frozen `PRODUCTION_APPROVAL_REGISTRY`;
 * there is no runtime input that can substitute another one.
 */
export async function verifyProductionSanitizationAttestation(root, manifestDigest, inventory, registry) {
  const attestationBytes = await readBoundedRegularFile(path.join(root, "attestation.json"), 64 * 1024);
  const attestationText = attestationBytes.toString("utf8");
  let attestation;
  try {
    attestation = JSON.parse(attestationText);
  } catch {
    throw new Error("production snapshot attestation is not valid JSON");
  }
  if (!exactKeys(attestation, ATTESTATION_KEYS) || !exactKeys(attestation.transformations, TRANSFORMATION_KEYS)) {
    throw new Error("production snapshot attestation does not use the closed v1 shape");
  }
  if (`${JSON.stringify(attestation, null, 2)}\n` !== attestationText) {
    throw new Error("production snapshot attestation must be canonical JSON");
  }
  if (
    attestation.contract !== PRODUCTION_SNAPSHOT_CONTRACT
    || attestation.schemaVersion !== 1
    || attestation.evidenceStatus !== PRODUCTION_EVIDENCE_STATUS
    || attestation.approvalEligible !== true
    || !SHA256_RE.test(attestation.sourceManifestSha256)
    || attestation.sanitizedManifestSha256 !== manifestDigest
    || !Number.isSafeInteger(attestation.sanitizedFileCount)
    || attestation.sanitizedFileCount < 1
    || attestation.sanitizedFileCount > MAX_FILES
    || inventory.size !== attestation.sanitizedFileCount
    || !Number.isSafeInteger(attestation.sourceFileCount)
    || attestation.sourceFileCount < attestation.sanitizedFileCount
    || attestation.sourceFileCount > MAX_FILES
    || typeof attestation.policyVersion !== "string"
    || !SHA256_RE.test(attestation.guardReportSha256)
    || attestation.guardSchemaVersion !== 2
    || attestation.guardFindings !== 0
  ) {
    throw new Error("production snapshot attestation does not match the approved release policy");
  }
  for (const key of TRANSFORMATION_KEYS) {
    const value = attestation.transformations[key];
    if (!Number.isSafeInteger(value) || value < 0 || value > attestation.sourceFileCount) {
      throw new Error(`production snapshot transformation count is out of bounds: ${key}`);
    }
  }
  const { changedFiles, textRedactionFiles, invalidValueRepairFiles, pngReencodeFiles } = attestation.transformations;
  if (
    changedFiles > attestation.sanitizedFileCount
    || changedFiles > textRedactionFiles + invalidValueRepairFiles + pngReencodeFiles
    || Math.max(textRedactionFiles, invalidValueRepairFiles, pngReencodeFiles) > changedFiles
  ) {
    throw new Error("production snapshot transformation accounting is inconsistent");
  }
  const actualHlsFiles = [...inventory.keys()].filter((relative) => relative.startsWith("static/video/")).length;
  if (attestation.transformations.hlsFilesPreserved !== actualHlsFiles) {
    throw new Error("production snapshot HLS preservation count does not match the content tree");
  }

  const guardBytes = await readBoundedRegularFile(
    path.join(root, "evidence", "public-output-guard.json"),
    8 * 1024 * 1024,
  );
  const approvalBytes = await readBoundedRegularFile(path.join(root, "approval.json"), 64 * 1024);
  const approval = verifyProductionApproval(
    approvalBytes,
    {
      approvalDigest: createHash("sha256").update(approvalBytes).digest("hex"),
      attestationSha256: createHash("sha256").update(attestationBytes).digest("hex"),
      sanitizedManifestSha256: manifestDigest,
      sourceManifestSha256: attestation.sourceManifestSha256,
      sanitizedFileCount: inventory.size,
      sourceFileCount: attestation.sourceFileCount,
      policyVersion: attestation.policyVersion,
      guardReportSha256: createHash("sha256").update(guardBytes).digest("hex"),
    },
    registry,
  );
  // Only the publishable projection travels into the build identity; see
  // publishedApprovalIdentity for why the source bucket URI is withheld.
  return { ...attestation, fixtureOnly: false, approval: publishedApprovalIdentity(approval) };
}

async function verifyGuardEvidence(root, expectedDigest) {
  const evidenceDirectory = path.join(root, "evidence");
  if (JSON.stringify((await readdir(evidenceDirectory)).sort()) !== JSON.stringify(["public-output-guard.json"])) {
    throw new Error("sanitized snapshot evidence directory is not closed");
  }
  const bytes = await readBoundedRegularFile(path.join(evidenceDirectory, "public-output-guard.json"), 8 * 1024 * 1024);
  if (createHash("sha256").update(bytes).digest("hex") !== expectedDigest) {
    throw new Error("sanitized snapshot guard evidence does not match its attestation");
  }
  let report;
  try {
    report = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("sanitized snapshot guard evidence is not valid JSON");
  }
  if (
    !exactKeys(report, ["findings", "missing_roots", "roots", "routes", "schema_version"])
    || report.schema_version !== 2
    || !Array.isArray(report.roots)
    || report.roots.length !== 1
    || !exactKeys(report.roots[0], ["identity", "label"])
    || !SHA256_RE.test(report.roots[0].identity)
    || report.roots[0].label !== "content"
    || !Array.isArray(report.missing_roots)
    || report.missing_roots.length !== 0
    || !Array.isArray(report.routes)
    || report.routes.length !== 0
    || !Array.isArray(report.findings)
    || report.findings.length !== 0
  ) {
    throw new Error("sanitized snapshot guard evidence is not a closed zero-finding v2 report");
  }
}

/**
 * Dispatch a non-fixture snapshot to its declared closed contract.
 *
 * The snapshot's own attestation names which contract it claims; each verifier
 * then proves that claim independently. A production claim buys nothing on its
 * own — it only routes to the stricter verifier, which additionally demands a
 * registry-trusted approval. The stage verifier is untouched and still rejects
 * `approvalEligible !== false`, so the legacy provisional capture cannot be
 * relabelled into eligibility.
 */
async function verifyAttestedSnapshot(root, manifestDigest, inventory, registry) {
  const bytes = await readBoundedRegularFile(path.join(root, "attestation.json"), 64 * 1024);
  let declared;
  try {
    declared = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("sanitized snapshot attestation is not valid JSON");
  }
  return declared?.contract === PRODUCTION_SNAPSHOT_CONTRACT
    ? verifyProductionSanitizationAttestation(root, manifestDigest, inventory, registry)
    : verifySanitizationAttestation(root, manifestDigest, inventory);
}

/**
 * Public snapshot verifier.
 *
 * It accepts exactly one option, `allowSyntheticFixture`. The trusted approval
 * registry is NOT an option: it is bound here, once, to the frozen module
 * constant. A caller cannot widen trust by passing anything in.
 */
export async function verifySnapshot(snapshotRoot, { allowSyntheticFixture = false } = {}) {
  return resolveSnapshot(snapshotRoot, { allowSyntheticFixture }, PRODUCTION_APPROVAL_REGISTRY);
}

/**
 * Test seam. Identical to `verifySnapshot` except that the trusted registry is
 * supplied explicitly, so the accept direction can be proven end-to-end without
 * shipping a real approval. Production code must call `verifySnapshot`.
 */
export async function __resolveSnapshotWithRegistry(snapshotRoot, options, registry) {
  return resolveSnapshot(snapshotRoot, options, registry);
}

async function resolveSnapshot(snapshotRoot, { allowSyntheticFixture = false } = {}, registry = PRODUCTION_APPROVAL_REGISTRY) {
  const root = path.resolve(snapshotRoot);
  const contentRoot = path.join(root, "content");
  const manifestPath = path.join(root, "manifests", "content.json");
  const { bytes: manifestBytes, digest: manifestDigest } = await sha256Bytes(manifestPath);
  if (manifestBytes.length > 8 * 1024 * 1024) throw new Error("snapshot content manifest is too large");

  let manifest;
  try {
    manifest = JSON.parse(manifestBytes.toString("utf8"));
  } catch {
    throw new Error("snapshot content manifest is not valid JSON");
  }
  if (
    Object.getPrototypeOf(manifest) !== Object.prototype ||
    Object.keys(manifest).sort().join(",") !== "files,version" ||
    manifest.version !== 1 ||
    Object.getPrototypeOf(manifest.files) !== Object.prototype
  ) {
    throw new Error("snapshot content manifest must use the closed legacy v1 shape");
  }

  const inventory = await inventoryTree(contentRoot);
  const manifestPaths = Object.keys(manifest.files).sort();
  const inventoryPaths = [...inventory.keys()].sort();
  if (JSON.stringify(manifestPaths) !== JSON.stringify(inventoryPaths)) {
    throw new Error("snapshot manifest does not exactly match content-tree membership");
  }

  for (const relative of manifestPaths) {
    safeManifestPath(relative);
    const expected = manifest.files[relative];
    if (typeof expected !== "string" || !SHA256_RE.test(expected)) {
      throw new Error(`snapshot manifest has an invalid digest: ${relative}`);
    }
    const actual = await sha256File(inventory.get(relative).absolute);
    if (actual !== expected) throw new Error(`snapshot digest mismatch: ${relative}`);
  }

  const sanitization = allowSyntheticFixture
    ? await verifySyntheticFixture(root)
    : await verifyAttestedSnapshot(root, manifestDigest, inventory, registry);
  const approved = sanitization.contract === PRODUCTION_SNAPSHOT_CONTRACT;
  if (!sanitization.fixtureOnly) {
    const closedRoot = approved
      ? ["approval.json", "attestation.json", "content", "evidence", "manifests"]
      : ["attestation.json", "content", "evidence", "manifests"];
    if (JSON.stringify((await readdir(root)).sort()) !== JSON.stringify(closedRoot)) {
      throw new Error("sanitized snapshot root is not closed");
    }
    if (JSON.stringify((await readdir(path.join(root, "manifests"))).sort()) !== JSON.stringify(["content.json"])) {
      throw new Error("sanitized snapshot manifest directory is not closed");
    }
    await verifyGuardEvidence(root, sanitization.guardReportSha256);
  }

  return {
    contentRoot,
    files: inventory,
    manifest,
    manifestDigest: `sha256:${manifestDigest}`,
    snapshotId: `${sanitization.fixtureOnly ? "synthetic-fixture" : "sanitized-content"}-sha256:${manifestDigest}`,
    evidenceStatus: approved ? PRODUCTION_EVIDENCE_STATUS : "provisional-only",
    approvalEligible: approved,
    mandatoryApprovalBoundary: approved
      ? `satisfied by approval ${sanitization.approval.approvalId}`
      : MANDATORY_APPROVAL_BOUNDARY,
    sanitization,
  };
}
