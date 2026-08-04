import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  APPROVAL_KEYS,
  PRODUCTION_APPROVAL_REGISTRY,
  PRODUCTION_RELEASE_CONTRACT,
  PRODUCTION_SNAPSHOT_CONTRACT,
  verifyProductionApproval,
} from "../scripts/lib/production-approval.mjs";
import { __resolveSnapshotWithRegistry, verifySnapshot } from "../scripts/lib/snapshot.mjs";
import { extractVerifiedTar, readReleaseDescriptor } from "../scripts/fetch-stage-snapshot.mjs";

const CONTENT = {
  "index.md": "# Verdify Lab\n",
  "greenhouse/crops/lettuce.md": "Observation retained; historical image unavailable.\n",
  "static/video/tour/segment-0.ts": "hls-a\n",
  "static/video/tour/segment-1.ts": "hls-b\n",
};

function canonical(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function guardReport() {
  // Key order is load-bearing: the verifier compares the exact key sequence.
  return {
    findings: [],
    missing_roots: [],
    roots: [{ identity: "b".repeat(64), label: "content" }],
    routes: [],
    schema_version: 2,
  };
}

function attestation({ sanitizedManifestSha256, guardReportSha256, overrides = {} }) {
  return {
    contract: PRODUCTION_SNAPSHOT_CONTRACT,
    schemaVersion: 1,
    evidenceStatus: "approved-immutable",
    approvalEligible: true,
    sourceManifestSha256: "a".repeat(64),
    sanitizedManifestSha256,
    sourceFileCount: 6,
    sanitizedFileCount: Object.keys(CONTENT).length,
    policyVersion: "verdify-public-output-production-v1",
    guardReportSha256,
    guardSchemaVersion: 2,
    guardFindings: 0,
    transformations: {
      changedFiles: 2,
      textRedactionFiles: 1,
      invalidValueRepairFiles: 1,
      pngReencodeFiles: 1,
      hlsFilesPreserved: 2,
      ...(overrides.transformations ?? {}),
    },
    ...Object.fromEntries(Object.entries(overrides).filter(([key]) => key !== "transformations")),
  };
}

function approvalRecord({ attestationSha256, sanitizedManifestSha256, guardReportSha256, overrides = {} }) {
  const record = {
    contract: "verdify.lab-production-snapshot-approval",
    schemaVersion: 1,
    approvalId: "lab-production-snapshot-20260804t1200z",
    snapshotAttestationSha256: attestationSha256,
    sanitizedManifestSha256,
    sourceManifestSha256: "a".repeat(64),
    sanitizedFileCount: Object.keys(CONTENT).length,
    sourceFileCount: 6,
    policyVersion: "verdify-public-output-production-v1",
    guardReportSha256,
    sourceOrigin: "s3://verdify-lab-content/lab/content",
    sourceCapturedAt: "2026-08-04T06:00:00Z",
    occurrenceSelectionPolicySha256: "c".repeat(64),
    approver: "jvallery",
    approvalRecordUrl: "https://github.com/VerdifyConsultancy/verdify-platform/issues/570#issuecomment-5175264057",
    approvedAt: "2026-08-04T15:35:00Z",
    releaseTag: "lab-production-snapshot-20260804t1200z",
    assetSha256: "d".repeat(64),
  };
  // Preserve the closed key order while applying overrides.
  const merged = { ...record, ...overrides };
  return Object.fromEntries(APPROVAL_KEYS.map((key) => [key, merged[key]]));
}

function registryEntry(record, approvalBytes, overrides = {}) {
  return { ...record, approvalSha256: sha256(approvalBytes), ...overrides };
}

/**
 * Materialize a complete, closed production snapshot on disk and return every
 * digest a verifier will independently recompute.
 */
async function buildProductionSnapshot(context, { content = CONTENT, mutate = {} } = {}) {
  const base = await realpath(await mkdtemp(path.join(tmpdir(), "verdify-lab-approval-")));
  context.after(() => rm(base, { recursive: true, force: true }));
  const root = path.join(base, "snapshot");
  const files = {};
  for (const [relative, body] of Object.entries(content)) {
    const absolute = path.join(root, "content", ...relative.split("/"));
    await mkdir(path.dirname(absolute), { recursive: true });
    await writeFile(absolute, body);
    files[relative] = sha256(body);
  }

  await mkdir(path.join(root, "manifests"), { recursive: true });
  const manifestBytes = canonical({ version: 1, files });
  await writeFile(path.join(root, "manifests", "content.json"), manifestBytes);
  const sanitizedManifestSha256 = sha256(manifestBytes);

  await mkdir(path.join(root, "evidence"), { recursive: true });
  const guardBytes = canonical(mutate.guardReport ?? guardReport());
  await writeFile(path.join(root, "evidence", "public-output-guard.json"), guardBytes);
  const guardReportSha256 = sha256(guardBytes);

  const attestationBytes = canonical(
    attestation({ sanitizedManifestSha256, guardReportSha256, overrides: mutate.attestation ?? {} }),
  );
  await writeFile(path.join(root, "attestation.json"), attestationBytes);
  const attestationSha256 = sha256(attestationBytes);

  const record = approvalRecord({
    attestationSha256,
    sanitizedManifestSha256,
    guardReportSha256,
    overrides: mutate.approval ?? {},
  });
  const approvalBytes = mutate.approvalBytes ? mutate.approvalBytes(record) : canonical(record);
  await writeFile(path.join(root, "approval.json"), approvalBytes);

  return { root, record, approvalBytes, attestationSha256, sanitizedManifestSha256, guardReportSha256 };
}

async function resolveWith(snapshot, registryOverrides = {}) {
  const entry = registryEntry(snapshot.record, snapshot.approvalBytes, registryOverrides);
  return __resolveSnapshotWithRegistry(snapshot.root, {}, [entry]);
}

// ---------------------------------------------------------------------------
// The shipped default: nothing is approved.
// ---------------------------------------------------------------------------

test("the shipped approval registry is empty, so merging the contract approves nothing", () => {
  assert.deepEqual(PRODUCTION_APPROVAL_REGISTRY, []);
  assert.ok(Object.isFrozen(PRODUCTION_APPROVAL_REGISTRY));
});

test("a complete production snapshot is still rejected by the shipped registry", async (context) => {
  const snapshot = await buildProductionSnapshot(context);
  await assert.rejects(() => verifySnapshot(snapshot.root), /not in the reviewed approval registry/);
});

test("verifySnapshot never accepts a caller-supplied approval registry", async (context) => {
  const snapshot = await buildProductionSnapshot(context);
  const forged = [registryEntry(snapshot.record, snapshot.approvalBytes)];
  await assert.rejects(
    () => verifySnapshot(snapshot.root, { allowSyntheticFixture: false, registry: forged, approvalRegistry: forged }),
    /not in the reviewed approval registry/,
  );
});

// ---------------------------------------------------------------------------
// Accept direction: a properly attested and registered snapshot.
// ---------------------------------------------------------------------------

test("a registered, content-bound approval makes the snapshot approval-eligible", async (context) => {
  const snapshot = await buildProductionSnapshot(context);
  const resolved = await resolveWith(snapshot);
  assert.equal(resolved.approvalEligible, true);
  assert.equal(resolved.evidenceStatus, "approved-immutable");
  assert.equal(resolved.mandatoryApprovalBoundary, "satisfied by approval lab-production-snapshot-20260804t1200z");
  assert.equal(resolved.snapshotId, `sanitized-content-sha256:${snapshot.sanitizedManifestSha256}`);
  assert.equal(resolved.sanitization.fixtureOnly, false);
  assert.equal(resolved.sanitization.approval.approver, "jvallery");
  // The private content-bucket URI must never reach the publicly served
  // dist/static-build.json, which spreads build.sanitization.
  assert.equal(resolved.sanitization.approval.sourceOrigin, undefined);
  assert.equal(
    JSON.stringify(resolved.sanitization).includes("verdify-lab-content"),
    false,
    "the source bucket must not be published in the build identity",
  );
  assert.equal(resolved.sanitization.approval.releaseTag, "lab-production-snapshot-20260804t1200z");
  assert.equal(resolved.files.size, Object.keys(CONTENT).length);
});

test("verify-production-output accepts exactly the shape the resolver emits", async (context) => {
  // The production verifier's gate is `approvalEligible === true` and
  // `localEvidenceStatus !== "provisional-only"`. Pin that the resolver output
  // satisfies it without the verifier being modified.
  const snapshot = await buildProductionSnapshot(context);
  const resolved = await resolveWith(snapshot);
  const build = { approvalEligible: resolved.approvalEligible, localEvidenceStatus: resolved.evidenceStatus };
  assert.ok(build.approvalEligible === true && build.localEvidenceStatus !== "provisional-only");
  const verifierSource = await readFile(new URL("../scripts/verify-production-output.mjs", import.meta.url), "utf8");
  assert.match(verifierSource, /build\.approvalEligible !== true \|\| build\.localEvidenceStatus === "provisional-only"/);
});

// ---------------------------------------------------------------------------
// Reject direction: fixtures.
// ---------------------------------------------------------------------------

test("a synthetic fixture can never become approval-eligible", async (context) => {
  const base = await realpath(await mkdtemp(path.join(tmpdir(), "verdify-lab-fixture-")));
  context.after(() => rm(base, { recursive: true, force: true }));
  const root = path.join(base, "snapshot");
  await mkdir(path.join(root, "content"), { recursive: true });
  await writeFile(path.join(root, "content", "index.md"), "# fixture\n");
  await mkdir(path.join(root, "manifests"), { recursive: true });
  await writeFile(
    path.join(root, "manifests", "content.json"),
    canonical({ version: 1, files: { "index.md": sha256("# fixture\n") } }),
  );
  await writeFile(
    path.join(root, "manifests", "synthetic-fixture.json"),
    canonical({ contract: "verdify.lab-stage-synthetic-fixture", schemaVersion: 1 }),
  );
  const fixture = await verifySnapshot(root, { allowSyntheticFixture: true });
  assert.equal(fixture.approvalEligible, false);
  assert.equal(fixture.evidenceStatus, "provisional-only");
  assert.equal(fixture.sanitization.fixtureOnly, true);
});

test("a fixture that also carries a production approval stays a fixture", async (context) => {
  const snapshot = await buildProductionSnapshot(context);
  await writeFile(
    path.join(snapshot.root, "manifests", "synthetic-fixture.json"),
    canonical({ contract: "verdify.lab-stage-synthetic-fixture", schemaVersion: 1 }),
  );
  const entry = registryEntry(snapshot.record, snapshot.approvalBytes);
  const resolved = await __resolveSnapshotWithRegistry(snapshot.root, { allowSyntheticFixture: true }, [entry]);
  assert.equal(resolved.approvalEligible, false);
  assert.equal(resolved.evidenceStatus, "provisional-only");
});

// ---------------------------------------------------------------------------
// Reject direction: the legacy provisional capture.
// ---------------------------------------------------------------------------

test("the legacy stage capture cannot be relabelled approval-eligible", async (context) => {
  const snapshot = await buildProductionSnapshot(context, {
    mutate: { attestation: { contract: "verdify.lab-stage-sanitized-snapshot" } },
  });
  // Routed to the untouched stage verifier, which hard-rejects approvalEligible !== false.
  await assert.rejects(() => resolveWith(snapshot), /stage release policy/);
});

test("flipping approvalEligible on a stage attestation is rejected by the stage verifier", async (context) => {
  const snapshot = await buildProductionSnapshot(context, {
    mutate: {
      attestation: { contract: "verdify.lab-stage-sanitized-snapshot", evidenceStatus: "provisional-only" },
    },
  });
  await assert.rejects(() => resolveWith(snapshot), /stage release policy/);
});

// ---------------------------------------------------------------------------
// Reject direction: tampering.
// ---------------------------------------------------------------------------

test("a tampered approval record fails its registered digest", async (context) => {
  const snapshot = await buildProductionSnapshot(context);
  const entry = registryEntry(snapshot.record, snapshot.approvalBytes);
  const tampered = canonical({ ...snapshot.record, approver: "jvallery", approvedAt: "2026-08-04T15:36:00Z" });
  await writeFile(path.join(snapshot.root, "approval.json"), tampered);
  await assert.rejects(
    () => __resolveSnapshotWithRegistry(snapshot.root, {}, [entry]),
    /do not match their registered approval digest/,
  );
});

test("a registry entry that disagrees with the byte-identical record is rejected", async (context) => {
  const snapshot = await buildProductionSnapshot(context);
  await assert.rejects(
    () => resolveWith(snapshot, { occurrenceSelectionPolicySha256: "e".repeat(64) }),
    /disagrees with its registry entry: occurrenceSelectionPolicySha256/,
  );
});

test("an approval cannot be replayed onto different content", async (context) => {
  const snapshot = await buildProductionSnapshot(context);
  const entry = registryEntry(snapshot.record, snapshot.approvalBytes);
  const other = await buildProductionSnapshot(context, {
    content: { ...CONTENT, "index.md": "# Verdify Lab (edited)\n" },
  });
  await assert.rejects(
    () => __resolveSnapshotWithRegistry(other.root, {}, [entry]),
    /do not match their registered approval digest/,
  );
});

test("a content byte changed after approval breaks the content manifest", async (context) => {
  const snapshot = await buildProductionSnapshot(context);
  const entry = registryEntry(snapshot.record, snapshot.approvalBytes);
  await writeFile(path.join(snapshot.root, "content", "index.md"), "# tampered\n");
  await assert.rejects(() => __resolveSnapshotWithRegistry(snapshot.root, {}, [entry]), /digest mismatch/);
});

test("non-canonical approval JSON is rejected", async (context) => {
  const snapshot = await buildProductionSnapshot(context, {
    mutate: { approvalBytes: (record) => `${JSON.stringify(record)}\n` },
  });
  await assert.rejects(() => resolveWith(snapshot), /canonical JSON/);
});

// ---------------------------------------------------------------------------
// Reject direction: provenance and gate fields.
// ---------------------------------------------------------------------------

for (const [label, overrides, pattern] of [
  ["an unrecognised approver", { approver: "verdify-bot" }, /recorded approval authority/],
  [
    "an approval link outside this repository",
    { approvalRecordUrl: "https://example.com/approved" },
    /recorded approval comment/,
  ],
  ["a stage sanitization policy version", { policyVersion: "verdify-public-output-stage-v1" }, /production public-output policy/],
  ["a source outside the Lab content prefix", { sourceOrigin: "s3://scratch/tmp/content" }, /authoritative Lab content source/],
  ["a non-canonical UTC instant", { approvedAt: "2026-08-04T15:35:00.000Z" }, /canonical UTC instant/],
  ["approval before capture", { approvedAt: "2026-08-04T05:00:00Z" }, /approved before its source was captured/],
  ["a malformed approval id", { approvalId: "approve-me" }, /invalid approval id/],
  ["a release tag that is not a production snapshot tag", { releaseTag: "v1.0.0" }, /immutable production release tag/],
]) {
  test(`an approval with ${label} is rejected`, async (context) => {
    const snapshot = await buildProductionSnapshot(context, { mutate: { approval: overrides } });
    await assert.rejects(() => resolveWith(snapshot), pattern);
  });
}

test("an approval that does not bind the snapshot's own attestation is rejected", async (context) => {
  const snapshot = await buildProductionSnapshot(context, {
    mutate: { approval: { snapshotAttestationSha256: "f".repeat(64) } },
  });
  await assert.rejects(() => resolveWith(snapshot), /does not bind the supplied snapshot: snapshotAttestationSha256/);
});

// ---------------------------------------------------------------------------
// Reject direction: the public-output gates the approval is supposed to carry.
// ---------------------------------------------------------------------------

test("a guard report with findings is rejected", async (context) => {
  const snapshot = await buildProductionSnapshot(context, {
    mutate: { guardReport: { ...guardReport(), findings: [{ route: "/x" }], routes: ["/x"] } },
  });
  await assert.rejects(() => resolveWith(snapshot), /zero-finding v2 report|closed/);
});

test("an attestation whose file count disagrees with the tree is rejected", async (context) => {
  const snapshot = await buildProductionSnapshot(context, {
    mutate: { attestation: { sanitizedFileCount: 3 } },
  });
  await assert.rejects(() => resolveWith(snapshot), /approved release policy/);
});

test("an attestation whose HLS preservation count is wrong is rejected", async (context) => {
  const snapshot = await buildProductionSnapshot(context, {
    mutate: { attestation: { transformations: { hlsFilesPreserved: 3 } } },
  });
  await assert.rejects(() => resolveWith(snapshot), /HLS preservation count/);
});

test("transformation accounting that hides redactions is rejected", async (context) => {
  // Claiming zero changed files while reporting redactions would let a capture
  // understate what sanitization actually did.
  const snapshot = await buildProductionSnapshot(context, {
    mutate: {
      attestation: {
        transformations: {
          changedFiles: 0,
          textRedactionFiles: 1,
          invalidValueRepairFiles: 0,
          pngReencodeFiles: 0,
          hlsFilesPreserved: 2,
        },
      },
    },
  });
  await assert.rejects(() => resolveWith(snapshot), /transformation accounting is inconsistent/);
});

// ---------------------------------------------------------------------------
// The bounded hydrator stays bounded.
// ---------------------------------------------------------------------------

test("a production release descriptor is unreadable without a registered approval", async (context) => {
  const base = await realpath(await mkdtemp(path.join(tmpdir(), "verdify-lab-descriptor-")));
  context.after(() => rm(base, { recursive: true, force: true }));
  const descriptor = path.join(base, "release.json");
  await writeFile(
    descriptor,
    canonical({
      contract: PRODUCTION_RELEASE_CONTRACT,
      schemaVersion: 1,
      assetUrl:
        "https://github.com/VerdifyConsultancy/verdify-platform/releases/download/lab-production-snapshot-20260804t1200z/verdify-lab-production-snapshot-20260804t1200z.tar",
      assetSha256: "d".repeat(64),
      assetBytes: 1024,
      assetFormat: "tar",
      attestationSha256: "a".repeat(64),
      approvalSha256: "b".repeat(64),
      approvalId: "lab-production-snapshot-20260804t1200z",
      sanitizedManifestSha256: "c".repeat(64),
      sourceManifestSha256: "e".repeat(64),
      fileCount: 4,
    }),
  );
  await assert.rejects(() => readReleaseDescriptor(descriptor), /not in the reviewed approval registry/);
});

test("the stage payload layout still refuses an approval record", async (context) => {
  const base = await realpath(await mkdtemp(path.join(tmpdir(), "verdify-lab-tar-")));
  context.after(() => rm(base, { recursive: true, force: true }));
  const archive = path.join(base, "payload.tar");
  const entries = [
    { name: "content/", type: "5" },
    { name: "content/page.txt", body: "safe\n" },
    { name: "manifests/", type: "5" },
    { name: "manifests/content.json", body: "{}\n" },
    { name: "attestation.json", body: "{}\n" },
    { name: "approval.json", body: "{}\n" },
    { name: "evidence/", type: "5" },
    { name: "evidence/public-output-guard.json", body: "{}\n" },
  ];
  const chunks = [];
  for (const entry of entries) {
    const body = Buffer.from(entry.body ?? "");
    const header = Buffer.alloc(512);
    header.write(entry.name, 0, 100, "utf8");
    const octal = (value, width) => `${value.toString(8).padStart(width - 1, "0")}\0`;
    header.write(octal(entry.type === "5" ? 0o755 : 0o644, 8), 100, 8, "ascii");
    header.write(octal(0, 8), 108, 8, "ascii");
    header.write(octal(0, 8), 116, 8, "ascii");
    header.write(octal(body.length, 12), 124, 12, "ascii");
    header.write(octal(0, 12), 136, 12, "ascii");
    header.fill(32, 148, 156);
    header.write(entry.type ?? "0", 156, 1, "ascii");
    header.write("ustar\0", 257, 6, "binary");
    header.write("00", 263, 2, "ascii");
    let checksum = 0;
    for (const byte of header) checksum += byte;
    header.write(`${checksum.toString(8).padStart(6, "0")}\0 `, 148, 8, "ascii");
    chunks.push(header);
    if (body.length) {
      chunks.push(body);
      chunks.push(Buffer.alloc((512 - (body.length % 512)) % 512));
    }
  }
  chunks.push(Buffer.alloc(1024));
  await writeFile(archive, Buffer.concat(chunks));
  await assert.rejects(
    () => extractVerifiedTar(archive, path.join(base, "stage"), { expectedContentFiles: 1 }),
    /closed snapshot payload layout/,
  );
  const result = await extractVerifiedTar(archive, path.join(base, "production"), {
    expectedContentFiles: 1,
    allowApprovalRecord: true,
  });
  assert.equal(result.contentFiles, 1);
});

// ---------------------------------------------------------------------------
// Pure-function guards.
// ---------------------------------------------------------------------------

test("verifyProductionApproval refuses a malformed registry outright", () => {
  assert.throws(
    () => verifyProductionApproval(Buffer.from("{}\n"), { approvalDigest: "0".repeat(64) }, [{ approvalId: "x" }]),
    /closed registry-entry shape/,
  );
});
