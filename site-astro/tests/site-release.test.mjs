import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { link, lstat, mkdtemp, mkdir, readFile, readdir, readlink, rename, rm, writeFile } from "node:fs/promises";
import { hostname, tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import test from "node:test";

import { runSiteReleaseCommand } from "../scripts/manage-site-release.mjs";
import { createBakedSiteBundle, hydrateSiteCache } from "../scripts/lib/site-release-cache.mjs";
import {
  LocalSiteReleaseStore,
  inventoryBuiltSite,
  publishSiteRelease,
  rollbackSiteRelease,
  siteContentIdentitySha256,
  siteReleasePayloadSha256,
  siteReleaseStatus,
} from "../scripts/lib/site-release-store.mjs";

const run = promisify(execFile);
const SNAPSHOT = "a".repeat(64);
const COMMIT = "b".repeat(40);

async function fixture() {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-site-release-"));
  const buildRoot = path.join(root, "build");
  const storeRoot = path.join(root, "store");
  const cacheRoot = path.join(root, "cache");
  await mkdir(path.join(buildRoot, "assets"), { recursive: true });
  await mkdir(storeRoot);
  await mkdir(cacheRoot);
  await writeFile(path.join(buildRoot, "index.html"), "<!doctype html><title>one</title>\n");
  await writeFile(path.join(buildRoot, "assets", "app.css"), "body { color: #123; }\n");
  return { root, buildRoot, storeRoot, cacheRoot };
}

async function request({
  buildRoot,
  storeRoot,
  sequence,
  expectedSelectionSha256 = null,
  policyVersion = "verdify-site-v1",
  occurredAt = `2026-07-12T12:${String(sequence % 60).padStart(2, "0")}:00Z`,
}) {
  const inventory = await inventoryBuiltSite(buildRoot);
  const files = inventory.files.map(({ sourcePath: _sourcePath, ...record }) => record);
  const contentIdentitySha256 = siteContentIdentitySha256({
    sourceSnapshotManifestSha256: SNAPSHOT,
    policyVersion,
    builderCommit: COMMIT,
    files,
  });
  const payloadSha256 = siteReleasePayloadSha256({
    sourceSnapshotManifestSha256: SNAPSHOT,
    policyVersion,
    builderCommit: COMMIT,
    contentIdentitySha256,
  });
  return {
    storeRoot,
    buildRoot,
    event: {
      contract: "verdify.lab-release-trigger",
      schemaVersion: 1,
      eventId: `evt_site_${String(sequence).padStart(4, "0")}`,
      eventType: "planner-completed",
      sourceId: "planner/public-snapshot",
      sourceWatermark: `planner-${sequence}`,
      occurredAt,
      payloadSha256,
    },
    sourceSnapshotManifestSha256: SNAPSHOT,
    policyVersion,
    builderCommit: COMMIT,
    releasedAt: new Date(Date.parse(occurredAt) + 60_000).toISOString(),
    expectedSelectionSha256,
  };
}

async function selected(storeRoot) {
  const store = await new LocalSiteReleaseStore(storeRoot).initialize();
  return store.readSelection();
}

async function currentLink(cacheRoot) {
  return readlink(path.join(cacheRoot, "current"));
}

async function treeBytes(root) {
  let total = 0;
  for (const name of await readdir(root)) {
    if ([".site-publish.lock", ".lease-tombstones"].includes(name)) continue;
    const target = path.join(root, name);
    const metadata = await lstat(target);
    total += metadata.isDirectory() ? await treeBytes(target) : metadata.size;
  }
  return total;
}

test("publishes a closed tree, change safeguards identical bytes, binds policy, reports freshness, and rolls back", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  const firstRequest = await request({ ...value, sequence: 1 });
  const first = await publishSiteRelease(firstRequest);
  assert.equal(first.idempotent, false);
  assert.equal(first.manifest.fileCount, 2);

  const unchangedRequest = await request({ ...value, sequence: 2, expectedSelectionSha256: first.selectionSha256 });
  const unchanged = await publishSiteRelease(unchangedRequest);
  assert.equal(unchanged.unchanged, true);
  assert.equal(unchanged.selectionSha256, first.selectionSha256);

  const policyRequest = await request({
    ...value,
    sequence: 3,
    expectedSelectionSha256: first.selectionSha256,
    policyVersion: "verdify-site-v2",
  });
  const policy = await publishSiteRelease(policyRequest);
  assert.notEqual(policy.releaseSha256, first.releaseSha256);
  assert.equal(policy.manifest.policyVersion, "verdify-site-v2");
  assert.equal(policy.manifest.sourceSnapshotManifestSha256, SNAPSHOT);
  assert.equal(policy.manifest.event.payloadSha256, policyRequest.event.payloadSha256);

  const status = await siteReleaseStatus({ storeRoot: value.storeRoot, asOf: "2026-07-12T12:19:00Z" });
  assert.equal(status.current.freshness.targetSeconds, 300);
  assert.equal(status.current.freshness.alertAfterSeconds, 900);
  assert.equal(status.current.freshness.status, "alert");
  assert.equal(status.health, "alert");
  assert.equal((await siteReleaseStatus({ storeRoot: value.storeRoot })).health, "alert");
  const rolledBack = await rollbackSiteRelease({
    storeRoot: value.storeRoot,
    expectedSelectionSha256: policy.selectionSha256,
    rolledBackAt: "2026-07-12T12:20:00Z",
  });
  assert.equal(rolledBack.selection.current.releaseSha256, first.releaseSha256);
  assert.equal(rolledBack.selection.previous.releaseSha256, policy.releaseSha256);
});

