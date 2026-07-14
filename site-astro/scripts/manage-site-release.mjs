import { constants as fsConstants, open } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { createBakedSiteBundle, hydrateSiteCache } from "./lib/site-release-cache.mjs";
import {
  inventoryBuiltSite,
  parseSiteReleaseStoreLocation,
  publishSiteRelease,
  rollbackSiteRelease,
  siteContentIdentitySha256,
  siteReleasePayloadSha256,
  siteReleaseStatus,
} from "./lib/site-release-store.mjs";
import {
  createSiteReleaseReaderStore,
  createSiteReleaseWriterStore,
} from "./lib/runtime-s3-binding.mjs";
import { evaluateEventFreshness } from "./lib/occurrence-release.mjs";

const MAX_REQUEST_BYTES = 8 * 1024 * 1024;
const SHA256_RE = /^[0-9a-f]{64}$/u;
const COMMIT_RE = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u;
const EVENT_ID_RE = /^evt_[A-Za-z0-9_-]{8,128}$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u;
const CONTROL_RE = /[\u0000-\u001f\u007f]/u;
const REQUEST_KEYS = [
  "storeRoot",
  "buildRoot",
  "event",
  "sourceSnapshotManifestSha256",
  "policyVersion",
  "builderCommit",
  "releasedAt",
  "expectedSelectionSha256",
];
const EVENT_KEYS = [
  "contract",
  "schemaVersion",
  "eventId",
  "eventType",
  "sourceId",
  "sourceWatermark",
  "occurredAt",
  "payloadSha256",
];

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

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).join(",") === keys.join(",");
}

