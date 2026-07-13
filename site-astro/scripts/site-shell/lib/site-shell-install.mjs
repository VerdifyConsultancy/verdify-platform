import { createHash, randomUUID } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import {
  link,
  lstat,
  mkdir,
  mkdtemp,
  open,
  opendir,
  realpath,
  rm,
  rmdir,
  unlink,
} from "node:fs/promises";
import path from "node:path";
import {
  MAX_ARCHIVE_BYTES,
  NORMALIZATION,
  readDescriptorBounded,
  sha256,
  validateManifest,
  validateSafeRelativePath,
  verifyArtifactBuffer,
} from "./site-shell-artifact.mjs";

const DIGEST_PATTERN = /^sha256:[a-f0-9]{64}$/;
const DIRECTORY_OPEN_FLAGS = fsConstants.O_RDONLY | (fsConstants.O_DIRECTORY ?? 0) | (fsConstants.O_NOFOLLOW ?? 0);
const FILE_OPEN_FLAGS = fsConstants.O_RDONLY | (fsConstants.O_NONBLOCK ?? 0) | (fsConstants.O_NOFOLLOW ?? 0);
const TRANSACTION_MARKER = ".site-shell-install-transaction.json";
const DESTINATION_RECORD = ".site-shell-install-destination.json";
export const SITE_SHELL_READY_RECORD = ".site-shell-ready.json";
const INTERNAL_RECORD_MAX_BYTES = 4096;
const RECOVERY_PARENT_ENTRY_MAX = 4096;
const RESERVED_ROOT_MODE = 0o700;
const READY_RECORD_MODE = 0o644;
const CONTRACT_VERSION_PATTERN = /^[0-9]+\.[0-9]+\.[0-9]+$/;

const fail = (message) => {
  throw new Error(message);
};

const normalizeDigest = (value, label) => {
  if (typeof value !== "string" || !DIGEST_PATTERN.test(value)) {
    fail(`${label} must be an independently supplied sha256:<64 lowercase hex> digest.`);
  }
  return value;
};

const sameIdentity = (left, right) => left.dev === right.dev && left.ino === right.ino;

const sameStableFileState = (left, right) => (
  sameIdentity(left, right)
  && left.mode === right.mode
  && left.size === right.size
  && left.nlink === right.nlink
  && left.mtimeNs === right.mtimeNs
  && left.ctimeNs === right.ctimeNs
);

const permissionMode = (stat) => Number(stat.mode & 0o777n);

const contained = (root, candidate) => {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
};