test("event intent makes every pre-selection failure retry-safe and selection-atomic", async (t) => {
  for (const [index, failAt] of ["afterBlobs", "afterManifest", "afterIntent", "beforeSelection"].entries()) {
    const value = await fixture();
    t.after(() => rm(value.root, { recursive: true, force: true }));
    const publishRequest = await request({ ...value, sequence: index + 10 });
    await assert.rejects(
      publishSiteRelease({ ...publishRequest, testHooks: { failAt } }),
      new RegExp(`injected site release failure at ${failAt}`),
    );
    assert.equal(await selected(value.storeRoot), null);
    const retried = await publishSiteRelease(publishRequest);
    assert.equal(retried.releaseSha256.length, 64);
    assert.equal((await selected(value.storeRoot)).document.current.releaseSha256, retried.releaseSha256);
  }
});

test("local publisher lease excludes concurrency and recovers a dead same-host owner", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  const publishRequest = await request({ ...value, sequence: 20 });
  let releaseLease;
  let leased;
  const leaseReached = new Promise((resolve) => { leased = resolve; });
  const held = publishSiteRelease({
    ...publishRequest,
    testHooks: {
      afterLease: async () => {
        leased();
        await new Promise((resolve) => { releaseLease = resolve; });
      },
    },
  });
  await leaseReached;
  await assert.rejects(publishSiteRelease(publishRequest), /another local site publisher is active/);
  releaseLease();
  await held;

  const nextRequest = await request({ ...value, sequence: 21, expectedSelectionSha256: (await selected(value.storeRoot)).sha256 });
  const staleLock = path.join(value.storeRoot, ".site-publish.lock");
  await mkdir(staleLock);
  await writeFile(path.join(staleLock, "owner.json"), `${JSON.stringify({
    contract: "verdify.lab-local-site-publish-lease",
    schemaVersion: 1,
    hostname: hostname(),
    pid: 999_999_999,
    nonce: "11111111-1111-4111-8111-111111111111",
  }, null, 2)}\n`, { mode: 0o600 });
  const staleCandidate = "22222222-2222-4222-8222-222222222222";
  await writeFile(path.join(value.storeRoot, `.candidate-${staleCandidate}`), "interrupted selector\n");
  const releaseCandidate = path.join(value.storeRoot, "releases", "sha256", `.candidate-${staleCandidate}`);
  const publishedRelease = (await readdir(path.join(value.storeRoot, "releases", "sha256"))).find((name) => name.endsWith(".json"));
  await link(path.join(value.storeRoot, "releases", "sha256", publishedRelease), releaseCandidate);
  await writeFile(path.join(value.storeRoot, "events", "sha256", `.candidate-${staleCandidate}`), "interrupted event\n");
  await writeFile(path.join(value.storeRoot, ".quarantine", `${staleCandidate}.blob`), "interrupted blob\n");
  await writeFile(path.join(value.buildRoot, "index.html"), "<!doctype html><title>two</title>\n");
  const updatedRequest = await request({
    ...value,
    sequence: 21,
    expectedSelectionSha256: nextRequest.expectedSelectionSha256,
  });
  let releaseRecovered;
  let recovered;
  const recoveredLease = new Promise((resolve) => { recovered = resolve; });
  const recovery = publishSiteRelease({
    ...updatedRequest,
    testHooks: {
      afterLease: async () => {
        recovered();
        await new Promise((resolve) => { releaseRecovered = resolve; });
      },
    },
  });
  await recoveredLease;
  await assert.rejects(publishSiteRelease(updatedRequest), /another local site publisher is active/);
  releaseRecovered();
  await recovery;
  assert.equal((await readdir(value.storeRoot)).some((name) => name === `.candidate-${staleCandidate}`), false);
  assert.equal((await readdir(path.join(value.storeRoot, "releases", "sha256"))).some((name) => name.startsWith(".candidate-")), false);
  assert.equal((await readdir(path.join(value.storeRoot, "events", "sha256"))).some((name) => name.startsWith(".candidate-")), false);
  assert.equal((await readdir(path.join(value.storeRoot, ".quarantine"))).length, 0);
});

