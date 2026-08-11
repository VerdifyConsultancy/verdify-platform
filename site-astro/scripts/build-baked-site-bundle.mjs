import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { createBakedSiteBundle } from "./lib/site-release-cache.mjs";
import {
  inventoryBuiltSite,
  publishSiteRelease,
  siteContentIdentitySha256,
  siteReleasePayloadSha256,
} from "./lib/site-release-store.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/;
const COMMIT_RE = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;

function options(argv) {
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined || values.has(key)) {
      throw new Error("invalid baked release arguments");
    }
    values.set(key, value);
  }
  const expected = ["--build", "--destination", "--commit", "--released-at"];
  if (values.size !== expected.length || expected.some((key) => !values.has(key))) {
    throw new Error(
      "Usage: node scripts/build-baked-site-bundle.mjs --build BUILD --destination DIRECTORY --commit COMMIT --released-at ISO_INSTANT",
    );
  }
  return Object.fromEntries(expected.map((key) => [key.slice(2), values.get(key)]));
}

async function readBuildIdentity(buildRoot) {
  const bytes = await readFile(path.join(buildRoot, "static-build.json"));
  if (bytes.length < 2 || bytes.length > 1024 * 1024) throw new Error("static build identity is not bounded");
  let build;
  try {
    build = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("static build identity is not valid JSON");
  }
  const snapshot = String(build.snapshotManifestDigest ?? "").replace(/^sha256:/, "");
  const policyVersion = build.sanitization?.policyVersion;
  if (
    build.contract !== "verdify.lab-astro-stage-build"
    || build.schemaVersion !== 1
    || build.siteOrigin !== "https://lab-stage.verdify.ai"
    || build.stageGlobalNoindex !== true
    || build.activationEligible !== false
    || build.sanitization?.fixtureOnly !== false
    || !SHA256_RE.test(snapshot)
    || typeof policyVersion !== "string"
    || policyVersion.length < 1
    || policyVersion.length > 256
  ) {
    throw new Error("only an attested non-fixture Lab build can become the baked release");
  }
  return { snapshot, policyVersion };
}

export async function buildBakedSiteBundle({ buildRoot, destination, builderCommit, releasedAt }) {
  if (!COMMIT_RE.test(builderCommit)) throw new Error("baked release builder commit is invalid");
  if (!ISO_INSTANT_RE.test(releasedAt) || !Number.isFinite(Date.parse(releasedAt))) {
    throw new Error("baked release time is invalid");
  }
  const absoluteBuildRoot = path.resolve(buildRoot);
  const { snapshot, policyVersion } = await readBuildIdentity(absoluteBuildRoot);
  const inventory = await inventoryBuiltSite(absoluteBuildRoot);
  const files = inventory.files.map(({ sourcePath: _sourcePath, ...record }) => record);
  const contentIdentitySha256 = siteContentIdentitySha256({
    sourceSnapshotManifestSha256: snapshot,
    policyVersion,
    builderCommit,
    files,
  });
  const payloadSha256 = siteReleasePayloadSha256({
    sourceSnapshotManifestSha256: snapshot,
    policyVersion,
    builderCommit,
    contentIdentitySha256,
  });
  const event = {
    contract: "verdify.lab-release-trigger",
    schemaVersion: 1,
    eventId: `evt_baked_${builderCommit.slice(0, 24)}`,
    eventType: "reconciliation",
    sourceId: "verdify-lab-release-runtime-image",
    sourceWatermark: builderCommit,
    occurredAt: releasedAt,
    payloadSha256,
  };
  const temporaryStore = await mkdtemp(path.join(tmpdir(), "verdify-lab-baked-store-"));
  try {
    const published = await publishSiteRelease({
      storeRoot: temporaryStore,
      buildRoot: absoluteBuildRoot,
      event,
      sourceSnapshotManifestSha256: snapshot,
      policyVersion,
      builderCommit,
      releasedAt,
      expectedSelectionSha256: null,
    });
    const bundle = await createBakedSiteBundle({
      storeRoot: temporaryStore,
      releaseSha256: published.releaseSha256,
      bundleRoot: path.resolve(destination),
    });
    return {
      contract: "verdify.lab-runtime-baked-release-result",
      schemaVersion: 1,
      builderCommit,
      sourceSnapshotManifestSha256: snapshot,
      policyVersion,
      ...bundle,
    };
  } finally {
    await rm(temporaryStore, { recursive: true, force: true });
  }
}

async function main() {
  const args = options(process.argv.slice(2));
  const result = await buildBakedSiteBundle({
    buildRoot: args.build,
    destination: args.destination,
    builderCommit: args.commit,
    releasedAt: args["released-at"],
  });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`build-baked-site-bundle: ${error.message}\n`);
    process.exitCode = 1;
  });
}
