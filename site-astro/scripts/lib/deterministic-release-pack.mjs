import { createHash } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import {
  lstat,
  mkdir,
  open,
  realpath,
  rm,
} from "node:fs/promises";
import path from "node:path";

const MAGIC = Buffer.from("VLABPACK", "ascii");
const FORMAT_VERSION = 1;
const HEADER_BYTES = 16;
const MAX_INDEX_BYTES = 8 * 1024 * 1024;
const MAX_FILES = 10_000;
const MAX_FILE_BYTES = 128 * 1024 * 1024;
const MAX_PAYLOAD_BYTES = 1024 * 1024 * 1024;
const MAX_PATH_BYTES = 4096;
const MAX_DEPTH = 24;
const FRAME_BYTES = 4;
const MAX_PACK_BYTES = HEADER_BYTES + MAX_INDEX_BYTES + MAX_PAYLOAD_BYTES + FRAME_BYTES * MAX_FILES;
const SAFE_SEGMENT_RE = /^[A-Za-z0-9._-]+$/u;
const SHA256_RE = /^[0-9a-f]{64}$/u;
const PACK_KINDS = new Set(["occurrence", "site"]);

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

function safeInteger(value, label, { minimum = 0, maximum = Number.MAX_SAFE_INTEGER } = {}) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function safeAdd(left, right, label) {
  return safeInteger(left + right, label);
}

function packKind(value) {
  if (typeof value !== "string" || !PACK_KINDS.has(value)) {
    throw new Error("release pack kind is invalid");
  }
  return value;
}

function safeRelativePath(value) {
  if (
    typeof value !== "string"
    || value.length === 0
    || Buffer.byteLength(value) > MAX_PATH_BYTES
    || value.startsWith("/")
    || value.endsWith("/")
    || value.includes("\\")
    || value.includes("//")
  ) {
    throw new Error("release pack path is unsafe");
  }
  const segments = value.split("/");
  if (
    segments.length > MAX_DEPTH
    || segments.some((segment) => segment === "." || segment === ".." || !SAFE_SEGMENT_RE.test(segment))
    || path.posix.normalize(value) !== value
  ) {
    throw new Error("release pack path is unsafe");
  }
  return value;
}

function validatePathSet(paths) {
  const exact = new Set();
  const folded = new Set();
  for (const value of paths) {
    safeRelativePath(value);
    if (exact.has(value)) throw new Error("release pack contains a duplicate path");
    exact.add(value);
    const identity = value.normalize("NFC").toLocaleLowerCase("en-US");
    if (folded.has(identity)) throw new Error("release pack contains a case-folded path collision");
    folded.add(identity);
  }
  for (const identity of folded) {
    const segments = identity.split("/");
    for (let length = 1; length < segments.length; length += 1) {
      if (folded.has(segments.slice(0, length).join("/"))) {
        throw new Error("release pack contains a file-directory path collision");
      }
    }
  }
}

function validateInputFile(value) {
  if (!exactKeys(value, ["path", "bytes"]) || !Buffer.isBuffer(value.bytes)) {
    throw new Error("release pack input must be a closed regular-file byte descriptor");
  }
  safeRelativePath(value.path);
  safeInteger(value.bytes.length, "release pack file byte count", { maximum: MAX_FILE_BYTES });
  return value;
}

function validateIndex(index) {
  if (!exactKeys(index, [
    "contract",
    "schemaVersion",
    "kind",
    "fileCount",
    "totalPayloadBytes",
    "entries",
  ])) {
    throw new Error("release pack index does not use the closed v1 schema");
  }
  if (
    index.contract !== "verdify.lab-deterministic-release-pack-index"
    || index.schemaVersion !== 1
  ) {
    throw new Error("release pack index does not use the closed v1 schema");
  }
  packKind(index.kind);
  safeInteger(index.fileCount, "release pack file count", { minimum: 1, maximum: MAX_FILES });
  safeInteger(index.totalPayloadBytes, "release pack payload byte count", { maximum: MAX_PAYLOAD_BYTES });
  if (!Array.isArray(index.entries) || index.entries.length !== index.fileCount) {
    throw new Error("release pack index file count is invalid");
  }
  const paths = [];
  let totalPayloadBytes = 0;
  let prior = null;
  for (const entry of index.entries) {
    if (!exactKeys(entry, ["path", "sha256", "bytes"])) {
      throw new Error("release pack index entry does not use the closed v1 schema");
    }
    safeRelativePath(entry.path);
    if (prior !== null && entry.path <= prior) {
      throw new Error("release pack index paths are not strictly sorted");
    }
    prior = entry.path;
    if (typeof entry.sha256 !== "string" || !SHA256_RE.test(entry.sha256)) {
      throw new Error("release pack index digest is invalid");
    }
    safeInteger(entry.bytes, "release pack index file byte count", { maximum: MAX_FILE_BYTES });
    totalPayloadBytes = safeAdd(totalPayloadBytes, entry.bytes, "release pack payload byte count");
    if (totalPayloadBytes > MAX_PAYLOAD_BYTES) {
      throw new Error("release pack payload exceeds its byte limit");
    }
    paths.push(entry.path);
  }
  validatePathSet(paths);
  if (totalPayloadBytes !== index.totalPayloadBytes) {
    throw new Error("release pack index payload byte count differs from its entries");
  }
  return index;
}