test("retention keeps at most ten manifests and reachability-collects orphan blobs", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  let expected = null;
  for (let sequence = 30; sequence < 42; sequence += 1) {
    await writeFile(path.join(value.buildRoot, "index.html"), `<!doctype html><title>${sequence}</title>\n`);
    const result = await publishSiteRelease(await request({
      ...value,
      sequence,
      expectedSelectionSha256: expected,
      occurredAt: `2026-07-12T${String(sequence - 20).padStart(2, "0")}:00:00Z`,
    }));
    expected = result.selectionSha256;
  }
  const manifests = await readdir(path.join(value.storeRoot, "releases", "sha256"));
  const blobs = await readdir(path.join(value.storeRoot, "blobs", "sha256"));
  const events = await readdir(path.join(value.storeRoot, "events", "sha256"));
  assert.equal(manifests.length, 10);
  assert.equal(events.length, 12);
  assert.equal(blobs.length, 11, "ten unique HTML blobs plus one shared CSS blob remain reachable");
});

test("retention evicts optional releases until the retained-byte cap fits", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  let expected = null;
  for (let sequence = 42; sequence < 49; sequence += 1) {
    await writeFile(path.join(value.buildRoot, "index.html"), `<!doctype html><title>cap-${sequence}-${"x".repeat(120)}</title>\n`);
    const result = await publishSiteRelease({
      ...await request({ ...value, sequence, expectedSelectionSha256: expected }),
      testHooks: sequence === 48 ? { storeByteLimit: 10_000 } : null,
    });
    expected = result.selectionSha256;
  }
  const manifests = await readdir(path.join(value.storeRoot, "releases", "sha256"));
  assert.ok(manifests.length < 7 && manifests.length >= 2);
  assert.ok(await treeBytes(value.storeRoot) <= 10_000);
});

test("unchanged publication rejects an event intent that would exceed the byte cap", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  const first = await publishSiteRelease(await request({ ...value, sequence: 49 }));
  const beforeBytes = await treeBytes(value.storeRoot);
  const beforeEvents = (await readdir(path.join(value.storeRoot, "events", "sha256"))).length;
  await assert.rejects(publishSiteRelease({
    ...await request({ ...value, sequence: 59, expectedSelectionSha256: first.selectionSha256 }),
    testHooks: { storeByteLimit: beforeBytes + 16 },
  }), /retained-byte cap/);
  assert.equal((await readdir(path.join(value.storeRoot, "events", "sha256"))).length, beforeEvents);
  assert.equal(await treeBytes(value.storeRoot), beforeBytes);
});

