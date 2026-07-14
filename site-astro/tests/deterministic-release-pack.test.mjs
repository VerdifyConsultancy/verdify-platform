import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  lstat,
  mkdtemp,
  readFile,
  readdir,
  rm,
  symlink,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";

import {
  decodeDeterministicReleasePack,
  deterministicReleasePackContract,
  encodeDeterministicReleasePack,
  materializeDeterministicReleasePack,
} from "../scripts/lib/deterministic-release-pack.mjs";
import { validatePngFile } from "../scripts/lib/png-validation.mjs";

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function rebuildWithIndex(pack, indexBytes) {
  const header = Buffer.from(pack.subarray(0, deterministicReleasePackContract.headerBytes));
  const oldIndexLength = pack.readUInt32BE(12);
  header.writeUInt32BE(indexBytes.length, 12);
  return Buffer.concat([
    header,
    indexBytes,
    pack.subarray(deterministicReleasePackContract.headerBytes + oldIndexLength),
  ]);
}

const CRC_TABLE = Array.from({ length: 256 }, (_, value) => {
  let crc = value;
  for (let bit = 0; bit < 8; bit += 1) {
    crc = (crc & 1) === 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  }
  return crc >>> 0;
});

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBytes = Buffer.from(type);
  const result = Buffer.alloc(12 + data.length);
  result.writeUInt32BE(data.length, 0);
  typeBytes.copy(result, 4);
  data.copy(result, 8);
  result.writeUInt32BE(crc32(Buffer.concat([typeBytes, data])), 8 + data.length);
  return result;
}

