import { createHash } from "node:crypto";

import {
  S3OccurrenceReleaseStore,
  occurrenceReleaseStoreContract,
} from "./occurrence-release-store.mjs";
import {
  S3SiteReleaseStore,
} from "./site-release-store.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const MEDIA_ID_RE = /^media_[0-9a-f]{24}$/u;
const SITE_SELECTION_RE = /^selection\.json$/u;
const SITE_BLOB_RE = /^blobs\/sha256\/([0-9a-f]{64})$/u;
const SITE_RELEASE_RE = /^releases\/sha256\/([0-9a-f]{64})\.json$/u;
const SITE_EVENT_RE = /^events\/sha256\/([0-9a-f]{64})\.json$/u;
const OCCURRENCE_SELECTION_RE = /^selection\.json$/u;
const OCCURRENCE_BLOB_RE = /^blobs\/sha256\/([0-9a-f]{64})\.png$/u;
const OCCURRENCE_MANIFEST_RE = /^manifests\/sha256\/([0-9a-f]{64})\.json$/u;
const OCCURRENCE_EVENT_RE = /^events\/sha256\/([0-9a-f]{64})\.json$/u;
const MEDIA_SELECTION_RE = /^occurrences\/(media_[0-9a-f]{24})\/selection\.json$/u;
const MEDIA_GENERATION_RE = /^occurrences\/(media_[0-9a-f]{24})\/generations\/sha256\/([0-9a-f]{64})\.json$/u;
const MEDIA_EVENT_RE = /^occurrences\/(media_[0-9a-f]{24})\/events\/sha256\/([0-9a-f]{64})\.json$/u;
const MAX_SITE_SELECTION_BYTES = 64 * 1024;
const MAX_SITE_MANIFEST_BYTES = 16 * 1024 * 1024;
const MAX_SITE_EVENT_BYTES = 32 * 1024;
const MAX_INVENTORY_OBJECTS = 25_000;

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function instant(value, label) {
  const parsed = Date.parse(value);
  if (
    typeof value !== "string"
    || !Number.isFinite(parsed)
    || new Date(parsed).toISOString() !== value
  ) throw new Error(`${label} is invalid`);
  return value;
}

