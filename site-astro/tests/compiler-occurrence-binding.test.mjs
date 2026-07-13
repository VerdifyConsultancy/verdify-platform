import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  loadCompilerOccurrenceBinding,
  verifyCompilerOccurrenceDiscovery,
  verifyCompleteSelectedOccurrenceEvidence,
} from "../scripts/compile-snapshot.mjs";
import {
  draftBlockedOccurrenceExportPolicy,
  occurrenceExportPolicySha256,
  staticOccurrenceDiscoveryProjection,
  staticOccurrenceDiscoverySha256,
} from "../scripts/lib/occurrence-export-contract.mjs";
import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
  occurrenceReleasePayloadSha256,
  publishOccurrenceRelease,
  staticOccurrenceManifest,
} from "../scripts/lib/occurrence-release.mjs";
import { verifySelectedEvidence } from "../scripts/verify-production-output.mjs";

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function discoveredOccurrences(sourceSnapshotManifestSha256) {
  const graphs = Array.from({ length: 143 }, (_, index) => discoverGraphOccurrence({
    route: `/evidence/graph-${index + 1}`,
    ordinal: 0,
    liveUrl: `https://graphs.verdify.ai/d-solo/site-home/public?panelId=${index + 1}&from=now-24h&to=now`,
    title: `Graph ${index + 1}`,
  }));
  const mediaSources = Array.from({ length: 2 }, (_, index) => ({
    route: `/greenhouse/camera-${index + 1}`,
    ordinal: 0,
    sourceUrl: `https://api.verdify.ai/api/v1/public/cameras/greenhouse_${index + 1}/latest.jpg?h=1080`,
    semanticRole: `Current greenhouse view ${index + 1}`,
  }));
  const currentMedia = mediaSources.map((source) => discoverCurrentMediaOccurrence(source));
  const manifest = staticOccurrenceManifest({
    snapshotId: `snapshot-sha256:${sourceSnapshotManifestSha256}`,
    discoveredGraphs: graphs,
    discoveredCurrentMedia: currentMedia,
  });
  return { graphs, currentMedia, mediaSources, manifest };
}

async function selectedReleaseFixture(context) {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-compiler-occurrence-binding-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  const sourceRoot = path.join(root, "source");
  const storeRoot = path.join(root, "store");
  await Promise.all([mkdir(sourceRoot), mkdir(storeRoot)]);

  const sourceSnapshotManifestSha256 = sha256("compiler-binding-snapshot");
  const discovered = discoveredOccurrences(sourceSnapshotManifestSha256);
  const blockedPolicy = draftBlockedOccurrenceExportPolicy({
    manifest: discovered.manifest,
    manifestSha256: staticOccurrenceDiscoverySha256(discovered.manifest),
    policyVersion: "compiler-occurrence-export-v1",
    approvedAt: "2026-07-13T17:00:00Z",
    cameraSources: discovered.mediaSources.map(({ sourceUrl: url }, index) => ({
      occurrenceId: discovered.currentMedia[index].occurrenceId,
      url,
    })),
  });
  const policy = {
    ...blockedPolicy,
    activation: {
      ...blockedPolicy.activation,
      state: "approved",
      approvedBy: "jason",
      approvedAt: "2026-07-13T17:00:00Z",
    },
  };
  const policyPath = path.join(root, "occurrence-policy.json");
  const blockedPolicyPath = path.join(root, "blocked-occurrence-policy.json");
  await writeFile(policyPath, canonicalBytes(policy));
  await writeFile(blockedPolicyPath, canonicalBytes(blockedPolicy));
  const policySha256 = occurrenceExportPolicySha256(policy);
  const request = {
    sourceSnapshotManifestSha256,
    policyVersion: policy.policyVersion,
    policySha256,
    graphs: [],
    currentMedia: [],
  };
  const event = {
    contract: "verdify.lab-release-trigger",
    schemaVersion: 1,
    eventId: "evt_compiler_binding_0001",
    eventType: "planner-completed",
    sourceId: "compiler-occurrence-binding-test",
    sourceWatermark: "wm_compiler_binding_0001",
    occurredAt: "2026-07-13T17:00:00Z",
    payloadSha256: occurrenceReleasePayloadSha256(request),
  };
  const published = await publishOccurrenceRelease({
    storeRoot,
    sourceRoot,
    event,
    ...request,
    publishedAt: "2026-07-13T17:01:00Z",
  });

  return {
    root,
    storeRoot,
    policy,
    policyPath,
    blockedPolicyPath,
    policySha256,
    published,
    discovered,
    snapshot: {
      manifestDigest: `sha256:${sourceSnapshotManifestSha256}`,
      sanitization: { policyVersion: "separate-snapshot-sanitization-policy" },
    },
  };
}

