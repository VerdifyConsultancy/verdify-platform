import { createHash } from "node:crypto";

import {
  S3OccurrenceReleaseStore,
  occurrenceReleaseStoreContract,
} from "./occurrence-release-store.mjs";
import {
  S3SiteReleaseStore,
  validateSiteReleaseManifest,
} from "./site-release-store.mjs";
import {
  parseSiteReleaseCheckpoint,
  siteReleaseCheckpointContract,
  siteReleaseCheckpointKey,
} from "./site-release-checkpoint-contract.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const MEDIA_ID_RE = /^media_[0-9a-f]{24}$/u;
const SITE_SELECTION_RE = /^selection\.json$/u;
const SITE_BLOB_RE = /^blobs\/sha256\/([0-9a-f]{64})$/u;
const SITE_RELEASE_RE = /^releases\/sha256\/([0-9a-f]{64})\.json$/u;
const SITE_EVENT_RE = /^events\/sha256\/([0-9a-f]{64})\.json$/u;
const SITE_CHECKPOINT_RE = /^checkpoints\/sha256\/([0-9a-f]{64})\.json$/u;
const OCCURRENCE_SELECTION_RE = /^selection\.json$/u;
const OCCURRENCE_BLOB_RE = /^blobs\/sha256\/([0-9a-f]{64})\.png$/u;
const OCCURRENCE_MANIFEST_RE = /^manifests\/sha256\/([0-9a-f]{64})\.json$/u;
const OCCURRENCE_EVENT_RE = /^events\/sha256\/([0-9a-f]{64})\.json$/u;
const MEDIA_SELECTION_RE = /^occurrences\/(media_[0-9a-f]{24})\/selection\.json$/u;
const MEDIA_GENERATION_RE = /^occurrences\/(media_[0-9a-f]{24})\/generations\/sha256\/([0-9a-f]{64})\.json$/u;
const MEDIA_EVENT_RE = /^occurrences\/(media_[0-9a-f]{24})\/events\/sha256\/([0-9a-f]{64})\.json$/u;
const MAX_SITE_SELECTION_BYTES = 64 * 1024;
const MAX_SITE_MANIFEST_BYTES = 16 * 1024 * 1024;
const MAX_INVENTORY_OBJECTS = 25_000;
const MAX_LIST_PAGES_PER_ROOT = 25;
// One ListObjectsV2 response can contain 1,000 maximum-length keys plus XML
// and metadata. Reserve a fixed two-MiB response envelope for every page
// before that page is requested; the page cardinality itself is exact.
const LIST_PAGE_EGRESS_BYTES = 2 * 1024 * 1024;

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
    sha256: value.sha256,
    etag: value.etag,
    bytes: value.bytes,
    currentKey,
    rollbackKey,
  };
}

