import { constants as fsConstants, open } from "node:fs/promises";
import path from "node:path";

import {
  loadSelectedCurrentMediaGeneration,
  loadSelectedOccurrenceRelease,
  publishCurrentMediaGeneration,
  publishOccurrenceRelease,
  rollbackCurrentMediaGeneration,
  rollbackOccurrenceRelease,
  summarizeOccurrenceFreshness,
} from "./lib/occurrence-release.mjs";

const MAX_REQUEST_BYTES = 8 * 1024 * 1024;

function usage() {
  return [
    "Usage:",
    "  node scripts/manage-occurrence-release.mjs publish --request REQUEST.json",
    "  node scripts/manage-occurrence-release.mjs status --store STORE",
    "  node scripts/manage-occurrence-release.mjs freshness --store STORE --at ISO_INSTANT",
    "  node scripts/manage-occurrence-release.mjs rollback --store STORE --expected SELECTION_SHA256 --at ISO_INSTANT",
    "  node scripts/manage-occurrence-release.mjs publish-media --request REQUEST.json",
    "  node scripts/manage-occurrence-release.mjs media-status --store STORE --occurrence MEDIA_ID",
    "  node scripts/manage-occurrence-release.mjs rollback-media --store STORE --occurrence MEDIA_ID --expected SELECTION_SHA256 --at ISO_INSTANT",
  ].join("\n");
}

function options(argv) {
  const result = { command: argv[0] ?? "", values: new Map() };
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined || result.values.has(key)) throw new Error("invalid command arguments");
    result.values.set(key, value);
  }
  return result;
}