async function writePolicy(root, name, policy) {
  const file = path.join(root, name);
  await writeFile(file, canonicalBytes(policy));
  return file;
}

test("compiler binds a selected store release to the snapshot and exact occurrence policy", async (context) => {
  const fixture = await selectedReleaseFixture(context);
  const binding = await loadCompilerOccurrenceBinding({
    snapshot: fixture.snapshot,
    occurrenceStore: fixture.storeRoot,
    occurrencePolicy: fixture.policyPath,
  });

  assert.equal(binding.release.selection.current.manifestSha256, fixture.published.manifestSha256);
  assert.equal(binding.release.current.sourceSnapshotManifestSha256, fixture.policy.sourceSnapshotManifestSha256);
  assert.equal(binding.release.current.policyVersion, fixture.policy.policyVersion);
  assert.equal(binding.release.current.policySha256, fixture.policySha256);
  assert.doesNotThrow(() => verifyCompilerOccurrenceDiscovery(binding, fixture.discovered.manifest));
  const incompleteServedManifest = staticOccurrenceManifest({
    snapshotId: fixture.discovered.manifest.snapshotId,
    selectedManifestSha256: binding.release.selection.current.manifestSha256,
    discoveredGraphs: fixture.discovered.graphs,
    discoveredCurrentMedia: fixture.discovered.currentMedia,
    selectedManifest: binding.release.current,
  });
  assert.throws(
    () => verifyCompleteSelectedOccurrenceEvidence(binding.release, incompleteServedManifest),
    /complete graph fallback coverage/,
  );
});

test("compiler keeps absent-store behavior pending and requires a policy for any supplied store", async (context) => {
  const fixture = await selectedReleaseFixture(context);
  assert.deepEqual(await loadCompilerOccurrenceBinding({
    snapshot: fixture.snapshot,
    occurrenceStore: "",
    occurrencePolicy: "",
  }), {
    release: { selection: null, current: null },
    policy: null,
    policySha256: null,
  });
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: fixture.snapshot,
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: "",
    }),
    /LAB_OCCURRENCE_POLICY must name the exact policy/,
  );
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: fixture.snapshot,
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: fixture.blockedPolicyPath,
    }),
    /policy is not approved for compiler use/,
  );
});

