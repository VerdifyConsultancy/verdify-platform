import { createHash, randomUUID } from "node:crypto";
import { constants as fsConstants, link, lstat, mkdir, open, realpath, unlink } from "node:fs/promises";
import path from "node:path";

import { validatePngFile } from "./png-validation.mjs";

const OCCURRENCE_ID_BY_COLLECTION = new Map([
  ["graphs", /^graph_[0-9a-f]{24}$/],
  ["current-media", /^media_[0-9a-f]{24}$/],
]);
const FILE_OPERATION_NAMES = new Set(["writeFile", "sync", "link", "unlink"]);
const DEFAULT_FILE_OPERATIONS = Object.freeze({
  writeFile: (handle, bytes) => handle.writeFile(bytes),
  sync: (handle) => handle.sync(),
  link: (source, target) => link(source, target),
  unlink: (target) => unlink(target),
});

function safeLabel(value) {
  if (typeof value !== "string" || !/^[a-z][a-z -]{0,63}$/.test(value)) {
    throw new Error("occurrence candidate label is invalid");
  }
  return value;
}

export function occurrenceCandidateFileOperations(overrides, label = "occurrence candidate") {
  safeLabel(label);
  if (overrides === undefined) return DEFAULT_FILE_OPERATIONS;
  if (
    overrides === null
    || typeof overrides !== "object"
    || Array.isArray(overrides)
    || Object.getPrototypeOf(overrides) !== Object.prototype
    || Object.keys(overrides).some((name) => !FILE_OPERATION_NAMES.has(name))
    || Object.values(overrides).some((operation) => typeof operation !== "function")
  ) throw new Error(`${label} file operations are invalid`);
  return { ...DEFAULT_FILE_OPERATIONS, ...overrides };
}

export async function canonicalCandidateDirectory(directory, label) {
  safeLabel(label);
  let metadata;
  let resolved;
  try {
    metadata = await lstat(directory);
    resolved = await realpath(directory);
  } catch {
    throw new Error(`${label} is not a canonical real directory`);
  }
  if (!metadata.isDirectory() || metadata.isSymbolicLink() || resolved !== directory) {
    throw new Error(`${label} is not a canonical real directory`);
  }
  return directory;
}

async function ensureCanonicalDirectory(directory, label) {
  try {
    await mkdir(directory, { mode: 0o700 });
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
  }
  return canonicalCandidateDirectory(directory, label);
}

async function unlinkIfPresent(file, fileOperations) {
  try {
    await fileOperations.unlink(file);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

async function writeTemporaryCandidate(temporary, png, fileOperations, label) {
  let handle;
  const failures = [];
  try {
    handle = await open(
      temporary,
      fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_EXCL | fsConstants.O_NOFOLLOW,
      0o600,
    );
    await fileOperations.writeFile(handle, png);
    await fileOperations.sync(handle);
  } catch (error) {
    failures.push(error);
  }
  if (handle) {
    try {
      await handle.close();
    } catch (error) {
      failures.push(error);
    }
  }
  if (failures.length === 0) return;
  try {
    await unlinkIfPresent(temporary, fileOperations);
  } catch (error) {
    failures.push(error);
  }
  throw new AggregateError(failures, `${label} temporary write failed`);
}

export async function persistOccurrenceCandidate({
  outputRoot,
  collection,
  occurrenceId,
  png,
  fileOperations: fileOperationOverrides,
  label = "occurrence candidate",
  collectionLabel = collection,
}) {
  safeLabel(label);
  safeLabel(collectionLabel);
  if (
    typeof outputRoot !== "string"
    || outputRoot.length === 0
    || outputRoot.length > 4096
    || /[\u0000-\u001f\u007f]/u.test(outputRoot)
  ) throw new Error(`${label} output root is invalid`);
  const occurrencePattern = OCCURRENCE_ID_BY_COLLECTION.get(collection);
  if (!occurrencePattern || !occurrencePattern.test(occurrenceId)) {
    throw new Error(`${label} occurrence identity is invalid`);
  }
  if (!Buffer.isBuffer(png) || png.length === 0) throw new Error(`${label} PNG bytes are invalid`);
  const fileOperations = occurrenceCandidateFileOperations(fileOperationOverrides, label);
  const root = await canonicalCandidateDirectory(path.resolve(outputRoot), `${label} output root`);
  const collectionRoot = await ensureCanonicalDirectory(
    path.join(root, collection),
    `${label} ${collectionLabel} directory`,
  );
  const occurrenceRoot = await ensureCanonicalDirectory(
    path.join(collectionRoot, occurrenceId),
    `${label} occurrence directory`,
  );
  const digest = createHash("sha256").update(png).digest("hex");
  const relativePath = `${collection}/${occurrenceId}/${digest}.png`;
  const target = path.join(occurrenceRoot, `${digest}.png`);
  const temporary = path.join(occurrenceRoot, `.${digest}.${randomUUID()}.tmp`);
  await writeTemporaryCandidate(temporary, png, fileOperations, label);
  let linkedTarget = false;
  let temporaryPresent = true;
  try {
    await canonicalCandidateDirectory(occurrenceRoot, `${label} occurrence directory`);
    try {
      await fileOperations.link(temporary, target);
      linkedTarget = true;
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
    }
    await fileOperations.unlink(temporary);
    temporaryPresent = false;
    const verified = await validatePngFile(root, relativePath);
    if (verified.sha256 !== digest || verified.bytes !== png.length) {
      throw new Error(`${label} does not match its content address`);
    }
    return { root, relativePath, verified };
  } catch (error) {
    const failures = [error];
    if (linkedTarget) {
      try {
        await unlinkIfPresent(target, fileOperations);
        linkedTarget = false;
      } catch (cleanupError) {
        failures.push(cleanupError);
      }
    }
    if (temporaryPresent) {
      try {
        await unlinkIfPresent(temporary, fileOperations);
        temporaryPresent = false;
      } catch (cleanupError) {
        failures.push(cleanupError);
      }
    }
    throw new AggregateError(failures, `${label} publication failed`);
  }
}