test("cache verifies bytes, atomically keeps two generations, falls back to previous, and preserves current on failure", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  const first = await publishSiteRelease(await request({ ...value, sequence: 50 }));
  await hydrateSiteCache({ storeRoot: value.storeRoot, cacheRoot: value.cacheRoot, asOf: "2026-07-12T12:51:00Z" });
  await writeFile(path.join(value.buildRoot, "index.html"), "<!doctype html><title>second</title>\n");
  const second = await publishSiteRelease(await request({
    ...value,
    sequence: 51,
    expectedSelectionSha256: first.selectionSha256,
  }));
  const hydrated = await hydrateSiteCache({ storeRoot: value.storeRoot, cacheRoot: value.cacheRoot, asOf: "2026-07-12T12:52:00Z" });
  assert.equal(hydrated.source, "store-current");
  assert.equal(hydrated.releaseSha256, second.releaseSha256);
  assert.match(await currentLink(value.cacheRoot), new RegExp(`^generations/${second.releaseSha256}-`));
  assert.equal((await readdir(path.join(value.cacheRoot, "generations"))).length, 2);

  await writeFile(path.join(value.buildRoot, "index.html"), "<!doctype html><title>third</title>\n");
  const third = await publishSiteRelease(await request({
    ...value,
    sequence: 52,
    expectedSelectionSha256: second.selectionSha256,
  }));
  await assert.rejects(hydrateSiteCache({
    storeRoot: value.storeRoot,
    cacheRoot: value.cacheRoot,
    testHooks: { failAt: "beforeCurrentSwap" },
  }), /injected cache failure before current swap/);
  assert.match(await currentLink(value.cacheRoot), new RegExp(`^generations/${second.releaseSha256}-`));
  assert.equal((await readdir(path.join(value.cacheRoot, "generations"))).length, 2);

  const store = await new LocalSiteReleaseStore(value.storeRoot).initialize();
  const currentManifest = await store.readRelease(third.releaseSha256);
  const uniqueIndex = currentManifest.files.find((file) => file.path === "index.html");
  await writeFile(store.blobPath(uniqueIndex.sha256), Buffer.alloc(uniqueIndex.bytes, 0x78));
  const fallback = await hydrateSiteCache({ storeRoot: value.storeRoot, cacheRoot: value.cacheRoot });
  assert.equal(fallback.source, "store-previous");
  assert.equal(fallback.releaseSha256, second.releaseSha256);
});

test("verified baked known-good bundle boots an empty cache during store outage", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  const published = await publishSiteRelease(await request({ ...value, sequence: 55 }));
  const bundleRoot = path.join(value.root, "baked-known-good");
  await createBakedSiteBundle({ storeRoot: value.storeRoot, releaseSha256: published.releaseSha256, bundleRoot });
  await rename(value.storeRoot, `${value.storeRoot}.offline`);
  const status = await hydrateSiteCache({
    storeRoot: value.storeRoot,
    cacheRoot: value.cacheRoot,
    bakedBundleRoot: bundleRoot,
    asOf: "2026-07-12T12:56:00Z",
  });
  assert.equal(status.ready, true);
  assert.equal(status.health, "degraded");
  assert.equal(status.source, "baked-known-good");
  assert.match(await currentLink(value.cacheRoot), new RegExp(`^generations/${published.releaseSha256}-`));
});

test("cache atomically self-heals selected local corruption from verified store bytes", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  const published = await publishSiteRelease(await request({ ...value, sequence: 57 }));
  await hydrateSiteCache({ storeRoot: value.storeRoot, cacheRoot: value.cacheRoot });
  const selectedTarget = await currentLink(value.cacheRoot);
  const selectedIndex = path.join(value.cacheRoot, selectedTarget, "tree", "index.html");
  await writeFile(selectedIndex, "locally corrupt\n");
  await hydrateSiteCache({ storeRoot: value.storeRoot, cacheRoot: value.cacheRoot });
  const repairedTarget = await currentLink(value.cacheRoot);
  assert.notEqual(repairedTarget, selectedTarget);
  assert.match(repairedTarget, new RegExp(`^generations/${published.releaseSha256}-`));
  assert.equal(await readFile(path.join(value.cacheRoot, repairedTarget, "tree", "index.html"), "utf8"), "<!doctype html><title>one</title>\n");
  assert.equal((await readdir(path.join(value.cacheRoot, "generations"))).length, 1);
});

test("cache lease serializes concurrent hydrate transactions", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  const published = await publishSiteRelease(await request({ ...value, sequence: 58 }));
  let releaseHydrator;
  let held;
  const hydrateHeld = new Promise((resolve) => { held = resolve; });
  const first = hydrateSiteCache({
    storeRoot: value.storeRoot,
    cacheRoot: value.cacheRoot,
    testHooks: {
      beforeCurrentSwap: async () => {
        held();
        await new Promise((resolve) => { releaseHydrator = resolve; });
      },
    },
  });
  await hydrateHeld;
  await assert.rejects(
    hydrateSiteCache({ storeRoot: value.storeRoot, cacheRoot: value.cacheRoot }),
    /another local site cache hydrator is active/,
  );
  releaseHydrator();
  await first;
  assert.match(await currentLink(value.cacheRoot), new RegExp(`^generations/${published.releaseSha256}-`));
});

