import { createHash, randomUUID } from "node:crypto";
import { open as openFile, chmod, lstat, mkdir, readFile, readdir, realpath, rename, rm, unlink } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { verifySnapshot } from "./lib/snapshot.mjs";
import { PRODUCTION_APPROVAL_REGISTRY, PRODUCTION_RELEASE_CONTRACT } from "./lib/production-approval.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SHA256_RE = /^[0-9a-f]{64}$/;
const RELEASE_KEYS = [
  "contract",
  "schemaVersion",
  "assetUrl",
  "assetSha256",
  "assetBytes",
  "assetFormat",
  "attestationSha256",
  "sanitizedManifestSha256",
  "sourceManifestSha256",
  "fileCount",
];
// The production descriptor adds the approval binding. It carries no hard-coded
// content pins: every pin must match a reviewed approval-registry entry, so an
// empty registry makes every production descriptor unreadable — fail-closed at
// download time, before a single byte is fetched.
const PRODUCTION_RELEASE_KEYS = [
  "contract",
  "schemaVersion",
  "assetUrl",
  "assetSha256",
  "assetBytes",
  "assetFormat",
  "attestationSha256",
  "approvalSha256",
  "approvalId",
  "sanitizedManifestSha256",
  "sourceManifestSha256",
  "fileCount",
];
const MAX_DESCRIPTOR_BYTES = 64 * 1024;
const MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024;
const MAX_ENTRY_BYTES = 128 * 1024 * 1024;
const MAX_ENTRIES = 10_000;
const TAR_BLOCK = 512;
const DOWNLOAD_HOSTS = new Set(["github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"]);
const textDecoder = new TextDecoder("utf-8", { fatal: true });

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).join(",") === keys.join(",");
}

async function readRegular(file, maximumBytes) {
  const metadata = await lstat(file);
  if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.nlink !== 1 || metadata.size > maximumBytes) {
    throw new Error("release input must be a bounded single-link regular file");
  }
  return readFile(file);
}

function validateGithubUrl(value, { initial = false } = {}) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("snapshot asset URL is invalid");
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || !DOWNLOAD_HOSTS.has(parsed.hostname)) {
    throw new Error("snapshot asset URL must be credential-free HTTPS on a GitHub release host");
  }
  if (
    initial
    && (
      parsed.hostname !== "github.com"
      || !/^\/VerdifyConsultancy\/verdify-platform\/releases\/download\/[^/]+\/[^/]+\.tar$/.test(parsed.pathname)
      || parsed.search
      || parsed.hash
    )
  ) {
    throw new Error("snapshot descriptor must name the canonical GitHub release asset URL");
  }
  return parsed;
}

function releaseTagFromAssetUrl(assetUrl) {
  return decodeURIComponent(new URL(assetUrl).pathname.split("/")[5] ?? "");
}

/**
 * Bind a production release descriptor to the reviewed approval registry.
 *
 * Every pinned value must equal the registered approval. There is no
 * hard-coded fallback and no permissive mode: with the shipped (empty)
 * registry this always throws.
 */
function bindProductionRelease(release, registry) {
  const matches = registry.filter((entry) => entry.approvalId === release.approvalId);
  if (matches.length !== 1) {
    throw new Error("production snapshot release descriptor is not in the reviewed approval registry");
  }
  const entry = matches[0];
  if (
    release.assetSha256 !== entry.assetSha256
    || release.approvalSha256 !== entry.approvalSha256
    || release.attestationSha256 !== entry.snapshotAttestationSha256
    || release.sanitizedManifestSha256 !== entry.sanitizedManifestSha256
    || release.sourceManifestSha256 !== entry.sourceManifestSha256
    || release.fileCount !== entry.sanitizedFileCount
    || releaseTagFromAssetUrl(release.assetUrl) !== entry.releaseTag
  ) {
    throw new Error("production snapshot release descriptor disagrees with its registered approval");
  }
  return release;
}