function parseCanonicalIndex(bytes) {
  let index;
  try {
    index = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("release pack index is not valid JSON");
  }
  if (!canonicalBytes(index).equals(bytes)) {
    throw new Error("release pack index is not canonical JSON");
  }
  return validateIndex(index);
}

function packReference({ kind, bytes, sha256: packSha256, indexSha256, fileCount, payloadBytes }) {
  const reference = {
    kind,
    key: `packs/${kind}/sha256/${packSha256}.vpack`,
    sha256: packSha256,
    indexSha256,
    bytes,
    fileCount,
    payloadBytes,
  };
  return validateDeterministicReleasePackReference(reference);
}

export function validateDeterministicReleasePackReference(value) {
  if (!exactKeys(value, [
    "kind",
    "key",
    "sha256",
    "indexSha256",
    "bytes",
    "fileCount",
    "payloadBytes",
  ])) {
    throw new Error("release pack reference does not use the closed v1 schema");
  }
  packKind(value.kind);
  if (
    typeof value.sha256 !== "string"
    || !SHA256_RE.test(value.sha256)
    || typeof value.indexSha256 !== "string"
    || !SHA256_RE.test(value.indexSha256)
    || value.key !== `packs/${value.kind}/sha256/${value.sha256}.vpack`
  ) {
    throw new Error("release pack reference identity is invalid");
  }
  safeInteger(value.bytes, "release pack reference byte count", { minimum: HEADER_BYTES + 1 });
  if (value.bytes > MAX_PACK_BYTES) throw new Error("release pack reference exceeds its byte limit");
  safeInteger(value.fileCount, "release pack reference file count", { minimum: 1, maximum: MAX_FILES });
  safeInteger(value.payloadBytes, "release pack reference payload byte count", { maximum: MAX_PAYLOAD_BYTES });
  return value;
}

export function encodeDeterministicReleasePack(input) {
  if (!exactKeys(input, ["kind", "files"])) {
    throw new Error("release pack input does not use the closed v1 shape");
  }
  const { kind, files } = input;
  packKind(kind);
  if (!Array.isArray(files) || files.length < 1 || files.length > MAX_FILES) {
    throw new Error("release pack input file count is invalid");
  }
  const sorted = files.map(validateInputFile).sort((left, right) => (
    left.path < right.path ? -1 : left.path > right.path ? 1 : 0
  ));
  validatePathSet(sorted.map(({ path: relative }) => relative));
  let totalPayloadBytes = 0;
  const entries = sorted.map((file) => {
    totalPayloadBytes = safeAdd(totalPayloadBytes, file.bytes.length, "release pack payload byte count");
    if (totalPayloadBytes > MAX_PAYLOAD_BYTES) {
      throw new Error("release pack payload exceeds its byte limit");
    }
    return {
      path: file.path,
      sha256: sha256(file.bytes),
      bytes: file.bytes.length,
    };
  });
  const index = validateIndex({
    contract: "verdify.lab-deterministic-release-pack-index",
    schemaVersion: 1,
    kind,
    fileCount: entries.length,
    totalPayloadBytes,
    entries,
  });
  const indexBytes = canonicalBytes(index);
  if (indexBytes.length > MAX_INDEX_BYTES) throw new Error("release pack index exceeds its byte limit");
  const header = Buffer.alloc(HEADER_BYTES);
  MAGIC.copy(header, 0);
  header.writeUInt16BE(FORMAT_VERSION, MAGIC.length);
  header.writeUInt16BE(0, MAGIC.length + 2);
  header.writeUInt32BE(indexBytes.length, MAGIC.length + 4);
  const chunks = [header, indexBytes];
  let packBytes = safeAdd(HEADER_BYTES, indexBytes.length, "release pack byte count");
  for (const file of sorted) {
    const frame = Buffer.alloc(FRAME_BYTES);
    frame.writeUInt32BE(file.bytes.length, 0);
    chunks.push(frame, file.bytes);
    packBytes = safeAdd(packBytes, FRAME_BYTES + file.bytes.length, "release pack byte count");
  }
  const bytes = Buffer.concat(chunks, packBytes);
  const packSha256 = sha256(bytes);
  const reference = packReference({
    kind,
    bytes: bytes.length,
    sha256: packSha256,
    indexSha256: sha256(indexBytes),
    fileCount: index.fileCount,
    payloadBytes: index.totalPayloadBytes,
  });
  return { bytes, sha256: packSha256, index, reference };
}

