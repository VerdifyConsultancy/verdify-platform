import { createHash, randomUUID } from "node:crypto";
import {
  constants as fsConstants,
  copyFile,
  link,
  lstat,
  mkdir,
  open,
  opendir,
  readdir,
  realpath,
  rename,
  rm,
  rmdir,
  unlink,
} from "node:fs/promises";
import { hostname } from "node:os";
import path from "node:path";

import { evaluateEventFreshness } from "./occurrence-release.mjs";
import { parseSiteReleaseStoreLocation, S3ObjectStore } from "./s3-object-store.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/;
const EVENT_ID_RE = /^evt_[A-Za-z0-9_-]{8,128}$/;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const COMMIT_RE = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;
const MAX_TREE_FILES = 10_000;
const MAX_FILE_BYTES = 128 * 1024 * 1024;
const MAX_TREE_BYTES = 1024 * 1024 * 1024;
const MAX_STORE_BYTES = 10 * 1024 * 1024 * 1024;
const MAX_DEPTH = 24;
const MAX_MANIFEST_BYTES = 16 * 1024 * 1024;
const RETAIN_RELEASES = 10;
const MAX_EVENT_RECORDS = 1_000_000;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SITE_RELEASE_NAMESPACE = "site-releases/v1";

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function siteReleaseStoreIdentity(location) {
  const document = location.kind === "local"
    ? {
        contract: "verdify.lab-site-release-store-identity",
        schemaVersion: 1,
        namespace: SITE_RELEASE_NAMESPACE,
        backend: "local",
        root: location.root,
      }
    : {
        contract: "verdify.lab-site-release-store-identity",
        schemaVersion: 1,
        namespace: SITE_RELEASE_NAMESPACE,
        backend: "s3",
        bucket: location.bucket,
        prefix: location.prefix,
      };
  return Object.freeze({
    document: Object.freeze(document),
    sha256: sha256(canonicalBytes(document)),
  });
}

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).join(",") === keys.join(",");
}

