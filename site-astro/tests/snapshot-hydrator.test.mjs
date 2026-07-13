import assert from "node:assert/strict";
import { lstat, mkdir, mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  extractVerifiedTar,
  readReleaseDescriptor,
  removeDirectoryIfIdentity,
  unlinkIfIdentity,
} from "../scripts/fetch-stage-snapshot.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function octal(value, width) {
  return `${value.toString(8).padStart(width - 1, "0")}\0`;
}

function tarHeader(name, size, type = "0") {
  const header = Buffer.alloc(512);
  header.write(name, 0, 100, "utf8");
  header.write(octal(type === "5" ? 0o755 : 0o644, 8), 100, 8, "ascii");
  header.write(octal(0, 8), 108, 8, "ascii");
  header.write(octal(0, 8), 116, 8, "ascii");
  header.write(octal(size, 12), 124, 12, "ascii");
  header.write(octal(0, 12), 136, 12, "ascii");
  header.fill(32, 148, 156);
  header.write(type, 156, 1, "ascii");
  header.write("ustar\0", 257, 6, "binary");
  header.write("00", 263, 2, "ascii");
  let checksum = 0;
  for (const byte of header) checksum += byte;
  header.write(`${checksum.toString(8).padStart(6, "0")}\0 `, 148, 8, "ascii");
  return header;
}

function tar(entries) {
  const chunks = [];
  for (const entry of entries) {
    const body = Buffer.from(entry.body ?? "");
    chunks.push(tarHeader(entry.name, body.length, entry.type ?? "0"));
    if (body.length) {
      chunks.push(body);
      chunks.push(Buffer.alloc((512 - body.length % 512) % 512));
    }
  }
  chunks.push(Buffer.alloc(1024));
  return Buffer.concat(chunks);
}

const required = [
  { name: "content/", type: "5" },
  { name: "content/page.txt", body: "safe\n" },
  { name: "manifests/", type: "5" },
  { name: "manifests/content.json", body: "{}\n" },
  { name: "attestation.json", body: "{}\n" },
  { name: "evidence/", type: "5" },
  { name: "evidence/public-output-guard.json", body: "{}\n" },
];

async function archiveCase(context, entries) {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-snapshot-tar-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  const archive = path.join(root, "payload.tar");
  const destination = path.join(root, "snapshot");
  await writeFile(archive, tar(entries));
  return { archive, destination };
}

test("checked-in release descriptor is closed and fully pinned", async () => {
  const descriptor = path.join(ROOT, "vendor/snapshot/verdify-lab-stage-20260712t1620z.json");
  const release = await readReleaseDescriptor(descriptor);
  assert.equal(release.fileCount, 429);
  assert.equal(release.assetBytes, 409600000);
  assert.equal(release.assetSha256, "fe9332c8cdfa95de90ac7da0aff79d4f0c60f39f0c2cbd7ab35b55fcb3fb4029");
});

test("ustar extractor accepts only the closed regular payload", async (context) => {
  const { archive, destination } = await archiveCase(context, required);
  const result = await extractVerifiedTar(archive, destination, { expectedContentFiles: 1 });
  assert.equal(result.contentFiles, 1);
  assert.equal(await readFile(path.join(destination, "content/page.txt"), "utf8"), "safe\n");
});

for (const [label, mutation, pattern] of [
  ["traversal", [{ name: "content/../escape.txt", body: "x" }], /traverses/],
  ["ambiguous separators", [{ name: "content//", type: "5" }], /unsafe path/],
  ["symlink", [{ name: "content/link", type: "2" }], /non-regular/],
  ["unexpected top level", [{ name: "private.txt", body: "x" }], /closed snapshot/],
  ["duplicate", [{ name: "content/page.txt", body: "again" }], /duplicate path/],
  ["case collision", [{ name: "content/PAGE.txt", body: "again" }], /case-folded/],
]) {
  test(`ustar extractor rejects ${label}`, async (context) => {
    const { archive, destination } = await archiveCase(context, [...required, ...mutation]);
    await assert.rejects(() => extractVerifiedTar(archive, destination, { expectedContentFiles: 1 }), pattern);
  });
}

test("ustar extractor rejects a corrupt header checksum", async (context) => {
  const { archive, destination } = await archiveCase(context, required);
  const bytes = await readFile(archive);
  bytes[0] ^= 1;
  await writeFile(archive, bytes);
  await assert.rejects(() => extractVerifiedTar(archive, destination, { expectedContentFiles: 1 }), /checksum/);
});

test("ustar extractor rejects nonzero file padding", async (context) => {
  const { archive, destination } = await archiveCase(context, required);
  const bytes = await readFile(archive);
  bytes[512 + 512 + Buffer.byteLength("safe\n")] = 1;
  await writeFile(archive, bytes);
  await assert.rejects(() => extractVerifiedTar(archive, destination, { expectedContentFiles: 1 }), /nonzero data padding/);
});

test("archive collision diagnostics do not reflect untrusted paths", async (context) => {
  const untrusted = "content/operator-private-label.txt";
  const { archive, destination } = await archiveCase(context, [
    ...required,
    { name: untrusted, body: "one" },
    { name: untrusted, body: "two" },
  ]);
  await assert.rejects(
    () => extractVerifiedTar(archive, destination, { expectedContentFiles: 2 }),
    (error) => {
      assert.match(error.message, /duplicate path/);
      assert.doesNotMatch(error.message, /operator-private-label/);
      return true;
    },
  );
});

test("cleanup helpers preserve foreign file and directory replacements", async (context) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-snapshot-cleanup-"));
  context.after(() => rm(root, { recursive: true, force: true }));

  const file = path.join(root, "lock");
  await writeFile(file, "original");
  const fileIdentity = await lstat(file, { bigint: true });
  await rename(file, `${file}.held`);
  await writeFile(file, "replacement");
  assert.equal(await unlinkIfIdentity(file, fileIdentity), false);
  assert.equal(await readFile(file, "utf8"), "replacement");

  const directory = path.join(root, "staged");
  await mkdir(directory);
  const directoryIdentity = await lstat(directory, { bigint: true });
  await rename(directory, `${directory}.held`);
  await mkdir(directory);
  await writeFile(path.join(directory, "foreign"), "replacement");
  assert.equal(await removeDirectoryIfIdentity(directory, directoryIdentity), false);
  assert.equal(await readFile(path.join(directory, "foreign"), "utf8"), "replacement");

  const ownedFile = path.join(root, "owned-file");
  await writeFile(ownedFile, "owned");
  assert.equal(await unlinkIfIdentity(ownedFile, await lstat(ownedFile, { bigint: true })), true);
  await assert.rejects(() => lstat(ownedFile), /ENOENT/);

  const ownedDirectory = path.join(root, "owned-directory");
  await mkdir(ownedDirectory);
  await writeFile(path.join(ownedDirectory, "payload"), "owned");
  assert.equal(await removeDirectoryIfIdentity(ownedDirectory, await lstat(ownedDirectory, { bigint: true })), true);
  await assert.rejects(() => lstat(ownedDirectory), /ENOENT/);
});