async function requestDocument(file) {
  const absolute = path.resolve(file);
  const handle = await open(absolute, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  let bytes;
  try {
    const metadata = await handle.stat({ bigint: true });
    if (!metadata.isFile() || metadata.nlink !== 1n || metadata.size < 1n || metadata.size > BigInt(MAX_REQUEST_BYTES)) {
      throw new Error("release request is not a bounded single-link regular file");
    }
    bytes = await handle.readFile();
    const after = await handle.stat({ bigint: true });
    if (after.dev !== metadata.dev || after.ino !== metadata.ino || after.size !== metadata.size || after.nlink !== 1n) {
      throw new Error("release request changed while being read");
    }
  } finally {
    await handle.close();
  }
  let document;
  try {
    document = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("release request is not valid JSON");
  }
  if (`${JSON.stringify(document, null, 2)}\n` !== bytes.toString("utf8")) {
    throw new Error("release request is not canonical JSON");
  }
  return document;
}

function requireKeys(document, keys) {
  if (Object.keys(document).join(",") !== keys.join(",")) throw new Error("release request does not use the closed v1 shape");
  return document;
}

async function main() {
  const { command, values } = options(process.argv.slice(2));
  if (command === "publish" && values.size === 1 && values.has("--request")) {
    const request = requireKeys(await requestDocument(values.get("--request")), [
      "storeRoot",
      "sourceRoot",
      "event",
      "sourceSnapshotManifestSha256",
      "policyVersion",
      "publishedAt",
      "graphs",
      "currentMedia",
      "expectedSelectionSha256",
    ]);
    const result = await publishOccurrenceRelease(request);
    process.stdout.write(`${JSON.stringify({
      contract: "verdify.lab-occurrence-publish-result",
      schemaVersion: 1,
      manifestSha256: result.manifestSha256,
      eventId: request.event.eventId,
      idempotent: result.idempotent,
      retained: result.retained ?? true,
      freshness: result.manifest?.freshness ?? null,
      graphCount: result.manifest?.occurrences.graphs.length ?? null,
      currentMediaCount: result.manifest?.occurrences.currentMedia.length ?? null,
    }, null, 2)}\n`);
    return;
  }
  if (command === "status" && values.size === 1 && values.has("--store")) {
    const selected = await loadSelectedOccurrenceRelease(values.get("--store"));
    process.stdout.write(`${JSON.stringify({
      contract: "verdify.lab-occurrence-status",
      schemaVersion: 1,
      generation: selected.selection?.generation ?? 0,
      selectionSha256: selected.selectionSha256,
      currentManifestSha256: selected.selection?.current.manifestSha256 ?? null,
      previousManifestSha256: selected.selection?.previous?.manifestSha256 ?? null,
      currentEventId: selected.current?.event.eventId ?? null,
      freshness: selected.current?.freshness ?? null,
    }, null, 2)}\n`);
    return;
  }
  if (
    command === "freshness"
    && values.size === 2
    && values.has("--store")
    && values.has("--at")
  ) {
    const selected = await loadSelectedOccurrenceRelease(values.get("--store"));
    const evaluated = summarizeOccurrenceFreshness(
      selected.current ?? { occurrences: { graphs: [], currentMedia: [] } },
      values.get("--at"),
    );
    const summary = selected.current ? evaluated : { ...evaluated, status: "missing" };
    process.stdout.write(`${JSON.stringify({
      contract: "verdify.lab-occurrence-freshness",
      schemaVersion: 1,
      generation: selected.selection?.generation ?? 0,
      currentManifestSha256: selected.selection?.current.manifestSha256 ?? null,
      ...summary,
    }, null, 2)}\n`);
    return;
  }
  if (
    command === "rollback"
    && values.size === 3
    && values.has("--store")
    && values.has("--expected")
    && values.has("--at")
  ) {
    const selection = await rollbackOccurrenceRelease({
      storeRoot: values.get("--store"),
      expectedSelectionSha256: values.get("--expected"),
      rolledBackAt: values.get("--at"),
    });
    process.stdout.write(`${JSON.stringify({
      contract: "verdify.lab-occurrence-rollback-result",
      schemaVersion: 1,
      generation: selection.selection.generation,
      selectionSha256: selection.selectionSha256,
      currentManifestSha256: selection.selection.current.manifestSha256,
      previousManifestSha256: selection.selection.previous.manifestSha256,
    }, null, 2)}\n`);
    return;
  }
  if (command === "publish-media" && values.size === 1 && values.has("--request")) {
    const request = requireKeys(await requestDocument(values.get("--request")), [
      "storeRoot",
      "sourceRoot",
      "event",
      "policyVersion",
      "publishedAt",
      "occurrence",
      "candidate",
      "expectedSelectionSha256",
    ]);
    const result = await publishCurrentMediaGeneration(request);
    process.stdout.write(`${JSON.stringify({
      contract: "verdify.lab-current-media-publish-result",
      schemaVersion: 1,
      eventId: request.event.eventId,
      occurrenceId: request.occurrence.occurrenceId,
      idempotent: result.idempotent,
      retained: result.retained,
      selectionSha256: result.selected?.selectionSha256 ?? null,
      generation: result.selected?.selection.generation ?? 0,
    }, null, 2)}\n`);
    return;
  }
  if (
    command === "media-status"
    && values.size === 2
    && values.has("--store")
    && values.has("--occurrence")
  ) {
    const selected = await loadSelectedCurrentMediaGeneration(values.get("--store"), values.get("--occurrence"));
    process.stdout.write(`${JSON.stringify({
      contract: "verdify.lab-current-media-status",
      schemaVersion: 1,
      occurrenceId: values.get("--occurrence"),
      selectionSha256: selected?.selectionSha256 ?? null,
      generation: selected?.selection.generation ?? 0,
      currentGenerationSha256: selected?.selection.current.generationSha256 ?? null,
      previousGenerationSha256: selected?.selection.previous?.generationSha256 ?? null,
    }, null, 2)}\n`);
    return;
  }
  if (
    command === "rollback-media"
    && values.size === 4
    && values.has("--store")
    && values.has("--occurrence")
    && values.has("--expected")
    && values.has("--at")
  ) {
    const selected = await rollbackCurrentMediaGeneration({
      storeRoot: values.get("--store"),
      occurrenceId: values.get("--occurrence"),
      expectedSelectionSha256: values.get("--expected"),
      rolledBackAt: values.get("--at"),
    });
    process.stdout.write(`${JSON.stringify({
      contract: "verdify.lab-current-media-rollback-result",
      schemaVersion: 1,
      occurrenceId: values.get("--occurrence"),
      selectionSha256: selected.selectionSha256,
      generation: selected.selection.generation,
      currentGenerationSha256: selected.selection.current.generationSha256,
      previousGenerationSha256: selected.selection.previous.generationSha256,
    }, null, 2)}\n`);
    return;
  }
  throw new Error(usage());
}

main().catch((error) => {
  process.stderr.write(`manage-occurrence-release: ${error.message}\n`);
  process.exitCode = 1;
});