const lstatOrNull = async (target) => {
  try {
    return await lstat(target, { bigint: true });
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
};

const descriptorPath = (handle, relativePath = "") => {
  if (process.platform !== "linux") return null;
  const root = `/proc/self/fd/${handle.fd}`;
  return relativePath ? path.join(root, ...relativePath.split("/")) : root;
};

const assertOpenedPath = async (handle, expectedPath, label) => {
  if (process.platform !== "linux") return;
  let actual;
  try {
    actual = await realpath(`/proc/self/fd/${handle.fd}`);
  } catch {
    fail(`Could not prove the opened ${label} path.`);
  }
  if (actual !== expectedPath) fail(`Opened ${label} changed path: expected ${expectedPath}; received ${actual}.`);
};

const requireDescriptorPath = (handle, relativePath = "") => {
  const anchored = descriptorPath(handle, relativePath);
  if (!anchored) fail("Descriptor-confined site-shell installation requires Linux /proc file-descriptor paths.");
  return anchored;
};

const assertDescriptorContained = async (rootHandle, childHandle, label) => {
  let rootPath;
  let childPath;
  try {
    [rootPath, childPath] = await Promise.all([
      realpath(`/proc/self/fd/${rootHandle.fd}`),
      realpath(`/proc/self/fd/${childHandle.fd}`),
    ]);
  } catch {
    fail(`Could not prove descriptor confinement for ${label}.`);
  }
  if (!contained(rootPath, childPath)) fail(`${label} descriptor escaped the held staging root.`);
};

const assertDirectoryChainNoSymlinks = async (absoluteDirectory) => {
  const resolved = path.resolve(absoluteDirectory);
  const parsed = path.parse(resolved);
  let current = parsed.root;
  const records = [];
  for (const segment of resolved.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    const stat = await lstatOrNull(current);
    if (!stat) fail(`Install parent component is missing: ${current}`);
    if (stat.isSymbolicLink()) fail(`Install parent symlink is forbidden: ${current}`);
    if (!stat.isDirectory()) fail(`Install parent component is not a directory: ${current}`);
    records.push({ path: current, stat });
  }
  const canonical = await realpath(resolved);
  if (canonical !== resolved) fail(`Install parent must have a canonical non-symlink path: ${resolved}`);
  return records;
};

const assertSameDirectoryChain = async (before) => {
  for (const record of before) {
    const stat = await lstatOrNull(record.path);
    if (!stat || stat.isSymbolicLink() || !stat.isDirectory() || !sameIdentity(record.stat, stat)) {
      fail(`Install parent changed during extraction: ${record.path}`);
    }
  }
};

const assertParentDescriptor = async (parentHandle, expectedStat, expectedPath) => {
  const descriptorStat = await parentHandle.stat({ bigint: true });
  if (!descriptorStat.isDirectory() || !sameIdentity(descriptorStat, expectedStat)) fail("Install parent descriptor identity changed.");
  await assertOpenedPath(parentHandle, expectedPath, "install parent");
};

const assertDestinationAbsent = async (destination) => {
  const existing = await lstatOrNull(destination);
  if (existing) fail(`Install destination already exists and will not be replaced: ${destination}`);
};

export const verifyPinnedArtifactBuffer = (archive, { expectedArchiveDigest, expectedSourceTreeDigest }) => {
  if (!Buffer.isBuffer(archive)) fail("Pinned artifact must be supplied as a Buffer.");
  const archiveDigest = normalizeDigest(expectedArchiveDigest, "expectedArchiveDigest");
  const sourceTreeDigest = normalizeDigest(expectedSourceTreeDigest, "expectedSourceTreeDigest");
  const actualArchiveDigest = `sha256:${sha256(archive)}`;
  if (actualArchiveDigest !== archiveDigest) fail(`Pinned archive SHA-256 mismatch: expected ${archiveDigest}; received ${actualArchiveDigest}.`);
  const verified = verifyArtifactBuffer(archive);
  if (verified.manifest.contract.sourceTreeDigest !== sourceTreeDigest) {
    fail(`Pinned source-tree digest mismatch: expected ${sourceTreeDigest}; received ${verified.manifest.contract.sourceTreeDigest}.`);
  }
  return { ...verified, archiveDigest, sourceTreeDigest };
};

export const validateInstallRelativePaths = (relativeEntries) => {
  const accepted = [];
  const sorted = [...relativeEntries].sort((left, right) => Buffer.compare(Buffer.from(left.path), Buffer.from(right.path)));
  for (const entry of sorted) {
    validateSafeRelativePath(entry.path, "Install payload path");
    if (entry.path === SITE_SHELL_READY_RECORD || entry.path.startsWith(`${SITE_SHELL_READY_RECORD}/`)) {
      fail(`Install payload path is reserved for the sealed readiness record: ${entry.path}`);
    }
    const foldedPath = entry.path.toLowerCase();
    for (const existing of accepted) {
      if (
        foldedPath === existing.foldedPath
        || foldedPath.startsWith(`${existing.foldedPath}/`)
        || existing.foldedPath.startsWith(`${foldedPath}/`)
      ) {
        const kind = foldedPath === existing.foldedPath ? "Case-insensitive install payload" : "Install file/directory prefix";
        fail(`${kind} collision: ${existing.path} and ${entry.path}`);
      }
    }
    accepted.push({ path: entry.path, foldedPath });
  }
  return sorted;
};

const installedTreeDigest = (entries) => {
  const normalized = validateInstallRelativePaths(entries.map((entry) => ({ path: entry.path })))
    .map(({ path: entryPath }) => {
      const entry = entries.find((candidate) => candidate.path === entryPath);
      const size = Buffer.isBuffer(entry.content) ? entry.content.length : entry.size;
      const digest = Buffer.isBuffer(entry.content)
        ? `sha256:${sha256(entry.content)}`
        : (entry.digest?.startsWith("sha256:") ? entry.digest : `sha256:${entry.digest}`);
      if (entry.mode !== NORMALIZATION.fileMode) fail(`Installed-tree mode is invalid: ${entry.path}`);
      if (!Number.isSafeInteger(size) || size < 0 || size > MAX_ARCHIVE_BYTES) {
        fail(`Installed-tree size is invalid: ${entry.path}`);
      }
      normalizeDigest(digest, `Installed-tree digest for ${entry.path}`);
      return { path: entry.path, mode: entry.mode, size, digest };
    });
  const hash = createHash("sha256");
  hash.update("verdify-site-shell-installed-tree-v1\0", "utf8");
  for (const entry of normalized) {
    for (const value of [entry.path, String(entry.mode), String(entry.size), entry.digest]) {
      hash.update(value, "utf8");
      hash.update("\0", "utf8");
    }
  }
  return `sha256:${hash.digest("hex")}`;
};

const verifyDirectoryDescriptor = async (
  handle,
  expected,
  logicalPath,
  label,
  expectedMode = NORMALIZATION.directoryMode,
) => {
  const stat = await handle.stat({ bigint: true });
  if (!stat.isDirectory() || !sameIdentity(stat, expected) || permissionMode(stat) !== expectedMode) {
    fail(`${label} directory identity or mode mismatch: ${logicalPath}`);
  }
  await assertOpenedPath(handle, logicalPath, `${label} directory`);
  return stat;
};

const ensureStagingDirectory = async (stagingHandle, stagingRoot, relativeDirectory, directoryInventory) => {
  if (relativeDirectory === "." || relativeDirectory === "") {
    return { handle: stagingHandle, logicalPath: stagingRoot, owned: false };
  }
  let currentHandle = stagingHandle;
  let currentOwned = false;
  let current = stagingRoot;
  let relative = "";
  try {
    for (const segment of relativeDirectory.split("/")) {
      relative = relative ? `${relative}/${segment}` : segment;
      current = path.join(current, segment);
      const anchored = requireDescriptorPath(currentHandle, segment);
      let pathnameStat = await lstatOrNull(anchored);
      let created = false;
      if (!pathnameStat) {
        await mkdir(anchored, { mode: NORMALIZATION.directoryMode });
        pathnameStat = await lstat(anchored, { bigint: true });
        created = true;
      }
      if (pathnameStat.isSymbolicLink()) fail(`Staging intermediate symlink is forbidden: ${relative}`);
      if (!pathnameStat.isDirectory()) fail(`Staging intermediate path is not a directory: ${relative}`);
      const nextHandle = await open(anchored, DIRECTORY_OPEN_FLAGS);
      let accepted = false;
      try {
        if (created) {
          await nextHandle.chmod(NORMALIZATION.directoryMode);
          await nextHandle.sync();
        }
        const opened = await nextHandle.stat({ bigint: true });
        const anchoredAfter = await lstat(anchored, { bigint: true });
        if (
          !opened.isDirectory()
          || anchoredAfter.isSymbolicLink()
          || !anchoredAfter.isDirectory()
          || !sameIdentity(opened, pathnameStat)
          || !sameIdentity(opened, anchoredAfter)
          || permissionMode(opened) !== NORMALIZATION.directoryMode
        ) {
          fail(`Staging directory changed during descriptor traversal: ${relative}`);
        }
        const expected = directoryInventory.get(relative);
        if (created) {
          if (expected) fail(`Unexpected duplicate staging directory: ${relative}`);
          directoryInventory.set(relative, opened);
        } else if (!expected || !sameIdentity(opened, expected)) {
          fail(`Unexpected staging directory identity: ${relative}`);
        }
        await assertDescriptorContained(stagingHandle, nextHandle, `staging directory ${relative}`);
        if (currentOwned) await currentHandle.close();
        currentHandle = nextHandle;
        currentOwned = true;
        accepted = true;
      } finally {
        if (!accepted) await nextHandle.close().catch(() => {});
      }
    }
    return { handle: currentHandle, logicalPath: current, owned: currentOwned };
  } catch (error) {
    if (currentOwned) await currentHandle.close().catch(() => {});
    throw error;
  }
};

const writeStagedFile = async (stagingHandle, stagingRoot, entry, directoryInventory, fileInventory, hooks) => {
  const relativeDirectory = path.posix.dirname(entry.path);
  const parent = await ensureStagingDirectory(stagingHandle, stagingRoot, relativeDirectory, directoryInventory);
  try {
    const destination = path.join(stagingRoot, ...entry.path.split("/"));
    if (!contained(stagingRoot, destination)) fail(`Staging file escaped its root: ${entry.path}`);
    const fileName = path.posix.basename(entry.path);
    const anchoredDestination = requireDescriptorPath(parent.handle, fileName);
    let handle;
    try {
      await hooks.beforeStagedFileOpen?.({
        staging: stagingRoot,
        destination,
        parent: parent.logicalPath,
        relativePath: entry.path,
      });
      handle = await open(
        anchoredDestination,
        fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_EXCL | (fsConstants.O_NOFOLLOW ?? 0),
        NORMALIZATION.fileMode,
      );
    } catch (error) {
      if (["EEXIST", "ELOOP", "EMLINK"].includes(error?.code)) fail(`Staging path collision or symlink rejected: ${entry.path}`);
      throw error;
    }
    let finalStat;
    try {
      const before = await handle.stat({ bigint: true });
      if (!before.isFile() || before.nlink !== 1n) fail(`Staging destination is not an unaliased regular file: ${entry.path}`);
      await assertDescriptorContained(stagingHandle, parent.handle, `staging parent for ${entry.path}`);
      await assertDescriptorContained(stagingHandle, handle, `staged file ${entry.path}`);
      await handle.writeFile(entry.content);
      await handle.chmod(NORMALIZATION.fileMode);
      await handle.sync();
      finalStat = await handle.stat({ bigint: true });
      if (
        !finalStat.isFile()
        || finalStat.nlink !== 1n
        || finalStat.size !== BigInt(entry.content.length)
        || permissionMode(finalStat) !== NORMALIZATION.fileMode
      ) {
        fail(`Staged file metadata mismatch: ${entry.path}`);
      }
    } finally {
      await handle.close();
    }
    const installed = await lstat(anchoredDestination, { bigint: true });
    if (installed.isSymbolicLink() || !installed.isFile() || !sameIdentity(installed, finalStat)) {
      fail(`Staged file changed type or identity after write: ${entry.path}`);
    }
    fileInventory.set(entry.path, {
      stat: finalStat,
      size: entry.content.length,
      digest: sha256(entry.content),
    });
  } finally {
    if (parent.owned) await parent.handle.close().catch(() => {});
  }
};

const openInventoryNode = async (stagingHandle, stagingRoot, relativePath, flags) => {
  if (relativePath) validateSafeRelativePath(relativePath, "Descriptor-relative tree path");
  const segments = relativePath ? relativePath.split("/") : [];
  let currentHandle = stagingHandle;
  let currentOwned = false;
  let logicalPath = stagingRoot;
  try {
    if (segments.length === 0) {
      const anchoredPath = `${requireDescriptorPath(stagingHandle)}/.`;
      const handle = await open(anchoredPath, flags);
      await assertDescriptorContained(stagingHandle, handle, "tree root");
      return { handle, logicalPath, anchoredPath };
    }
    for (let index = 0; index < segments.length; index += 1) {
      const segment = segments[index];
      const final = index === segments.length - 1;
      const anchoredPath = requireDescriptorPath(currentHandle, segment);
      const pathnameBefore = await lstatOrNull(anchoredPath);
      if (!pathnameBefore || pathnameBefore.isSymbolicLink()) {
        fail(`Closed staging tree node changed or became a symlink: ${relativePath}`);
      }
      const nextHandle = await open(anchoredPath, final ? flags : DIRECTORY_OPEN_FLAGS);
      let accepted = false;
      try {
        const opened = await nextHandle.stat({ bigint: true });
        const pathnameAfter = await lstatOrNull(anchoredPath);
        if (
          !pathnameAfter
          || pathnameAfter.isSymbolicLink()
          || !sameIdentity(opened, pathnameBefore)
          || !sameIdentity(opened, pathnameAfter)
          || (!final && !opened.isDirectory())
        ) fail(`Closed staging tree descriptor traversal changed: ${relativePath}`);
        await assertDescriptorContained(stagingHandle, nextHandle, `tree node ${relativePath}`);
        if (currentOwned) await currentHandle.close();
        currentHandle = nextHandle;
        currentOwned = true;
        logicalPath = path.join(logicalPath, segment);
        accepted = true;
        if (final) return { handle: currentHandle, logicalPath, anchoredPath };
      } finally {
        if (!accepted) await nextHandle.close().catch(() => {});
      }
    }
    fail(`Could not open descriptor-relative tree node: ${relativePath || "."}`);
  } catch (error) {
    if (currentOwned) await currentHandle.close().catch(() => {});
    if (["ELOOP", "EMLINK", "ENOENT", "ENOTDIR"].includes(error?.code)) {
      fail(`Closed staging tree node changed or became a symlink: ${relativePath || "."}`);
    }
    throw error;
  }
};

const closeDirectory = async (directory) => {
  try {
    await directory.close();
  } catch (error) {
    if (error?.code !== "ERR_DIR_CLOSED") throw error;
  }
};

const readDirectoryEntriesBounded = async (directoryPath, maxEntries, label) => {
  if (!Number.isSafeInteger(maxEntries) || maxEntries < 0) fail(`${label} entry limit is invalid.`);
  const entries = [];
  const directory = await opendir(directoryPath);
  try {
    for await (const entry of directory) {
      if (entries.length >= maxEntries) fail(`${label} exceeded the closed inventory bound.`);
      entries.push(entry);
    }
  } finally {
    await closeDirectory(directory);
  }
  return entries;
};

const verifyInventoryDirectory = async (
  stagingHandle,
  stagingRoot,
  relativePath,
  expected,
  expectedMode,
  maxEntries,
) => {
  const { handle, logicalPath, anchoredPath } = await openInventoryNode(
    stagingHandle,
    stagingRoot,
    relativePath,
    DIRECTORY_OPEN_FLAGS,
  );
  try {
    await verifyDirectoryDescriptor(handle, expected, logicalPath, "Closed staging tree", expectedMode);
    const pathnameStat = await lstat(logicalPath, { bigint: true });
    if (pathnameStat.isSymbolicLink() || !pathnameStat.isDirectory() || !sameIdentity(pathnameStat, expected)) {
      fail(`Closed staging tree directory path changed: ${relativePath || "."}`);
    }
    return await readDirectoryEntriesBounded(
      descriptorPath(handle) ?? anchoredPath,
      maxEntries,
      "Closed staging tree directory enumeration",
    );
  } finally {
    await handle.close();
  }
};

const verifyInventoryFile = async (stagingHandle, stagingRoot, relativePath, expected) => {
  const { handle, logicalPath } = await openInventoryNode(stagingHandle, stagingRoot, relativePath, FILE_OPEN_FLAGS);
  try {
    const before = await handle.stat({ bigint: true });
    if (
      !before.isFile()
      || before.nlink !== 1n
      || !sameIdentity(before, expected.stat)
      || before.mode !== expected.stat.mode
      || permissionMode(before) !== (expected.mode ?? NORMALIZATION.fileMode)
      || before.size !== BigInt(expected.size)
    ) {
      fail(`Closed staging tree file identity or metadata mismatch: ${relativePath}`);
    }
    await assertOpenedPath(handle, logicalPath, "closed staging tree file");
    const bytes = await readDescriptorBounded(handle, expected.size, `Closed staging tree file ${relativePath}`);
    const after = await handle.stat({ bigint: true });
    if (!sameStableFileState(before, after)) fail(`Closed staging tree file mutated during verification: ${relativePath}`);
    if (bytes.length !== expected.size || sha256(bytes) !== expected.digest) fail(`Closed staging tree file digest mismatch: ${relativePath}`);
    const pathnameStat = await lstat(logicalPath, { bigint: true });
    if (pathnameStat.isSymbolicLink() || !pathnameStat.isFile() || !sameStableFileState(after, pathnameStat)) {
      fail(`Closed staging tree file path no longer matches its descriptor: ${relativePath}`);
    }
  } finally {
    await handle.close();
  }
};

const verifyClosedStagingTree = async ({
  stagingHandle,
  stagingRoot,
  directoryInventory,
  fileInventory,
  rootMode = NORMALIZATION.directoryMode,
}) => {
  const verifyPass = async () => {
    const actualDirectories = new Set();
    const actualFiles = new Set();
    const visit = async (relativeDirectory) => {
      const expected = directoryInventory.get(relativeDirectory);
      if (!expected) fail(`Closed staging tree contains an unexpected directory: ${relativeDirectory || "."}`);
      actualDirectories.add(relativeDirectory);
      const children = await verifyInventoryDirectory(
        stagingHandle,
        stagingRoot,
        relativeDirectory,
        expected,
        relativeDirectory ? NORMALIZATION.directoryMode : rootMode,
        directoryInventory.size + fileInventory.size,
      );
      children.sort((left, right) => Buffer.compare(Buffer.from(left.name), Buffer.from(right.name)));
      for (const child of children) {
        const relativePath = relativeDirectory ? `${relativeDirectory}/${child.name}` : child.name;
        if (child.isSymbolicLink()) fail(`Closed staging tree contains a symlink: ${relativePath}`);
        if (child.isDirectory()) {
          await visit(relativePath);
        } else if (child.isFile()) {
          const expectedFile = fileInventory.get(relativePath);
          if (!expectedFile) fail(`Closed staging tree contains an unexpected file: ${relativePath}`);
          actualFiles.add(relativePath);
          await verifyInventoryFile(stagingHandle, stagingRoot, relativePath, expectedFile);
        } else {
          fail(`Closed staging tree contains a special node: ${relativePath}`);
        }
      }
    };
    await visit("");
    if (
      actualDirectories.size !== directoryInventory.size
      || [...directoryInventory.keys()].some((relativePath) => !actualDirectories.has(relativePath))
    ) {
      fail("Closed staging tree is missing an installed directory.");
    }
    if (actualFiles.size !== fileInventory.size || [...fileInventory.keys()].some((relativePath) => !actualFiles.has(relativePath))) {
      fail("Closed staging tree is missing an installed file.");
    }
  };
  await verifyPass();
  // A complete second pass closes the interval opened by recursive enumeration,
  // including the exact child-name set rather than metadata alone.
  await verifyPass();
};

const fsyncInventoryDirectories = async (
  stagingHandle,
  stagingRoot,
  directoryInventory,
  rootMode = NORMALIZATION.directoryMode,
) => {
  const directories = [...directoryInventory.keys()].sort((left, right) => right.split("/").length - left.split("/").length);
  for (const relativePath of directories) {
    const expected = directoryInventory.get(relativePath);
    const { handle, logicalPath } = await openInventoryNode(stagingHandle, stagingRoot, relativePath, DIRECTORY_OPEN_FLAGS);
    try {
      await verifyDirectoryDescriptor(
        handle,
        expected,
        logicalPath,
        "Staging fsync",
        relativePath ? NORMALIZATION.directoryMode : rootMode,
      );
      await handle.sync();
    } finally {
      await handle.close();
    }
  }
};

const cleanupOwnedStaging = async (staging, stagingHandle, expectedRoot) => {
  let descriptorStat;
  try {
    descriptorStat = await stagingHandle.stat({ bigint: true });
  } catch {
    await stagingHandle.close().catch(() => {});
    return;
  }
  if (!descriptorStat.isDirectory() || (expectedRoot && !sameIdentity(descriptorStat, expectedRoot))) {
    await stagingHandle.close().catch(() => {});
    return;
  }

  // On Linux, children are removed through the held descriptor. A pathname
  // replacement therefore cannot redirect cleanup into a foreign directory.
  const anchoredRoot = descriptorPath(stagingHandle);
  if (anchoredRoot) {
    const directory = await opendir(anchoredRoot).catch(() => null);
    if (directory) {
      try {
        for await (const child of directory) {
          await rm(path.join(anchoredRoot, child.name), { recursive: true, force: true }).catch(() => {});
        }
      } finally {
        await closeDirectory(directory).catch(() => {});
      }
    }
  }

  const pathnameStat = await lstatOrNull(staging);
  const pathnameStillOwned = Boolean(
    pathnameStat
    && pathnameStat.isDirectory()
    && !pathnameStat.isSymbolicLink()
    && sameIdentity(pathnameStat, descriptorStat)
  );
  await stagingHandle.close().catch(() => {});
  if (pathnameStillOwned) {
    // Only the now-empty, identity-checked original root is removed by name.
    await rmdir(staging).catch(() => {});
  }
};

const stableRecordBytes = (record) => Buffer.from(`${JSON.stringify(record, null, 2)}\n`, "utf8");

const createInternalRecord = async (directoryHandle, name, record, mode = 0o600) => {
  const bytes = stableRecordBytes(record);
  if (bytes.length > INTERNAL_RECORD_MAX_BYTES) fail("Install recovery record is too large.");
  const anchored = requireDescriptorPath(directoryHandle, name);
  const handle = await open(
    anchored,
    fsConstants.O_WRONLY
      | fsConstants.O_CREAT
      | fsConstants.O_EXCL
      | (fsConstants.O_NONBLOCK ?? 0)
      | (fsConstants.O_NOFOLLOW ?? 0),
    mode,
  );
  let finalStat;
  try {
    const before = await handle.stat({ bigint: true });
    if (!before.isFile() || before.nlink !== 1n) fail("Install recovery record is not a single-link regular file.");
    await handle.writeFile(bytes);
    await handle.chmod(mode);
    await handle.sync();
    finalStat = await handle.stat({ bigint: true });
    if (
      !finalStat.isFile()
      || finalStat.nlink !== 1n
      || finalStat.size !== BigInt(bytes.length)
      || permissionMode(finalStat) !== mode
    ) {
      fail("Install recovery record metadata is invalid.");
    }
  } finally {
    await handle.close();
  }
  const pathnameStat = await lstat(anchored, { bigint: true });
  if (pathnameStat.isSymbolicLink() || !sameStableFileState(finalStat, pathnameStat)) {
    fail("Install recovery record changed after creation.");
  }
  await directoryHandle.sync();
  return {
    bytes,
    stat: finalStat,
    size: bytes.length,
    digest: sha256(bytes),
    internal: true,
    mode,
  };
};

const readInternalRecordDetails = async (directoryHandle, name, mode = 0o600) => {
  const anchored = requireDescriptorPath(directoryHandle, name);
  const pathnameBefore = await lstatOrNull(anchored);
  if (
    !pathnameBefore
    || pathnameBefore.isSymbolicLink()
    || !pathnameBefore.isFile()
    || pathnameBefore.nlink !== 1n
    || permissionMode(pathnameBefore) !== mode
    || pathnameBefore.size > BigInt(INTERNAL_RECORD_MAX_BYTES)
  ) return null;
  let handle;
  try {
    handle = await open(anchored, FILE_OPEN_FLAGS);
  } catch {
    return null;
  }
  try {
    const before = await handle.stat({ bigint: true });
    if (!sameStableFileState(before, pathnameBefore)) return null;
    const bytes = await readDescriptorBounded(handle, INTERNAL_RECORD_MAX_BYTES, `Install record ${name}`);
    const after = await handle.stat({ bigint: true });
    const pathnameAfter = await lstatOrNull(anchored);
    if (
      bytes.length > INTERNAL_RECORD_MAX_BYTES
      || !sameStableFileState(before, after)
      || !pathnameAfter
      || !sameStableFileState(after, pathnameAfter)
    ) return null;
    try {
      const record = JSON.parse(bytes.toString("utf8"));
      if (!stableRecordBytes(record).equals(bytes)) return null;
      return { record, bytes, stat: after };
    } catch {
      return null;
    }
  } finally {
    await handle.close();
  }
};

const readInternalRecord = async (directoryHandle, name, mode = 0o600) => (
  (await readInternalRecordDetails(directoryHandle, name, mode))?.record ?? null
);

const exactRecordKeys = (record, expected) => (
  record
  && typeof record === "object"
  && !Array.isArray(record)
  && Object.keys(record).sort().join("\0") === [...expected].sort().join("\0")
);

const READY_RECORD_KEYS = [
  "archiveDigest",
  "contractVersion",
  "destinationDev",
  "destinationIno",
  "installedFileCount",
  "installedTreeDigest",
  "manifestDigest",
  "schemaVersion",
  "sourceTreeDigest",
  "transactionId",
];

const validReadyRecord = (record, {
  archiveDigest,
  sourceTreeDigest,
  contractVersion,
  installedFileCount,
  manifestDigest,
  expectedInstalledTreeDigest,
  destinationStat,
}) => (
  exactRecordKeys(record, READY_RECORD_KEYS)
  && record.schemaVersion === 2
  && typeof record.transactionId === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(record.transactionId)
  && record.archiveDigest === archiveDigest
  && record.sourceTreeDigest === sourceTreeDigest
  && record.contractVersion === contractVersion
  && record.installedFileCount === installedFileCount
  && Number.isSafeInteger(record.installedFileCount)
  && record.installedFileCount >= 0
  && record.manifestDigest === manifestDigest
  && DIGEST_PATTERN.test(record.manifestDigest)
  && DIGEST_PATTERN.test(record.installedTreeDigest)
  && (expectedInstalledTreeDigest === undefined || record.installedTreeDigest === expectedInstalledTreeDigest)
  && record.destinationDev === String(destinationStat.dev)
  && record.destinationIno === String(destinationStat.ino)
);

const sameStableDirectoryState = (left, right) => (
  sameIdentity(left, right)
  && left.mode === right.mode
  && left.nlink === right.nlink
  && left.size === right.size
  && left.mtimeNs === right.mtimeNs
  && left.ctimeNs === right.ctimeNs
);

const readPinnedInstalledFile = async ({
  rootHandle,
  rootPath,
  relativePath,
  expectedDigest,
  expectedSize,
  expectedStat,
  maxBytes = MAX_ARCHIVE_BYTES,
}) => {
  const { handle, logicalPath } = await openInventoryNode(rootHandle, rootPath, relativePath, FILE_OPEN_FLAGS);
  try {
    const before = await handle.stat({ bigint: true });
    if (
      !before.isFile()
      || before.nlink !== 1n
      || permissionMode(before) !== NORMALIZATION.fileMode
      || before.size > BigInt(maxBytes)
      || (expectedSize !== undefined && before.size !== BigInt(expectedSize))
      || (expectedStat && !sameStableFileState(before, expectedStat))
    ) fail(`Ready installation file metadata mismatch: ${relativePath}`);
    await assertOpenedPath(handle, logicalPath, `ready installation file ${relativePath}`);
    const bytes = await readDescriptorBounded(handle, maxBytes, `Ready installation file ${relativePath}`);
    const after = await handle.stat({ bigint: true });
    const pathnameAfter = await lstat(logicalPath, { bigint: true });
    if (
      bytes.length > maxBytes
      || !sameStableFileState(before, after)
      || pathnameAfter.isSymbolicLink()
      || !pathnameAfter.isFile()
      || !sameStableFileState(after, pathnameAfter)
    ) fail(`Ready installation file changed during verification: ${relativePath}`);
    const actualDigest = `sha256:${sha256(bytes)}`;
    if (actualDigest !== expectedDigest) fail(`Ready installation file digest mismatch: ${relativePath}`);
    return { bytes, stat: after };
  } finally {
    await handle.close();
  }
};

const expectedInstalledDirectories = (filePaths) => {
  const directories = new Set([""]);
  for (const filePath of filePaths) {
    validateSafeRelativePath(filePath, "Ready installation expected path");
    const segments = filePath.split("/");
    for (let length = 1; length < segments.length; length += 1) {
      directories.add(segments.slice(0, length).join("/"));
    }
  }
  return directories;
};

const verifyInstalledTreePass = async ({
  rootHandle,
  rootPath,
  rootStat,
  expectedFiles,
  expectedDirectories,
  priorDirectoryStats,
  priorFileStats,
}) => {
  const actualDirectories = new Set([""]);
  const actualFiles = new Set();
  const directoryStats = new Map();
  const fileStats = new Map();
  const directoryPaths = [...expectedDirectories].sort((left, right) => Buffer.compare(Buffer.from(left), Buffer.from(right)));
  for (const relativeDirectory of directoryPaths) {
    const { handle, logicalPath } = await openInventoryNode(
      rootHandle,
      rootPath,
      relativeDirectory,
      DIRECTORY_OPEN_FLAGS,
    );
    try {
      const before = await handle.stat({ bigint: true });
      const expectedPrior = relativeDirectory === "" ? rootStat : priorDirectoryStats?.get(relativeDirectory);
      if (
        !before.isDirectory()
        || permissionMode(before) !== NORMALIZATION.directoryMode
        || (expectedPrior && !sameStableDirectoryState(before, expectedPrior))
      ) fail(`Ready installation directory metadata mismatch: ${relativeDirectory || "."}`);
      await assertOpenedPath(handle, logicalPath, `ready installation directory ${relativeDirectory || "."}`);
      const children = await readDirectoryEntriesBounded(
        requireDescriptorPath(handle),
        expectedFiles.size + expectedDirectories.size,
        "Ready installation directory enumeration",
      );
      children.sort((left, right) => Buffer.compare(Buffer.from(left.name), Buffer.from(right.name)));
      for (const child of children) {
        const relativePath = relativeDirectory ? `${relativeDirectory}/${child.name}` : child.name;
        const anchoredChild = requireDescriptorPath(handle, child.name);
        const childStat = await lstatOrNull(anchoredChild);
        if (!childStat || childStat.isSymbolicLink()) fail(`Ready installation contains a symlink or vanished node: ${relativePath}`);
        if (childStat.isDirectory()) {
          if (!expectedDirectories.has(relativePath)) fail(`Ready installation contains an unexpected directory: ${relativePath}`);
          actualDirectories.add(relativePath);
        } else if (childStat.isFile()) {
          if (!expectedFiles.has(relativePath)) fail(`Ready installation contains an unexpected file: ${relativePath}`);
          actualFiles.add(relativePath);
        } else {
          fail(`Ready installation contains a special node: ${relativePath}`);
        }
      }
      const after = await handle.stat({ bigint: true });
      const pathnameAfter = await lstat(logicalPath, { bigint: true });
      if (
        !sameStableDirectoryState(before, after)
        || pathnameAfter.isSymbolicLink()
        || !pathnameAfter.isDirectory()
        || !sameStableDirectoryState(after, pathnameAfter)
      ) fail(`Ready installation directory changed during enumeration: ${relativeDirectory || "."}`);
      directoryStats.set(relativeDirectory, after);
    } finally {
      await handle.close();
    }
  }
  if (
    actualDirectories.size !== expectedDirectories.size
    || [...expectedDirectories].some((relativePath) => !actualDirectories.has(relativePath))
    || actualFiles.size !== expectedFiles.size
    || [...expectedFiles.keys()].some((relativePath) => !actualFiles.has(relativePath))
  ) fail("Ready installation paths do not exactly match the sealed manifest inventory.");

  for (const [relativePath, expected] of expectedFiles) {
    const verified = await readPinnedInstalledFile({
      rootHandle,
      rootPath,
      relativePath,
      expectedDigest: expected.digest,
      expectedSize: expected.size,
      expectedStat: expected.pinnedStat ?? priorFileStats?.get(relativePath),
      maxBytes: expected.maxBytes ?? MAX_ARCHIVE_BYTES,
    });
    fileStats.set(relativePath, verified.stat);
  }
  for (const [relativeDirectory, expected] of directoryStats) {
    const { handle, logicalPath } = await openInventoryNode(
      rootHandle,
      rootPath,
      relativeDirectory,
      DIRECTORY_OPEN_FLAGS,
    );
    try {
      const afterFiles = await handle.stat({ bigint: true });
      const pathnameAfterFiles = await lstat(logicalPath, { bigint: true });
      if (
        !sameStableDirectoryState(afterFiles, expected)
        || pathnameAfterFiles.isSymbolicLink()
        || !pathnameAfterFiles.isDirectory()
        || !sameStableDirectoryState(pathnameAfterFiles, expected)
      ) fail(`Ready installation directory changed after file verification: ${relativeDirectory || "."}`);
    } finally {
      await handle.close();
    }
  }
  return { directoryStats, fileStats };
};

export const verifySiteShellInstallReady = async ({
  destination,
  expectedArchiveDigest,
  expectedSourceTreeDigest,
  expectedContractVersion,
  expectedInstalledFileCount,
  expectedManifestDigest,
  hooks = {},
}) => {
  if (typeof destination !== "string" || !destination) fail("Install destination is required.");
  const archiveDigest = normalizeDigest(expectedArchiveDigest, "expectedArchiveDigest");
  const sourceTreeDigest = normalizeDigest(expectedSourceTreeDigest, "expectedSourceTreeDigest");
  const manifestDigest = normalizeDigest(expectedManifestDigest, "expectedManifestDigest");
  if (typeof expectedContractVersion !== "string" || !CONTRACT_VERSION_PATTERN.test(expectedContractVersion)) {
    fail("expectedContractVersion must be exact numeric x.y.z.");
  }
  if (!Number.isSafeInteger(expectedInstalledFileCount) || expectedInstalledFileCount <= 0) {
    fail("expectedInstalledFileCount must be a positive safe integer.");
  }
  const absoluteDestination = path.resolve(destination);
  const chain = await assertDirectoryChainNoSymlinks(absoluteDestination);
  const canonical = await realpath(absoluteDestination);
  if (canonical !== absoluteDestination) fail("Ready installation destination must be a canonical non-symlink path.");
  const expectedRoot = chain.at(-1)?.stat ?? await lstat(absoluteDestination, { bigint: true });
  const handle = await open(absoluteDestination, DIRECTORY_OPEN_FLAGS);
  try {
    const root = await handle.stat({ bigint: true });
    if (
      !root.isDirectory()
      || !sameIdentity(root, expectedRoot)
      || permissionMode(root) !== NORMALIZATION.directoryMode
    ) fail("Ready installation root identity or mode is invalid.");
    await assertOpenedPath(handle, absoluteDestination, "ready installation root");
    const readyDetails = await readInternalRecordDetails(handle, SITE_SHELL_READY_RECORD, READY_RECORD_MODE);
    const record = readyDetails?.record;
    if (!validReadyRecord(record, {
      archiveDigest,
      sourceTreeDigest,
      contractVersion: expectedContractVersion,
      installedFileCount: expectedInstalledFileCount,
      manifestDigest,
      destinationStat: root,
    })) fail("Installation is incomplete or its sealed readiness record is invalid.");
    await hooks.afterReadyRecordRead?.({ destination: absoluteDestination, record });

    const manifestVerified = await readPinnedInstalledFile({
      rootHandle: handle,
      rootPath: absoluteDestination,
      relativePath: "MANIFEST.json",
      expectedDigest: manifestDigest,
      maxBytes: MAX_ARCHIVE_BYTES,
    });
    let manifest;
    try {
      manifest = validateManifest(JSON.parse(manifestVerified.bytes.toString("utf8")));
    } catch (error) {
      fail(`Installed MANIFEST.json is invalid: ${error.message}`);
    }
    if (!Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, "utf8").equals(manifestVerified.bytes)) {
      fail("Installed MANIFEST.json is not canonical.");
    }
    if (
      manifest.contract.version !== expectedContractVersion
      || manifest.contract.sourceTreeDigest !== sourceTreeDigest
      || manifest.files.length + 1 !== expectedInstalledFileCount
    ) fail("Installed MANIFEST.json does not match the independent release pins.");
    const installedContentBytes = manifest.files.reduce((total, file) => total + file.size, manifestVerified.bytes.length);
    if (!Number.isSafeInteger(installedContentBytes) || installedContentBytes > MAX_ARCHIVE_BYTES) {
      fail("Installed manifest inventory exceeds the bounded artifact content size.");
    }
    await hooks.afterManifestRead?.({ destination: absoluteDestination, manifest });

    const manifestEntries = [
      {
        path: "MANIFEST.json",
        mode: NORMALIZATION.fileMode,
        size: manifestVerified.bytes.length,
        digest: manifestDigest,
      },
      ...manifest.files.map((file) => ({
        path: file.path,
        mode: file.mode,
        size: file.size,
        digest: `sha256:${file.sha256}`,
      })),
    ];
    const expectedTreeDigest = installedTreeDigest(manifestEntries);
    if (!validReadyRecord(record, {
      archiveDigest,
      sourceTreeDigest,
      contractVersion: expectedContractVersion,
      installedFileCount: expectedInstalledFileCount,
      manifestDigest,
      expectedInstalledTreeDigest: expectedTreeDigest,
      destinationStat: root,
    })) fail("Sealed readiness tree digest does not match the independently pinned manifest.");

    const expectedFiles = new Map(manifestEntries.map((entry) => [entry.path, { ...entry }]));
    expectedFiles.set(SITE_SHELL_READY_RECORD, {
      mode: READY_RECORD_MODE,
      size: readyDetails.bytes.length,
      digest: `sha256:${sha256(readyDetails.bytes)}`,
      pinnedStat: readyDetails.stat,
      maxBytes: INTERNAL_RECORD_MAX_BYTES,
    });
    const expectedDirectories = expectedInstalledDirectories(expectedFiles.keys());
    const firstPass = await verifyInstalledTreePass({
      rootHandle: handle,
      rootPath: absoluteDestination,
      rootStat: root,
      expectedFiles,
      expectedDirectories,
    });
    await hooks.afterReadyTreeFirstPass?.({ destination: absoluteDestination, manifest, record });
    await verifyInstalledTreePass({
      rootHandle: handle,
      rootPath: absoluteDestination,
      rootStat: root,
      expectedFiles,
      expectedDirectories,
      priorDirectoryStats: firstPass.directoryStats,
      priorFileStats: firstPass.fileStats,
    });

    await assertSameDirectoryChain(chain);
    const pathAfter = await lstat(absoluteDestination, { bigint: true });
    const descriptorAfter = await handle.stat({ bigint: true });
    if (
      !sameStableDirectoryState(root, descriptorAfter)
      || pathAfter.isSymbolicLink()
      || !sameStableDirectoryState(root, pathAfter)
    ) fail("Ready installation root changed during verification.");
    return record;
  } finally {
    await handle.close();
  }
};