test("cache lease never recovers a foreign-host owner", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  await publishSiteRelease(await request({ ...value, sequence: 56 }));
  const lock = path.join(value.cacheRoot, ".hydrate.lock");
  await mkdir(lock);
  await writeFile(path.join(lock, "owner.json"), `${JSON.stringify({
    contract: "verdify.lab-local-site-cache-lease",
    schemaVersion: 1,
    hostname: "foreign-node.invalid",
    pid: 999_999_999,
    nonce: "33333333-3333-4333-8333-333333333333",
  }, null, 2)}\n`);
  await assert.rejects(
    hydrateSiteCache({ storeRoot: value.storeRoot, cacheRoot: value.cacheRoot }),
    /another local site cache hydrator is active/,
  );
  assert.equal((await readFile(path.join(lock, "owner.json"), "utf8")).includes("foreign-node.invalid"), true);
});

test("site release CLI completes prepare, publish, status, hydrate, bundle, and rollback end to end", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  const cli = path.resolve("scripts/manage-site-release.mjs");
  async function invoke(...arguments_) {
    const result = await run(process.execPath, [cli, ...arguments_], { cwd: path.resolve(".") });
    return JSON.parse(result.stdout);
  }
  const prepared = await invoke("prepare", "--build", value.buildRoot, "--snapshot", SNAPSHOT, "--policy", "verdify-site-v1", "--commit", COMMIT);
  assert.equal(prepared.contract, "verdify.lab-site-release-preparation");
  const firstRequest = await request({ ...value, sequence: 60 });
  assert.equal(firstRequest.event.payloadSha256, prepared.payloadSha256);
  const firstFile = path.join(value.root, "request-1.json");
  await writeFile(firstFile, `${JSON.stringify(firstRequest, null, 2)}\n`);
  const first = await invoke("publish", "--request", firstFile);
  const status = await invoke("status", "--store", value.storeRoot, "--at", "2026-07-12T13:01:00Z");
  assert.equal(status.current.releaseSha256, first.releaseSha256);
  const bundleRoot = path.join(value.root, "cli-bundle");
  const bundle = await invoke("bundle", "--store", value.storeRoot, "--release", first.releaseSha256, "--destination", bundleRoot);
  assert.equal(bundle.releaseSha256, first.releaseSha256);
  const hydrated = await invoke("hydrate", "--store", value.storeRoot, "--cache", value.cacheRoot, "--baked", bundleRoot);
  assert.equal(hydrated.ready, true);

  await writeFile(path.join(value.buildRoot, "index.html"), "<!doctype html><title>cli second</title>\n");
  const secondRequest = await request({ ...value, sequence: 61, expectedSelectionSha256: first.selectionSha256 });
  const secondFile = path.join(value.root, "request-2.json");
  await writeFile(secondFile, `${JSON.stringify(secondRequest, null, 2)}\n`);
  const second = await invoke("publish", "--request", secondFile);
  const rolledBack = await invoke(
    "rollback",
    "--store",
    value.storeRoot,
    "--expected",
    second.selectionSha256,
    "--at",
    "2026-07-12T13:03:00Z",
  );
  assert.equal(rolledBack.currentReleaseSha256, first.releaseSha256);
});

