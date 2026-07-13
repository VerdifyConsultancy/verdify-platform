import { createHash, randomUUID } from "node:crypto";
import {
  constants as fsConstants,
  copyFile,
  lstat,
  mkdir,
  open,
  readdir,
  readlink,
  realpath,
  rename,
  rm,
  rmdir,
  symlink,
  unlink,
} from "node:fs/promises";
import { hostname } from "node:os";
import path from "node:path";

import {
  LocalSiteReleaseStore,
  inventoryBuiltSite,
  validateSiteReleaseManifest,
} from "./site-release-store.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).join(",") === keys.join(",");
}

async function canonicalDirectory(root, label) {
  const absolute = path.resolve(root);
  const metadata = await lstat(absolute, { bigint: true });
  if (!metadata.isDirectory() || metadata.isSymbolicLink() || (await realpath(absolute)) !== absolute) {
    throw new Error(`${label} is not a canonical real directory`);
  }
  return absolute;
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
    if (!metadata.isFile() || metadata.nlink !== 1n) throw new Error("site cache file cannot be made durable");
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function syncManifestDirectories(root, manifest) {
  const directories = new Set([root]);
  for (const file of manifest.files) {
    let parent = path.posix.dirname(file.path);
    while (parent !== ".") {
      directories.add(path.join(root, ...parent.split("/")));
      parent = path.posix.dirname(parent);
    }
  }
  const ordered = [...directories].sort((left, right) => right.split(path.sep).length - left.split(path.sep).length);
  for (const directory of ordered) await syncDirectory(directory);
}

async function readBounded(file, maximumBytes, label) {
  const handle = await open(file, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  try {
    const before = await handle.stat({ bigint: true });
    if (!before.isFile() || before.nlink !== 1n || before.size < 1n || before.size > BigInt(maximumBytes)) {
      throw new Error(`${label} is not a bounded single-link regular file`);
    }
    const bytes = await handle.readFile();
    const after = await handle.stat({ bigint: true });
    if (after.dev !== before.dev || after.ino !== before.ino || after.size !== before.size || after.nlink !== 1n) {
      throw new Error(`${label} changed while being read`);
    }
    return bytes;
  } finally {
    await handle.close();
  }
}

async function parseCanonical(file, maximumBytes, label) {
  const bytes = await readBounded(file, maximumBytes, label);
  let document;
  try {
    document = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error(`${label} is not valid JSON`);
  }
  if (!canonicalBytes(document).equals(bytes)) throw new Error(`${label} is not canonical JSON`);
  return { document, bytes };
}

function publicRecords(inventory) {
  return inventory.files.map(({ sourcePath: _sourcePath, ...record }) => record);
}

function sameRecords(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

async function verifyTree(treeRoot, manifest) {
  const inventory = await inventoryBuiltSite(treeRoot);
  if (
    inventory.totalBytes !== manifest.totalBytes
    || inventory.files.length !== manifest.fileCount
    || !sameRecords(publicRecords(inventory), manifest.files)
  ) throw new Error("hydrated site tree does not match its closed release manifest");
}

async function writeCanonical(file, value) {
  const handle = await open(file, "wx", 0o644);
  try {
    await handle.writeFile(canonicalBytes(value));
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function materializeStoreRelease(store, releaseSha256, destination) {
  const manifest = await store.readRelease(releaseSha256);
  await mkdir(destination, { mode: 0o755 });
  for (const file of manifest.files) {
    const target = path.join(destination, ...file.path.split("/"));
    await mkdir(path.dirname(target), { recursive: true, mode: 0o755 });
    await copyFile(store.blobPath(file.sha256), target, fsConstants.COPYFILE_EXCL);
    const copied = await readBounded(target, file.bytes, "hydrated site file");
    if (copied.length !== file.bytes || sha256(copied) !== file.sha256) {
      throw new Error("hydrated site file failed byte verification");
    }
    await syncFile(target);
  }
  await verifyTree(destination, manifest);
  await syncManifestDirectories(destination, manifest);
  return manifest;
}

async function readBakedBundle(bundleRoot) {
  const root = await canonicalDirectory(bundleRoot, "baked site bundle root");
  const bundleValue = await parseCanonical(path.join(root, "bundle.json"), 64 * 1024, "baked site bundle descriptor");
  const bundle = bundleValue.document;
  if (
    !exactKeys(bundle, [
      "contract",
      "schemaVersion",
      "releaseSha256",
      "manifestSha256",
      "fileCount",
      "totalBytes",
    ])
    || bundle.contract !== "verdify.lab-baked-site-bundle"
    || bundle.schemaVersion !== 1
    || !SHA256_RE.test(bundle.releaseSha256)
    || bundle.manifestSha256 !== bundle.releaseSha256
    || !Number.isSafeInteger(bundle.fileCount)
    || !Number.isSafeInteger(bundle.totalBytes)
  ) throw new Error("baked site bundle does not use the closed v1 contract");
  const manifestValue = await parseCanonical(path.join(root, "manifest.json"), 16 * 1024 * 1024, "baked site manifest");
  if (sha256(manifestValue.bytes) !== bundle.releaseSha256) throw new Error("baked site manifest digest mismatch");
  const manifest = validateSiteReleaseManifest(manifestValue.document, manifestValue.bytes);
  if (manifest.fileCount !== bundle.fileCount || manifest.totalBytes !== bundle.totalBytes) {
    throw new Error("baked site bundle accounting mismatch");
  }
  const tree = await canonicalDirectory(path.join(root, "tree"), "baked site tree");
  await verifyTree(tree, manifest);
  return { root, tree, manifest, releaseSha256: bundle.releaseSha256 };
}

export async function createBakedSiteBundle({ storeRoot, releaseSha256, bundleRoot }) {
  if (!SHA256_RE.test(releaseSha256)) throw new Error("baked site release digest is invalid");
  const destination = path.resolve(bundleRoot);
  try {
    await lstat(destination);
    throw new Error("baked site bundle destination already exists");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const parent = await canonicalDirectory(path.dirname(destination), "baked site bundle parent");
  const temporary = path.join(parent, `.site-bundle-${randomUUID()}`);
  const store = await new LocalSiteReleaseStore(storeRoot).initialize();
  try {
    await mkdir(temporary, { mode: 0o755 });
    const tree = path.join(temporary, "tree");
    const manifest = await materializeStoreRelease(store, releaseSha256, tree);
    const manifestBytes = canonicalBytes(manifest);
    if (sha256(manifestBytes) !== releaseSha256) throw new Error("baked site release digest changed");
    await writeCanonical(path.join(temporary, "manifest.json"), manifest);
    await writeCanonical(path.join(temporary, "bundle.json"), {
      contract: "verdify.lab-baked-site-bundle",
      schemaVersion: 1,
      releaseSha256,
      manifestSha256: releaseSha256,
      fileCount: manifest.fileCount,
      totalBytes: manifest.totalBytes,
    });
    await syncDirectory(temporary);
    await rename(temporary, destination);
    await syncDirectory(parent);
    await readBakedBundle(destination);
    return { releaseSha256, bundleRoot: destination, fileCount: manifest.fileCount, totalBytes: manifest.totalBytes };
  } catch (error) {
    await rm(temporary, { recursive: true, force: true });
    throw error;
  }
}

async function copyBakedTree(bundle, destination) {
  await mkdir(destination, { mode: 0o755 });
  for (const file of bundle.manifest.files) {
    const target = path.join(destination, ...file.path.split("/"));
    await mkdir(path.dirname(target), { recursive: true, mode: 0o755 });
    await copyFile(path.join(bundle.tree, ...file.path.split("/")), target, fsConstants.COPYFILE_EXCL);
    const copied = await readBounded(target, file.bytes, "baked cache file");
    if (copied.length !== file.bytes || sha256(copied) !== file.sha256) throw new Error("baked cache file failed byte verification");
    await syncFile(target);
  }
  await verifyTree(destination, bundle.manifest);
  await syncManifestDirectories(destination, bundle.manifest);
}

function generationRelease(name) {
  return /^([0-9a-f]{64})-([0-9a-f-]{36})$/.exec(name)?.[1] ?? null;
}

async function generationReady(generationsRoot, generationName, releaseSha256, manifest) {
  const generation = path.join(generationsRoot, generationName);
  try {
    const ready = await parseCanonical(path.join(generation, ".release-ready.json"), 64 * 1024, "site cache ready record");
    if (
      !exactKeys(ready.document, ["contract", "schemaVersion", "releaseSha256"])
      || ready.document.contract !== "verdify.lab-site-cache-ready"
      || ready.document.schemaVersion !== 1
      || ready.document.releaseSha256 !== releaseSha256
    ) return false;
    await verifyTree(path.join(generation, "tree"), manifest);
    return true;
  } catch {
    return false;
  }
}

async function installGeneration({ generationsRoot, releaseSha256, manifest, materialize, testHooks }) {
  for (const name of await readdir(generationsRoot)) {
    if (generationRelease(name) === releaseSha256 && await generationReady(generationsRoot, name, releaseSha256, manifest)) {
      return name;
    }
  }
  const temporary = path.join(generationsRoot, `.candidate-${releaseSha256}-${randomUUID()}`);
  const generationName = `${releaseSha256}-${randomUUID()}`;
  const generation = path.join(generationsRoot, generationName);
  try {
    await mkdir(temporary, { mode: 0o755 });
    const tree = path.join(temporary, "tree");
    await materialize(tree);
    await verifyTree(tree, manifest);
    await writeCanonical(path.join(temporary, ".release-ready.json"), {
      contract: "verdify.lab-site-cache-ready",
      schemaVersion: 1,
      releaseSha256,
    });
    await syncDirectory(temporary);
    if (typeof testHooks?.beforeGenerationInstall === "function") await testHooks.beforeGenerationInstall();
    if (testHooks?.failAt === "beforeGenerationInstall") throw new Error("injected cache failure before generation install");
    await rename(temporary, generation);
    await syncDirectory(generationsRoot);
    return generationName;
  } catch (error) {
    await rm(temporary, { recursive: true, force: true });
    throw error;
  }
}

async function selectedGeneration(cacheRoot, name) {
  const linkPath = path.join(cacheRoot, name);
  try {
    const metadata = await lstat(linkPath);
    if (!metadata.isSymbolicLink()) throw new Error(`site cache ${name} is not a symlink`);
    const target = await readlink(linkPath);
    const match = /^generations\/([0-9a-f]{64}-[0-9a-f-]{36})$/.exec(target);
    if (!match) throw new Error(`site cache ${name} target is invalid`);
    return { name: match[1], releaseSha256: generationRelease(match[1]) };
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

async function atomicGenerationLink(cacheRoot, name, generationName) {
  const temporary = path.join(cacheRoot, `.${name}-${randomUUID()}`);
  await symlink(`generations/${generationName}`, temporary);
  await rename(temporary, path.join(cacheRoot, name));
  await syncDirectory(cacheRoot);
}

function freshness(manifest, asOf) {
  const evaluatedAt = asOf ?? new Date().toISOString();
  if (!ISO_INSTANT_RE.test(evaluatedAt) || !Number.isFinite(Date.parse(evaluatedAt))) throw new Error("cache status time is invalid");
  const elapsedSeconds = Math.floor((Date.parse(evaluatedAt) - Date.parse(manifest.event.occurredAt)) / 1000);
  if (elapsedSeconds < 0) throw new Error("cache status time precedes its event");
  return {
    completedAt: manifest.event.occurredAt,
    releasedAt: manifest.releasedAt,
    evaluatedAt,
    elapsedSeconds,
    targetSeconds: manifest.freshness.targetSeconds,
    alertAfterSeconds: manifest.freshness.alertAfterSeconds,
    status: elapsedSeconds >= manifest.freshness.alertAfterSeconds ? "alert" : elapsedSeconds > manifest.freshness.targetSeconds ? "late" : "fresh",
  };
}

async function acquireCacheLease(cacheRoot, attempts = 0) {
  if (attempts > 4) throw new Error("site cache lease recovery did not converge");
  const lockPath = path.join(cacheRoot, ".hydrate.lock");
  const tombstones = path.join(cacheRoot, ".hydrate-tombstones");
  await mkdir(tombstones, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await canonicalDirectory(tombstones, "site cache lease tombstones");
  const owner = {
    contract: "verdify.lab-local-site-cache-lease",
    schemaVersion: 1,
    hostname: hostname(),
    pid: process.pid,
    nonce: randomUUID(),
  };
  const candidate = path.join(cacheRoot, `.hydrate-candidate-${owner.nonce}`);
  try {
    await mkdir(candidate, { mode: 0o700 });
    await writeCanonical(path.join(candidate, "owner.json"), owner);
    await syncDirectory(candidate);
    await rename(candidate, lockPath);
    await syncDirectory(cacheRoot);
    return { lockPath, owner, identity: await lstat(lockPath, { bigint: true }) };
  } catch (error) {
    await rm(candidate, { recursive: true, force: true }).catch(() => {});
    if (!["EEXIST", "ENOTEMPTY"].includes(error.code)) throw error;
    let existing;
    try {
      existing = (await parseCanonical(path.join(lockPath, "owner.json"), 16 * 1024, "site cache lease")).document;
    } catch {
      throw new Error("site cache lease requires operator inspection");
    }
    if (
      !exactKeys(existing, ["contract", "schemaVersion", "hostname", "pid", "nonce"])
      || existing.contract !== owner.contract
      || existing.schemaVersion !== 1
      || existing.hostname !== owner.hostname
      || !Number.isSafeInteger(existing.pid)
      || existing.pid <= 0
      || !/^[0-9a-f-]{36}$/.test(existing.nonce)
    ) throw new Error("another local site cache hydrator is active");
    try {
      process.kill(existing.pid, 0);
      throw new Error("another local site cache hydrator is active");
    } catch (probe) {
      if (probe.code !== "ESRCH") throw probe;
    }
    try {
      await rename(lockPath, path.join(tombstones, existing.nonce));
      await syncDirectory(tombstones);
      await syncDirectory(cacheRoot);
    } catch (recoveryError) {
      if (!["ENOENT", "EEXIST", "ENOTEMPTY"].includes(recoveryError.code)) throw recoveryError;
    }
    return acquireCacheLease(cacheRoot, attempts + 1);
  }
}

async function withCacheLease(cacheRoot, callback) {
  const lease = await acquireCacheLease(cacheRoot);
  try {
    return await callback();
  } finally {
    const selected = await lstat(lease.lockPath, { bigint: true }).catch((error) => error.code === "ENOENT" ? null : Promise.reject(error));
    if (selected && selected.isDirectory() && selected.dev === lease.identity.dev && selected.ino === lease.identity.ino) {
      await unlink(path.join(lease.lockPath, "owner.json"));
      await rmdir(lease.lockPath);
      await syncDirectory(cacheRoot);
    }
  }
}

export async function hydrateSiteCache({ storeRoot, cacheRoot, bakedBundleRoot = null, asOf = null, testHooks = null }) {
  const root = await canonicalDirectory(cacheRoot, "site cache root");
  return withCacheLease(root, async () => {
  const generationsRoot = path.join(root, "generations");
  try {
    await mkdir(generationsRoot, { mode: 0o755 });
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
  }
  await canonicalDirectory(generationsRoot, "site cache generations root");

  let candidate = null;
  let storeFailure = null;
  try {
    const store = await new LocalSiteReleaseStore(storeRoot).initialize();
    const selected = await store.readSelection();
    for (const [source, pointer] of [["store-current", selected?.document.current], ["store-previous", selected?.document.previous]]) {
      if (!pointer) continue;
      try {
        const manifest = await store.readRelease(pointer.releaseSha256);
        candidate = {
          source,
          releaseSha256: pointer.releaseSha256,
          manifest,
          manifestFor: (digest) => store.readRelease(digest),
          materialize: (tree) => materializeStoreRelease(store, pointer.releaseSha256, tree),
        };
        break;
      } catch (error) {
        storeFailure = error;
      }
    }
  } catch (error) {
    storeFailure = error;
  }
  if (!candidate && bakedBundleRoot) {
    const bundle = await readBakedBundle(bakedBundleRoot);
    candidate = {
      source: "baked-known-good",
      releaseSha256: bundle.releaseSha256,
      manifest: bundle.manifest,
      manifestFor: async (digest) => {
        if (digest !== bundle.releaseSha256) throw new Error("release is absent from baked bundle");
        return bundle.manifest;
      },
      materialize: (tree) => copyBakedTree(bundle, tree),
    };
  }
  if (!candidate) throw new Error(`no verified site release is available${storeFailure ? `: ${storeFailure.message}` : ""}`);

  const oldCurrent = await selectedGeneration(root, "current");
  let oldPrevious = await selectedGeneration(root, "previous");
  if (oldPrevious) {
    try {
      const previousManifest = await candidate.manifestFor(oldPrevious.releaseSha256);
      if (!await generationReady(generationsRoot, oldPrevious.name, oldPrevious.releaseSha256, previousManifest)) {
        throw new Error("previous cache generation failed verification");
      }
    } catch {
      await unlink(path.join(root, "previous"));
      await syncDirectory(root);
      oldPrevious = null;
    }
  }
  const installedGeneration = await installGeneration({
    generationsRoot,
    ...candidate,
    testHooks,
  });
  try {
    if (typeof testHooks?.beforeCurrentSwap === "function") await testHooks.beforeCurrentSwap();
    if (testHooks?.failAt === "beforeCurrentSwap") throw new Error("injected cache failure before current swap");
  } catch (error) {
    if (installedGeneration !== oldCurrent?.name && installedGeneration !== oldPrevious?.name) {
      await rm(path.join(generationsRoot, installedGeneration), { recursive: true, force: true });
      await syncDirectory(generationsRoot);
    }
    throw error;
  }
  if (oldCurrent && oldCurrent.releaseSha256 !== candidate.releaseSha256) {
    try {
      const oldManifest = await candidate.manifestFor(oldCurrent.releaseSha256);
      if (await generationReady(generationsRoot, oldCurrent.name, oldCurrent.releaseSha256, oldManifest)) {
        await atomicGenerationLink(root, "previous", oldCurrent.name);
      }
    } catch {
      // Keep an already verified previous link rather than selecting corrupt/unavailable bytes.
    }
  }
  await atomicGenerationLink(root, "current", installedGeneration);

  const keep = new Set([installedGeneration]);
  const previous = await selectedGeneration(root, "previous");
  if (previous && previous.name !== installedGeneration) keep.add(previous.name);
  const names = await readdir(generationsRoot);
  for (const name of names) {
    if (name.startsWith(".candidate-")) {
      await rm(path.join(generationsRoot, name), { recursive: true, force: true });
    } else if (generationRelease(name) && !keep.has(name)) {
      await rm(path.join(generationsRoot, name), { recursive: true, force: true });
    } else if (!generationRelease(name)) {
      throw new Error("site cache generation membership is invalid");
    }
  }
  await syncDirectory(generationsRoot);
  const resultFreshness = freshness(candidate.manifest, asOf);
  return {
    contract: "verdify.lab-site-cache-status",
    schemaVersion: 1,
    ready: true,
    health: resultFreshness.status === "alert"
      ? "alert"
      : candidate.source === "store-current" && resultFreshness.status === "fresh"
        ? "ready"
        : "degraded",
    source: candidate.source,
    releaseSha256: candidate.releaseSha256,
    previousReleaseSha256: previous?.releaseSha256 ?? null,
    fileCount: candidate.manifest.fileCount,
    totalBytes: candidate.manifest.totalBytes,
    freshness: resultFreshness,
  };
  });
}