export async function readReleaseDescriptor(file, { allowPlaceholders = false, registry = PRODUCTION_APPROVAL_REGISTRY } = {}) {
  const bytes = await readRegular(file, MAX_DESCRIPTOR_BYTES);
  let release;
  try {
    release = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("snapshot release descriptor is not valid JSON");
  }
  const production = release?.contract === PRODUCTION_RELEASE_CONTRACT;
  const keys = production ? PRODUCTION_RELEASE_KEYS : RELEASE_KEYS;
  if (!exactKeys(release, keys) || `${JSON.stringify(release, null, 2)}\n` !== bytes.toString("utf8")) {
    throw new Error("snapshot release descriptor must use the closed canonical v1 shape");
  }
  validateGithubUrl(release.assetUrl, { initial: true });
  if (production) {
    if (
      release.schemaVersion !== 1
      || !SHA256_RE.test(release.assetSha256)
      || !Number.isSafeInteger(release.assetBytes)
      || release.assetBytes < 1
      || release.assetBytes > MAX_ARCHIVE_BYTES
      || release.assetFormat !== "tar"
      || !SHA256_RE.test(release.attestationSha256)
      || !SHA256_RE.test(release.approvalSha256)
      || !SHA256_RE.test(release.sanitizedManifestSha256)
      || !SHA256_RE.test(release.sourceManifestSha256)
      || typeof release.approvalId !== "string"
      || !Number.isSafeInteger(release.fileCount)
      || release.fileCount < 1
      || release.fileCount > MAX_ENTRIES
    ) {
      throw new Error("production snapshot release descriptor violates the approved release contract");
    }
    return bindProductionRelease(release, registry);
  }
  if (
    release.contract !== "verdify.lab-stage-snapshot-release"
    || release.schemaVersion !== 1
    || !SHA256_RE.test(release.assetSha256)
    || !Number.isSafeInteger(release.assetBytes)
    || release.assetBytes < 0
    || release.assetBytes > MAX_ARCHIVE_BYTES
    || release.assetFormat !== "tar"
    || !SHA256_RE.test(release.attestationSha256)
    || !SHA256_RE.test(release.sanitizedManifestSha256)
    || !SHA256_RE.test(release.sourceManifestSha256)
    || release.sourceManifestSha256 !== "05d4373ebf59bef3a7899c5e94514971d663fd7264db09b2b5cb26fec78410b1"
    || release.fileCount !== 429
  ) {
    throw new Error("snapshot release descriptor violates the stage contract");
  }
  if (
    !allowPlaceholders
    && (
      release.assetBytes === 0
      || /^0+$/.test(release.assetSha256)
      || /^0+$/.test(release.attestationSha256)
      || release.assetUrl.includes("REPLACE_ME")
    )
  ) {
    throw new Error("snapshot release descriptor still contains unresolved pins");
  }
  return release;
}

async function fetchWithBoundedRedirects(url) {
  let current = validateGithubUrl(url, { initial: true });
  for (let redirects = 0; redirects <= 5; redirects += 1) {
    const response = await fetch(current, {
      redirect: "manual",
      signal: AbortSignal.timeout(15 * 60 * 1000),
      headers: {
        Accept: "application/octet-stream",
        "User-Agent": "verdify-lab-stage-snapshot-hydrator/1",
      },
    });
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      if (redirects === 5) throw new Error("snapshot asset exceeded the redirect limit");
      const location = response.headers.get("location");
      if (!location) throw new Error("snapshot asset redirect lacks a location");
      current = validateGithubUrl(new URL(location, current).toString());
      continue;
    }
    if (response.status !== 200 || !response.body) {
      throw new Error(`snapshot asset request failed with HTTP ${response.status}`);
    }
    const contentEncoding = response.headers.get("content-encoding");
    if (contentEncoding !== null && contentEncoding !== "identity") {
      throw new Error("snapshot asset response uses unsupported content encoding");
    }
    return response;
  }
  throw new Error("snapshot asset redirect loop");
}