test("in-process site release commands validate before selecting reader or writer authority", async (t) => {
  const value = await fixture();
  t.after(() => rm(value.root, { recursive: true, force: true }));
  const publishRequest = await request({ ...value, sequence: 62 });
  const reader = Object.freeze({ role: "reader" });
  const writer = Object.freeze({ role: "writer" });
  const environment = Object.freeze({ AMBIENT_VALUE: "must-not-be-forwarded" });
  const factoryCalls = [];
  const emitted = [];
  const dependencies = {
    environment,
    async createReaderStore(storeRoot, options) {
      factoryCalls.push({ role: "reader", storeRoot, options });
      return reader;
    },
    async createWriterStore(storeRoot, options) {
      factoryCalls.push({ role: "writer", storeRoot, options });
      return writer;
    },
    readRequest: async () => publishRequest,
    async publishRelease(input) {
      assert.equal(input.store, writer);
      return {
        releaseSha256: "1".repeat(64),
        selectionSha256: "2".repeat(64),
        idempotent: false,
        retained: true,
      };
    },
    async readStatus(input) {
      assert.equal(input.store, reader);
      return { contract: "test-status", storeRoot: input.storeRoot };
    },
    async rollbackRelease(input) {
      assert.equal(input.store, writer);
      return {
        selectionSha256: "3".repeat(64),
        selection: {
          generation: 2,
          current: { releaseSha256: "4".repeat(64) },
          previous: { releaseSha256: "5".repeat(64) },
        },
      };
    },
    async bakeBundle(input) {
      assert.equal(input.store, reader);
      return {
        releaseSha256: input.releaseSha256,
        manifestSha256: input.releaseSha256,
        fileCount: 2,
        totalBytes: 42,
      };
    },
    async hydrateCache(input) {
      assert.equal(input.store, reader);
      return { contract: "test-hydration", ready: true };
    },
  };

  const prepared = await runSiteReleaseCommand([
    "prepare",
    "--build", value.buildRoot,
    "--snapshot", SNAPSHOT,
    "--policy", "verdify-site-v1",
    "--commit", COMMIT,
  ], dependencies);
  assert.equal(prepared.contract, "verdify.lab-site-release-preparation");
  assert.equal(factoryCalls.length, 0, "prepare has no release-store authority");

  const published = await runSiteReleaseCommand(["publish", "--request", "unused.json"], {
    ...dependencies,
    outputWriter: async (bytes) => emitted.push(bytes),
  });
  assert.equal(published.contract, "verdify.lab-site-publish-result");
  assert.equal(emitted[0], `${JSON.stringify(published, null, 2)}\n`);
  await runSiteReleaseCommand(["status", "--store", value.storeRoot], dependencies);
  await runSiteReleaseCommand([
    "rollback",
    "--store", value.storeRoot,
    "--expected", "2".repeat(64),
    "--at", "2026-07-12T13:03:00Z",
  ], dependencies);
  await runSiteReleaseCommand([
    "bundle",
    "--store", value.storeRoot,
    "--release", "1".repeat(64),
    "--destination", path.join(value.root, "bundle-output"),
  ], dependencies);
  await runSiteReleaseCommand([
    "hydrate",
    "--store", value.storeRoot,
    "--cache", value.cacheRoot,
  ], dependencies);

  assert.deepEqual(factoryCalls.map(({ role }) => role), [
    "writer",
    "reader",
    "writer",
    "reader",
    "reader",
  ]);
  assert.equal(factoryCalls[0].options.create, true);
  for (const call of factoryCalls) assert.equal(call.options.environment, environment);

  const callsBeforeInvalidRequest = factoryCalls.length;
  await assert.rejects(
    runSiteReleaseCommand(["publish", "--request", "unused.json"], {
      ...dependencies,
      readRequest: async () => ({ ...publishRequest, builderCommit: "invalid" }),
    }),
    /builder commit is invalid/u,
  );
  assert.equal(factoryCalls.length, callsBeforeInvalidRequest, "an invalid request never constructs a writer");

  let implicitEnvironment = "not-called";
  await runSiteReleaseCommand(["status", "--store", value.storeRoot], {
    async createReaderStore(_storeRoot, options) {
      implicitEnvironment = options.environment;
      return reader;
    },
    readStatus: async () => ({ contract: "test-status" }),
  });
  assert.equal(implicitEnvironment, undefined, "library composition never selects the ambient process environment");

  const missingStore = new Error("local store is absent");
  missingStore.code = "ENOENT";
  let fallbackInput = null;
  const fallback = await runSiteReleaseCommand([
    "hydrate",
    "--store", path.join(value.root, "absent-store"),
    "--cache", value.cacheRoot,
    "--baked", path.join(value.root, "known-good"),
  ], {
    createReaderStore: async () => { throw missingStore; },
    hydrateCache: async (input) => {
      fallbackInput = input;
      return { contract: "test-hydration", source: "baked-known-good" };
    },
  });
  assert.equal(fallback.source, "baked-known-good");
  assert.equal(fallbackInput.store, null, "only an absent local reader delegates to the baked fallback");

  await assert.rejects(
    runSiteReleaseCommand([
      "hydrate",
      "--store", value.storeRoot,
      "--cache", value.cacheRoot,
      "--baked", path.join(value.root, "known-good"),
    ], {
      createReaderStore: async () => { throw new Error("reader configuration is invalid"); },
      hydrateCache: async () => { throw new Error("hydrate must not run"); },
    }),
    /reader configuration is invalid/u,
  );
});