async function siteInventory(store, entries) {
  const selectors = [];
  const objects = [];
  for (const entry of entries) {
    if (SITE_SELECTION_RE.test(entry.key)) {
      const selected = await store.readSelection();
      if (selected === null || selected.etag !== entry.etag) {
        throw new Error("site release selection changed during complete inventory");
      }
      selectors.push(selectionRecord(
        "site",
        "site",
        null,
        entry,
        { sha256: selected.sha256, etag: selected.etag, bytes: entry.bytes },
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
      const value = await exactRead(
        store.objects,
        entry,
        MAX_SITE_MANIFEST_BYTES,
        "site release manifest",
      );
      const manifest = JSON.parse(value.bytes.toString("utf8"));
      validateSiteReleaseManifest(manifest, value.bytes);
      if (sha256(value.bytes) !== release[1]) {
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
      // Event bodies are validated when their exact idempotency key is replayed.
      // Complete inventory uses immutable key identity plus listing metadata so a
      // bounded tombstone horizon does not GET every retained event each cycle.
      objects.push(immutable("site", entry.key, "event", event[1], entry));
      continue;
    }
    const checkpoint = SITE_CHECKPOINT_RE.exec(entry.key);
    if (checkpoint !== null) {
      const value = await exactRead(
        store.objects,
        entry,
        siteReleaseCheckpointContract.maximumBytes,
        "occurrence site checkpoint",
      );
      const selected = parseSiteReleaseCheckpoint(value);
      if (siteReleaseCheckpointKey(selected.document.eventId) !== entry.key) {
        throw new Error("occurrence site checkpoint key identity is invalid");
      }
      objects.push(immutable(
        "site",
        entry.key,
        "checkpoint",
        checkpoint[1],
        entry,
      ));
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

async function occurrenceInventory(store, entries) {
  const selectors = [];
  const objects = [];
  for (const entry of entries) {
    if (OCCURRENCE_SELECTION_RE.test(entry.key)) {
      const selected = await store.readAggregateSelection();
      if (selected === null || selected.etag !== entry.etag) {
        throw new Error("aggregate occurrence selection changed during complete inventory");
      }
      if (selected.bytes.length !== entry.bytes) {
        throw new Error("aggregate occurrence selection changed during complete inventory");
      }
      selectors.push(selectionRecord(
        "occurrence",
        "aggregate",
        null,
        entry,
        { sha256: selected.sha256, etag: selected.etag, bytes: selected.bytes.length },
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
      if (selected.bytes.length !== entry.bytes) {
        throw new Error("current-media selection changed during complete inventory");
      }
      selectors.push(selectionRecord(
        "occurrence",
        "current-media",
        occurrenceId,
        entry,
        { sha256: selected.sha256, etag: selected.etag, bytes: selected.bytes.length },
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
      if (
        selected.sha256 !== manifest[1]
        || selected.etag !== entry.etag
        || selected.bytes.length !== entry.bytes
      ) {
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
      if (
        selected.sha256 !== generation[2]
        || selected.etag !== entry.etag
        || selected.bytes.length !== entry.bytes
      ) {
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
      objects.push(immutable(
        "occurrence",
        entry.key,
        "event",
        aggregateEvent?.[1] ?? mediaEvent[2],
        entry,
      ));
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

function maximumInventoryObjects(value) {
  if (
    !Number.isSafeInteger(value)
    || value < 2
    || value > MAX_INVENTORY_OBJECTS
  ) throw new Error("release inventory object limit is invalid");
  return value;
}

function listingDocument(siteStore, occurrenceStore, site, occurrence) {
  return {
    contract: "verdify.lab-release-storage-s3-listing",
    schemaVersion: 1,
    bucket: siteStore.objects.bucket,
    sitePrefix: siteStore.objects.prefix,
    occurrencePrefix: occurrenceStore.objects.prefix,
    site,
    occurrence,
  };
}

function validateListing(listing, siteStore, occurrenceStore, maximumObjects) {
  if (
    listing === null
    || typeof listing !== "object"
    || Array.isArray(listing)
    || Object.keys(listing).join(",") !== "document,sha256"
    || listing.document === null
    || typeof listing.document !== "object"
    || Array.isArray(listing.document)
    || Object.keys(listing.document).join(",")
      !== "contract,schemaVersion,bucket,sitePrefix,occurrencePrefix,site,occurrence"
    || listing.document.contract !== "verdify.lab-release-storage-s3-listing"
    || listing.document.schemaVersion !== 1
    || listing.document.bucket !== siteStore.objects.bucket
    || listing.document.sitePrefix !== siteStore.objects.prefix
    || listing.document.occurrencePrefix !== occurrenceStore.objects.prefix
    || !exactListing(listing.document.site)
    || !exactListing(listing.document.occurrence)
    || listing.document.site.entries.length + listing.document.occurrence.entries.length > maximumObjects
    || sha256(canonicalBytes(listing.document)) !== listing.sha256
  ) throw new Error("release storage S3 listing evidence is invalid");
  return listing.document;
}

function exactListing(value) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.keys(value).join(",") === "pageCount,entries"
    && Number.isSafeInteger(value.pageCount)
    && value.pageCount >= 1
    && value.pageCount <= MAX_LIST_PAGES_PER_ROOT
    && Array.isArray(value.entries);
}

export async function listReleaseStorageS3Inventory({
  siteStore,
  occurrenceStore,
  maximumObjects = MAX_INVENTORY_OBJECTS,
  beforePage = null,
}) {
  maximumInventoryObjects(maximumObjects);
  storePair(siteStore, occurrenceStore);
  if (beforePage !== null && typeof beforePage !== "function") {
    throw new Error("release inventory page callback is invalid");
  }
  const list = async (namespace, objects) => objects.listInventory("", {
    maximumObjects,
    maximumPages: MAX_LIST_PAGES_PER_ROOT,
    includePageCount: true,
    beforePage: beforePage === null
      ? null
      : ({ pageNumber }) => beforePage(Object.freeze({ namespace, pageNumber })),
  });
  // Sequence the roots so a coordinator callback can maintain one exact daily
  // reservation snapshot without concurrent budget races.
  const site = await list("site", siteStore.objects);
  const occurrence = await list("occurrence", occurrenceStore.objects);
  if (site.objects.length + occurrence.objects.length > maximumObjects) {
    throw new Error("release inventory exceeds its combined membership limit");
  }
  const document = listingDocument(
    siteStore,
    occurrenceStore,
    { pageCount: site.pageCount, entries: [...site.objects] },
    { pageCount: occurrence.pageCount, entries: [...occurrence.objects] },
  );
  return Object.freeze({ document, sha256: sha256(canonicalBytes(document)) });
}

function observedRead(entry, maximumBytes, label) {
  if (!Number.isSafeInteger(entry.bytes) || entry.bytes < 1 || entry.bytes > maximumBytes) {
    throw new Error(`${label} listing bytes exceed the exact-read bound`);
  }
  return entry.bytes;
}

export function planReleaseStorageS3InventoryReads(listing) {
  if (
    listing === null
    || typeof listing !== "object"
    || listing.document?.contract !== "verdify.lab-release-storage-s3-listing"
    || sha256(canonicalBytes(listing.document)) !== listing.sha256
  ) throw new Error("release storage S3 listing evidence is invalid");
  let requests = 0;
  let egressBytes = 0;
  for (const entry of listing.document.site.entries) {
    if (SITE_SELECTION_RE.test(entry.key)) {
      requests += 1;
      egressBytes += observedRead(entry, MAX_SITE_SELECTION_BYTES, "site selection");
    } else if (SITE_RELEASE_RE.test(entry.key)) {
      requests += 1;
      egressBytes += observedRead(entry, MAX_SITE_MANIFEST_BYTES, "site manifest");
    } else if (SITE_CHECKPOINT_RE.test(entry.key)) {
      requests += 1;
      egressBytes += observedRead(
        entry,
        siteReleaseCheckpointContract.maximumBytes,
        "occurrence site checkpoint",
      );
    }
  }
  for (const entry of listing.document.occurrence.entries) {
    if (OCCURRENCE_SELECTION_RE.test(entry.key) || MEDIA_SELECTION_RE.test(entry.key)) {
      requests += 1;
      egressBytes += observedRead(
        entry,
        occurrenceReleaseStoreContract.maximumSelectionBytes,
        "occurrence selection",
      );
    } else if (OCCURRENCE_MANIFEST_RE.test(entry.key)) {
      requests += 1;
      egressBytes += observedRead(
        entry,
        occurrenceReleaseStoreContract.maximumManifestBytes,
        "occurrence manifest",
      );
    } else if (MEDIA_GENERATION_RE.test(entry.key)) {
      requests += 1;
      egressBytes += observedRead(
        entry,
        occurrenceReleaseStoreContract.maximumGenerationBytes,
        "occurrence generation",
      );
    }
  }
  return Object.freeze({
    contract: "verdify.lab-release-storage-s3-inventory-read-plan",
    schemaVersion: 1,
    listingSha256: listing.sha256,
    canonicalObjectCount: requests,
    usage: Object.freeze({ writtenBytes: 0, deletedBytes: 0, egressBytes, requests }),
  });
}

export async function captureReleaseStorageS3Inventory({
  siteStore,
  occurrenceStore,
  capturedAt,
  maximumObjects = MAX_INVENTORY_OBJECTS,
  listing = null,
}) {
  maximumInventoryObjects(maximumObjects);
  instant(capturedAt, "release inventory capture time");
  storePair(siteStore, occurrenceStore);
  const selectedListing = listing ?? await listReleaseStorageS3Inventory({
    siteStore,
    occurrenceStore,
    maximumObjects,
  });
  const listed = validateListing(selectedListing, siteStore, occurrenceStore, maximumObjects);
  const [site, occurrence] = await Promise.all([
    siteInventory(siteStore, listed.site.entries),
    occurrenceInventory(occurrenceStore, listed.occurrence.entries),
  ]);
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
  listing: Object.freeze({
    contract: "verdify.lab-release-storage-s3-listing",
    schemaVersion: 1,
    maximumPagesPerRoot: MAX_LIST_PAGES_PER_ROOT,
    pageReservationUsage: Object.freeze({
      writtenBytes: 0,
      deletedBytes: 0,
      egressBytes: LIST_PAGE_EGRESS_BYTES,
      requests: 1,
    }),
  }),
  checkpoint: siteReleaseCheckpointContract,
  contract: "verdify.lab-release-storage-inventory",
  schemaVersion: 1,
});