test("compiler rejects snapshot, discovery, policy-version, and canonical-policy drift", async (context) => {
  const fixture = await selectedReleaseFixture(context);
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: { manifestDigest: `sha256:${sha256("different-snapshot")}` },
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: fixture.policyPath,
    }),
    /exact snapshot manifest/,
  );
  const differentPolicySnapshotPath = await writePolicy(fixture.root, "different-policy-snapshot.json", {
    ...fixture.policy,
    sourceSnapshotManifestSha256: sha256("different-policy-snapshot"),
  });
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: fixture.snapshot,
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: differentPolicySnapshotPath,
    }),
    /exact snapshot manifest/,
  );
  const differentVersionPath = await writePolicy(fixture.root, "different-version.json", {
    ...fixture.policy,
    policyVersion: "compiler-occurrence-export-v2",
  });
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: fixture.snapshot,
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: differentVersionPath,
    }),
    /export policy version/,
  );
  const differentBytesPath = await writePolicy(fixture.root, "different-bytes.json", {
    ...fixture.policy,
    reviewedAt: "2026-07-13T16:59:59Z",
  });
  await assert.rejects(
    loadCompilerOccurrenceBinding({
      snapshot: fixture.snapshot,
      occurrenceStore: fixture.storeRoot,
      occurrencePolicy: differentBytesPath,
    }),
    /exact occurrence export policy bytes/,
  );

  const binding = await loadCompilerOccurrenceBinding({
    snapshot: fixture.snapshot,
    occurrenceStore: fixture.storeRoot,
    occurrencePolicy: fixture.policyPath,
  });
  const changedDiscovery = {
    ...fixture.discovered.manifest,
    graphs: fixture.discovered.manifest.graphs.slice(1),
  };
  assert.throws(
    () => verifyCompilerOccurrenceDiscovery(binding, changedDiscovery),
    /stable discovery manifest/,
  );
});

test("selected builds retain a stable discovery hash and require 143 graph plus 2 camera fallbacks", async (context) => {
  const fixture = await selectedReleaseFixture(context);
  const manifestSha256 = "a".repeat(64);
  const fallbackSha256 = "b".repeat(64);
  const fallback = {
    sha256: fallbackSha256,
    publicPath: `/evidence/blobs/sha256/${fallbackSha256}.png`,
  };
  const selectedManifest = {
    occurrences: {
      graphs: fixture.discovered.graphs.map((occurrence) => ({ ...occurrence, fallback })),
      currentMedia: fixture.discovered.currentMedia.map((occurrence) => ({ ...occurrence, fallback })),
    },
  };
  const servedManifest = staticOccurrenceManifest({
    snapshotId: fixture.discovered.manifest.snapshotId,
    selectedManifestSha256: manifestSha256,
    discoveredGraphs: fixture.discovered.graphs,
    discoveredCurrentMedia: fixture.discovered.currentMedia,
    selectedManifest,
  });
  const release = {
    selection: { current: { manifestSha256 } },
    current: selectedManifest,
  };
  assert.equal(staticOccurrenceDiscoverySha256(servedManifest), fixture.policy.sourceOccurrenceManifestSha256);
  assert.deepEqual(staticOccurrenceDiscoveryProjection(servedManifest), fixture.discovered.manifest);
  assert.doesNotThrow(() => verifyCompleteSelectedOccurrenceEvidence(release, servedManifest));
  assert.equal(servedManifest.graphs.filter((occurrence) => occurrence.selected?.fallback).length, 143);
  assert.equal(servedManifest.currentMedia.filter((occurrence) => occurrence.selected?.fallback).length, 2);

  const build = {
    selectedOccurrenceManifestSha256: `sha256:${manifestSha256}`,
    materializedOccurrenceBlobCount: 1,
  };
  assert.doesNotThrow(() => verifySelectedEvidence(build, servedManifest));
  assert.throws(
    () => verifySelectedEvidence({ ...build, selectedOccurrenceManifestSha256: manifestSha256 }, servedManifest),
    /no selected immutable occurrence manifest/,
  );
  assert.throws(
    () => verifySelectedEvidence(build, { ...servedManifest, selectedManifestSha256: "d".repeat(64) }),
    /select different releases/,
  );

  const incomplete = {
    ...servedManifest,
    currentMedia: servedManifest.currentMedia.map((occurrence, index) => (
      index === 0 ? { ...occurrence, selected: { ...occurrence.selected, fallback: null } } : occurrence
    )),
  };
  assert.throws(
    () => verifyCompleteSelectedOccurrenceEvidence(release, incomplete),
    /complete current-media fallback coverage/,
  );
});