function sameIdentity(metadata, identity, kind) {
  return metadata.dev === identity.dev
    && metadata.ino === identity.ino
    && (kind === "file" ? metadata.nlink === 1n && metadata.isFile() : metadata.isDirectory())
    && !metadata.isSymbolicLink();
}

export async function unlinkIfIdentity(target, identity) {
  try {
    const metadata = await lstat(target, { bigint: true });
    if (!sameIdentity(metadata, identity, "file")) return false;
    await unlink(target);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

export async function removeDirectoryIfIdentity(target, identity) {
  try {
    const metadata = await lstat(target, { bigint: true });
    if (!sameIdentity(metadata, identity, "directory")) return false;
    await rm(target, { recursive: true, force: false });
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

async function downloadVerifiedAsset(release, destination) {
  const response = await fetchWithBoundedRedirects(release.assetUrl);
  const declaredLength = response.headers.get("content-length");
  if (declaredLength !== null && (!/^(?:0|[1-9][0-9]*)$/.test(declaredLength) || Number(declaredLength) !== release.assetBytes)) {
    throw new Error("snapshot asset Content-Length does not match the release descriptor");
  }
  const output = await openFile(destination, "wx", 0o600);
  const identity = await output.stat({ bigint: true });
  const digest = createHash("sha256");
  let bytes = 0;
  try {
    for await (const chunk of response.body) {
      const buffer = Buffer.from(chunk);
      bytes += buffer.length;
      if (bytes > release.assetBytes || bytes > MAX_ARCHIVE_BYTES) {
        throw new Error("snapshot asset exceeded its exact byte cap");
      }
      digest.update(buffer);
      await output.write(buffer);
    }
    await output.sync();
    if (bytes !== release.assetBytes || digest.digest("hex") !== release.assetSha256) {
      throw new Error("snapshot asset byte count or SHA-256 does not match the release descriptor");
    }
    return identity;
  } catch (error) {
    await output.close().catch(() => {});
    await unlinkIfIdentity(destination, identity).catch(() => {});
    throw error;
  } finally {
    await output.close().catch(() => {});
  }
}

function tarText(bytes, label) {
  const nul = bytes.indexOf(0);
  const payload = bytes.subarray(0, nul === -1 ? bytes.length : nul);
  if (nul !== -1 && bytes.subarray(nul).some((value) => value !== 0)) throw new Error(`tar ${label} has bytes after NUL`);
  let value;
  try {
    value = textDecoder.decode(payload);
  } catch {
    throw new Error(`tar ${label} is not UTF-8`);
  }
  if (/[\u0000-\u001f\u007f\\]/u.test(value)) throw new Error(`tar ${label} contains unsafe characters`);
  return value;
}

function tarOctal(bytes, label) {
  if (bytes[0] & 0x80) throw new Error(`tar ${label} uses unsupported base-256 encoding`);
  const text = Buffer.from(bytes).toString("ascii").replace(/\0.*$/s, "").trim();
  if (!text) return 0;
  if (!/^[0-7]+$/.test(text)) throw new Error(`tar ${label} is not canonical octal`);
  const value = Number.parseInt(text, 8);
  if (!Number.isSafeInteger(value)) throw new Error(`tar ${label} is outside the safe integer range`);
  return value;
}

function parseTarHeader(header) {
  const checksum = tarOctal(header.subarray(148, 156), "checksum");
  let actual = 0;
  for (let index = 0; index < header.length; index += 1) {
    actual += index >= 148 && index < 156 ? 32 : header[index];
  }
  if (checksum !== actual) throw new Error("tar header checksum mismatch");
  if (header.subarray(257, 263).toString("binary") !== "ustar\0" || header.subarray(263, 265).toString("ascii") !== "00") {
    throw new Error("snapshot archive must use POSIX ustar headers");
  }
  const name = tarText(header.subarray(0, 100), "name");
  const prefix = tarText(header.subarray(345, 500), "prefix");
  const rawPath = prefix ? `${prefix}/${name}` : name;
  const typeByte = header[156];
  const type = typeByte === 0 || typeByte === 48 ? "file" : typeByte === 53 ? "directory" : "unsupported";
  const size = tarOctal(header.subarray(124, 136), "size");
  if (type === "unsupported") throw new Error("snapshot archive contains a non-regular/non-directory tar entry");
  if (type === "directory" && size !== 0) throw new Error("tar directory entry has a nonzero size");
  if (type === "file" && size > MAX_ENTRY_BYTES) throw new Error("tar entry exceeds the per-file byte cap");
  if (header.subarray(157, 257).some((value) => value !== 0)) throw new Error("tar link target must be empty");
  return { rawPath, type, size };
}

function safePayloadPath(rawPath, type, allowApprovalRecord = false) {
  const metadataFiles = allowApprovalRecord
    ? ["manifests/content.json", "evidence/public-output-guard.json", "attestation.json", "approval.json"]
    : ["manifests/content.json", "evidence/public-output-guard.json", "attestation.json"];
  let relative = rawPath;
  if (type === "directory") {
    if (relative.endsWith("//")) throw new Error("tar entry has an unsafe path");
    if (relative.endsWith("/")) relative = relative.slice(0, -1);
  }
  if (!relative || relative.length > 4096 || relative.startsWith("/") || relative.includes("//")) {
    throw new Error("tar entry has an unsafe path");
  }
  const normalized = path.posix.normalize(relative);
  if (normalized !== relative || normalized === "." || normalized === ".." || normalized.startsWith("../")) {
    throw new Error("tar entry path traverses the snapshot root");
  }
  const allowed = relative === "content"
    || relative.startsWith("content/")
    || relative === "manifests"
    || relative === "evidence"
    || metadataFiles.includes(relative);
  if (!allowed) throw new Error("tar entry is outside the closed snapshot payload layout");
  if (type === "file" && !relative.startsWith("content/") && !metadataFiles.includes(relative)) {
    throw new Error("tar file occupies a directory-only payload path");
  }
  if (type === "directory" && !["content", "manifests", "evidence"].includes(relative) && !relative.startsWith("content/")) {
    throw new Error("tar directory is outside the closed snapshot payload layout");
  }
  return relative;
}

async function readExact(handle, length, position) {
  const buffer = Buffer.alloc(length);
  let offset = 0;
  while (offset < length) {
    const result = await handle.read(buffer, offset, length - offset, position + offset);
    if (result.bytesRead === 0) throw new Error("snapshot archive is truncated");
    offset += result.bytesRead;
  }
  return buffer;
}

export async function extractVerifiedTar(
  archive,
  destination,
  { expectedContentFiles = 429, allowApprovalRecord = false } = {},
) {
  const archiveMetadata = await lstat(archive, { bigint: true });
  if (!archiveMetadata.isFile() || archiveMetadata.isSymbolicLink() || archiveMetadata.nlink !== 1n) {
    throw new Error("snapshot archive must be a single-link regular file");
  }
  if (archiveMetadata.size <= 0n || archiveMetadata.size > BigInt(MAX_ARCHIVE_BYTES) || archiveMetadata.size % 512n !== 0n) {
    throw new Error("snapshot archive size is invalid");
  }
  await mkdir(destination, { recursive: false, mode: 0o700 });
  const destinationIdentity = await lstat(destination, { bigint: true });
  const archiveHandle = await openFile(archive, "r");
  const paths = new Map();
  const folded = new Map();
  let position = 0;
  let zeroBlocks = 0;
  let contentFiles = 0;
  let entryCount = 0;
  try {
    while (position < Number(archiveMetadata.size)) {
      const header = await readExact(archiveHandle, TAR_BLOCK, position);
      position += TAR_BLOCK;
      if (header.every((value) => value === 0)) {
        zeroBlocks += 1;
        if (zeroBlocks < 2) continue;
        while (position < Number(archiveMetadata.size)) {
          const trailing = await readExact(archiveHandle, TAR_BLOCK, position);
          position += TAR_BLOCK;
          if (!trailing.every((value) => value === 0)) throw new Error("tar contains data after its end marker");
        }
        break;
      }
      if (zeroBlocks !== 0) throw new Error("tar has a partial end marker");
      const entry = parseTarHeader(header);
      const relative = safePayloadPath(entry.rawPath, entry.type, allowApprovalRecord);
      entryCount += 1;
      if (entryCount > MAX_ENTRIES) throw new Error("snapshot archive contains too many entries");
      const parts = relative.split("/");
      for (let index = 1; index < parts.length; index += 1) {
        const ancestor = parts.slice(0, index).join("/");
        const ancestorCaseKey = ancestor.normalize("NFC").toLocaleLowerCase("en-US");
        if (paths.get(ancestor) === "file") throw new Error("tar contains a file/directory prefix collision");
        if (paths.has(ancestor)) continue;
        if (folded.has(ancestorCaseKey)) throw new Error("tar contains a case-folded path collision");
        paths.set(ancestor, "directory");
        folded.set(ancestorCaseKey, ancestor);
      }
      if (paths.has(relative)) throw new Error("tar contains a duplicate path");
      const caseKey = relative.normalize("NFC").toLocaleLowerCase("en-US");
      if (folded.has(caseKey)) throw new Error("tar contains a case-folded path collision");
      for (const [existing, existingType] of paths) {
        if ((relative.startsWith(`${existing}/`) && existingType === "file") || (existing.startsWith(`${relative}/`) && entry.type === "file")) {
          throw new Error("tar contains a file/directory prefix collision");
        }
      }
      paths.set(relative, entry.type);
      folded.set(caseKey, relative);

      const output = path.join(destination, ...relative.split("/"));
      try {
        if (entry.type === "directory") {
          await mkdir(output, { recursive: false, mode: 0o755 });
        } else {
          const parent = path.dirname(output);
          await mkdir(parent, { recursive: true, mode: 0o755 });
          const file = await openFile(output, "wx", 0o600);
          try {
            let remaining = entry.size;
            while (remaining > 0) {
              const length = Math.min(1024 * 1024, remaining);
              const chunk = await readExact(archiveHandle, length, position + entry.size - remaining);
              await file.write(chunk);
              remaining -= length;
            }
            await file.sync();
          } finally {
            await file.close();
          }
          await chmod(output, 0o644);
          if (relative.startsWith("content/")) contentFiles += 1;
        }
      } catch {
        throw new Error("tar entry could not be materialized safely");
      }
      const paddedSize = Math.ceil(entry.size / TAR_BLOCK) * TAR_BLOCK;
      const paddingSize = paddedSize - entry.size;
      if (paddingSize > 0) {
        const padding = await readExact(archiveHandle, paddingSize, position + entry.size);
        if (!padding.every((value) => value === 0)) throw new Error("tar entry has nonzero data padding");
      }
      position += paddedSize;
      if (position > Number(archiveMetadata.size)) throw new Error("tar entry extends beyond the archive");
    }
    if (zeroBlocks !== 2) throw new Error("tar archive lacks its two-block end marker");
    if (contentFiles !== expectedContentFiles) throw new Error("tar content file count does not match the release descriptor");
    const requiredFiles = ["manifests/content.json", "attestation.json", "evidence/public-output-guard.json"];
    if (allowApprovalRecord) requiredFiles.push("approval.json");
    for (const required of requiredFiles) {
      if (paths.get(required) !== "file") throw new Error(`tar payload lacks required file: ${required}`);
    }
    const finalMetadata = await archiveHandle.stat({ bigint: true });
    if (
      finalMetadata.dev !== archiveMetadata.dev
      || finalMetadata.ino !== archiveMetadata.ino
      || finalMetadata.size !== archiveMetadata.size
      || finalMetadata.mtimeNs !== archiveMetadata.mtimeNs
      || finalMetadata.nlink !== 1n
    ) {
      throw new Error("snapshot archive changed during extraction");
    }
    await chmod(destination, 0o755);
    return { contentFiles, entries: new Map(paths) };
  } catch (error) {
    await archiveHandle.close().catch(() => {});
    await removeDirectoryIfIdentity(destination, destinationIdentity).catch(() => {});
    throw error;
  } finally {
    await archiveHandle.close().catch(() => {});
  }
}

async function sha256(file) {
  return createHash("sha256").update(await readFile(file)).digest("hex");
}

function cleanGuardReport(report) {
  return exactKeys(report, ["findings", "missing_roots", "roots", "routes", "schema_version"])
    && report.schema_version === 2
    && Array.isArray(report.roots)
    && report.roots.length === 1
    && exactKeys(report.roots[0], ["identity", "label"])
    && SHA256_RE.test(report.roots[0].identity)
    && report.roots[0].label === "content"
    && Array.isArray(report.missing_roots)
    && report.missing_roots.length === 0
    && Array.isArray(report.routes)
    && report.routes.length === 0
    && Array.isArray(report.findings)
    && report.findings.length === 0;
}

export async function verifyHydratedSnapshot(snapshotRoot, release) {
  const production = release.contract === PRODUCTION_RELEASE_CONTRACT;
  const top = (await readdir(snapshotRoot)).sort();
  const expectedTop = production
    ? ["approval.json", "attestation.json", "content", "evidence", "manifests"]
    : ["attestation.json", "content", "evidence", "manifests"];
  if (JSON.stringify(top) !== JSON.stringify(expectedTop)) {
    throw new Error("hydrated snapshot has unexpected top-level members");
  }
  if (JSON.stringify((await readdir(path.join(snapshotRoot, "manifests"))).sort()) !== JSON.stringify(["content.json"])) {
    throw new Error("hydrated snapshot has unexpected manifest members");
  }
  if (JSON.stringify((await readdir(path.join(snapshotRoot, "evidence"))).sort()) !== JSON.stringify(["public-output-guard.json"])) {
    throw new Error("hydrated snapshot has unexpected evidence members");
  }
  const attestationPath = path.join(snapshotRoot, "attestation.json");
  const manifestPath = path.join(snapshotRoot, "manifests", "content.json");
  const evidencePath = path.join(snapshotRoot, "evidence", "public-output-guard.json");
  if ((await sha256(attestationPath)) !== release.attestationSha256) throw new Error("attestation SHA-256 does not match the release descriptor");
  if ((await sha256(manifestPath)) !== release.sanitizedManifestSha256) throw new Error("sanitized manifest SHA-256 does not match the release descriptor");
  if (production && (await sha256(path.join(snapshotRoot, "approval.json"))) !== release.approvalSha256) {
    throw new Error("approval SHA-256 does not match the release descriptor");
  }
  const snapshot = await verifySnapshot(snapshotRoot);
  if (
    snapshot.files.size !== release.fileCount
    || snapshot.sanitization.sourceManifestSha256 !== release.sourceManifestSha256
    || snapshot.sanitization.sanitizedManifestSha256 !== release.sanitizedManifestSha256
    || snapshot.approvalEligible !== production
  ) {
    throw new Error("hydrated snapshot does not match descriptor content pins");
  }
  const evidenceBytes = await readRegular(evidencePath, 8 * 1024 * 1024);
  if (createHash("sha256").update(evidenceBytes).digest("hex") !== snapshot.sanitization.guardReportSha256) {
    throw new Error("public-output guard evidence SHA-256 does not match the attestation");
  }
  let report;
  try {
    report = JSON.parse(evidenceBytes.toString("utf8"));
  } catch {
    throw new Error("public-output guard evidence is not valid JSON");
  }
  if (!cleanGuardReport(report)) throw new Error("public-output guard evidence is not a closed zero-finding v2 report");
  return snapshot;
}

async function pathMustBeAbsent(target, label) {
  try {
    await lstat(target);
  } catch (error) {
    if (error.code === "ENOENT") return;
    throw error;
  }
  throw new Error(`${label} must be absent`);
}

export async function hydrateStageSnapshot(releasePath, destination) {
  const release = await readReleaseDescriptor(releasePath);
  const resolvedDestination = path.resolve(destination);
  if (path.basename(resolvedDestination) !== ".snapshot") throw new Error("snapshot destination must be named .snapshot");
  const parent = path.dirname(resolvedDestination);
  const parentMetadata = await lstat(parent);
  if (!parentMetadata.isDirectory() || parentMetadata.isSymbolicLink() || (await realpath(parent)) !== parent) {
    throw new Error("snapshot destination parent must be a real directory");
  }
  await pathMustBeAbsent(resolvedDestination, "snapshot destination");
  const token = randomUUID();
  const archive = path.join(parent, `.snapshot.asset-${token}.tar`);
  const staged = path.join(parent, `.snapshot.hydrate-${token}`);
  const lockPath = path.join(parent, ".snapshot.hydrate.lock");
  const lock = await openFile(lockPath, "wx", 0o600);
  const lockIdentity = await lock.stat({ bigint: true });
  let archiveIdentity;
  let stagedIdentity;
  let selectedIdentity;
  let selectedCommitted = false;
  try {
    archiveIdentity = await downloadVerifiedAsset(release, archive);
    await extractVerifiedTar(archive, staged, {
      expectedContentFiles: release.fileCount,
      allowApprovalRecord: release.contract === PRODUCTION_RELEASE_CONTRACT,
    });
    stagedIdentity = await lstat(staged, { bigint: true });
    await verifyHydratedSnapshot(staged, release);
    const stableStaged = await lstat(staged, { bigint: true });
    if (!sameIdentity(stableStaged, stagedIdentity, "directory")) throw new Error("staged snapshot identity changed before selection");
    await pathMustBeAbsent(resolvedDestination, "snapshot destination");
    selectedIdentity = stagedIdentity;
    await rename(staged, resolvedDestination);
    stagedIdentity = undefined;
    const selected = await lstat(resolvedDestination, { bigint: true });
    if (!sameIdentity(selected, selectedIdentity, "directory")) {
      throw new Error("atomically selected snapshot identity changed");
    }
    await verifyHydratedSnapshot(resolvedDestination, release);
    selectedCommitted = true;
  } finally {
    await lock.close().catch(() => {});
    await unlinkIfIdentity(lockPath, lockIdentity).catch(() => {});
    if (archiveIdentity) await unlinkIfIdentity(archive, archiveIdentity).catch(() => {});
    if (stagedIdentity) await removeDirectoryIfIdentity(staged, stagedIdentity).catch(() => {});
    if (!selectedCommitted && selectedIdentity) {
      await removeDirectoryIfIdentity(resolvedDestination, selectedIdentity).catch(() => {});
    }
  }
}

function parseArgs(args) {
  if (args.length !== 4) throw new Error("Usage: node scripts/fetch-stage-snapshot.mjs --release DESCRIPTOR --destination .snapshot");
  const values = {};
  for (let index = 0; index < args.length; index += 2) {
    const flag = args[index];
    if (!["--release", "--destination"].includes(flag) || !args[index + 1] || values[flag]) {
      throw new Error("Usage: node scripts/fetch-stage-snapshot.mjs --release DESCRIPTOR --destination .snapshot");
    }
    values[flag] = args[index + 1];
  }
  if (!values["--release"] || !values["--destination"]) throw new Error("release and destination are required");
  return values;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = parseArgs(process.argv.slice(2));
  hydrateStageSnapshot(path.resolve(ROOT, args["--release"]), path.resolve(ROOT, args["--destination"]))
    .then(() => process.stdout.write("hydrated and verified .snapshot\n"))
    .catch((error) => {
      process.stderr.write(`fetch-stage-snapshot: ${error.message}\n`);
      process.exitCode = 1;
    });
}
