import assert from "node:assert/strict";
import test from "node:test";

import { encodeDeterministicReleasePack } from "../scripts/lib/deterministic-release-pack.mjs";
import {
  createPackedReleasePair,
  createPackedReleaseSelectedRoot,
  packedReleaseSelectedRootContract,
  parsePackedReleaseSelectedRoot,
  serializePackedReleaseSelectedRoot,
  validatePackedReleasePair,
  validatePackedReleaseSelectedRoot,
} from "../scripts/lib/packed-release-selected-root.mjs";

function pack(kind, label) {
  return encodeDeterministicReleasePack({
    kind,
    files: [{
      path: kind === "occurrence" ? "evidence/image.png" : "index.html",
      bytes: Buffer.from(label),
    }],
  }).reference;
}

function pair(label) {
  return createPackedReleasePair({
    eventId: `evt_${label}_fixture`,
    occurrencePack: pack("occurrence", `occurrence-${label}`),
    sitePack: pack("site", `site-${label}`),
  });
}

test("one canonical selected root atomically binds current and rollback occurrence/site pairs", () => {
  const current = pair("current");
  const rollback = pair("rollback");
  const first = createPackedReleaseSelectedRoot({
    generation: 2,
    current,
    rollback,
    selectedAt: "2026-07-14T12:00:00.000Z",
    reason: "publish",
  });
  const second = createPackedReleaseSelectedRoot({
    generation: 2,
    current,
    rollback,
    selectedAt: "2026-07-14T12:00:00.000Z",
    reason: "publish",
  });
  assert.equal(first.sha256, second.sha256);
  assert.deepEqual(first.bytes, second.bytes);
  assert.equal(first.document.current.occurrencePack.kind, "occurrence");
  assert.equal(first.document.current.sitePack.kind, "site");
  assert.equal(first.document.rollback.occurrencePack.kind, "occurrence");
  assert.equal(first.document.rollback.sitePack.kind, "site");
  assert.equal(packedReleaseSelectedRootContract.key, "selected-root.json");
  assert.equal(packedReleaseSelectedRootContract.selectionUnit, "one-occurrence-pack-plus-one-site-pack");

  const parsed = parsePackedReleaseSelectedRoot(first.bytes);
  assert.equal(parsed.sha256, first.sha256);
  assert.deepEqual(parsed.document, first.document);
  assert.deepEqual(serializePackedReleaseSelectedRoot(parsed), first.bytes);
});

test("selected-root validation rejects partial, swapped, altered, duplicate, and noncanonical pairs", () => {
  const current = pair("current");
  const rollback = pair("rollback");
  const selected = createPackedReleaseSelectedRoot({
    generation: 2,
    current,
    rollback,
    selectedAt: "2026-07-14T12:00:00.000Z",
    reason: "publish",
  });

  const partial = structuredClone(current);
  delete partial.sitePack;
  assert.throws(() => validatePackedReleasePair(partial), /closed v1 schema/);

  const swapped = structuredClone(current);
  [swapped.occurrencePack, swapped.sitePack] = [swapped.sitePack, swapped.occurrencePack];
  assert.throws(() => validatePackedReleasePair(swapped), /one occurrence pack and one site pack/);

  const altered = structuredClone(current);
  altered.sitePack.bytes += 1;
  assert.throws(() => validatePackedReleasePair(altered), /pair digest/);

  const duplicate = structuredClone(selected.document);
  duplicate.rollback = duplicate.current;
  assert.throws(() => validatePackedReleaseSelectedRoot(duplicate), /identical/);

  const missingRollback = structuredClone(selected.document);
  missingRollback.rollback = null;
  missingRollback.reason = "rollback";
  assert.throws(() => validatePackedReleaseSelectedRoot(missingRollback), /requires both generations/);

  assert.throws(
    () => parsePackedReleaseSelectedRoot(Buffer.from(JSON.stringify(selected.document))),
    /canonical JSON/,
  );
});

test("an initial publish may bind one complete current pair with no rollback", () => {
  const selected = createPackedReleaseSelectedRoot({
    generation: 1,
    current: pair("initial"),
    rollback: null,
    selectedAt: "2026-07-14T12:00:00.000Z",
    reason: "publish",
  });
  assert.equal(selected.document.rollback, null);
  assert.equal(parsePackedReleaseSelectedRoot(selected.bytes).sha256, selected.sha256);
});