export function decodeDeterministicReleasePack(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length < HEADER_BYTES + 1) {
    throw new Error("release pack is truncated");
  }
  if (bytes.length > MAX_PACK_BYTES) throw new Error("release pack exceeds its byte limit");
  if (!bytes.subarray(0, MAGIC.length).equals(MAGIC)) {
    throw new Error("release pack magic is invalid");
  }
  if (bytes.readUInt16BE(MAGIC.length) !== FORMAT_VERSION) {
    throw new Error("release pack version is unsupported");
  }
  if (bytes.readUInt16BE(MAGIC.length + 2) !== 0) {
    throw new Error("release pack reserved header bits are nonzero");
  }
  const indexLength = bytes.readUInt32BE(MAGIC.length + 4);
  if (
    indexLength < 1
    || indexLength > MAX_INDEX_BYTES
    || HEADER_BYTES + indexLength > bytes.length
  ) {
    throw new Error("release pack index framing is invalid");
  }
  const indexBytes = bytes.subarray(HEADER_BYTES, HEADER_BYTES + indexLength);
  const index = parseCanonicalIndex(indexBytes);
  const files = [];
  let offset = HEADER_BYTES + indexLength;
  for (const entry of index.entries) {
    if (offset + FRAME_BYTES > bytes.length) throw new Error("release pack frame is truncated");
    const frameLength = bytes.readUInt32BE(offset);
    offset += FRAME_BYTES;
    if (frameLength !== entry.bytes || offset + frameLength > bytes.length) {
      throw new Error("release pack frame length differs from its index");
    }
    const payload = bytes.subarray(offset, offset + frameLength);
    if (sha256(payload) !== entry.sha256) {
      throw new Error("release pack frame digest differs from its index");
    }
    files.push({ path: entry.path, bytes: Buffer.from(payload) });
    offset += frameLength;
  }
  if (offset !== bytes.length) throw new Error("release pack has trailing bytes");
  const packSha256 = sha256(bytes);
  const reference = packReference({
    kind: index.kind,
    bytes: bytes.length,
    sha256: packSha256,
    indexSha256: sha256(indexBytes),
    fileCount: index.fileCount,
    payloadBytes: index.totalPayloadBytes,
  });
  return { sha256: packSha256, index, files, reference };
}

async function canonicalDirectory(value, label) {
  const absolute = path.resolve(value);
  const metadata = await lstat(absolute, { bigint: true });
  if (
    !metadata.isDirectory()
    || metadata.isSymbolicLink()
    || (await realpath(absolute)) !== absolute
  ) {
    throw new Error(`${label} is not a canonical real directory`);
  }
  return absolute;
}

async function materializationDirectory(root, segments) {
  let current = root;
  for (const segment of segments) {
    current = path.join(current, segment);
    try {
      await mkdir(current, { mode: 0o755 });
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
    }
    const metadata = await lstat(current, { bigint: true });
    if (
      !metadata.isDirectory()
      || metadata.isSymbolicLink()
      || (await realpath(current)) !== current
    ) {
      throw new Error("release pack materialization path contains a link or non-directory");
    }
  }
  return current;
}

export async function materializeDeterministicReleasePack(bytes, destination) {
  const decoded = decodeDeterministicReleasePack(bytes);
  const absolute = path.resolve(destination);
  const parent = await canonicalDirectory(path.dirname(absolute), "release pack destination parent");
  if (path.dirname(absolute) !== parent) {
    throw new Error("release pack destination parent is not canonical");
  }
  let created = false;
  try {
    await mkdir(absolute, { mode: 0o755 });
    created = true;
    if ((await realpath(absolute)) !== absolute) {
      throw new Error("release pack destination is not canonical");
    }
    for (const file of decoded.files) {
      const segments = file.path.split("/");
      const directory = await materializationDirectory(absolute, segments.slice(0, -1));
      const target = path.join(directory, segments.at(-1));
      const handle = await open(
        target,
        fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_EXCL | fsConstants.O_NOFOLLOW,
        0o644,
      );
      try {
        await handle.writeFile(file.bytes);
        await handle.sync();
        const metadata = await handle.stat({ bigint: true });
        if (!metadata.isFile() || metadata.nlink !== 1n || metadata.size !== BigInt(file.bytes.length)) {
          throw new Error("release pack materialized file is not a single-link exact file");
        }
      } finally {
        await handle.close();
      }
    }
  } catch (error) {
    if (created) await rm(absolute, { recursive: true, force: true }).catch(() => {});
    throw error;
  }
  return {
    kind: decoded.index.kind,
    packSha256: decoded.sha256,
    fileCount: decoded.index.fileCount,
    totalPayloadBytes: decoded.index.totalPayloadBytes,
    paths: decoded.index.entries.map(({ path: relative }) => relative),
  };
}

export const deterministicReleasePackContract = Object.freeze({
  magic: MAGIC.toString("ascii"),
  formatVersion: FORMAT_VERSION,
  headerBytes: HEADER_BYTES,
  frame: "uint32be-length-followed-by-exact-bytes",
  compression: "none",
  metadata: "paths-and-content-only",
  limits: Object.freeze({
    maxIndexBytes: MAX_INDEX_BYTES,
    maxFiles: MAX_FILES,
    maxFileBytes: MAX_FILE_BYTES,
    maxPayloadBytes: MAX_PAYLOAD_BYTES,
    maxPackBytes: MAX_PACK_BYTES,
    maxPathBytes: MAX_PATH_BYTES,
    maxDepth: MAX_DEPTH,
  }),
});