const assertNoPriorInstallResidues = async ({ parentHandle, destinationName }) => {
  const parentDescriptorRoot = requireDescriptorPath(parentHandle);
  const prefix = `.${destinationName}.site-shell-staging-`;
  let inspected = 0;
  const directory = await opendir(parentDescriptorRoot);
  for await (const entry of directory) {
    inspected += 1;
    if (inspected > RECOVERY_PARENT_ENTRY_MAX) {
      fail(
        "Site-shell install requires manual recovery: parent enumeration exceeded its bound; no path was modified.",
      );
    }
    if (entry.name.startsWith(prefix)) {
      // Records inside this entry are intentionally not consulted. They contain
      // only public values and filesystem metadata that another same-uid process
      // can forge. Cross-process deletion can therefore never be authorized by
      // them, even when every field and inode number appears to match.
      fail(
        "Site-shell install requires manual recovery: a prior staging residue was preserved; automatic cross-process deletion is disabled.",
      );
    }
  }
};

const transferStagingTreeToReservedDestination = async ({
  stagingHandle,
  stagingRoot,
  destinationHandle,
  destinationRoot,
  directoryInventory,
  fileInventory,
}) => {
  const destinationRootStat = await destinationHandle.stat({ bigint: true });
  const destinationDirectories = new Map([["", destinationRootStat]]);
  const destinationFiles = new Map();
  const directories = [...directoryInventory.keys()]
    .filter(Boolean)
    .sort((left, right) => left.split("/").length - right.split("/").length || Buffer.compare(Buffer.from(left), Buffer.from(right)));
  for (const relativePath of directories) {
    const opened = await ensureStagingDirectory(destinationHandle, destinationRoot, relativePath, destinationDirectories);
    if (opened.owned) await opened.handle.close();
  }

  const files = [...fileInventory.entries()]
    .filter(([, expected]) => !expected.internal)
    .sort(([left], [right]) => Buffer.compare(Buffer.from(left), Buffer.from(right)));
  for (const [relativePath, expected] of files) {
    const source = await openInventoryNode(stagingHandle, stagingRoot, relativePath, FILE_OPEN_FLAGS);
    const destinationParent = await ensureStagingDirectory(
      destinationHandle,
      destinationRoot,
      path.posix.dirname(relativePath),
      destinationDirectories,
    );
    try {
      const before = await source.handle.stat({ bigint: true });
      if (
        !before.isFile()
        || before.nlink !== 1n
        || !sameStableFileState(before, expected.stat)
        || permissionMode(before) !== NORMALIZATION.fileMode
      ) fail(`Staging source changed before no-replace publication: ${relativePath}`);
      const bytes = await readDescriptorBounded(
        source.handle,
        expected.size,
        `Staging source ${relativePath}`,
      );
      const afterRead = await source.handle.stat({ bigint: true });
      if (!sameStableFileState(before, afterRead) || bytes.length !== expected.size || sha256(bytes) !== expected.digest) {
        fail(`Staging source digest changed before no-replace publication: ${relativePath}`);
      }
      const sourcePath = requireDescriptorPath(stagingHandle, relativePath);
      const destinationPath = requireDescriptorPath(destinationParent.handle, path.posix.basename(relativePath));
      await link(sourcePath, destinationPath);
      const linked = await lstat(destinationPath, { bigint: true });
      if (linked.isSymbolicLink() || !linked.isFile() || !sameIdentity(linked, afterRead) || linked.nlink !== 2n) {
        fail(`No-replace hard-link publication identity mismatch: ${relativePath}`);
      }
      await unlink(sourcePath);
      const finalStat = await source.handle.stat({ bigint: true });
      const finalPathStat = await lstat(destinationPath, { bigint: true });
      if (
        !finalStat.isFile()
        || finalStat.nlink !== 1n
        || !sameStableFileState(finalStat, finalPathStat)
        || finalStat.size !== BigInt(expected.size)
        || permissionMode(finalStat) !== NORMALIZATION.fileMode
      ) fail(`Published file did not settle as a single-link regular file: ${relativePath}`);
      destinationFiles.set(relativePath, { ...expected, stat: finalStat });
    } catch (error) {
      if (error?.code === "EEXIST") fail(`Reserved destination child already exists: ${relativePath}`);
      throw error;
    } finally {
      await source.handle.close().catch(() => {});
      if (destinationParent.owned) await destinationParent.handle.close().catch(() => {});
    }
  }
  return { directoryInventory: destinationDirectories, fileInventory: destinationFiles };
};