function png(index) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(1, 0);
  header.writeUInt32BE(1, 4);
  header[8] = 8;
  header[9] = 6;
  const row = Buffer.from([0, index & 0xff, (index >>> 8) & 0xff, 96, 255]);
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk("IHDR", header),
    pngChunk("IDAT", deflateSync(row)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

test("release packs are byte-deterministic, canonical, sorted, and identity-framed", () => {
  const files = [
    { path: "z/index.html", bytes: Buffer.from("zeta") },
    { path: "a.txt", bytes: Buffer.from("alpha") },
  ];
  const first = encodeDeterministicReleasePack({ kind: "site", files });
  const second = encodeDeterministicReleasePack({ kind: "site", files: [...files].reverse() });
  assert.deepEqual(first.bytes, second.bytes);
  assert.equal(first.sha256, second.sha256);
  assert.deepEqual(first.index.entries.map(({ path: relative }) => relative), ["a.txt", "z/index.html"]);
  assert.equal(first.index.totalPayloadBytes, 9);
  assert.equal(first.reference.key, `packs/site/sha256/${first.sha256}.vpack`);
  assert.equal(deterministicReleasePackContract.magic, "VLABPACK");
  assert.equal(deterministicReleasePackContract.formatVersion, 1);
  assert.equal(deterministicReleasePackContract.compression, "none");
  assert.equal(deterministicReleasePackContract.metadata, "paths-and-content-only");
  assert.doesNotMatch(JSON.stringify(first.index), /compress|timestamp|mtime|mode|owner|uid|gid/i);
  assert.equal(first.bytes.subarray(0, 8).toString("ascii"), "VLABPACK");
  assert.equal(first.bytes.readUInt16BE(8), 1);
  assert.equal(first.bytes.readUInt16BE(10), 0);
  const indexLength = first.bytes.readUInt32BE(12);
  assert.deepEqual(first.bytes.subarray(16, 16 + indexLength), canonicalBytes(first.index));
  let frameOffset = 16 + indexLength;
  assert.equal(first.bytes.readUInt32BE(frameOffset), 5);
  frameOffset += 4;
  assert.deepEqual(first.bytes.subarray(frameOffset, frameOffset + 5), Buffer.from("alpha"));
  frameOffset += 5;
  assert.equal(first.bytes.readUInt32BE(frameOffset), 4);
  frameOffset += 4;
  assert.deepEqual(first.bytes.subarray(frameOffset), Buffer.from("zeta"));

  const decoded = decodeDeterministicReleasePack(first.bytes);
  assert.deepEqual(decoded.index, first.index);
  assert.equal(decoded.sha256, first.sha256);
  assert.deepEqual(decoded.reference, first.reference);
  assert.deepEqual(decoded.files, [
    { path: "a.txt", bytes: Buffer.from("alpha") },
    { path: "z/index.html", bytes: Buffer.from("zeta") },
  ]);
});

test("release packs reject link-shaped descriptors, unsafe paths, duplicates, and path collisions", () => {
  const bytes = Buffer.from("fixture");
  for (const relative of [
    "../escape",
    "/absolute",
    "a\\b",
    "a//b",
    "a/./b",
    "a/../b",
    "a:b",
    "a/b/",
  ]) {
    assert.throws(
      () => encodeDeterministicReleasePack({ kind: "site", files: [{ path: relative, bytes }] }),
      /unsafe/,
    );
  }
  assert.throws(
    () => encodeDeterministicReleasePack({
      kind: "site",
      files: [{ path: "link", bytes, type: "symlink", target: "elsewhere" }],
    }),
    /closed regular-file byte descriptor/,
  );
  assert.throws(
    () => encodeDeterministicReleasePack({
      kind: "site",
      files: [{ path: "a", bytes }, { path: "a", bytes }],
    }),
    /duplicate path/,
  );
  assert.throws(
    () => encodeDeterministicReleasePack({
      kind: "site",
      files: [{ path: "A.txt", bytes }, { path: "a.txt", bytes }],
    }),
    /case-folded path collision/,
  );
  assert.throws(
    () => encodeDeterministicReleasePack({
      kind: "site",
      files: [{ path: "evidence", bytes }, { path: "evidence/image.png", bytes }],
    }),
    /file-directory path collision/,
  );
});

test("release pack decoding rejects malformed, noncanonical, truncated, and altered bytes", () => {
  const encoded = encodeDeterministicReleasePack({
    kind: "occurrence",
    files: [{ path: "evidence/image.png", bytes: Buffer.from("payload") }],
  });

  const magic = Buffer.from(encoded.bytes);
  magic[0] ^= 0xff;
  assert.throws(() => decodeDeterministicReleasePack(magic), /magic/);

  const version = Buffer.from(encoded.bytes);
  version.writeUInt16BE(2, 8);
  assert.throws(() => decodeDeterministicReleasePack(version), /version/);

  const flags = Buffer.from(encoded.bytes);
  flags.writeUInt16BE(1, 10);
  assert.throws(() => decodeDeterministicReleasePack(flags), /reserved/);

  assert.throws(
    () => decodeDeterministicReleasePack(encoded.bytes.subarray(0, encoded.bytes.length - 1)),
    /frame length|digest|truncated/,
  );
  assert.throws(
    () => decodeDeterministicReleasePack(Buffer.concat([encoded.bytes, Buffer.from([0])])),
    /trailing/,
  );

  const indexLength = encoded.bytes.readUInt32BE(12);
  const frameOffset = deterministicReleasePackContract.headerBytes + indexLength;
  const frame = Buffer.from(encoded.bytes);
  frame.writeUInt32BE(frame.readUInt32BE(frameOffset) + 1, frameOffset);
  assert.throws(() => decodeDeterministicReleasePack(frame), /frame length/);

  const content = Buffer.from(encoded.bytes);
  content[content.length - 1] ^= 0xff;
  assert.throws(() => decodeDeterministicReleasePack(content), /digest/);

  const compactIndex = Buffer.from(JSON.stringify(encoded.index));
  assert.throws(
    () => decodeDeterministicReleasePack(rebuildWithIndex(encoded.bytes, compactIndex)),
    /canonical JSON/,
  );

  const duplicateIndex = {
    ...encoded.index,
    fileCount: 2,
    totalPayloadBytes: 14,
    entries: [encoded.index.entries[0], encoded.index.entries[0]],
  };
  assert.throws(
    () => decodeDeterministicReleasePack(rebuildWithIndex(encoded.bytes, canonicalBytes(duplicateIndex))),
    /strictly sorted|duplicate/,
  );

  const unsafeIndex = structuredClone(encoded.index);
  unsafeIndex.entries[0].path = "../escape";
  assert.throws(
    () => decodeDeterministicReleasePack(rebuildWithIndex(encoded.bytes, canonicalBytes(unsafeIndex))),
    /unsafe/,
  );

  const collisionIndex = {
    ...encoded.index,
    fileCount: 2,
    totalPayloadBytes: 14,
    entries: [
      { ...encoded.index.entries[0], path: "A.png" },
      { ...encoded.index.entries[0], path: "a.png" },
    ],
  };
  assert.throws(
    () => decodeDeterministicReleasePack(rebuildWithIndex(encoded.bytes, canonicalBytes(collisionIndex))),
    /case-folded path collision/,
  );
});

test("packed hydration materializes the canonical individual 143 graph plus 2 camera PNG paths", async (context) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-packed-hydration-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  const graphImages = Array.from({ length: 143 }, (_, index) => ({ role: "graph", bytes: png(index) }));
  const cameraImages = Array.from({ length: 2 }, (_, index) => ({ role: "camera", bytes: png(143 + index) }));
  const images = [...graphImages, ...cameraImages].map((item) => ({
    ...item,
    digest: sha256(item.bytes),
  }));
  assert.equal(new Set(images.map(({ digest }) => digest)).size, 145);
  const files = images.map(({ bytes, digest }) => ({
    path: `evidence/blobs/sha256/${digest}.png`,
    bytes,
  }));
  const packed = encodeDeterministicReleasePack({ kind: "occurrence", files: [...files].reverse() });
  assert.equal(packed.index.fileCount, 145);
  assert.equal(graphImages.length, 143);
  assert.equal(cameraImages.length, 2);

  const destination = path.join(root, "hydrated");
  const result = await materializeDeterministicReleasePack(packed.bytes, destination);
  assert.equal(result.kind, "occurrence");
  assert.equal(result.fileCount, 145);
  assert.deepEqual(result.paths, files.map(({ path: relative }) => relative).sort());
  const blobRoot = path.join(destination, "evidence", "blobs", "sha256");
  assert.deepEqual(
    (await readdir(blobRoot)).sort(),
    images.map(({ digest }) => `${digest}.png`).sort(),
  );
  for (const image of images) {
    const relative = `evidence/blobs/sha256/${image.digest}.png`;
    const target = path.join(destination, ...relative.split("/"));
    const metadata = await lstat(target, { bigint: true });
    assert.equal(metadata.isFile(), true);
    assert.equal(metadata.isSymbolicLink(), false);
    assert.equal(metadata.nlink, 1n);
    assert.deepEqual(await readFile(target), image.bytes);
    const validated = await validatePngFile(destination, relative);
    assert.equal(validated.sha256, image.digest);
  }

  await assert.rejects(
    () => materializeDeterministicReleasePack(packed.bytes, destination),
    /EEXIST/,
  );
  const linkedParent = path.join(root, "linked-parent");
  await symlink(root, linkedParent);
  await assert.rejects(
    () => materializeDeterministicReleasePack(packed.bytes, path.join(linkedParent, "escape")),
    /canonical real directory/,
  );
  assert.equal((await readdir(blobRoot)).length, 145);
});
