import assert from "node:assert/strict";
import { open as openFile, lstat, unlink } from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LOCK = path.join(ROOT, ".astro-stage-build.lock");

test("a stable root lock excludes a concurrent cooperating build and preserves the holder", async () => {
  const holder = await openFile(LOCK, "wx", 0o600);
  const identity = await holder.stat({ bigint: true });
  try {
    const result = spawnSync(process.execPath, [path.join(ROOT, "scripts/build-stage.mjs"), "--check"], {
      cwd: ROOT,
      encoding: "utf8",
      env: {
        ...process.env,
        LAB_SNAPSHOT: "tests/fixtures/snapshot",
        ALLOW_SYNTHETIC_FIXTURE: "true",
      },
      timeout: 10_000,
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /another cooperating Lab stage build is active/);
    const selected = await lstat(LOCK, { bigint: true });
    assert.equal(selected.dev, identity.dev);
    assert.equal(selected.ino, identity.ino);
  } finally {
    await holder.close();
    const selected = await lstat(LOCK, { bigint: true });
    if (selected.dev === identity.dev && selected.ino === identity.ino) await unlink(LOCK);
  }
});
