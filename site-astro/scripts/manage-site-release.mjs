import { constants as fsConstants, open } from "node:fs/promises";
import path from "node:path";

import {
  inventoryBuiltSite,
  publishSiteRelease,
  rollbackSiteRelease,
  siteContentIdentitySha256,
  siteReleasePayloadSha256,
  siteReleaseStatus,
} from "./lib/site-release-store.mjs";
import { createBakedSiteBundle, hydrateSiteCache } from "./lib/site-release-cache.mjs";

const MAX_REQUEST_BYTES = 8 * 1024 * 1024;
const SHA256_RE = /^[0-9a-f]{64}$/;
const COMMIT_RE = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;

function usage() {
  return [
    "Usage:",
    "  node scripts/manage-site-release.mjs prepare --build BUILD --snapshot SHA256 --policy VERSION --commit COMMIT",
    "  node scripts/manage-site-release.mjs publish --request REQUEST.json",
    "  node scripts/manage-site-release.mjs status --store STORE [--at ISO_INSTANT]",
    "  node scripts/manage-site-release.mjs rollback --store STORE --expected SELECTION_SHA256 --at ISO_INSTANT",
    "  node scripts/manage-site-release.mjs bundle --store STORE --release RELEASE_SHA256 --destination DIRECTORY",
    "  node scripts/manage-site-release.mjs hydrate --store STORE --cache CACHE [--baked DIRECTORY] [--at ISO_INSTANT]",
  ].join("\n");
}

function options(argv) {
  const result = { command: argv[0] ?? "", values: new Map() };
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined || result.values.has(key)) throw new Error("invalid command arguments");
    result.values.set(key, value);
  }
  return result;
}

function hasExactly(values, required, optional = []) {
  return required.every((key) => values.has(key))
    && [...values.keys()].every((key) => required.includes(key) || optional.includes(key));
}

async function requestDocument(file) {
  const absolute = path.resolve(file);
  const handle = await open(absolute, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  let bytes;
  try {
    const metadata = await handle.stat({ bigint: true });
    if (!metadata.isFile() || metadata.nlink !== 1n || metadata.size < 1n || metadata.size > BigInt(MAX_REQUEST_BYTES)) {
      throw new Error("site release request is not a bounded single-link regular file");
    }
    bytes = await handle.readFile();
    const after = await handle.stat({ bigint: true });
    if (after.dev !== metadata.dev || after.ino !== metadata.ino || after.size !== metadata.size || after.nlink !== 1n) {
      throw new Error("site release request changed while being read");
    }
  } finally {
    await handle.close();
  }
  let document;
  try {
    document = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("site release request is not valid JSON");
  }
  if (`${JSON.stringify(document, null, 2)}\n` !== bytes.toString("utf8")) throw new Error("site release request is not canonical JSON");
  const keys = [
    "storeRoot",
    "buildRoot",
    "event",
    "sourceSnapshotManifestSha256",
    "policyVersion",
    "builderCommit",
    "releasedAt",
    "expectedSelectionSha256",
  ];
  if (Object.keys(document).join(",") !== keys.join(",")) throw new Error("site release request does not use the closed v1 shape");
  return document;
}

function output(document) {
  process.stdout.write(`${JSON.stringify(document, null, 2)}\n`);
}

async function main() {
  const { command, values } = options(process.argv.slice(2));
  if (command === "prepare" && hasExactly(values, ["--build", "--snapshot", "--policy", "--commit"])) {
    if (!SHA256_RE.test(values.get("--snapshot"))) throw new Error("site source snapshot digest is invalid");
    if (!COMMIT_RE.test(values.get("--commit"))) throw new Error("site builder commit is invalid");
    if (!values.get("--policy") || values.get("--policy").length > 256 || /[\u0000-\u001f\u007f]/u.test(values.get("--policy"))) {
      throw new Error("site policy version is invalid");
    }
    const inventory = await inventoryBuiltSite(values.get("--build"));
    const files = inventory.files.map(({ sourcePath: _sourcePath, ...record }) => record);
    const identity = siteContentIdentitySha256({
      sourceSnapshotManifestSha256: values.get("--snapshot"),
      policyVersion: values.get("--policy"),
      builderCommit: values.get("--commit"),
      files,
    });
    output({
      contract: "verdify.lab-site-release-preparation",
      schemaVersion: 1,
      contentIdentitySha256: identity,
      payloadSha256: siteReleasePayloadSha256({
        sourceSnapshotManifestSha256: values.get("--snapshot"),
        policyVersion: values.get("--policy"),
        builderCommit: values.get("--commit"),
        contentIdentitySha256: identity,
      }),
      fileCount: files.length,
      totalBytes: inventory.totalBytes,
    });
    return;
  }
  if (command === "publish" && hasExactly(values, ["--request"])) {
    const result = await publishSiteRelease(await requestDocument(values.get("--request")));
    output({
      contract: "verdify.lab-site-publish-result",
      schemaVersion: 1,
      releaseSha256: result.releaseSha256,
      selectionSha256: result.selectionSha256,
      idempotent: result.idempotent,
      unchanged: result.unchanged ?? false,
      retained: result.retained,
      ignoredStaleReplay: result.ignoredStaleReplay ?? false,
    });
    return;
  }
  if (command === "status" && hasExactly(values, ["--store"], ["--at"])) {
    output(await siteReleaseStatus({ storeRoot: values.get("--store"), asOf: values.get("--at") ?? null }));
    return;
  }
  if (command === "rollback" && hasExactly(values, ["--store", "--expected", "--at"])) {
    const result = await rollbackSiteRelease({
      storeRoot: values.get("--store"),
      expectedSelectionSha256: values.get("--expected"),
      rolledBackAt: values.get("--at"),
    });
    output({
      contract: "verdify.lab-site-rollback-result",
      schemaVersion: 1,
      selectionSha256: result.selectionSha256,
      generation: result.selection.generation,
      currentReleaseSha256: result.selection.current.releaseSha256,
      previousReleaseSha256: result.selection.previous.releaseSha256,
    });
    return;
  }
  if (command === "bundle" && hasExactly(values, ["--store", "--release", "--destination"])) {
    output({
      contract: "verdify.lab-baked-site-bundle-result",
      schemaVersion: 1,
      ...await createBakedSiteBundle({
        storeRoot: values.get("--store"),
        releaseSha256: values.get("--release"),
        bundleRoot: values.get("--destination"),
      }),
    });
    return;
  }
  if (command === "hydrate" && hasExactly(values, ["--store", "--cache"], ["--baked", "--at"])) {
    output(await hydrateSiteCache({
      storeRoot: values.get("--store"),
      cacheRoot: values.get("--cache"),
      bakedBundleRoot: values.get("--baked") ?? null,
      asOf: values.get("--at") ?? null,
    }));
    return;
  }
  throw new Error(usage());
}

main().catch((error) => {
  process.stderr.write(`manage-site-release: ${error.message}\n`);
  process.exitCode = 1;
});
