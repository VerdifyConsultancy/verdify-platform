import { createHash, randomUUID } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import {
  lstat,
  link,
  mkdir,
  mkdtemp,
  open,
  realpath,
  rename,
  rm,
  unlink,
} from "node:fs/promises";
import path from "node:path";

import { decodePng, limits as pngLimits, validatePngFile } from "./png-validation.mjs";
import { S3ObjectStore, parseSiteReleaseStoreLocation } from "./s3-object-store.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const EVENT_ID_RE = /^evt_[A-Za-z0-9_-]{8,128}$/u;
const MEDIA_OCCURRENCE_ID_RE = /^media_[0-9a-f]{24}$/u;
const TYPE_NAMESPACE = "occurrence-releases/v1";
const MAX_SELECTION_BYTES = 64 * 1024;
const MAX_MANIFEST_BYTES = 8 * 1024 * 1024;
const MAX_GENERATION_BYTES = 128 * 1024;
const MAX_EVENT_BYTES = 32 * 1024;
const MAX_S3_KEY_BYTES = 1024;
const MAX_RELATIVE_OBJECT_KEY_BYTES = 160;
const MATERIALIZATION_STAGE_PREFIX = ".occurrence-stage-";

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function digest(value, label) {
  if (typeof value !== "string" || !SHA256_RE.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function eventId(value) {
  if (typeof value !== "string" || !EVENT_ID_RE.test(value)) {
    throw new Error("occurrence release event ID is invalid");
  }
  return value;
}

function mediaOccurrenceId(value) {
  if (typeof value !== "string" || !MEDIA_OCCURRENCE_ID_RE.test(value)) {
    throw new Error("current media occurrence identity is invalid");
  }
  return value;
}

function validateExpectedSelection(value) {
  if (value !== null) digest(value, "occurrence selection precondition");
  return value;
}

function parseCanonicalJson(bytes, label) {
  let document;
  try {
    document = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error(`${label} is not valid JSON`);
  }
  if (canonicalBytes(document).compare(bytes) !== 0) {
    throw new Error(`${label} is not canonical JSON`);
  }
  return document;
}

function storeIdentity(location) {
  const document = location.kind === "local"
    ? {
        contract: "verdify.lab-occurrence-release-store-identity",
        schemaVersion: 1,
        namespace: TYPE_NAMESPACE,
        backend: "local",
        root: location.root,
      }
    : {
        contract: "verdify.lab-occurrence-release-store-identity",
        schemaVersion: 1,
        namespace: TYPE_NAMESPACE,
        backend: "s3",
        bucket: location.bucket,
        prefix: location.prefix,
      };
  return Object.freeze({
    document: Object.freeze(document),
    sha256: sha256(canonicalBytes(document)),
  });
}

function validateS3OccurrenceLocation(value) {
  if (
    value === null
    || typeof value !== "object"
    || value.kind !== "s3"
    || typeof value.bucket !== "string"
    || typeof value.prefix !== "string"
    || !value.prefix.endsWith(`/${TYPE_NAMESPACE}`)
    || Buffer.byteLength(value.prefix) + 1 + MAX_RELATIVE_OBJECT_KEY_BYTES
      > MAX_S3_KEY_BYTES
  ) {
    throw new Error("S3 occurrence release store location is invalid");
  }
  return Object.freeze({
    kind: "s3",
    bucket: value.bucket,
    prefix: value.prefix,
  });
}

export function parseOccurrenceReleaseStoreLocation(value) {
  let parsed;
  try {
    parsed = parseSiteReleaseStoreLocation(value);
  } catch {
    throw new Error("occurrence release store location is invalid");
  }
  if (parsed.kind === "local") return parsed;
  return validateS3OccurrenceLocation({
    kind: "s3",
    bucket: parsed.bucket,
    prefix: `${parsed.prefix}/${TYPE_NAMESPACE}`,
  });
}

async function canonicalRoot(root) {
  const absolute = path.resolve(root);
  const metadata = await lstat(absolute, { bigint: true });
  if (
    !metadata.isDirectory()
    || metadata.isSymbolicLink()
    || (await realpath(absolute)) !== absolute
  ) {
    throw new Error("occurrence store root is invalid");
  }
  return absolute;
}

async function secureDirectory(root, relative, { create = false, leafMode = 0o755 } = {}) {
  let current = root;
  const segments = relative.split("/");
  for (let index = 0; index < segments.length; index += 1) {
    current = path.join(current, segments[index]);
    if (create) {
      try {
        await mkdir(current, {
          mode: index === segments.length - 1 ? leafMode : 0o755,
        });
      } catch (error) {
        if (error.code !== "EEXIST") throw error;
      }
    }
    const metadata = await lstat(current, { bigint: true });
    if (
      !metadata.isDirectory()
      || metadata.isSymbolicLink()
      || (await realpath(current)) !== current
    ) {
      throw new Error("occurrence store layout is invalid");
    }
  }
  return current;
}

async function syncDirectory(directory) {
  const handle = await open(directory, fsConstants.O_RDONLY);
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function readBoundedSingleLink(file, maximumBytes, label, { missing = false } = {}) {
  let handle;
  try {
    handle = await open(file, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  } catch (error) {
    if (missing && error.code === "ENOENT") return null;
    throw error;
  }
  try {
    const metadata = await handle.stat({ bigint: true });
    if (
      !metadata.isFile()
      || metadata.nlink !== 1n
      || metadata.size < 1n
      || metadata.size > BigInt(maximumBytes)
    ) {
      throw new Error(`${label} file is invalid`);
    }
    const bytes = await handle.readFile();
    const after = await handle.stat({ bigint: true });
    if (
      bytes.length !== Number(metadata.size)
      || after.dev !== metadata.dev
      || after.ino !== metadata.ino
      || after.size !== metadata.size
      || after.nlink !== 1n
    ) {
      throw new Error(`${label} changed while being read`);
    }
    return bytes;
  } finally {
    await handle.close();
  }
}

async function atomicWrite(destination, bytes) {
  const directory = path.dirname(destination);
  const candidate = path.join(directory, `.candidate-${randomUUID()}`);
  const handle = await open(candidate, "wx", 0o600);
  try {
    await handle.writeFile(bytes);
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await rename(candidate, destination);
    await syncDirectory(directory);
  } finally {
    await unlink(candidate).catch(() => {});
  }
}

async function withLocalSelectorLock(selectionFile, callback) {
  const lockFile = `${selectionFile}.cas.lock`;
  let handle;
  try {
    handle = await open(lockFile, "wx", 0o600);
  } catch (error) {
    if (error.code === "EEXIST") {
      throw new Error("another cooperating occurrence selector writer is active");
    }
    throw error;
  }
  const identity = await handle.stat({ bigint: true });
  try {
    return await callback();
  } finally {
    await handle.close().catch(() => {});
    try {
      const selected = await lstat(lockFile, { bigint: true });
      if (
        selected.isFile()
        && selected.dev === identity.dev
        && selected.ino === identity.ino
      ) {
        await unlink(lockFile);
      }
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
}

async function publishAbsent(destination, bytes, maximumBytes, collisionMessage) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 1 || bytes.length > maximumBytes) {
    throw new Error("immutable occurrence release object is outside its byte limit");
  }
  const directory = path.dirname(destination);
  const candidate = path.join(directory, `.candidate-${randomUUID()}`);
  const handle = await open(candidate, "wx", 0o600);
  try {
    await handle.writeFile(bytes);
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    try {
      await link(candidate, destination);
      await syncDirectory(directory);
      return { written: true, recovered: false };
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      const existing = await readBoundedSingleLink(
        destination,
        maximumBytes,
        "immutable occurrence release object",
      );
      if (!existing.equals(bytes)) throw new Error(collisionMessage);
      return { written: false, recovered: false };
    }
  } finally {
    await unlink(candidate).catch(() => {});
  }
}

function jsonValue(value, maximumBytes, label) {
  const bytes = canonicalBytes(value);
  if (bytes.length < 1 || bytes.length > maximumBytes) {
    throw new Error(`${label} exceeds its byte limit`);
  }
  return bytes;
}

function boundEventIntent(value, storeIdentitySha256, expectedEventId) {
  if (
    value === null
    || typeof value !== "object"
    || Array.isArray(value)
    || Object.getPrototypeOf(value) !== Object.prototype
    || value.storeIdentitySha256 !== storeIdentitySha256
    || value.eventId !== expectedEventId
  ) {
    throw new Error("occurrence event intent does not match its canonical store identity");
  }
  return value;
}

function boundCurrentMediaDocument(value, expectedOccurrenceId, label) {
  if (
    value === null
    || typeof value !== "object"
    || Array.isArray(value)
    || Object.getPrototypeOf(value) !== Object.prototype
    || value.occurrenceId !== expectedOccurrenceId
  ) {
    throw new Error(`${label} does not match its occurrence identity`);
  }
  return value;
}

function blobValue(bytes, expectedSha256) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 1 || bytes.length > pngLimits.maxFileBytes) {
    throw new Error("occurrence PNG blob is outside its byte limit");
  }
  digest(expectedSha256, "occurrence PNG blob digest");
  if (sha256(bytes) !== expectedSha256) {
    throw new Error("occurrence PNG blob digest mismatch");
  }
  const decoded = decodePng(bytes);
  return {
    body: bytes,
    bytes: bytes.length,
    sha256: expectedSha256,
    decodedSha256: decoded.decodedSha256,
    decodedBytes: decoded.decodedBytes,
    width: decoded.width,
    height: decoded.height,
    mediaType: decoded.mediaType,
  };
}

function validateMaterializationFileOperations(fileOperations) {
  if (
    fileOperations === null
    || typeof fileOperations !== "object"
    || Array.isArray(fileOperations)
  ) {
    throw new Error("occurrence materialization file operations are invalid");
  }
  return fileOperations;
}

async function canonicalMaterializationDirectory(directory) {
  const absolute = path.resolve(directory);
  const metadata = await lstat(absolute, { bigint: true });
  if (
    !metadata.isDirectory()
    || metadata.isSymbolicLink()
    || (await realpath(absolute)) !== absolute
  ) {
    throw new Error("occurrence materialization directory is invalid");
  }
  return absolute;
}

function validateMaterializationStage(stage) {
  if (
    stage === null
    || typeof stage !== "object"
    || typeof stage.directory !== "string"
    || typeof stage.parent !== "string"
    || path.dirname(stage.directory) !== stage.parent
    || !path.basename(stage.directory).startsWith(MATERIALIZATION_STAGE_PREFIX)
    || path.basename(stage.directory).length > MATERIALIZATION_STAGE_PREFIX.length + 64
  ) {
    throw new Error("occurrence materialization stage is invalid");
  }
  return stage;
}

async function createMaterializationStage(directory, { fileOperations = {} } = {}) {
  validateMaterializationFileOperations(fileOperations);
  const parent = await canonicalMaterializationDirectory(directory);
  const createTemporaryDirectory = fileOperations.mkdtemp ?? mkdtemp;
  const stagedDirectory = path.resolve(
    await createTemporaryDirectory(path.join(parent, MATERIALIZATION_STAGE_PREFIX)),
  );
  const stage = Object.freeze({ directory: stagedDirectory, parent });
  validateMaterializationStage(stage);
  const metadata = await lstat(stagedDirectory, { bigint: true });
  if (
    !metadata.isDirectory()
    || metadata.isSymbolicLink()
    || (await realpath(stagedDirectory)) !== stagedDirectory
  ) {
    throw new Error("occurrence materialization stage is invalid");
  }
  return stage;
}

async function discardMaterializationStage(stage, { fileOperations = {} } = {}) {
  validateMaterializationFileOperations(fileOperations);
  validateMaterializationStage(stage);
  const removeDirectory = fileOperations.rm ?? rm;
  await removeDirectory(stage.directory, { recursive: true, force: true });
}

function sameMaterializedPng(left, right) {
  return left.sha256 === right.sha256
    && left.bytes === right.bytes
    && left.decodedSha256 === right.decodedSha256
    && left.decodedBytes === right.decodedBytes
    && left.width === right.width
    && left.height === right.height
    && left.mediaType === right.mediaType;
}

async function stageMaterializationBytes(
  bytes,
  expectedSha256,
  stage,
  { fileOperations = {} } = {},
) {
  validateMaterializationFileOperations(fileOperations);
  validateMaterializationStage(stage);
  const expected = blobValue(bytes, expectedSha256);
  const openFile = fileOperations.open ?? open;
  const validateFile = fileOperations.validatePngFile ?? validatePngFile;
  const relative = `${expectedSha256}.png`;
  const stagedPath = path.join(stage.directory, relative);
  let handle = null;
  try {
    handle = await openFile(stagedPath, "wx", 0o644);
    const identity = await handle.stat({ bigint: true });
    if (!identity.isFile() || identity.nlink !== 1n || identity.size !== 0n) {
      throw new Error("occurrence materialization staged file is invalid");
    }
    await handle.writeFile(bytes);
    await handle.sync();
    const completed = await handle.stat({ bigint: true });
    if (
      !completed.isFile()
      || completed.dev !== identity.dev
      || completed.ino !== identity.ino
      || completed.nlink !== 1n
      || completed.size !== BigInt(bytes.length)
    ) {
      throw new Error("occurrence materialization staged file changed while being written");
    }
    await handle.close();
    handle = null;
    const verified = await validateFile(stage.directory, relative);
    if (!sameMaterializedPng(verified, expected)) {
      throw new Error("staged occurrence materialization does not match its blob");
    }
    return Object.freeze({
      ...verified,
      sourcePath: stagedPath,
      staging: Object.freeze({
        stage,
        stagedPath,
        dev: completed.dev,
        ino: completed.ino,
      }),
    });
  } finally {
    await handle?.close().catch(() => {});
  }
}

function validateStagedMaterialization(value) {
  if (
    value === null
    || typeof value !== "object"
    || value.staging === null
    || typeof value.staging !== "object"
    || typeof value.staging.stagedPath !== "string"
    || typeof value.staging.dev !== "bigint"
    || typeof value.staging.ino !== "bigint"
  ) {
    throw new Error("staged occurrence materialization is invalid");
  }
  const stage = validateMaterializationStage(value.staging.stage);
  if (
    path.dirname(value.staging.stagedPath) !== stage.directory
    || path.basename(value.staging.stagedPath) !== `${value.sha256}.png`
  ) {
    throw new Error("staged occurrence materialization is invalid");
  }
  return value;
}

async function exactExistingMaterialization(destination, staged, validateFile) {
  let verified;
  try {
    verified = await validateFile(path.dirname(destination), path.basename(destination));
  } catch {
    return null;
  }
  if (!sameMaterializedPng(verified, staged)) return null;
  return {
    ...verified,
    sourcePath: destination,
    created: false,
  };
}

async function commitStagedMaterialization(
  stagedValue,
  destination,
  { fileOperations = {} } = {},
) {
  validateMaterializationFileOperations(fileOperations);
  const staged = validateStagedMaterialization(stagedValue);
  const absoluteDestination = path.resolve(destination);
  if (path.dirname(absoluteDestination) !== staged.staging.stage.parent) {
    throw new Error("occurrence materialization destination is outside its stage");
  }
  const lstatFile = fileOperations.lstat ?? lstat;
  const linkFile = fileOperations.link ?? link;
  const validateFile = fileOperations.validatePngFile ?? validatePngFile;
  const syncTargetDirectory = fileOperations.syncDirectory ?? syncDirectory;
  const metadata = await lstatFile(staged.staging.stagedPath, { bigint: true });
  if (
    !metadata.isFile()
    || metadata.isSymbolicLink()
    || metadata.dev !== staged.staging.dev
    || metadata.ino !== staged.staging.ino
    || metadata.nlink !== 1n
    || metadata.size !== BigInt(staged.bytes)
  ) {
    throw new Error("staged occurrence materialization changed before commit");
  }
  try {
    await linkFile(staged.staging.stagedPath, absoluteDestination);
  } catch (error) {
    const existing = await exactExistingMaterialization(
      absoluteDestination,
      staged,
      validateFile,
    );
    if (existing !== null) return existing;
    if (error.code === "EEXIST") {
      throw new Error("existing occurrence materialization conflicts with staged blob");
    }
    throw error;
  }
  await syncTargetDirectory(path.dirname(absoluteDestination));
  const { staging: _staging, ...verified } = staged;
  return {
    ...verified,
    sourcePath: absoluteDestination,
    created: true,
  };
}

export class OccurrenceReleaseStore {
  constructor(location) {
    this.location = location;
    this.identity = storeIdentity(location);
  }

  async initialize(_options = {}) {
    throw new Error("occurrence release store initialize is not implemented");
  }

  async readAggregateSelection() {
    throw new Error("aggregate occurrence selection read is not implemented");
  }

  async writeAggregateSelection(_selection, _expectedSelectionSha256) {
    throw new Error("aggregate occurrence selection write is not implemented");
  }

  async publishAggregateManifest(_manifest) {
    throw new Error("aggregate occurrence manifest publication is not implemented");
  }

  async readAggregateManifest(_digest) {
    throw new Error("aggregate occurrence manifest read is not implemented");
  }

  async publishAggregateEventIntent(_eventId, _intent) {
    throw new Error("aggregate occurrence event publication is not implemented");
  }

  async readAggregateEventIntent(_eventId) {
    throw new Error("aggregate occurrence event read is not implemented");
  }

  async readCurrentMediaSelection(_occurrenceId) {
    throw new Error("current media selection read is not implemented");
  }

  async writeCurrentMediaSelection(_occurrenceId, _selection, _expectedSelectionSha256) {
    throw new Error("current media selection write is not implemented");
  }

  async publishCurrentMediaGeneration(_occurrenceId, _generation) {
    throw new Error("current media generation publication is not implemented");
  }

  async readCurrentMediaGeneration(_occurrenceId, _digest) {
    throw new Error("current media generation read is not implemented");
  }

  async publishCurrentMediaEventIntent(_occurrenceId, _eventId, _intent) {
    throw new Error("current media event publication is not implemented");
  }

  async readCurrentMediaEventIntent(_occurrenceId, _eventId) {
    throw new Error("current media event read is not implemented");
  }

  async publishPngBlob(_bytes, _expectedSha256) {
    throw new Error("occurrence PNG blob publication is not implemented");
  }

  async readPngBlob(_digest, _options = {}) {
    throw new Error("occurrence PNG blob read is not implemented");
  }

  async createMaterializationStage(directory, options = {}) {
    return createMaterializationStage(directory, options);
  }

  async discardMaterializationStage(stage, options = {}) {
    return discardMaterializationStage(stage, options);
  }

  async stagePngBlob(digestValue, stage, options = {}) {
    const { fileOperations, ...readOptions } = options;
    const value = await this.readPngBlob(digestValue, readOptions);
    return stageMaterializationBytes(value.body, digestValue, stage, {
      fileOperations,
    });
  }

  async commitStagedPngBlob(staged, destination, options = {}) {
    return commitStagedMaterialization(staged, destination, options);
  }

  async materializePngBlob(digestValue, destination, options = {}) {
    const { fileOperations, ...readOptions } = options;
    const stage = await this.createMaterializationStage(path.dirname(destination), {
      fileOperations,
    });
    let operationError = null;
    try {
      const staged = await this.stagePngBlob(digestValue, stage, {
        ...readOptions,
        fileOperations,
      });
      return await this.commitStagedPngBlob(staged, destination, { fileOperations });
    } catch (error) {
      operationError = error;
      throw error;
    } finally {
      try {
        await this.discardMaterializationStage(stage, { fileOperations });
      } catch (cleanupError) {
        if (operationError !== null) {
          throw new AggregateError(
            [operationError, cleanupError],
            "occurrence materialization failed and private staging cleanup did not complete",
          );
        }
        throw cleanupError;
      }
    }
  }
}

export class LocalOccurrenceReleaseStore extends OccurrenceReleaseStore {
  constructor(root) {
    const location = Object.freeze({ kind: "local", root: path.resolve(root) });
    super(location);
    this.root = location.root;
  }

  async initialize({ create = false } = {}) {
    this.root = await canonicalRoot(this.root);
    this.location = Object.freeze({ kind: "local", root: this.root });
    this.identity = storeIdentity(this.location);
    for (const relative of ["blobs/sha256", "manifests/sha256", "events/sha256"]) {
      await secureDirectory(this.root, relative, { create });
    }
    await secureDirectory(this.root, ".quarantine", { create, leafMode: 0o700 });
    return this;
  }

  async mediaDirectory(occurrenceIdValue, { create = false } = {}) {
    mediaOccurrenceId(occurrenceIdValue);
    const relative = `occurrences/${occurrenceIdValue}`;
    await secureDirectory(this.root, `${relative}/generations/sha256`, { create });
    await secureDirectory(this.root, `${relative}/events/sha256`, { create });
    return path.join(this.root, ...relative.split("/"));
  }

  async readJson(file, maximumBytes, label, { missing = false } = {}) {
    const bytes = await readBoundedSingleLink(file, maximumBytes, label, { missing });
    if (bytes === null) return null;
    return {
      document: parseCanonicalJson(bytes, label),
      bytes,
      sha256: sha256(bytes),
      etag: null,
      storeIdentitySha256: this.identity.sha256,
    };
  }

  async writeSelection(file, selection, expectedSelectionSha256, reader) {
    validateExpectedSelection(expectedSelectionSha256);
    const bytes = jsonValue(selection, MAX_SELECTION_BYTES, "occurrence selection");
    return withLocalSelectorLock(file, async () => {
      const current = await reader();
      if ((current?.sha256 ?? null) !== expectedSelectionSha256) {
        throw new Error("occurrence selection precondition failed");
      }
      await atomicWrite(file, bytes);
      const committed = await reader();
      if (committed === null || !committed.bytes.equals(bytes)) {
        throw new Error("occurrence selection changed after conditional write");
      }
      return sha256(bytes);
    });
  }

  async publishJson(file, document, maximumBytes, collisionMessage) {
    const bytes = jsonValue(document, maximumBytes, "immutable occurrence release JSON");
    await publishAbsent(file, bytes, maximumBytes, collisionMessage);
    return sha256(bytes);
  }

  async readAggregateSelection() {
    return this.readJson(
      path.join(this.root, "selection.json"),
      MAX_SELECTION_BYTES,
      "occurrence selection",
      { missing: true },
    );
  }

  async writeAggregateSelection(selection, expectedSelectionSha256) {
    return this.writeSelection(
      path.join(this.root, "selection.json"),
      selection,
      expectedSelectionSha256,
      () => this.readAggregateSelection(),
    );
  }

  async publishAggregateManifest(manifest) {
    const bytes = jsonValue(manifest, MAX_MANIFEST_BYTES, "occurrence manifest");
    const valueSha256 = sha256(bytes);
    await publishAbsent(
      path.join(this.root, "manifests", "sha256", `${valueSha256}.json`),
      bytes,
      MAX_MANIFEST_BYTES,
      "content-addressed occurrence manifest collision",
    );
    return valueSha256;
  }

  async readAggregateManifest(digestValue) {
    digest(digestValue, "occurrence manifest digest");
    const value = await this.readJson(
      path.join(this.root, "manifests", "sha256", `${digestValue}.json`),
      MAX_MANIFEST_BYTES,
      "occurrence manifest",
    );
    if (value.sha256 !== digestValue) throw new Error("occurrence manifest digest mismatch");
    return value;
  }

  aggregateEventPath(eventIdValue) {
    eventId(eventIdValue);
    return path.join(this.root, "events", "sha256", `${sha256(Buffer.from(eventIdValue))}.json`);
  }

  async publishAggregateEventIntent(eventIdValue, intent) {
    boundEventIntent(intent, this.identity.sha256, eventIdValue);
    return this.publishJson(
      this.aggregateEventPath(eventIdValue),
      intent,
      MAX_EVENT_BYTES,
      "content-addressed occurrence event collision",
    );
  }

  async readAggregateEventIntent(eventIdValue) {
    const value = await this.readJson(
      this.aggregateEventPath(eventIdValue),
      MAX_EVENT_BYTES,
      "occurrence event intent",
      { missing: true },
    );
    if (value !== null) boundEventIntent(value.document, this.identity.sha256, eventIdValue);
    return value;
  }

  async readCurrentMediaSelection(occurrenceIdValue) {
    let directory;
    try {
      directory = await this.mediaDirectory(occurrenceIdValue);
    } catch (error) {
      if (error.code === "ENOENT") return null;
      throw error;
    }
    const value = await this.readJson(
      path.join(directory, "selection.json"),
      MAX_SELECTION_BYTES,
      "current media selection",
      { missing: true },
    );
    if (value !== null) {
      boundCurrentMediaDocument(value.document, occurrenceIdValue, "current media selection");
    }
    return value;
  }

  async writeCurrentMediaSelection(occurrenceIdValue, selection, expectedSelectionSha256) {
    boundCurrentMediaDocument(selection, occurrenceIdValue, "current media selection");
    const directory = await this.mediaDirectory(occurrenceIdValue, { create: true });
    return this.writeSelection(
      path.join(directory, "selection.json"),
      selection,
      expectedSelectionSha256,
      () => this.readCurrentMediaSelection(occurrenceIdValue),
    );
  }

  async publishCurrentMediaGeneration(occurrenceIdValue, generation) {
    boundCurrentMediaDocument(generation, occurrenceIdValue, "current media generation");
    const directory = await this.mediaDirectory(occurrenceIdValue, { create: true });
    const bytes = jsonValue(generation, MAX_GENERATION_BYTES, "current media generation");
    const valueSha256 = sha256(bytes);
    await publishAbsent(
      path.join(directory, "generations", "sha256", `${valueSha256}.json`),
      bytes,
      MAX_GENERATION_BYTES,
      "content-addressed current media generation collision",
    );
    return valueSha256;
  }

  async readCurrentMediaGeneration(occurrenceIdValue, digestValue) {
    digest(digestValue, "current media generation digest");
    const directory = await this.mediaDirectory(occurrenceIdValue);
    const value = await this.readJson(
      path.join(directory, "generations", "sha256", `${digestValue}.json`),
      MAX_GENERATION_BYTES,
      "current media generation",
    );
    if (value.sha256 !== digestValue) throw new Error("current media generation digest mismatch");
    boundCurrentMediaDocument(value.document, occurrenceIdValue, "current media generation");
    return value;
  }

  async publishCurrentMediaEventIntent(occurrenceIdValue, eventIdValue, intent) {
    const directory = await this.mediaDirectory(occurrenceIdValue, { create: true });
    eventId(eventIdValue);
    boundEventIntent(intent, this.identity.sha256, eventIdValue);
    return this.publishJson(
      path.join(directory, "events", "sha256", `${sha256(Buffer.from(eventIdValue))}.json`),
      intent,
      MAX_EVENT_BYTES,
      "content-addressed current media event collision",
    );
  }

  async readCurrentMediaEventIntent(occurrenceIdValue, eventIdValue) {
    let directory;
    try {
      directory = await this.mediaDirectory(occurrenceIdValue);
    } catch (error) {
      if (error.code === "ENOENT") return null;
      throw error;
    }
    eventId(eventIdValue);
    const value = await this.readJson(
      path.join(directory, "events", "sha256", `${sha256(Buffer.from(eventIdValue))}.json`),
      MAX_EVENT_BYTES,
      "current media event intent",
      { missing: true },
    );
    if (value !== null) boundEventIntent(value.document, this.identity.sha256, eventIdValue);
    return value;
  }

  async publishPngBlob(bytes, expectedSha256) {
    const value = blobValue(bytes, expectedSha256);
    await publishAbsent(
      path.join(this.root, "blobs", "sha256", `${expectedSha256}.png`),
      bytes,
      pngLimits.maxFileBytes,
      "content-addressed occurrence PNG collision",
    );
    return value;
  }

  async readPngBlob(digestValue, { maximumBytes = pngLimits.maxFileBytes } = {}) {
    digest(digestValue, "occurrence PNG blob digest");
    if (
      !Number.isSafeInteger(maximumBytes)
      || maximumBytes < 1
      || maximumBytes > pngLimits.maxFileBytes
    ) {
      throw new Error("occurrence PNG blob read limit is invalid");
    }
    const bytes = await readBoundedSingleLink(
      path.join(this.root, "blobs", "sha256", `${digestValue}.png`),
      maximumBytes,
      "occurrence PNG blob",
    );
    return blobValue(bytes, digestValue);
  }
}

export class S3OccurrenceReleaseStore extends OccurrenceReleaseStore {
  constructor(location, options = {}) {
    const parsed = typeof location === "string"
      ? parseOccurrenceReleaseStoreLocation(location)
      : location;
    const normalized = validateS3OccurrenceLocation(parsed);
    super(normalized);
    this.objects = new S3ObjectStore({
      bucket: normalized.bucket,
      prefix: normalized.prefix,
      client: options.client ?? null,
      clientConfig: options.clientConfig ?? {},
      clientFactory: options.clientFactory,
    });
  }

  async initialize(_options = {}) {
    await this.objects.initialize();
    return this;
  }

  async publishImmutable(key, bytes, maximumBytes, collisionMessage, contentType) {
    if (!Buffer.isBuffer(bytes) || bytes.length < 1 || bytes.length > maximumBytes) {
      throw new Error("immutable occurrence release object is outside its byte limit");
    }
    let published;
    try {
      published = await this.objects.putIfAbsent(key, bytes, { contentType });
    } catch (error) {
      let existing;
      try {
        existing = await this.objects.read(key, {
          maximumBytes,
          label: "immutable occurrence release object",
          missing: true,
        });
      } catch {
        throw error;
      }
      if (existing === null) throw error;
      if (!existing.bytes.equals(bytes)) throw new Error(collisionMessage);
      return { written: false, recovered: true };
    }
    if (published.written) return { written: true, recovered: false };
    const existing = await this.objects.read(key, {
      maximumBytes,
      label: "immutable occurrence release object",
    });
    if (!existing.bytes.equals(bytes)) throw new Error(collisionMessage);
    return { written: false, recovered: false };
  }

  async readJson(key, maximumBytes, label, { missing = false } = {}) {
    const value = await this.objects.read(key, { maximumBytes, label, missing });
    if (value === null) return null;
    return {
      document: parseCanonicalJson(value.bytes, label),
      bytes: value.bytes,
      sha256: sha256(value.bytes),
      etag: value.etag,
      storeIdentitySha256: this.identity.sha256,
    };
  }

  async writeSelection(key, selection, expectedSelectionSha256, reader) {
    validateExpectedSelection(expectedSelectionSha256);
    const bytes = jsonValue(selection, MAX_SELECTION_BYTES, "occurrence selection");
    const selected = await reader();
    if ((selected?.sha256 ?? null) !== expectedSelectionSha256) {
      throw new Error("occurrence selection precondition failed");
    }
    let result;
    try {
      result = selected === null
        ? await this.objects.putIfAbsent(key, bytes, { contentType: "application/json" })
        : await this.objects.putIfMatch(key, bytes, selected.etag, { contentType: "application/json" });
    } catch (error) {
      const recovered = await reader().catch(() => null);
      if (recovered?.bytes.equals(bytes)) return sha256(bytes);
      throw error;
    }
    if (!result.written) throw new Error("occurrence selection precondition failed");
    const committed = await reader();
    if (committed === null || !committed.bytes.equals(bytes)) {
      throw new Error("occurrence selection changed after conditional write");
    }
    return sha256(bytes);
  }

  async publishJson(key, document, maximumBytes, collisionMessage) {
    const bytes = jsonValue(document, maximumBytes, "immutable occurrence release JSON");
    await this.publishImmutable(key, bytes, maximumBytes, collisionMessage, "application/json");
    return sha256(bytes);
  }

  async readAggregateSelection() {
    return this.readJson("selection.json", MAX_SELECTION_BYTES, "occurrence selection", { missing: true });
  }

  async writeAggregateSelection(selection, expectedSelectionSha256) {
    return this.writeSelection(
      "selection.json",
      selection,
      expectedSelectionSha256,
      () => this.readAggregateSelection(),
    );
  }

  async publishAggregateManifest(manifest) {
    const bytes = jsonValue(manifest, MAX_MANIFEST_BYTES, "occurrence manifest");
    const valueSha256 = sha256(bytes);
    await this.publishImmutable(
      `manifests/sha256/${valueSha256}.json`,
      bytes,
      MAX_MANIFEST_BYTES,
      "content-addressed occurrence manifest collision",
      "application/json",
    );
    return valueSha256;
  }

  async readAggregateManifest(digestValue) {
    digest(digestValue, "occurrence manifest digest");
    const value = await this.readJson(
      `manifests/sha256/${digestValue}.json`,
      MAX_MANIFEST_BYTES,
      "occurrence manifest",
    );
    if (value.sha256 !== digestValue) throw new Error("occurrence manifest digest mismatch");
    return value;
  }

  aggregateEventKey(eventIdValue) {
    eventId(eventIdValue);
    return `events/sha256/${sha256(Buffer.from(eventIdValue))}.json`;
  }

  async publishAggregateEventIntent(eventIdValue, intent) {
    boundEventIntent(intent, this.identity.sha256, eventIdValue);
    return this.publishJson(
      this.aggregateEventKey(eventIdValue),
      intent,
      MAX_EVENT_BYTES,
      "content-addressed occurrence event collision",
    );
  }

  async readAggregateEventIntent(eventIdValue) {
    const value = await this.readJson(
      this.aggregateEventKey(eventIdValue),
      MAX_EVENT_BYTES,
      "occurrence event intent",
      { missing: true },
    );
    if (value !== null) boundEventIntent(value.document, this.identity.sha256, eventIdValue);
    return value;
  }

  mediaPrefix(occurrenceIdValue) {
    mediaOccurrenceId(occurrenceIdValue);
    return `occurrences/${occurrenceIdValue}`;
  }

  async readCurrentMediaSelection(occurrenceIdValue) {
    const value = await this.readJson(
      `${this.mediaPrefix(occurrenceIdValue)}/selection.json`,
      MAX_SELECTION_BYTES,
      "current media selection",
      { missing: true },
    );
    if (value !== null) {
      boundCurrentMediaDocument(value.document, occurrenceIdValue, "current media selection");
    }
    return value;
  }

  async writeCurrentMediaSelection(occurrenceIdValue, selection, expectedSelectionSha256) {
    boundCurrentMediaDocument(selection, occurrenceIdValue, "current media selection");
    const key = `${this.mediaPrefix(occurrenceIdValue)}/selection.json`;
    return this.writeSelection(
      key,
      selection,
      expectedSelectionSha256,
      () => this.readCurrentMediaSelection(occurrenceIdValue),
    );
  }

  async publishCurrentMediaGeneration(occurrenceIdValue, generation) {
    boundCurrentMediaDocument(generation, occurrenceIdValue, "current media generation");
    const bytes = jsonValue(generation, MAX_GENERATION_BYTES, "current media generation");
    const valueSha256 = sha256(bytes);
    await this.publishImmutable(
      `${this.mediaPrefix(occurrenceIdValue)}/generations/sha256/${valueSha256}.json`,
      bytes,
      MAX_GENERATION_BYTES,
      "content-addressed current media generation collision",
      "application/json",
    );
    return valueSha256;
  }

  async readCurrentMediaGeneration(occurrenceIdValue, digestValue) {
    digest(digestValue, "current media generation digest");
    const value = await this.readJson(
      `${this.mediaPrefix(occurrenceIdValue)}/generations/sha256/${digestValue}.json`,
      MAX_GENERATION_BYTES,
      "current media generation",
    );
    if (value.sha256 !== digestValue) throw new Error("current media generation digest mismatch");
    boundCurrentMediaDocument(value.document, occurrenceIdValue, "current media generation");
    return value;
  }

  mediaEventKey(occurrenceIdValue, eventIdValue) {
    eventId(eventIdValue);
    return `${this.mediaPrefix(occurrenceIdValue)}/events/sha256/${sha256(Buffer.from(eventIdValue))}.json`;
  }

  async publishCurrentMediaEventIntent(occurrenceIdValue, eventIdValue, intent) {
    boundEventIntent(intent, this.identity.sha256, eventIdValue);
    return this.publishJson(
      this.mediaEventKey(occurrenceIdValue, eventIdValue),
      intent,
      MAX_EVENT_BYTES,
      "content-addressed current media event collision",
    );
  }

  async readCurrentMediaEventIntent(occurrenceIdValue, eventIdValue) {
    const value = await this.readJson(
      this.mediaEventKey(occurrenceIdValue, eventIdValue),
      MAX_EVENT_BYTES,
      "current media event intent",
      { missing: true },
    );
    if (value !== null) boundEventIntent(value.document, this.identity.sha256, eventIdValue);
    return value;
  }

  async publishPngBlob(bytes, expectedSha256) {
    const value = blobValue(bytes, expectedSha256);
    await this.publishImmutable(
      `blobs/sha256/${expectedSha256}.png`,
      bytes,
      pngLimits.maxFileBytes,
      "content-addressed occurrence PNG collision",
      "image/png",
    );
    return value;
  }

  async readPngBlob(digestValue, { maximumBytes = pngLimits.maxFileBytes } = {}) {
    digest(digestValue, "occurrence PNG blob digest");
    if (
      !Number.isSafeInteger(maximumBytes)
      || maximumBytes < 1
      || maximumBytes > pngLimits.maxFileBytes
    ) {
      throw new Error("occurrence PNG blob read limit is invalid");
    }
    const value = await this.objects.read(`blobs/sha256/${digestValue}.png`, {
      maximumBytes,
      label: "occurrence PNG blob",
    });
    return blobValue(value.bytes, digestValue);
  }
}

export function createOccurrenceReleaseStore(location, options = {}) {
  const parsed = parseOccurrenceReleaseStoreLocation(location);
  return parsed.kind === "local"
    ? new LocalOccurrenceReleaseStore(parsed.root)
    : new S3OccurrenceReleaseStore(parsed, options);
}

export const occurrenceReleaseStoreContract = Object.freeze({
  namespace: TYPE_NAMESPACE,
  maximumSelectionBytes: MAX_SELECTION_BYTES,
  maximumManifestBytes: MAX_MANIFEST_BYTES,
  maximumGenerationBytes: MAX_GENERATION_BYTES,
  maximumEventBytes: MAX_EVENT_BYTES,
  maximumPngBytes: pngLimits.maxFileBytes,
});