function safeText(value, label, maximum = 512) {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum || /[\u0000-\u001f\u007f]/u.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function instant(value, label) {
  safeText(value, label, 32);
  if (!ISO_INSTANT_RE.test(value) || !Number.isFinite(Date.parse(value))) throw new Error(`${label} is invalid`);
  return value;
}

function safeRelativePath(value) {
  if (
    typeof value !== "string"
    || value.length === 0
    || value.length > 4096
    || value.includes("\\")
    || value.startsWith("/")
    || path.posix.normalize(value) !== value
    || value === ".."
    || value.startsWith("../")
  ) {
    throw new Error("site release contains an invalid path");
  }
  return value;
}

function mediaType(relative) {
  const extension = path.posix.extname(relative).toLowerCase();
  return new Map([
    [".html", "text/html; charset=utf-8"],
    [".css", "text/css; charset=utf-8"],
    [".js", "text/javascript; charset=utf-8"],
    [".json", "application/json"],
    [".xml", "application/xml"],
    [".txt", "text/plain; charset=utf-8"],
    [".csv", "text/csv; charset=utf-8"],
    [".svg", "image/svg+xml"],
    [".png", "image/png"],
    [".jpg", "image/jpeg"],
    [".jpeg", "image/jpeg"],
    [".webp", "image/webp"],
    [".woff2", "font/woff2"],
    [".wasm", "application/wasm"],
    [".m3u8", "application/vnd.apple.mpegurl"],
    [".ts", "video/mp2t"],
    [".mp4", "video/mp4"],
  ]).get(extension) ?? "application/octet-stream";
}

async function canonicalRoot(root, label) {
  const absolute = path.resolve(root);
  const metadata = await lstat(absolute, { bigint: true });
  if (!metadata.isDirectory() || metadata.isSymbolicLink() || (await realpath(absolute)) !== absolute) {
    throw new Error(`${label} is not a canonical real directory`);
  }
  return absolute;
}

async function secureDirectory(root, relative, { create = false, leafMode = 0o755 } = {}) {
  let current = root;
  for (const [index, segment] of relative.split("/").entries()) {
    current = path.join(current, segment);
    if (create) {
      try {
        await mkdir(current, { mode: index === relative.split("/").length - 1 ? leafMode : 0o755 });
      } catch (error) {
        if (error.code !== "EEXIST") throw error;
      }
    }
    const metadata = await lstat(current, { bigint: true });
    if (!metadata.isDirectory() || metadata.isSymbolicLink() || (await realpath(current)) !== current) {
      throw new Error("site release store layout is invalid");
    }
  }
  return current;
}

async function readSingleLink(file, maximumBytes, label) {
  const handle = await open(file, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  try {
    const before = await handle.stat({ bigint: true });
    if (!before.isFile() || before.nlink !== 1n || before.size < 1n || before.size > BigInt(maximumBytes)) {
      throw new Error(`${label} is not a bounded single-link regular file`);
    }
    const bytes = await handle.readFile();
    const after = await handle.stat({ bigint: true });
    if (
      bytes.length !== Number(before.size)
      || after.dev !== before.dev
      || after.ino !== before.ino
      || after.size !== before.size
      || after.nlink !== 1n
    ) {
      throw new Error(`${label} changed while being read`);
    }
    return { bytes, metadata: before };
  } finally {
    await handle.close();
  }
}

async function digestFile(file, maximumBytes = MAX_FILE_BYTES) {
  const { bytes, metadata } = await readSingleLink(file, maximumBytes, "site release input");
  return { sha256: sha256(bytes), bytes: bytes.length, metadata };
}

async function syncDirectory(directory) {
  const handle = await open(directory, fsConstants.O_RDONLY);
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function syncFile(file) {
  const handle = await open(file, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  try {
    const metadata = await handle.stat({ bigint: true });
    if (!metadata.isFile() || metadata.nlink !== 1n) throw new Error("site release file cannot be made durable");
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function atomicCanonicalWrite(destination, value) {
  const directory = path.dirname(destination);
  const temporary = path.join(directory, `.candidate-${randomUUID()}`);
  const bytes = canonicalBytes(value);
  const handle = await open(temporary, "wx", 0o644);
  try {
    await handle.writeFile(bytes);
    await handle.sync();
  } finally {
    await handle.close();
  }
  await rename(temporary, destination);
  await syncDirectory(directory);
  return sha256(bytes);
}

async function publishCanonicalAbsent(destination, value) {
  const directory = path.dirname(destination);
  const temporary = path.join(directory, `.candidate-${randomUUID()}`);
  const bytes = canonicalBytes(value);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(bytes);
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await link(temporary, destination);
    await syncDirectory(directory);
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    const existing = await readSingleLink(destination, bytes.length, "content-addressed site JSON");
    if (!existing.bytes.equals(bytes)) throw new Error("content-addressed site JSON collision");
  } finally {
    await unlink(temporary).catch(() => {});
  }
  return sha256(bytes);
}

function validateEvent(event) {
  if (
    !exactKeys(event, [
      "contract",
      "schemaVersion",
      "eventId",
      "eventType",
      "sourceId",
      "sourceWatermark",
      "occurredAt",
      "payloadSha256",
    ])
    || event.contract !== "verdify.lab-release-trigger"
    || event.schemaVersion !== 1
    || !EVENT_ID_RE.test(event.eventId)
    || !["planner-completed", "forecast-published", "dataset-published", "reconciliation"].includes(event.eventType)
    || !SHA256_RE.test(event.payloadSha256)
  ) {
    throw new Error("site release event does not use the closed v1 contract");
  }
  safeText(event.sourceId, "site release source ID", 256);
  safeText(event.sourceWatermark, "site release source watermark", 512);
  instant(event.occurredAt, "site release occurrence time");
  return event;
}

export function siteReleasePayloadSha256({
  sourceSnapshotManifestSha256,
  policyVersion,
  builderCommit,
  contentIdentitySha256,
}) {
  return sha256(canonicalBytes({
    contract: "verdify.lab-site-release-payload",
    schemaVersion: 1,
    sourceSnapshotManifestSha256,
    policyVersion,
    builderCommit,
    contentIdentitySha256,
  }));
}

export async function inventoryBuiltSite(buildRoot) {
  const root = await canonicalRoot(buildRoot, "site build root");
  const pending = [{ absolute: root, relative: "", depth: 0 }];
  const files = [];
  const folded = new Set();
  let totalBytes = 0;
  while (pending.length > 0) {
    const directory = pending.pop();
    if (directory.depth > MAX_DEPTH) throw new Error("site build tree exceeds its depth limit");
    const handle = await opendir(directory.absolute);
    for await (const entry of handle) {
      const relative = directory.relative ? `${directory.relative}/${entry.name}` : entry.name;
      safeRelativePath(relative);
      const identity = relative.normalize("NFC").toLocaleLowerCase("en-US");
      if (folded.has(identity)) throw new Error("site build tree has a case-folded collision");
      folded.add(identity);
      const absolute = path.join(root, ...relative.split("/"));
      const metadata = await lstat(absolute, { bigint: true });
      if (metadata.isSymbolicLink()) throw new Error("site build tree contains a symlink");
      if (metadata.isDirectory()) {
        pending.push({ absolute, relative, depth: directory.depth + 1 });
        continue;
      }
      if (!metadata.isFile() || metadata.nlink !== 1n) throw new Error("site build tree contains a non-single-link file");
      const digest = await digestFile(absolute);
      if (digest.metadata.dev !== metadata.dev || digest.metadata.ino !== metadata.ino) {
        throw new Error("site build file identity changed during inventory");
      }
      totalBytes += digest.bytes;
      if (totalBytes > MAX_TREE_BYTES) throw new Error("site build tree exceeds its byte limit");
      files.push({
        path: relative,
        sha256: digest.sha256,
        bytes: digest.bytes,
        mediaType: mediaType(relative),
        sourcePath: absolute,
      });
      if (files.length > MAX_TREE_FILES) throw new Error("site build tree exceeds its file limit");
    }
  }
  files.sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
  if (!files.some((file) => file.path === "index.html")) throw new Error("site build tree lacks index.html");
  return { root, files, totalBytes };
}

function manifestFileRecords(inventory) {
  return inventory.files.map(({ sourcePath: _sourcePath, ...record }) => record);
}

export function siteContentIdentitySha256({ sourceSnapshotManifestSha256, policyVersion, builderCommit, files }) {
  return sha256(canonicalBytes({
    contract: "verdify.lab-site-content-identity",
    schemaVersion: 1,
    sourceSnapshotManifestSha256,
    policyVersion,
    builderCommit,
    files,
  }));
}

function selectionRecord(current, previous, generation, selectedAt, reason) {
  return {
    contract: "verdify.lab-site-release-selection",
    schemaVersion: 1,
    generation,
    current,
    previous,
    selectedAt,
    reason,
  };
}

function validateSelectionPointer(pointer) {
  if (pointer === null) return null;
  if (
    !exactKeys(pointer, ["releaseSha256", "eventId"])
    || !SHA256_RE.test(pointer.releaseSha256)
    || !EVENT_ID_RE.test(pointer.eventId)
  ) throw new Error("site release selection pointer is invalid");
  return pointer;
}

function validateSelection(selection, bytes) {
  if (
    !exactKeys(selection, ["contract", "schemaVersion", "generation", "current", "previous", "selectedAt", "reason"])
    || selection.contract !== "verdify.lab-site-release-selection"
    || selection.schemaVersion !== 1
    || !Number.isSafeInteger(selection.generation)
    || selection.generation < 1
    || !["publish", "rollback"].includes(selection.reason)
    || canonicalBytes(selection).compare(bytes) !== 0
  ) throw new Error("site release selection does not use the canonical v1 contract");
  validateSelectionPointer(selection.current);
  validateSelectionPointer(selection.previous);
  if (selection.current === null || selection.current.releaseSha256 === selection.previous?.releaseSha256) {
    throw new Error("site release selection pointers are inconsistent");
  }
  instant(selection.selectedAt, "site release selection time");
  return selection;
}

function eventIntent(event, releaseSha256, expectedSelectionSha256, storeIdentitySha256) {
  return {
    contract: "verdify.lab-site-release-event-intent",
    schemaVersion: 2,
    storeIdentitySha256,
    eventId: event.eventId,
    eventSha256: sha256(canonicalBytes(event)),
    payloadSha256: event.payloadSha256,
    releaseSha256,
    expectedSelectionSha256,
  };
}

function validateSiteEventIntent(intent, bytes, expectedStoreIdentitySha256) {
  if (
    !exactKeys(intent, [
      "contract",
      "schemaVersion",
      "storeIdentitySha256",
      "eventId",
      "eventSha256",
      "payloadSha256",
      "releaseSha256",
      "expectedSelectionSha256",
    ])
    || intent.contract !== "verdify.lab-site-release-event-intent"
    || intent.schemaVersion !== 2
    || intent.storeIdentitySha256 !== expectedStoreIdentitySha256
    || !SHA256_RE.test(intent.storeIdentitySha256)
    || !EVENT_ID_RE.test(intent.eventId)
    || !SHA256_RE.test(intent.eventSha256)
    || !SHA256_RE.test(intent.payloadSha256)
    || !SHA256_RE.test(intent.releaseSha256)
    || (intent.expectedSelectionSha256 !== null && !SHA256_RE.test(intent.expectedSelectionSha256))
    || canonicalBytes(intent).compare(bytes) !== 0
  ) throw new Error("site release event intent does not match its canonical store identity");
  return intent;
}

// Backend contract for a future object-store implementation. Implementations must
// preserve absent-only immutable objects and conditional/atomic selector semantics;
// the local backend serializes those operations with its same-host lease.
export class SiteReleaseStore {
  constructor(location) {
    this.location = location;
    this.identity = siteReleaseStoreIdentity(location);
  }

  async initialize(_options = {}) { throw new Error("site release store initialize is not implemented"); }
  async readSelection() { throw new Error("site release store selection read is not implemented"); }
  async writeSelection(_selection, _expectedSelectionSha256) { throw new Error("site release store selection write is not implemented"); }
  async publishBlob(_source) { throw new Error("site release store blob publication is not implemented"); }
  async readBlob(_digest, _options = {}) { throw new Error("site release store blob read is not implemented"); }
  async publishRelease(_manifest) { throw new Error("site release store manifest publication is not implemented"); }
  async readRelease(_digest, _options = {}) { throw new Error("site release store manifest read is not implemented"); }
  async publishEventIntent(_intent) { throw new Error("site release store event publication is not implemented"); }
  async readEventIntent(_eventId) { throw new Error("site release store event read is not implemented"); }
  async listReleaseDigests() { throw new Error("site release store listing is not implemented"); }

  async withPublication(callback) {
    return callback();
  }

  async prepareSelectionTransition(_plannedSelection, _priorSelection, _options = {}) {
    // Object-store retention/GC is deliberately a separate gated slice. The
    // immutable objects and selector CAS remain safe without deleting objects.
    return null;
  }
}

export class LocalSiteReleaseStore extends SiteReleaseStore {
  constructor(root) {
    const location = Object.freeze({ kind: "local", root: path.resolve(root) });
    super(location);
    this.root = location.root;
  }

  async initialize({ create = false } = {}) {
    this.root = await canonicalRoot(this.root, "site release store root");
    this.location = Object.freeze({ kind: "local", root: this.root });
    this.identity = siteReleaseStoreIdentity(this.location);
    for (const relative of ["blobs/sha256", "releases/sha256", "events/sha256", ".lease-tombstones"]) {
      await secureDirectory(this.root, relative, { create });
    }
    await secureDirectory(this.root, ".quarantine", { create, leafMode: 0o700 });
    return this;
  }

  blobPath(digest) {
    if (!SHA256_RE.test(digest)) throw new Error("site blob digest is invalid");
    return path.join(this.root, "blobs", "sha256", digest);
  }

  releasePath(digest) {
    if (!SHA256_RE.test(digest)) throw new Error("site release digest is invalid");
    return path.join(this.root, "releases", "sha256", `${digest}.json`);
  }

  eventPath(eventId) {
    if (!EVENT_ID_RE.test(eventId)) throw new Error("site event ID is invalid");
    return path.join(this.root, "events", "sha256", `${sha256(Buffer.from(eventId))}.json`);
  }

  async readSelection() {
    let value;
    try {
      value = await readSingleLink(path.join(this.root, "selection.json"), 64 * 1024, "site release selection");
    } catch (error) {
      if (error.code === "ENOENT") return null;
      throw error;
    }
    let selection;
    try {
      selection = JSON.parse(value.bytes.toString("utf8"));
    } catch {
      throw new Error("site release selection is not valid JSON");
    }
    return { document: validateSelection(selection, value.bytes), sha256: sha256(value.bytes) };
  }

  async writeSelection(selection, expectedSelectionSha256) {
    const selected = await this.readSelection();
    if ((selected?.sha256 ?? null) !== expectedSelectionSha256) throw new Error("site selection precondition failed");
    return atomicCanonicalWrite(path.join(this.root, "selection.json"), selection);
  }

  async publishBlob(source) {
    const destination = this.blobPath(source.sha256);
    const temporary = path.join(this.root, ".quarantine", `${randomUUID()}.blob`);
    try {
      await copyFile(source.sourcePath, temporary, fsConstants.COPYFILE_EXCL);
      const copied = await digestFile(temporary);
      if (copied.sha256 !== source.sha256 || copied.bytes !== source.bytes) throw new Error("site blob changed during import");
      await syncFile(temporary);
      try {
        await link(temporary, destination);
        await syncDirectory(path.dirname(destination));
      } catch (error) {
        if (error.code !== "EEXIST") throw error;
        const existing = await digestFile(destination);
        if (existing.sha256 !== source.sha256 || existing.bytes !== source.bytes) throw new Error("content-addressed site blob collision");
      }
    } finally {
      await unlink(temporary).catch(() => {});
    }
  }

  async readBlob(digest, { maximumBytes = MAX_FILE_BYTES } = {}) {
    if (!SHA256_RE.test(digest)) throw new Error("site blob digest is invalid");
    if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1 || maximumBytes > MAX_FILE_BYTES) {
      throw new Error("site blob read limit is invalid");
    }
    const value = await readSingleLink(this.blobPath(digest), maximumBytes, "site release blob");
    if (sha256(value.bytes) !== digest) throw new Error("site release blob digest mismatch");
    return { body: value.bytes, bytes: value.bytes.length, sha256: digest };
  }

  async publishRelease(manifest) {
    const digest = sha256(canonicalBytes(manifest));
    await publishCanonicalAbsent(this.releasePath(digest), manifest);
    return digest;
  }

  async readRelease(digest, { verifyBlobs = true } = {}) {
    const value = await readSingleLink(this.releasePath(digest), MAX_MANIFEST_BYTES, "site release manifest");
    if (sha256(value.bytes) !== digest) throw new Error("site release manifest digest mismatch");
    let manifest;
    try {
      manifest = JSON.parse(value.bytes.toString("utf8"));
    } catch {
      throw new Error("site release manifest is not valid JSON");
    }
    validateSiteReleaseManifest(manifest, value.bytes);
    if (verifyBlobs) {
      const verified = new Set();
      for (const file of manifest.files) {
        if (verified.has(file.sha256)) continue;
        const blob = await this.readBlob(file.sha256, { maximumBytes: file.bytes });
        if (blob.bytes !== file.bytes) throw new Error("site release blob verification failed");
        verified.add(file.sha256);
      }
    }
    return manifest;
  }

  async publishEventIntent(intent) {
    validateSiteEventIntent(intent, canonicalBytes(intent), this.identity.sha256);
    await publishCanonicalAbsent(this.eventPath(intent.eventId), intent);
  }

  async readEventIntent(eventId) {
    let value;
    try {
      value = await readSingleLink(this.eventPath(eventId), 32 * 1024, "site release event intent");
    } catch (error) {
      if (error.code === "ENOENT") return null;
      throw error;
    }
    let intent;
    try {
      intent = JSON.parse(value.bytes.toString("utf8"));
    } catch {
      throw new Error("site release event intent is not valid JSON");
    }
    return validateSiteEventIntent(intent, value.bytes, this.identity.sha256);
  }

  async listReleaseDigests() {
    const names = await readdir(path.join(this.root, "releases", "sha256"));
    if (names.length > 1000 || names.some((name) => !/^[0-9a-f]{64}\.json$/.test(name))) {
      throw new Error("site release manifest membership is invalid");
    }
    return names.map((name) => name.slice(0, -5));
  }

  async withPublication(callback) {
    return withLocalLease(this, callback);
  }

  async prepareSelectionTransition(plannedSelection, priorSelection, options = {}) {
    return pruneAndCollect(this, plannedSelection, priorSelection, options);
  }
}

function parseS3CanonicalJson(bytes, label) {
  try {
    return JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error(`${label} is not valid JSON`);
  }
}

export class S3SiteReleaseStore extends SiteReleaseStore {
  constructor(location, options = {}) {
    const parsed = typeof location === "string" ? parseSiteReleaseStoreLocation(location) : location;
    if (
      parsed === null
      || typeof parsed !== "object"
      || parsed.kind !== "s3"
      || typeof parsed.bucket !== "string"
      || typeof parsed.prefix !== "string"
    ) throw new Error("S3 site release store location is invalid");
    const normalized = Object.freeze({ kind: "s3", bucket: parsed.bucket, prefix: parsed.prefix });
    super(normalized);
    this.objects = new S3ObjectStore({
      bucket: parsed.bucket,
      prefix: parsed.prefix,
      client: options.client ?? null,
      clientConfig: options.clientConfig ?? {},
      clientFactory: options.clientFactory,
    });
  }

  async initialize(_options = {}) {
    await this.objects.initialize();
    return this;
  }

  blobKey(digest) {
    if (!SHA256_RE.test(digest)) throw new Error("site blob digest is invalid");
    return `blobs/sha256/${digest}`;
  }

  releaseKey(digest) {
    if (!SHA256_RE.test(digest)) throw new Error("site release digest is invalid");
    return `releases/sha256/${digest}.json`;
  }

  eventKey(eventId) {
    if (!EVENT_ID_RE.test(eventId)) throw new Error("site event ID is invalid");
    return `events/sha256/${sha256(Buffer.from(eventId))}.json`;
  }

  async publishImmutable(key, bytes, maximumBytes, collisionMessage, contentType) {
    if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1) {
      throw new Error("immutable site release object byte limit is invalid");
    }
    if (!Buffer.isBuffer(bytes) || bytes.length < 1 || bytes.length > maximumBytes) {
      throw new Error("immutable site release object is outside its byte limit");
    }
    const published = await this.objects.putIfAbsent(key, bytes, { contentType });
    if (published.written) return;
    const existing = await this.objects.read(key, { maximumBytes, label: "immutable site release object" });
    if (!existing.bytes.equals(bytes)) throw new Error(collisionMessage);
  }

  async readSelection() {
    const value = await this.objects.read("selection.json", {
      maximumBytes: 64 * 1024,
      label: "site release selection",
      missing: true,
    });
    if (value === null) return null;
    const selection = parseS3CanonicalJson(value.bytes, "site release selection");
    return {
      document: validateSelection(selection, value.bytes),
      sha256: sha256(value.bytes),
      etag: value.etag,
    };
  }

  async writeSelection(selection, expectedSelectionSha256) {
    const bytes = canonicalBytes(selection);
    validateSelection(selection, bytes);
    const selected = await this.readSelection();
    if ((selected?.sha256 ?? null) !== expectedSelectionSha256) throw new Error("site selection precondition failed");
    const result = selected === null
      ? await this.objects.putIfAbsent("selection.json", bytes, { contentType: "application/json" })
      : await this.objects.putIfMatch("selection.json", bytes, selected.etag, { contentType: "application/json" });
    if (!result.written) throw new Error("site selection precondition failed");
    return sha256(bytes);
  }

  async publishBlob(source) {
    const key = this.blobKey(source.sha256);
    if (!Number.isSafeInteger(source.bytes) || source.bytes < 1 || source.bytes > MAX_FILE_BYTES) {
      throw new Error("site blob byte count is invalid");
    }
    const value = await readSingleLink(source.sourcePath, MAX_FILE_BYTES, "site release input");
    if (sha256(value.bytes) !== source.sha256 || value.bytes.length !== source.bytes) throw new Error("site blob changed during import");
    await this.publishImmutable(key, value.bytes, MAX_FILE_BYTES, "content-addressed site blob collision", "application/octet-stream");
  }

  async readBlob(digest, { maximumBytes = MAX_FILE_BYTES } = {}) {
    if (!SHA256_RE.test(digest)) throw new Error("site blob digest is invalid");
    if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1 || maximumBytes > MAX_FILE_BYTES) {
      throw new Error("site blob read limit is invalid");
    }
    const value = await this.objects.read(this.blobKey(digest), {
      maximumBytes,
      label: "site release blob",
    });
    if (sha256(value.bytes) !== digest) throw new Error("site release blob digest mismatch");
    return { body: value.bytes, bytes: value.bytes.length, sha256: digest };
  }

  async publishRelease(manifest) {
    const bytes = canonicalBytes(manifest);
    validateSiteReleaseManifest(manifest, bytes);
    const digest = sha256(bytes);
    await this.publishImmutable(
      this.releaseKey(digest),
      bytes,
      MAX_MANIFEST_BYTES,
      "content-addressed site JSON collision",
      "application/json",
    );
    return digest;
  }

  async readRelease(digest, { verifyBlobs = true } = {}) {
    const value = await this.objects.read(this.releaseKey(digest), {
      maximumBytes: MAX_MANIFEST_BYTES,
      label: "site release manifest",
    });
    if (sha256(value.bytes) !== digest) throw new Error("site release manifest digest mismatch");
    const manifest = parseS3CanonicalJson(value.bytes, "site release manifest");
    validateSiteReleaseManifest(manifest, value.bytes);
    if (verifyBlobs) {
      const verified = new Set();
      for (const file of manifest.files) {
        if (verified.has(file.sha256)) continue;
        const blob = await this.readBlob(file.sha256, { maximumBytes: file.bytes });
        if (blob.bytes !== file.bytes) {
          throw new Error("site release blob verification failed");
        }
        verified.add(file.sha256);
      }
    }
    return manifest;
  }

  async publishEventIntent(intent) {
    const bytes = canonicalBytes(intent);
    validateSiteEventIntent(intent, bytes, this.identity.sha256);
    await this.publishImmutable(
      this.eventKey(intent.eventId),
      bytes,
      32 * 1024,
      "content-addressed site JSON collision",
      "application/json",
    );
  }

  async readEventIntent(eventId) {
    const value = await this.objects.read(this.eventKey(eventId), {
      maximumBytes: 32 * 1024,
      label: "site release event intent",
      missing: true,
    });
    if (value === null) return null;
    return validateSiteEventIntent(
      parseS3CanonicalJson(value.bytes, "site release event intent"),
      value.bytes,
      this.identity.sha256,
    );
  }

  async listReleaseDigests() {
    const keys = await this.objects.list("releases/sha256/", { maximumObjects: 1000 });
    if (keys.some((key) => !/^releases\/sha256\/[0-9a-f]{64}\.json$/u.test(key))) {
      throw new Error("site release manifest membership is invalid");
    }
    return keys.map((key) => key.slice("releases/sha256/".length, -5));
  }
}

export function createSiteReleaseStore(location, options = {}) {
  const parsed = parseSiteReleaseStoreLocation(location);
  return parsed.kind === "local"
    ? new LocalSiteReleaseStore(parsed.root)
    : new S3SiteReleaseStore(parsed, options);
}

export async function initializeSiteReleaseStore({ storeRoot, store = null, create = false }) {
  const location = parseSiteReleaseStoreLocation(storeRoot);
  const expectedIdentity = siteReleaseStoreIdentity(location);
  let selected = store;
  if (selected === null) {
    if (location.kind !== "local") {
      throw new Error("S3 site release operations require an explicitly injected store");
    }
    selected = new LocalSiteReleaseStore(location.root);
  } else if (!(selected instanceof SiteReleaseStore)) {
    throw new Error("injected site release store is invalid");
  }
  if (selected.identity?.sha256 !== expectedIdentity.sha256) {
    throw new Error("injected site release store does not match its canonical location identity");
  }
  return selected.initialize({ create });
}

export { parseSiteReleaseStoreLocation };

export function validateSiteReleaseManifest(manifest, bytes = canonicalBytes(manifest)) {
  if (
    !exactKeys(manifest, [
      "contract",
      "schemaVersion",
      "sourceSnapshotManifestSha256",
      "policyVersion",
      "builderCommit",
      "event",
      "releasedAt",
      "freshness",
      "contentIdentitySha256",
      "fileCount",
      "totalBytes",
      "files",
    ])
    || manifest.contract !== "verdify.lab-site-release"
    || manifest.schemaVersion !== 1
    || !SHA256_RE.test(manifest.sourceSnapshotManifestSha256)
    || !COMMIT_RE.test(manifest.builderCommit)
    || !SHA256_RE.test(manifest.contentIdentitySha256)
    || !Number.isSafeInteger(manifest.fileCount)
    || manifest.fileCount < 1
    || manifest.fileCount > MAX_TREE_FILES
    || !Number.isSafeInteger(manifest.totalBytes)
    || manifest.totalBytes < 1
    || manifest.totalBytes > MAX_TREE_BYTES
    || !Array.isArray(manifest.files)
    || manifest.files.length !== manifest.fileCount
    || canonicalBytes(manifest).compare(bytes) !== 0
  ) throw new Error("site release manifest does not use the canonical v1 contract");
  safeText(manifest.policyVersion, "site release policy version", 256);
  validateEvent(manifest.event);
  instant(manifest.releasedAt, "site release time");
  if (JSON.stringify(manifest.freshness) !== JSON.stringify(evaluateEventFreshness(manifest.event, manifest.releasedAt))) {
    throw new Error("site release freshness does not match its event");
  }
  const folded = new Set();
  let totalBytes = 0;
  let prior = "";
  for (const file of manifest.files) {
    if (
      !exactKeys(file, ["path", "sha256", "bytes", "mediaType"])
      || !SHA256_RE.test(file.sha256)
      || !Number.isSafeInteger(file.bytes)
      || file.bytes < 1
      || file.bytes > MAX_FILE_BYTES
      || file.mediaType !== mediaType(file.path)
    ) throw new Error("site release file record is invalid");
    safeRelativePath(file.path);
    if (prior && prior >= file.path) throw new Error("site release files are not strictly sorted");
    prior = file.path;
    const identity = file.path.normalize("NFC").toLocaleLowerCase("en-US");
    if (folded.has(identity)) throw new Error("site release has a case-folded collision");
    folded.add(identity);
    totalBytes += file.bytes;
  }
  if (totalBytes !== manifest.totalBytes || !manifest.files.some((file) => file.path === "index.html")) {
    throw new Error("site release file accounting is invalid");
  }
  const expectedIdentity = siteContentIdentitySha256(manifest);
  if (expectedIdentity !== manifest.contentIdentitySha256) throw new Error("site release content identity mismatch");
  if (manifest.event.payloadSha256 !== siteReleasePayloadSha256({
    sourceSnapshotManifestSha256: manifest.sourceSnapshotManifestSha256,
    policyVersion: manifest.policyVersion,
    builderCommit: manifest.builderCommit,
    contentIdentitySha256: manifest.contentIdentitySha256,
  })) throw new Error("site release event payload binding mismatch");
  return manifest;
}

async function acquireLocalLease(store) {
  const lockPath = path.join(store.root, ".site-publish.lock");
  const owner = {
    contract: "verdify.lab-local-site-publish-lease",
    schemaVersion: 1,
    hostname: hostname(),
    pid: process.pid,
    nonce: randomUUID(),
  };
  async function attempt(allowRecovery, attempts = 0) {
    if (attempts > 4) throw new Error("local site publisher lease recovery did not converge");
    const candidate = path.join(store.root, `.lease-candidate-${owner.nonce}`);
    try {
      await mkdir(candidate, { mode: 0o700 });
      await atomicCanonicalWrite(path.join(candidate, "owner.json"), owner);
      await syncDirectory(candidate);
      await rename(candidate, lockPath);
      await syncDirectory(store.root);
      const identity = await lstat(lockPath, { bigint: true });
      return { identity, lockPath, owner };
    } catch (error) {
      await rm(candidate, { recursive: true, force: true }).catch(() => {});
      if (!["EEXIST", "ENOTEMPTY"].includes(error.code)) throw error;
      if (!allowRecovery) throw new Error("another local site publisher is active");
      let existing;
      try {
        const lockMetadata = await lstat(lockPath, { bigint: true });
        if (!lockMetadata.isDirectory() || lockMetadata.isSymbolicLink()) throw new Error("invalid lease directory");
        const value = await readSingleLink(path.join(lockPath, "owner.json"), 16 * 1024, "local site publisher lease");
        existing = JSON.parse(value.bytes.toString("utf8"));
      } catch {
        throw new Error("local site publisher lease requires operator inspection");
      }
      if (
        !exactKeys(existing, ["contract", "schemaVersion", "hostname", "pid", "nonce"])
        || existing.contract !== owner.contract
        || existing.schemaVersion !== 1
        || existing.hostname !== owner.hostname
        || !UUID_RE.test(existing.nonce)
        || !Number.isSafeInteger(existing.pid)
        || existing.pid <= 0
      ) throw new Error("another local site publisher is active");
      try {
        process.kill(existing.pid, 0);
        throw new Error("another local site publisher is active");
      } catch (probe) {
        if (probe.code !== "ESRCH") throw probe;
      }
      const tombstone = path.join(store.root, ".lease-tombstones", existing.nonce);
      try {
        await rename(lockPath, tombstone);
        await syncDirectory(path.dirname(tombstone));
        await syncDirectory(store.root);
      } catch (recoveryError) {
        if (!["ENOENT", "EEXIST", "ENOTEMPTY"].includes(recoveryError.code)) throw recoveryError;
      }
      owner.nonce = randomUUID();
      return attempt(true, attempts + 1);
    }
  }
  return attempt(true);
}

async function reapStoreCandidates(store) {
  const locations = [
    [store.root, /^\.candidate-([0-9a-f-]+)$/, "selector"],
    [path.join(store.root, "releases", "sha256"), /^\.candidate-([0-9a-f-]+)$/, "release"],
    [path.join(store.root, "events", "sha256"), /^\.candidate-([0-9a-f-]+)$/, "event"],
    [path.join(store.root, ".quarantine"), /^([0-9a-f-]+)\.blob$/, "blob"],
  ];
  for (const [directory, pattern, kind] of locations) {
    for (const name of await readdir(directory)) {
      const match = pattern.exec(name);
      if (!match || !UUID_RE.test(match[1])) continue;
      const candidate = path.join(directory, name);
      const metadata = await lstat(candidate, { bigint: true });
      if (!metadata.isFile() || metadata.isSymbolicLink() || ![1n, 2n].includes(metadata.nlink)) {
        throw new Error("stale site release candidate requires operator inspection");
      }
      if (metadata.nlink === 2n) {
        const handle = await open(candidate, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
        const bytes = await handle.readFile();
        await handle.close();
        let destination = null;
        if (kind === "release") destination = store.releasePath(sha256(bytes));
        if (kind === "blob") destination = store.blobPath(sha256(bytes));
        if (kind === "event") {
          try {
            destination = store.eventPath(JSON.parse(bytes.toString("utf8")).eventId);
          } catch {
            throw new Error("paired stale event candidate is invalid");
          }
        }
        if (!destination) throw new Error("paired stale selector candidate is invalid");
        const published = await lstat(destination, { bigint: true });
        if (published.dev !== metadata.dev || published.ino !== metadata.ino) {
          throw new Error("stale site release candidate is not paired with its published object");
        }
      }
      await unlink(candidate);
    }
    await syncDirectory(directory);
  }
  for (const name of await readdir(store.root)) {
    const match = /^\.lease-candidate-([0-9a-f-]+)$/.exec(name);
    if (!match || !UUID_RE.test(match[1])) continue;
    await rm(path.join(store.root, name), { recursive: true, force: true });
  }
  await syncDirectory(store.root);
}

async function withLocalLease(store, callback) {
  const lease = await acquireLocalLease(store);
  try {
    await reapStoreCandidates(store);
    return await callback();
  } finally {
    try {
      const selected = await lstat(lease.lockPath, { bigint: true });
      if (selected.isDirectory() && selected.dev === lease.identity.dev && selected.ino === lease.identity.ino) {
        const current = await readSingleLink(path.join(lease.lockPath, "owner.json"), 16 * 1024, "local site publisher lease");
        const owner = JSON.parse(current.bytes.toString("utf8"));
        if (owner.nonce !== lease.owner.nonce) throw new Error("local site publisher lease ownership changed");
        await unlink(path.join(lease.lockPath, "owner.json"));
        await rmdir(lease.lockPath);
        await syncDirectory(store.root);
      }
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
}

async function pruneAndCollect(store, plannedSelection, priorSelection, { prospectiveIntent = null, byteLimit = MAX_STORE_BYTES } = {}) {
  if (!Number.isSafeInteger(byteLimit) || byteLimit < 1 || byteLimit > MAX_STORE_BYTES) throw new Error("site store byte limit is invalid");
  const digests = await store.listReleaseDigests();
  const records = [];
  for (const digest of digests) {
    const manifest = await store.readRelease(digest, { verifyBlobs: false });
    const metadata = await lstat(store.releasePath(digest), { bigint: true });
    records.push({ digest, manifest, bytes: Number(metadata.size) });
  }
  records.sort((left, right) =>
    right.manifest.releasedAt.localeCompare(left.manifest.releasedAt) || right.digest.localeCompare(left.digest));
  const mandatory = new Set([
    plannedSelection.current.releaseSha256,
    plannedSelection.previous?.releaseSha256,
    priorSelection?.current.releaseSha256,
    priorSelection?.previous?.releaseSha256,
  ].filter(Boolean));
  const blobNames = await readdir(path.join(store.root, "blobs", "sha256"));
  if (blobNames.length > MAX_TREE_FILES * (RETAIN_RELEASES + 1) || blobNames.some((name) => !SHA256_RE.test(name))) {
    throw new Error("site release blob membership is invalid");
  }
  const blobBytes = new Map();
  for (const digest of blobNames) {
    const file = store.blobPath(digest);
    const metadata = await lstat(file, { bigint: true });
    if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.nlink !== 1n) {
      throw new Error("site release blob is not a single-link regular file");
    }
    blobBytes.set(digest, Number(metadata.size));
  }
  const eventDirectory = path.join(store.root, "events", "sha256");
  const eventNames = await readdir(eventDirectory);
  if (eventNames.length > MAX_EVENT_RECORDS || eventNames.some((name) => !/^[0-9a-f]{64}\.json$/.test(name))) {
    throw new Error("site release event membership is invalid");
  }
  let eventBytes = 0;
  for (const name of eventNames) {
    const metadata = await lstat(path.join(eventDirectory, name), { bigint: true });
    if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.nlink !== 1n) {
      throw new Error("site release event is not a single-link regular file");
    }
    eventBytes += Number(metadata.size);
  }
  const tombstoneDirectory = path.join(store.root, ".lease-tombstones");
  for (const name of await readdir(tombstoneDirectory)) {
    if (!UUID_RE.test(name)) throw new Error("site lease tombstone membership is invalid");
    const owner = await lstat(path.join(tombstoneDirectory, name, "owner.json"), { bigint: true });
    if (!owner.isFile() || owner.isSymbolicLink() || owner.nlink !== 1n) throw new Error("site lease tombstone is invalid");
    eventBytes += Number(owner.size);
  }
  let prospectiveBytes = 0;
  if (prospectiveIntent && !(await store.readEventIntent(prospectiveIntent.eventId))) {
    prospectiveBytes = canonicalBytes(prospectiveIntent).length;
  }
  const byDigest = new Map(records.map((record) => [record.digest, record]));
  const transitionSelectorBytes = Math.max(
    canonicalBytes(plannedSelection).length,
    priorSelection ? canonicalBytes(priorSelection).length : 0,
  );
  function projected(keep, baseBytes = eventBytes + prospectiveBytes + transitionSelectorBytes) {
    const reachable = new Set();
    let bytes = baseBytes;
    for (const digest of keep) {
      const record = byDigest.get(digest);
      if (!record) throw new Error("selected site release manifest is missing");
      bytes += record.bytes;
      for (const file of record.manifest.files) reachable.add(file.sha256);
    }
    for (const digest of reachable) {
      if (!blobBytes.has(digest)) throw new Error("selected site release blob is missing");
      bytes += blobBytes.get(digest);
    }
    return { bytes, reachable };
  }
  const keep = new Set(mandatory);
  if (projected(keep).bytes <= byteLimit) {
    for (const record of records) {
      if (keep.size >= RETAIN_RELEASES) break;
      const candidate = new Set(keep).add(record.digest);
      if (projected(candidate).bytes <= byteLimit) keep.add(record.digest);
    }
  } else {
    keep.clear();
    for (const digest of [priorSelection?.current.releaseSha256, priorSelection?.previous?.releaseSha256].filter(Boolean)) keep.add(digest);
    const priorBaseBytes = eventBytes + (priorSelection ? canonicalBytes(priorSelection).length : 0);
    if (projected(keep, priorBaseBytes).bytes > byteLimit) throw new Error("existing selected site releases exceed the retained-byte cap");
  }
  const retainedProjection = keep.has(plannedSelection.current.releaseSha256)
    ? projected(keep)
    : projected(keep, eventBytes + (priorSelection ? canonicalBytes(priorSelection).length : 0));
  const { bytes: retainedBytes, reachable } = retainedProjection;
  for (const record of records) if (!keep.has(record.digest)) await unlink(store.releasePath(record.digest));
  for (const digest of blobNames) if (!reachable.has(digest)) await unlink(store.blobPath(digest));
  await syncDirectory(path.join(store.root, "releases", "sha256"));
  await syncDirectory(path.join(store.root, "blobs", "sha256"));
  if (retainedBytes > byteLimit) throw new Error("site release exceeds the retained-byte cap");
  if (!keep.has(plannedSelection.current.releaseSha256)) throw new Error("site release exceeds the retained-byte cap");
  return { retainedBytes, retainedReleases: keep.size };
}

async function hook(testHooks, name, context = {}) {
  if (typeof testHooks?.[name] === "function") await testHooks[name](context);
  if (testHooks?.failAt === name) throw new Error(`injected site release failure at ${name}`);
}

export async function publishSiteRelease({
  storeRoot,
  store = null,
  buildRoot,
  event,
  sourceSnapshotManifestSha256,
  policyVersion,
  builderCommit,
  releasedAt,
  expectedSelectionSha256 = null,
  testHooks = null,
}) {
  validateEvent(event);
  if (!SHA256_RE.test(sourceSnapshotManifestSha256)) throw new Error("site source snapshot digest is invalid");
  safeText(policyVersion, "site policy version", 256);
  if (!COMMIT_RE.test(builderCommit)) throw new Error("site builder commit is invalid");
  instant(releasedAt, "site release time");
  const freshness = evaluateEventFreshness(event, releasedAt);
  if (expectedSelectionSha256 !== null && !SHA256_RE.test(expectedSelectionSha256)) {
    throw new Error("site selection precondition is invalid");
  }
  const inventory = await inventoryBuiltSite(buildRoot);
  const files = manifestFileRecords(inventory);
  const contentIdentitySha256 = siteContentIdentitySha256({ sourceSnapshotManifestSha256, policyVersion, builderCommit, files });
  if (event.payloadSha256 !== siteReleasePayloadSha256({
    sourceSnapshotManifestSha256,
    policyVersion,
    builderCommit,
    contentIdentitySha256,
  })) throw new Error("site release event payload digest mismatch");

  const releaseStore = await initializeSiteReleaseStore({ storeRoot, store, create: true });
  return releaseStore.withPublication(async () => {
    await hook(testHooks, "afterLease", { store: releaseStore });
    const selected = await releaseStore.readSelection();
    const eventIntentRecord = await releaseStore.readEventIntent(event.eventId);
    if (eventIntentRecord) {
      if (
        eventIntentRecord.eventSha256 !== sha256(canonicalBytes(event))
        || eventIntentRecord.payloadSha256 !== event.payloadSha256
      ) throw new Error("site release event ID was reused with another payload or envelope");
      let manifest;
      try {
        manifest = await releaseStore.readRelease(eventIntentRecord.releaseSha256);
      } catch (error) {
        if (error.code === "ENOENT") {
          return { idempotent: true, retained: false, releaseSha256: eventIntentRecord.releaseSha256, selectionSha256: selected?.sha256 ?? null };
        }
        throw error;
      }
      if (selected?.document.current.releaseSha256 === eventIntentRecord.releaseSha256) {
        return { idempotent: true, retained: true, releaseSha256: eventIntentRecord.releaseSha256, selectionSha256: selected.sha256, manifest };
      }
      if ((selected?.sha256 ?? null) === eventIntentRecord.expectedSelectionSha256) {
        const pointer = { releaseSha256: eventIntentRecord.releaseSha256, eventId: event.eventId };
        const next = selectionRecord(
          pointer,
          selected?.document.current ?? null,
          (selected?.document.generation ?? 0) + 1,
          manifest.releasedAt,
          "publish",
        );
        await releaseStore.prepareSelectionTransition(next, selected?.document ?? null, {
          byteLimit: testHooks?.storeByteLimit ?? MAX_STORE_BYTES,
        });
        const selectionSha256 = await releaseStore.writeSelection(next, eventIntentRecord.expectedSelectionSha256);
        return { idempotent: true, retained: true, releaseSha256: eventIntentRecord.releaseSha256, selectionSha256, manifest };
      }
      return {
        idempotent: true,
        retained: true,
        ignoredStaleReplay: true,
        releaseSha256: eventIntentRecord.releaseSha256,
        selectionSha256: selected?.sha256 ?? null,
        manifest,
      };
    }

    if (selected) {
      const current = await releaseStore.readRelease(selected.document.current.releaseSha256);
      if (current.contentIdentitySha256 === contentIdentitySha256) {
        const intent = eventIntent(
          event,
          selected.document.current.releaseSha256,
          selected.sha256,
          releaseStore.identity.sha256,
        );
        await releaseStore.prepareSelectionTransition(selected.document, selected.document, {
          prospectiveIntent: intent,
          byteLimit: testHooks?.storeByteLimit ?? MAX_STORE_BYTES,
        });
        await releaseStore.publishEventIntent(intent);
        return {
          idempotent: false,
          unchanged: true,
          retained: true,
          releaseSha256: selected.document.current.releaseSha256,
          selectionSha256: selected.sha256,
          manifest: current,
        };
      }
      if (Date.parse(event.occurredAt) < Date.parse(current.event.occurredAt)) {
        throw new Error("site release event is older than the selected release");
      }
      if (expectedSelectionSha256 === null) throw new Error("site selection precondition is required");
    }
    if ((selected?.sha256 ?? null) !== expectedSelectionSha256) throw new Error("site selection precondition failed");

    for (const file of inventory.files) await releaseStore.publishBlob(file);
    await hook(testHooks, "afterBlobs", { store: releaseStore });
    const manifest = {
      contract: "verdify.lab-site-release",
      schemaVersion: 1,
      sourceSnapshotManifestSha256,
      policyVersion,
      builderCommit,
      event,
      releasedAt,
      freshness,
      contentIdentitySha256,
      fileCount: files.length,
      totalBytes: inventory.totalBytes,
      files,
    };
    validateSiteReleaseManifest(manifest, canonicalBytes(manifest));
    const releaseSha256 = await releaseStore.publishRelease(manifest);
    await hook(testHooks, "afterManifest", { store: releaseStore, releaseSha256 });
    const intent = eventIntent(event, releaseSha256, expectedSelectionSha256, releaseStore.identity.sha256);
    const pointer = { releaseSha256, eventId: event.eventId };
    const next = selectionRecord(
      pointer,
      selected?.document.current ?? null,
      (selected?.document.generation ?? 0) + 1,
      releasedAt,
      "publish",
    );
    await releaseStore.prepareSelectionTransition(next, selected?.document ?? null, {
      prospectiveIntent: intent,
      byteLimit: testHooks?.storeByteLimit ?? MAX_STORE_BYTES,
    });
    await releaseStore.publishEventIntent(intent);
    await hook(testHooks, "afterIntent", { store: releaseStore, releaseSha256 });
    await hook(testHooks, "beforeSelection", { store: releaseStore, releaseSha256 });
    const selectionSha256 = await releaseStore.writeSelection(next, expectedSelectionSha256);
    return { idempotent: false, retained: true, releaseSha256, selectionSha256, manifest };
  });
}

export async function siteReleaseStatus({ storeRoot, store = null, asOf = null }) {
  const releaseStore = await initializeSiteReleaseStore({ storeRoot, store });
  const selected = await releaseStore.readSelection();
  if (!selected) {
    return {
      contract: "verdify.lab-site-release-status",
      schemaVersion: 1,
      selectionSha256: null,
      generation: 0,
      ready: false,
      health: "unavailable",
      current: null,
      previous: null,
    };
  }
  const current = await releaseStore.readRelease(selected.document.current.releaseSha256);
  const previous = selected.document.previous
    ? await releaseStore.readRelease(selected.document.previous.releaseSha256)
    : null;
  const evaluatedAt = asOf ?? new Date().toISOString();
  instant(evaluatedAt, "site status evaluation time");
  const elapsedSeconds = Math.floor((Date.parse(evaluatedAt) - Date.parse(current.event.occurredAt)) / 1000);
  if (elapsedSeconds < 0) throw new Error("site status time precedes the selected event");
  const freshness = {
    completedAt: current.event.occurredAt,
    releasedAt: current.releasedAt,
    evaluatedAt,
    elapsedSeconds,
    targetSeconds: current.freshness.targetSeconds,
    alertAfterSeconds: current.freshness.alertAfterSeconds,
    status: elapsedSeconds >= current.freshness.alertAfterSeconds
      ? "alert"
      : elapsedSeconds > current.freshness.targetSeconds
        ? "late"
        : "fresh",
  };
  return {
    contract: "verdify.lab-site-release-status",
    schemaVersion: 1,
    selectionSha256: selected.sha256,
    generation: selected.document.generation,
    ready: true,
    health: freshness.status === "alert" ? "alert" : freshness.status === "late" ? "degraded" : "ready",
    current: {
      releaseSha256: selected.document.current.releaseSha256,
      eventId: current.event.eventId,
      sourceWatermark: current.event.sourceWatermark,
      sourceSnapshotManifestSha256: current.sourceSnapshotManifestSha256,
      policyVersion: current.policyVersion,
      fileCount: current.fileCount,
      totalBytes: current.totalBytes,
      freshness,
    },
    previous: previous
      ? {
          releaseSha256: selected.document.previous.releaseSha256,
          eventId: previous.event.eventId,
          fileCount: previous.fileCount,
          totalBytes: previous.totalBytes,
        }
      : null,
  };
}

export async function rollbackSiteRelease({ storeRoot, store = null, expectedSelectionSha256, rolledBackAt }) {
  if (!SHA256_RE.test(expectedSelectionSha256)) throw new Error("site rollback precondition is invalid");
  instant(rolledBackAt, "site rollback time");
  const releaseStore = await initializeSiteReleaseStore({ storeRoot, store });
  return releaseStore.withPublication(async () => {
    const selected = await releaseStore.readSelection();
    if (!selected || selected.sha256 !== expectedSelectionSha256) throw new Error("site rollback precondition failed");
    if (!selected.document.previous) throw new Error("site rollback has no previous release");
    await releaseStore.readRelease(selected.document.previous.releaseSha256);
    const next = selectionRecord(
      selected.document.previous,
      selected.document.current,
      selected.document.generation + 1,
      rolledBackAt,
      "rollback",
    );
    const selectionSha256 = await releaseStore.writeSelection(next, expectedSelectionSha256);
    return { selection: next, selectionSha256 };
  });
}

export const siteReleaseLimits = {
  maxTreeFiles: MAX_TREE_FILES,
  maxFileBytes: MAX_FILE_BYTES,
  maxTreeBytes: MAX_TREE_BYTES,
  maxStoreBytes: MAX_STORE_BYTES,
  retainReleases: RETAIN_RELEASES,
};