function digest(value, label) {
  if (typeof value !== "string" || !SHA256_RE.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function canonicalDocument(bytes, label) {
  let document;
  try {
    document = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error(`${label} is not valid JSON`);
  }
  if (!canonicalBytes(document).equals(bytes)) {
    throw new Error(`${label} is not canonical JSON`);
  }
  return document;
}

function references(values, label) {
  const unique = [...new Set(values)];
  if (unique.some((value) => typeof value !== "string" || value.length === 0)) {
    throw new Error(`${label} contains an invalid reference`);
  }
  return unique.sort();
}

function immutable(namespace, key, kind, digestValue, entry, objectReferences = []) {
  if (!Number.isSafeInteger(entry.bytes) || entry.bytes < 1) {
    throw new Error(`${namespace} release inventory contains an empty object`);
  }
  return {
    namespace,
    key,
    kind,
    sha256: digest(digestValue, `${namespace} release object digest`),
    bytes: entry.bytes,
    createdAt: instant(entry.lastModified, `${namespace} release object time`),
    references: references(objectReferences, `${namespace} release object`),
  };
}

async function exactRead(objects, entry, maximumBytes, label) {
  const value = await objects.read(entry.key, { maximumBytes, label });
  if (value.etag !== entry.etag || value.bytes.length !== entry.bytes) {
    throw new Error(`${label} changed during complete inventory`);
  }
  return value;
}

function selectionRecord(namespace, selectorKind, occurrenceId, entry, value, currentKey, rollbackKey) {
  return {
    namespace,
    selectorKind,
    occurrenceId,
    key: entry.key,
    sha256: sha256(value.bytes),
    etag: value.etag,
    bytes: value.bytes.length,
    currentKey,
    rollbackKey,
  };
}

async function siteInventory(store, maximumObjects) {
  const entries = await store.objects.listInventory("", { maximumObjects });
  const selectors = [];
  const objects = [];
  for (const entry of entries) {
    if (SITE_SELECTION_RE.test(entry.key)) {
      const selected = await store.readSelection();
      if (selected === null || selected.etag !== entry.etag) {
        throw new Error("site release selection changed during complete inventory");
      }
      const value = await exactRead(
        store.objects,
        entry,
        MAX_SITE_SELECTION_BYTES,
        "site release selection",
      );
      if (selected.sha256 !== sha256(value.bytes)) {
        throw new Error("site release selection identity changed during complete inventory");
      }
      selectors.push(selectionRecord(
        "site",
        "site",
        null,
        entry,
        value,
        `releases/sha256/${selected.document.current.releaseSha256}.json`,
        selected.document.previous === null
          ? null
          : `releases/sha256/${selected.document.previous.releaseSha256}.json`,
      ));
      continue;
    }
    const blob = SITE_BLOB_RE.exec(entry.key);
    if (blob !== null) {
      objects.push(immutable("site", entry.key, "blob", blob[1], entry));
      continue;
    }
    const release = SITE_RELEASE_RE.exec(entry.key);
    if (release !== null) {
      const manifest = await store.readRelease(release[1], { verifyBlobs: false });
      const value = await exactRead(
        store.objects,
        entry,
        MAX_SITE_MANIFEST_BYTES,
        "site release manifest",
      );
      if (sha256(value.bytes) !== release[1] || !canonicalBytes(manifest).equals(value.bytes)) {
        throw new Error("site release manifest identity changed during complete inventory");
      }
      objects.push(immutable(
        "site",
        entry.key,
        "release",
        release[1],
        entry,
        references(
          manifest.files.map((file) => `blobs/sha256/${file.sha256}`),
          "site release manifest",
        ),
      ));
      continue;
    }
    const event = SITE_EVENT_RE.exec(entry.key);
    if (event !== null) {
      const value = await exactRead(
        store.objects,
        entry,
        MAX_SITE_EVENT_BYTES,
        "site release event",
      );
      const document = canonicalDocument(value.bytes, "site release event");
      if (
        typeof document.eventId !== "string"
        || sha256(Buffer.from(document.eventId)) !== event[1]
        || JSON.stringify(await store.readEventIntent(document.eventId)) !== JSON.stringify(document)
      ) throw new Error("site release event identity is invalid");
      objects.push(immutable("site", entry.key, "event", sha256(value.bytes), entry));
      continue;
    }
    throw new Error("site release inventory contains bytes outside the closed root layout");
  }
  return { entries, selectors, objects };
}

function occurrenceManifestReferences(manifest) {
  if (
    manifest?.occurrences === null
    || typeof manifest?.occurrences !== "object"
    || !Array.isArray(manifest.occurrences.graphs)
    || !Array.isArray(manifest.occurrences.currentMedia)
  ) throw new Error("occurrence manifest inventory membership is invalid");
  const found = [];
  for (const graph of manifest.occurrences.graphs) {
    if (graph?.fallback !== null) {
      found.push(`blobs/sha256/${digest(graph?.fallback?.sha256, "graph fallback digest")}`);
    }
  }
  for (const media of manifest.occurrences.currentMedia) {
    if (media?.pointer !== null) {
      if (!MEDIA_ID_RE.test(media?.occurrenceId ?? "")) {
        throw new Error("current-media occurrence identity is invalid");
      }
      found.push(
        `occurrences/${media.occurrenceId}/generations/sha256/${digest(
          media?.pointer?.generationSha256,
          "current-media generation digest",
        )}.json`,
      );
    }
  }
  return references(found, "occurrence manifest");
}

async function occurrenceInventory(store, maximumObjects) {
  const entries = await store.objects.listInventory("", { maximumObjects });
  const selectors = [];
  const objects = [];
  for (const entry of entries) {
    if (OCCURRENCE_SELECTION_RE.test(entry.key)) {
      const selected = await store.readAggregateSelection();
      if (selected === null || selected.etag !== entry.etag) {
        throw new Error("aggregate occurrence selection changed during complete inventory");
      }
      const value = await exactRead(
        store.objects,
        entry,
        occurrenceReleaseStoreContract.maximumSelectionBytes,
        "aggregate occurrence selection",
      );
      selectors.push(selectionRecord(
        "occurrence",
        "aggregate",
        null,
        entry,
        value,
        `manifests/sha256/${selected.document.current.manifestSha256}.json`,
        selected.document.previous === null
          ? null
          : `manifests/sha256/${selected.document.previous.manifestSha256}.json`,
      ));
      continue;
    }
    const mediaSelection = MEDIA_SELECTION_RE.exec(entry.key);
    if (mediaSelection !== null) {
      const occurrenceId = mediaSelection[1];
      const selected = await store.readCurrentMediaSelection(occurrenceId);
      if (selected === null || selected.etag !== entry.etag) {
        throw new Error("current-media selection changed during complete inventory");
      }
      const value = await exactRead(
        store.objects,
        entry,
        occurrenceReleaseStoreContract.maximumSelectionBytes,
        "current-media selection",
      );
      selectors.push(selectionRecord(
        "occurrence",
        "current-media",
        occurrenceId,
        entry,
        value,
        `occurrences/${occurrenceId}/generations/sha256/${selected.document.current.generationSha256}.json`,
        selected.document.previous === null
          ? null
          : `occurrences/${occurrenceId}/generations/sha256/${selected.document.previous.generationSha256}.json`,
      ));
      continue;
    }
    const blob = OCCURRENCE_BLOB_RE.exec(entry.key);
    if (blob !== null) {
      objects.push(immutable(
        "occurrence",
        `blobs/sha256/${blob[1]}`,
        "blob",
        blob[1],
        entry,
      ));
      continue;
    }
    const manifest = OCCURRENCE_MANIFEST_RE.exec(entry.key);
    if (manifest !== null) {
      const selected = await store.readAggregateManifest(manifest[1]);
      const value = await exactRead(
        store.objects,
        entry,
        occurrenceReleaseStoreContract.maximumManifestBytes,
        "occurrence manifest",
      );
      if (selected.sha256 !== manifest[1] || !selected.bytes.equals(value.bytes)) {
        throw new Error("occurrence manifest identity changed during complete inventory");
      }
      objects.push(immutable(
        "occurrence",
        entry.key,
        "manifest",
        manifest[1],
        entry,
        occurrenceManifestReferences(selected.document),
      ));
      continue;
    }
    const generation = MEDIA_GENERATION_RE.exec(entry.key);
    if (generation !== null) {
      const selected = await store.readCurrentMediaGeneration(generation[1], generation[2]);
      const value = await exactRead(
        store.objects,
        entry,
        occurrenceReleaseStoreContract.maximumGenerationBytes,
        "current-media generation",
      );
      if (selected.sha256 !== generation[2] || !selected.bytes.equals(value.bytes)) {
        throw new Error("current-media generation identity changed during complete inventory");
      }
      objects.push(immutable(
        "occurrence",
        entry.key,
        "generation",
        generation[2],
        entry,
        [`blobs/sha256/${digest(selected.document?.fallback?.sha256, "current-media fallback digest")}`],
      ));
      continue;
    }
    const aggregateEvent = OCCURRENCE_EVENT_RE.exec(entry.key);
    const mediaEvent = MEDIA_EVENT_RE.exec(entry.key);
    if (aggregateEvent !== null || mediaEvent !== null) {
      const value = await exactRead(
        store.objects,
        entry,
        occurrenceReleaseStoreContract.maximumEventBytes,
        "occurrence event",
      );
      const document = canonicalDocument(value.bytes, "occurrence event");
      const eventId = document.eventId;
      if (
        typeof eventId !== "string"
        || sha256(Buffer.from(eventId)) !== (aggregateEvent?.[1] ?? mediaEvent[2])
      ) throw new Error("occurrence event identity is invalid");
      const selected = aggregateEvent !== null
        ? await store.readAggregateEventIntent(eventId)
        : await store.readCurrentMediaEventIntent(mediaEvent[1], eventId);
      if (selected === null || !selected.bytes.equals(value.bytes)) {
        throw new Error("occurrence event changed during complete inventory");
      }
      objects.push(immutable("occurrence", entry.key.replace(/\.png$/u, ""), "event", sha256(value.bytes), entry));
      continue;
    }
    throw new Error("occurrence release inventory contains bytes outside the closed nested-root layout");
  }
  return { entries, selectors, objects };
}

function storePair(siteStore, occurrenceStore) {
  const sitePrefix = siteStore?.objects?.prefix;
  const occurrencePrefix = occurrenceStore?.objects?.prefix;
  if (
    !(siteStore instanceof S3SiteReleaseStore)
    || !(occurrenceStore instanceof S3OccurrenceReleaseStore)
    || siteStore.objects.bucket !== occurrenceStore.objects.bucket
    || sitePrefix === occurrencePrefix
    || sitePrefix.startsWith(`${occurrencePrefix}/`)
    || occurrencePrefix.startsWith(`${sitePrefix}/`)
  ) throw new Error("release inventory requires distinct S3 site and occurrence stores in one bucket");
}

export async function captureReleaseStorageS3Inventory({
  siteStore,
  occurrenceStore,
  capturedAt,
  maximumObjects = MAX_INVENTORY_OBJECTS,
}) {
  if (
    !Number.isSafeInteger(maximumObjects)
    || maximumObjects < 2
    || maximumObjects > MAX_INVENTORY_OBJECTS
  ) throw new Error("release inventory object limit is invalid");
  instant(capturedAt, "release inventory capture time");
  storePair(siteStore, occurrenceStore);
  const [site, occurrence] = await Promise.all([
    siteInventory(siteStore, maximumObjects),
    occurrenceInventory(occurrenceStore, maximumObjects),
  ]);
  if (site.entries.length + occurrence.entries.length > maximumObjects) {
    throw new Error("release inventory exceeds its combined membership limit");
  }
  const selectors = [...site.selectors, ...occurrence.selectors]
    .sort((left, right) => `${left.namespace}\u0000${left.key}`.localeCompare(`${right.namespace}\u0000${right.key}`));
  const objects = [...site.objects, ...occurrence.objects]
    .sort((left, right) => `${left.namespace}\u0000${left.key}`.localeCompare(`${right.namespace}\u0000${right.key}`));
  return {
    contract: "verdify.lab-release-storage-inventory",
    schemaVersion: 1,
    capturedAt,
    listings: {
      site: { complete: true, continuationToken: null },
      occurrence: { complete: true, continuationToken: null },
    },
    selectors,
    objects,
  };
}

export const releaseStorageS3InventoryContract = Object.freeze({
  maximumObjects: MAX_INVENTORY_OBJECTS,
  contract: "verdify.lab-release-storage-inventory",
  schemaVersion: 1,
});