function safeText(value, label, maximum = 512) {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum || CONTROL_RE.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function instant(value, label) {
  safeText(value, label, 32);
  if (!ISO_INSTANT_RE.test(value) || !Number.isFinite(Date.parse(value))) throw new Error(`${label} is invalid`);
  return value;
}

function storeLocation(value) {
  safeText(value, "site release store", 2048);
  parseSiteReleaseStoreLocation(value);
  return value;
}

function directory(value, label) {
  return safeText(value, label, 4096);
}

function validateEvent(event) {
  if (
    !exactKeys(event, EVENT_KEYS)
    || event.contract !== "verdify.lab-release-trigger"
    || event.schemaVersion !== 1
    || !EVENT_ID_RE.test(event.eventId)
    || !["planner-completed", "forecast-published", "dataset-published", "reconciliation"].includes(event.eventType)
    || !SHA256_RE.test(event.payloadSha256)
  ) throw new Error("site release event does not use the closed v1 contract");
  safeText(event.sourceId, "site release source ID", 256);
  safeText(event.sourceWatermark, "site release source watermark", 512);
  instant(event.occurredAt, "site release occurrence time");
  return event;
}

async function validatePublishRequest(document, inventorySite) {
  if (!exactKeys(document, REQUEST_KEYS)) throw new Error("site release request does not use the closed v1 shape");
  storeLocation(document.storeRoot);
  directory(document.buildRoot, "site build root");
  validateEvent(document.event);
  if (!SHA256_RE.test(document.sourceSnapshotManifestSha256)) throw new Error("site source snapshot digest is invalid");
  safeText(document.policyVersion, "site policy version", 256);
  if (!COMMIT_RE.test(document.builderCommit)) throw new Error("site builder commit is invalid");
  instant(document.releasedAt, "site release time");
  evaluateEventFreshness(document.event, document.releasedAt);
  if (document.expectedSelectionSha256 !== null && !SHA256_RE.test(document.expectedSelectionSha256)) {
    throw new Error("site selection precondition is invalid");
  }
  const inventory = await inventorySite(document.buildRoot);
  const files = inventory.files.map(({ sourcePath: _sourcePath, ...record }) => record);
  const contentIdentitySha256 = siteContentIdentitySha256({
    sourceSnapshotManifestSha256: document.sourceSnapshotManifestSha256,
    policyVersion: document.policyVersion,
    builderCommit: document.builderCommit,
    files,
  });
  const expectedPayloadSha256 = siteReleasePayloadSha256({
    sourceSnapshotManifestSha256: document.sourceSnapshotManifestSha256,
    policyVersion: document.policyVersion,
    builderCommit: document.builderCommit,
    contentIdentitySha256,
  });
  if (document.event.payloadSha256 !== expectedPayloadSha256) throw new Error("site release event payload digest mismatch");
  return document;
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
  if (!exactKeys(document, REQUEST_KEYS)) throw new Error("site release request does not use the closed v1 shape");
  return document;
}

async function emit(document, outputWriter) {
  if (outputWriter !== null) {
    if (typeof outputWriter !== "function") throw new Error("site release output writer is invalid");
    await outputWriter(`${JSON.stringify(document, null, 2)}\n`);
  }
  return document;
}

export async function runSiteReleaseCommand(argv, {
  environment,
  createReaderStore = createSiteReleaseReaderStore,
  createWriterStore = createSiteReleaseWriterStore,
  inventorySite = inventoryBuiltSite,
  readRequest = requestDocument,
  publishRelease = publishSiteRelease,
  readStatus = siteReleaseStatus,
  rollbackRelease = rollbackSiteRelease,
  bakeBundle = createBakedSiteBundle,
  hydrateCache = hydrateSiteCache,
  outputWriter = null,
} = {}) {
  const { command, values } = options(argv);
  if (command === "prepare" && hasExactly(values, ["--build", "--snapshot", "--policy", "--commit"])) {
    directory(values.get("--build"), "site build root");
    if (!SHA256_RE.test(values.get("--snapshot"))) throw new Error("site source snapshot digest is invalid");
    if (!COMMIT_RE.test(values.get("--commit"))) throw new Error("site builder commit is invalid");
    safeText(values.get("--policy"), "site policy version", 256);
    const inventory = await inventorySite(values.get("--build"));
    const files = inventory.files.map(({ sourcePath: _sourcePath, ...record }) => record);
    const identity = siteContentIdentitySha256({
      sourceSnapshotManifestSha256: values.get("--snapshot"),
      policyVersion: values.get("--policy"),
      builderCommit: values.get("--commit"),
      files,
    });
    return emit({
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
    }, outputWriter);
  }
  if (command === "publish" && hasExactly(values, ["--request"])) {
    directory(values.get("--request"), "site release request path");
    const request = await validatePublishRequest(await readRequest(values.get("--request")), inventorySite);
    const store = await createWriterStore(request.storeRoot, { environment, create: true });
    const result = await publishRelease({ ...request, store });
    return emit({
      contract: "verdify.lab-site-publish-result",
      schemaVersion: 1,
      releaseSha256: result.releaseSha256,
      selectionSha256: result.selectionSha256,
      idempotent: result.idempotent,
      unchanged: result.unchanged ?? false,
      retained: result.retained,
      ignoredStaleReplay: result.ignoredStaleReplay ?? false,
    }, outputWriter);
  }
  if (command === "status" && hasExactly(values, ["--store"], ["--at"])) {
    const selectedStore = storeLocation(values.get("--store"));
    const asOf = values.get("--at") ?? null;
    if (asOf !== null) instant(asOf, "site status evaluation time");
    const store = await createReaderStore(selectedStore, { environment });
    return emit(await readStatus({ storeRoot: selectedStore, store, asOf }), outputWriter);
  }
  if (command === "rollback" && hasExactly(values, ["--store", "--expected", "--at"])) {
    const selectedStore = storeLocation(values.get("--store"));
    if (!SHA256_RE.test(values.get("--expected"))) throw new Error("site rollback precondition is invalid");
    instant(values.get("--at"), "site rollback time");
    const store = await createWriterStore(selectedStore, { environment });
    const result = await rollbackRelease({
      storeRoot: selectedStore,
      store,
      expectedSelectionSha256: values.get("--expected"),
      rolledBackAt: values.get("--at"),
    });
    return emit({
      contract: "verdify.lab-site-rollback-result",
      schemaVersion: 1,
      selectionSha256: result.selectionSha256,
      generation: result.selection.generation,
      currentReleaseSha256: result.selection.current.releaseSha256,
      previousReleaseSha256: result.selection.previous.releaseSha256,
    }, outputWriter);
  }
  if (command === "bundle" && hasExactly(values, ["--store", "--release", "--destination"])) {
    const selectedStore = storeLocation(values.get("--store"));
    if (!SHA256_RE.test(values.get("--release"))) throw new Error("baked site release digest is invalid");
    directory(values.get("--destination"), "baked site bundle destination");
    const store = await createReaderStore(selectedStore, { environment });
    return emit({
      contract: "verdify.lab-baked-site-bundle-result",
      schemaVersion: 1,
      ...await bakeBundle({
        storeRoot: selectedStore,
        store,
        releaseSha256: values.get("--release"),
        bundleRoot: values.get("--destination"),
      }),
    }, outputWriter);
  }
  if (command === "hydrate" && hasExactly(values, ["--store", "--cache"], ["--baked", "--at"])) {
    const selectedStore = storeLocation(values.get("--store"));
    directory(values.get("--cache"), "site cache root");
    const bakedBundleRoot = values.get("--baked") ?? null;
    if (bakedBundleRoot !== null) directory(bakedBundleRoot, "baked site bundle root");
    const asOf = values.get("--at") ?? null;
    if (asOf !== null) instant(asOf, "site status evaluation time");
    let store;
    try {
      store = await createReaderStore(selectedStore, { environment });
    } catch (error) {
      const location = parseSiteReleaseStoreLocation(selectedStore);
      if (bakedBundleRoot === null || location.kind !== "local" || error.code !== "ENOENT") throw error;
      store = null;
    }
    return emit(await hydrateCache({
      storeRoot: selectedStore,
      store,
      cacheRoot: values.get("--cache"),
      bakedBundleRoot,
      asOf,
    }), outputWriter);
  }
  throw new Error(usage());
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  runSiteReleaseCommand(process.argv.slice(2), {
    environment: process.env,
    outputWriter: (bytes) => process.stdout.write(bytes),
  }).catch((error) => {
    process.stderr.write(`manage-site-release: ${error.message}\n`);
    process.exitCode = 1;
  });
}