export const installSiteShellArtifact = async ({
  archive,
  expectedArchiveDigest,
  expectedSourceTreeDigest,
  destination,
  hooks = {},
}) => {
  const pinned = verifyPinnedArtifactBuffer(archive, { expectedArchiveDigest, expectedSourceTreeDigest });
  if (typeof destination !== "string" || !destination) fail("Install destination is required.");
  const absoluteDestination = path.resolve(destination);
  const parent = path.dirname(absoluteDestination);
  const destinationName = path.basename(absoluteDestination);
  if (absoluteDestination === parent || destinationName === "." || destinationName === "..") {
    fail("Install destination must be a new child directory.");
  }
  const rootPrefix = `${pinned.manifest.artifact.root}/`;
  const relativeEntries = pinned.entries.map((entry) => {
    if (!entry.path.startsWith(rootPrefix)) fail(`Archive entry is outside the version root: ${entry.path}`);
    return { ...entry, path: entry.path.slice(rootPrefix.length) };
  });
  const installEntries = validateInstallRelativePaths(relativeEntries);
  const manifestInstallEntries = installEntries.filter((entry) => entry.path === pinned.manifest.artifact.manifestPath);
  if (manifestInstallEntries.length !== 1) fail("Installed archive must contain exactly one canonical manifest entry.");
  const manifestDigest = `sha256:${sha256(manifestInstallEntries[0].content)}`;
  const expectedInstalledTreeDigest = installedTreeDigest(installEntries);
  const parentChain = await assertDirectoryChainNoSymlinks(parent);
  const parentCanonical = await realpath(parent);
  const parentStat = parentChain.at(-1)?.stat ?? await lstat(parent, { bigint: true });
  const parentHandle = await open(parent, DIRECTORY_OPEN_FLAGS);
  let staging;
  let stagingHandle;
  let stagingRootStat;
  let destinationHandle;
  let destinationRootStat;
  let anchoredDestination;
  let committed = false;
  try {
    await assertParentDescriptor(parentHandle, parentStat, parentCanonical);
    await assertNoPriorInstallResidues({ parentHandle, destinationName });
    await assertSameDirectoryChain(parentChain);
    await assertParentDescriptor(parentHandle, parentStat, parentCanonical);
    await assertDestinationAbsent(absoluteDestination);

    const anchoredStaging = await mkdtemp(
      path.join(requireDescriptorPath(parentHandle), `.${destinationName}.site-shell-staging-`),
    );
    staging = anchoredStaging;
    stagingHandle = await open(anchoredStaging, DIRECTORY_OPEN_FLAGS);
    const stagingCanonical = await realpath(anchoredStaging);
    staging = stagingCanonical;
    await stagingHandle.chmod(NORMALIZATION.directoryMode);
    await stagingHandle.sync();
    stagingRootStat = await stagingHandle.stat({ bigint: true });
    if (!stagingRootStat.isDirectory() || permissionMode(stagingRootStat) !== NORMALIZATION.directoryMode) {
      fail("Staging root is not a normalized real directory.");
    }
    const stagingPathStat = await lstat(staging, { bigint: true });
    if (stagingPathStat.isSymbolicLink() || !sameIdentity(stagingRootStat, stagingPathStat)) fail("Staging root path and descriptor differ.");
    if (!contained(parentCanonical, stagingCanonical) || path.dirname(stagingCanonical) !== parentCanonical) {
      fail("Staging root escaped the install parent.");
    }
    await assertDescriptorContained(parentHandle, stagingHandle, "staging root");
    await assertParentDescriptor(parentHandle, parentStat, parentCanonical);
    await assertOpenedPath(stagingHandle, stagingCanonical, "staging root");

    const directoryInventory = new Map([["", stagingRootStat]]);
    const fileInventory = new Map();
    const transactionId = randomUUID();
    const transactionRecord = {
      schemaVersion: 1,
      transactionId,
      destinationName,
      stagingName: path.basename(staging),
      stagingDev: String(stagingRootStat.dev),
      stagingIno: String(stagingRootStat.ino),
      archiveDigest: pinned.archiveDigest,
      sourceTreeDigest: pinned.sourceTreeDigest,
    };
    fileInventory.set(
      TRANSACTION_MARKER,
      await createInternalRecord(stagingHandle, TRANSACTION_MARKER, transactionRecord),
    );
    await hooks.afterRecoveryRecordCreated?.({
      staging,
      destination: absoluteDestination,
      transactionId,
    });

    await hooks.afterStagingCreated?.({ staging, destination: absoluteDestination });
    const stagingAfterHook = await lstatOrNull(staging);
    if (
      !stagingAfterHook
      || !stagingAfterHook.isDirectory()
      || stagingAfterHook.isSymbolicLink()
      || !sameIdentity(stagingRootStat, stagingAfterHook)
    ) {
      fail("Staging root changed or became a symlink before extraction.");
    }
    await assertOpenedPath(stagingHandle, stagingCanonical, "staging root");

    for (const entry of installEntries) {
      await writeStagedFile(stagingHandle, staging, entry, directoryInventory, fileInventory, hooks);
    }

    await fsyncInventoryDirectories(stagingHandle, staging, directoryInventory);
    await hooks.beforeCommit?.({ staging, destination: absoluteDestination });

    await assertSameDirectoryChain(parentChain);
    await assertParentDescriptor(parentHandle, parentStat, parentCanonical);
    if (await realpath(parent) !== parentCanonical) fail("Install parent canonical path changed before commit.");
    const finalStagingPath = await realpath(staging).catch(() => null);
    const finalStagingStat = await lstatOrNull(staging);
    if (
      finalStagingPath !== stagingCanonical
      || !finalStagingStat
      || finalStagingStat.isSymbolicLink()
      || !finalStagingStat.isDirectory()
      || !sameIdentity(finalStagingStat, stagingRootStat)
    ) {
      fail("Staging root changed before commit.");
    }

    await verifyClosedStagingTree({ stagingHandle, stagingRoot: staging, directoryInventory, fileInventory });

    // Repeat parent/root checks immediately before the kernel no-replace
    // destination reservation. mkdir(2) either creates this exact name or
    // fails with EEXIST; it never replaces even an empty attacker directory.
    await assertSameDirectoryChain(parentChain);
    await assertParentDescriptor(parentHandle, parentStat, parentCanonical);
    const rootImmediatelyBeforeReservation = await lstatOrNull(staging);
    if (
      !rootImmediatelyBeforeReservation
      || rootImmediatelyBeforeReservation.isSymbolicLink()
      || !rootImmediatelyBeforeReservation.isDirectory()
      || !sameIdentity(rootImmediatelyBeforeReservation, stagingRootStat)
    ) {
      fail("Staging root identity changed immediately before commit.");
    }
    anchoredDestination = path.join(requireDescriptorPath(parentHandle), destinationName);
    try {
      await mkdir(anchoredDestination, { mode: RESERVED_ROOT_MODE });
    } catch (error) {
      if (error?.code === "EEXIST") {
        fail(`Install destination already exists and will not be replaced: ${absoluteDestination}`);
      }
      throw error;
    }
    destinationRootStat = await lstat(anchoredDestination, { bigint: true });
    if (
      destinationRootStat.isSymbolicLink()
      || !destinationRootStat.isDirectory()
      || permissionMode(destinationRootStat) !== RESERVED_ROOT_MODE
    ) fail("Reserved install destination is not a private real directory.");
    destinationHandle = await open(anchoredDestination, DIRECTORY_OPEN_FLAGS);
    await destinationHandle.chmod(RESERVED_ROOT_MODE);
    await destinationHandle.sync();
    destinationRootStat = await destinationHandle.stat({ bigint: true });
    const reservedPathStat = await lstat(anchoredDestination, { bigint: true });
    const reservedAbsoluteStat = await lstat(absoluteDestination, { bigint: true });
    if (
      !destinationRootStat.isDirectory()
      || !sameIdentity(destinationRootStat, reservedPathStat)
      || !sameIdentity(destinationRootStat, reservedAbsoluteStat)
      || permissionMode(destinationRootStat) !== RESERVED_ROOT_MODE
    ) {
      fail("Reserved install destination identity mismatch.");
    }
    await assertOpenedPath(destinationHandle, absoluteDestination, "reserved install destination");
    await assertDescriptorContained(parentHandle, destinationHandle, "reserved install destination");

    fileInventory.set(
      DESTINATION_RECORD,
      await createInternalRecord(stagingHandle, DESTINATION_RECORD, {
        transactionId,
        destinationDev: String(destinationRootStat.dev),
        destinationIno: String(destinationRootStat.ino),
      }),
    );
    await hooks.afterDestinationReserved?.({
      staging,
      destination: absoluteDestination,
      transactionId,
    });

    await assertSameDirectoryChain(parentChain);
    await assertParentDescriptor(parentHandle, parentStat, parentCanonical);
    await verifyClosedStagingTree({ stagingHandle, stagingRoot: staging, directoryInventory, fileInventory });
    const reservedAfterHook = await lstatOrNull(anchoredDestination);
    const reservedDescriptorAfterHook = await destinationHandle.stat({ bigint: true });
    if (
      !reservedAfterHook
      || reservedAfterHook.isSymbolicLink()
      || !reservedAfterHook.isDirectory()
      || !sameIdentity(reservedAfterHook, destinationRootStat)
      || !sameIdentity(reservedDescriptorAfterHook, destinationRootStat)
      || permissionMode(reservedDescriptorAfterHook) !== RESERVED_ROOT_MODE
    ) {
      fail("Reserved install destination changed before population.");
    }

    const destinationInventory = await transferStagingTreeToReservedDestination({
      stagingHandle,
      stagingRoot: staging,
      destinationHandle,
      destinationRoot: absoluteDestination,
      directoryInventory,
      fileInventory,
    });
    await fsyncInventoryDirectories(
      destinationHandle,
      absoluteDestination,
      destinationInventory.directoryInventory,
      RESERVED_ROOT_MODE,
    );
    await verifyClosedStagingTree({
      stagingHandle: destinationHandle,
      stagingRoot: absoluteDestination,
      directoryInventory: destinationInventory.directoryInventory,
      fileInventory: destinationInventory.fileInventory,
      rootMode: RESERVED_ROOT_MODE,
    });
    await hooks.afterDestinationPopulated?.({ staging, destination: absoluteDestination, transactionId });
    await hooks.beforeVisibilityCommit?.({ staging, destination: absoluteDestination, transactionId });
    await verifyClosedStagingTree({
      stagingHandle: destinationHandle,
      stagingRoot: absoluteDestination,
      directoryInventory: destinationInventory.directoryInventory,
      fileInventory: destinationInventory.fileInventory,
      rootMode: RESERVED_ROOT_MODE,
    });

    await assertSameDirectoryChain(parentChain);
    await assertParentDescriptor(parentHandle, parentStat, parentCanonical);
    const rootImmediatelyBeforeVisibility = await lstatOrNull(anchoredDestination);
    const descriptorImmediatelyBeforeVisibility = await destinationHandle.stat({ bigint: true });
    if (
      !rootImmediatelyBeforeVisibility
      || rootImmediatelyBeforeVisibility.isSymbolicLink()
      || !rootImmediatelyBeforeVisibility.isDirectory()
      || !sameIdentity(rootImmediatelyBeforeVisibility, destinationRootStat)
      || !sameIdentity(descriptorImmediatelyBeforeVisibility, destinationRootStat)
      || permissionMode(descriptorImmediatelyBeforeVisibility) !== RESERVED_ROOT_MODE
    ) fail("Reserved install destination changed before visibility commit.");

    const readyRecord = {
      schemaVersion: 2,
      transactionId,
      archiveDigest: pinned.archiveDigest,
      sourceTreeDigest: pinned.sourceTreeDigest,
      contractVersion: pinned.manifest.contract.version,
      installedFileCount: installEntries.length,
      manifestDigest,
      installedTreeDigest: expectedInstalledTreeDigest,
      destinationDev: String(destinationRootStat.dev),
      destinationIno: String(destinationRootStat.ino),
    };
    const readyTemporaryName = `.site-shell-ready-${transactionId}.tmp`;
    const readyExpected = await createInternalRecord(
      stagingHandle,
      readyTemporaryName,
      readyRecord,
      READY_RECORD_MODE,
    );
    await hooks.afterReadyRecordPrepared?.({ staging, destination: absoluteDestination, transactionId });

    // Normalize the root before readiness is exposed. A same-uid reader can
    // traverse 0700, so mode is deliberately not the acceptance signal.
    await destinationHandle.chmod(NORMALIZATION.directoryMode);
    await destinationHandle.sync();
    await hooks.afterRootNormalized?.({ staging, destination: absoluteDestination, transactionId });

    await verifyClosedStagingTree({
      stagingHandle: destinationHandle,
      stagingRoot: absoluteDestination,
      directoryInventory: destinationInventory.directoryInventory,
      fileInventory: destinationInventory.fileInventory,
      rootMode: NORMALIZATION.directoryMode,
    });
    const readySource = requireDescriptorPath(stagingHandle, readyTemporaryName);
    const readyDestination = requireDescriptorPath(destinationHandle, SITE_SHELL_READY_RECORD);
    try {
      await link(readySource, readyDestination);
    } catch (error) {
      if (error?.code === "EEXIST") fail("Sealed site-shell readiness record already exists.");
      throw error;
    }
    const readyLinked = await lstat(readyDestination, { bigint: true });
    if (
      readyLinked.isSymbolicLink()
      || !readyLinked.isFile()
      || !sameIdentity(readyLinked, readyExpected.stat)
      || readyLinked.nlink !== 2n
    ) fail("Linked site-shell readiness record identity is invalid.");
    await hooks.afterReadyRecordLinked?.({ staging, destination: absoluteDestination, transactionId });
    await unlink(readySource);
    await stagingHandle.sync();
    await destinationHandle.sync();
    const readyFinal = await lstat(readyDestination, { bigint: true });
    if (
      readyFinal.isSymbolicLink()
      || !readyFinal.isFile()
      || !sameIdentity(readyFinal, readyExpected.stat)
      || readyFinal.nlink !== 1n
      || permissionMode(readyFinal) !== READY_RECORD_MODE
    ) fail("Sealed site-shell readiness record did not settle as a single-link regular file.");
    destinationInventory.fileInventory.set(SITE_SHELL_READY_RECORD, {
      ...readyExpected,
      stat: readyFinal,
      internal: false,
    });
    const sealedReady = await readInternalRecord(destinationHandle, SITE_SHELL_READY_RECORD, READY_RECORD_MODE);
    if (!validReadyRecord(sealedReady, {
      archiveDigest: pinned.archiveDigest,
      sourceTreeDigest: pinned.sourceTreeDigest,
      contractVersion: pinned.manifest.contract.version,
      installedFileCount: installEntries.length,
      manifestDigest,
      expectedInstalledTreeDigest,
      destinationStat: destinationRootStat,
    })) fail("Sealed site-shell readiness record failed final validation.");

    // The nlink 2 -> 1 transition above is the logical commit: consumers must
    // require this exact, stable, single-link record before using the tree.
    committed = true;
    await hooks.afterReadyRecordSealed?.({ staging, destination: absoluteDestination, transactionId });
    await verifyClosedStagingTree({
      stagingHandle: destinationHandle,
      stagingRoot: absoluteDestination,
      directoryInventory: destinationInventory.directoryInventory,
      fileInventory: destinationInventory.fileInventory,
      rootMode: NORMALIZATION.directoryMode,
    });

    const installedRoot = await lstat(absoluteDestination, { bigint: true });
    const installedDescriptor = await destinationHandle.stat({ bigint: true });
    if (
      !installedRoot.isDirectory()
      || installedRoot.isSymbolicLink()
      || !sameIdentity(destinationRootStat, installedRoot)
      || !sameIdentity(installedDescriptor, installedRoot)
      || permissionMode(installedRoot) !== NORMALIZATION.directoryMode
    ) fail("No-replace install destination identity mismatch after visibility commit.");
    const installedCanonical = await realpath(absoluteDestination);
    if (installedCanonical !== absoluteDestination || !contained(parentCanonical, installedCanonical)) {
      fail("Installed destination escaped its parent.");
    }
    await assertSameDirectoryChain(parentChain);
    await assertParentDescriptor(parentHandle, parentStat, parentCanonical);

    // The destination is durable before recovery records are removed. Cleanup
    // is descriptor-bound, then the parent directory is synced once more.
    await parentHandle.sync();
    await cleanupOwnedStaging(staging, stagingHandle, stagingRootStat);
    stagingHandle = null;
    await parentHandle.sync();
    return {
      destination: absoluteDestination,
      archiveDigest: pinned.archiveDigest,
      sourceTreeDigest: pinned.sourceTreeDigest,
      contractVersion: pinned.manifest.contract.version,
      manifestDigest,
      installedTreeDigest: expectedInstalledTreeDigest,
      installedFiles: installEntries.map((entry) => entry.path),
    };
  } finally {
    if (stagingHandle) {
      await cleanupOwnedStaging(staging, stagingHandle, stagingRootStat).catch(() => {});
    }
    if (destinationHandle) {
      if (committed) await destinationHandle.close().catch(() => {});
      else await cleanupOwnedStaging(absoluteDestination, destinationHandle, destinationRootStat).catch(() => {});
    } else if (!committed && anchoredDestination && destinationRootStat) {
      const pathnameStat = await lstatOrNull(anchoredDestination).catch(() => null);
      if (
        pathnameStat
        && pathnameStat.isDirectory()
        && !pathnameStat.isSymbolicLink()
        && sameIdentity(pathnameStat, destinationRootStat)
      ) await rmdir(anchoredDestination).catch(() => {});
    }
    await parentHandle.close().catch(() => {});
  }
};
