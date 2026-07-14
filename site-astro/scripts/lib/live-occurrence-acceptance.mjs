import { createHash } from "node:crypto";

import { validateStaticOccurrenceManifest } from "./occurrence-export-contract.mjs";

export const LIVE_OCCURRENCE_EXPECTATIONS = Object.freeze({
  graphCount: 143,
  currentMediaCount: 2,
  maximumBlobBytes: 32 * 1024 * 1024,
  maximumTotalBlobBytes: 1024 * 1024 * 1024,
  immutableCacheControl: "public, max-age=31536000, immutable",
});

export const LIVE_OCCURRENCE_ATTESTED_ORIGIN = "https://lab-stage.verdify.ai";

const SHA256_RE = /^(?:sha256:)?([0-9a-f]{64})$/u;
const PNG_PATH_RE = /^\/evidence\/blobs\/sha256\/([0-9a-f]{64})\.png$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u;
const GRAPH_SELECTED_KEYS = [
  "occurrenceId",
  "route",
  "ordinal",
  "semanticRole",
  "uid",
  "panelId",
  "query",
  "variables",
  "timeRange",
  "liveUrl",
  "renderCadenceSeconds",
  "staleAfterSeconds",
  "probeStatus",
  "state",
  "fallback",
];
const CAMERA_SELECTED_KEYS = [
  "occurrenceId",
  "route",
  "ordinal",
  "classification",
  "semanticRole",
  "sourceProvenanceSha256",
  "stableTarget",
  "captureCadenceSeconds",
  "policySha256",
  "requestProvenanceSha256",
  "staleAfterSeconds",
  "captureStatus",
  "state",
  "fallback",
  "pointer",
];
const FALLBACK_KEYS = [
  "publicPath",
  "sha256",
  "decodedSha256",
  "decodedBytes",
  "bytes",
  "mediaType",
  "width",
  "height",
  "capturedAt",
  "verifiedAt",
  "policyVersion",
];
const CAMERA_POINTER_KEYS = [
  "selectionSha256",
  "generation",
  "currentGenerationSha256",
  "previousGenerationSha256",
];

function requireRecord(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value;
}

function exactKeys(value, keys) {
  return requireRecord(value, "closed record")
    && Object.keys(value).join(",") === keys.join(",");
}

function requirePositiveInteger(value, label, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) {
    throw new Error(`${label} must be a positive bounded integer`);
  }
  return value;
}

function requireOccurrenceId(value, label) {
  if (!/^(?:graph|media)_[0-9a-f]{24}$/u.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function requireInstant(value, label) {
  if (!ISO_INSTANT_RE.test(value)) throw new Error(`${label} is not a canonical UTC instant`);
  const parsed = Date.parse(value);
  const normalized = Number.isFinite(parsed) ? new Date(parsed).toISOString() : "";
  const expected = value.includes(".") ? normalized : normalized.replace(".000Z", "Z");
  if (value !== expected) throw new Error(`${label} is not a real canonical UTC instant`);
  return value;
}

function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function normalizeSha256(value, label = "SHA-256") {
  if (typeof value !== "string") throw new Error(`${label} is missing`);
  const match = SHA256_RE.exec(value);
  if (!match) throw new Error(`${label} is not a canonical SHA-256`);
  return match[1];
}

function normalizeHttpOrigin(value, label) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${label} is not a URL`);
  }
  if (
    !["http:", "https:"].includes(parsed.protocol)
    || parsed.username
    || parsed.password
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
    || (value !== parsed.origin && value !== `${parsed.origin}/`)
  ) {
    throw new Error(`${label} must be a canonical HTTP(S) origin without credentials, path, query, or fragment`);
  }
  return parsed.origin;
}

export function normalizeLiveOccurrenceOrigin(value) {
  return normalizeHttpOrigin(value, "live occurrence origin");
}

export function normalizeLiveOccurrenceTransportOrigin(value) {
  const origin = normalizeHttpOrigin(value, "live occurrence transport origin");
  const { hostname } = new URL(origin);
  const octets = hostname.split(".");
  const ipv4Loopback = octets.length === 4
    && octets[0] === "127"
    && octets.every((octet) => /^(?:0|[1-9][0-9]{0,2})$/u.test(octet) && Number(octet) <= 255);
  if (!ipv4Loopback && hostname !== "[::1]") {
    throw new Error("live occurrence transport origin must use a canonical literal loopback host in 127/8 or [::1]");
  }
  return origin;
}

export function canonicalEvidenceBlobUrl(origin, fallback) {
  const normalizedOrigin = normalizeLiveOccurrenceOrigin(origin);
  requireRecord(fallback, "occurrence fallback");
  const digest = normalizeSha256(fallback.sha256, "occurrence fallback digest");
  if (fallback.sha256 !== digest) {
    throw new Error("occurrence fallback digest must use the raw lowercase representation");
  }
  const expectedPath = `/evidence/blobs/sha256/${digest}.png`;
  if (fallback.publicPath !== expectedPath || !PNG_PATH_RE.test(fallback.publicPath)) {
    throw new Error("occurrence fallback does not use its canonical evidence blob path");
  }
  const url = new URL(fallback.publicPath, `${normalizedOrigin}/`);
  if (
    url.origin !== normalizedOrigin
    || url.pathname !== expectedPath
    || url.search
    || url.hash
    || url.username
    || url.password
  ) {
    throw new Error("occurrence fallback is not a canonical same-origin evidence blob");
  }
  return url.href;
}

function validateFallback(origin, fallback, label) {
  requireRecord(fallback, `${label} fallback`);
  if (!exactKeys(fallback, FALLBACK_KEYS)) {
    throw new Error(`${label} fallback does not use the closed occurrence shape`);
  }
  const sha256 = normalizeSha256(fallback.sha256, `${label} fallback digest`);
  const url = canonicalEvidenceBlobUrl(origin, fallback);
  if (
    fallback.sha256 !== sha256
    || fallback.decodedSha256 !== normalizeSha256(fallback.decodedSha256, `${label} decoded fallback digest`)
  ) {
    throw new Error(`${label} fallback digests must use their raw lowercase representations`);
  }
  if (fallback.mediaType !== "image/png") {
    throw new Error(`${label} fallback is not declared as image/png`);
  }
  requirePositiveInteger(
    fallback.bytes,
    `${label} fallback bytes`,
    LIVE_OCCURRENCE_EXPECTATIONS.maximumBlobBytes,
  );
  requirePositiveInteger(fallback.decodedBytes, `${label} decoded fallback bytes`, 128 * 1024 * 1024);
  requirePositiveInteger(fallback.width, `${label} fallback width`);
  requirePositiveInteger(fallback.height, `${label} fallback height`);
  requireInstant(fallback.capturedAt, `${label} fallback capture time`);
  requireInstant(fallback.verifiedAt, `${label} fallback verification time`);
  if (
    Date.parse(fallback.capturedAt) > Date.parse(fallback.verifiedAt)
    || typeof fallback.policyVersion !== "string"
    || fallback.policyVersion.length < 1
    || fallback.policyVersion.length > 256
    || /[\u0000-\u001f\u007f]/u.test(fallback.policyVersion)
  ) {
    throw new Error(`${label} fallback timing or policy identity is invalid`);
  }
  return Object.freeze({
    ...fallback,
    url,
  });
}

function validateSelectedBinding(occurrence, discovered, kind) {
  const selected = occurrence.selected;
  const expectedKeys = kind === "graph" ? GRAPH_SELECTED_KEYS : CAMERA_SELECTED_KEYS;
  if (!exactKeys(selected, expectedKeys)) {
    throw new Error(`${kind} occurrence ${occurrence.occurrenceId} selected record does not use the closed shape`);
  }
  for (const [key, value] of Object.entries(discovered)) {
    if (!sameValue(selected[key], value)) {
      throw new Error(`${kind} occurrence ${occurrence.occurrenceId} selected record is not bound to its discovery identity`);
    }
  }
  if (kind === "graph") {
    if (
      selected.staleAfterSeconds !== Math.max(selected.renderCadenceSeconds * 2, 30 * 60)
      || !["success", "timeout", "http-error", "decode-error", "missing", "policy-rejected"].includes(selected.probeStatus)
      || !["verified", "retained-last-known-good"].includes(selected.state)
      || (selected.state === "verified" && selected.probeStatus !== "success")
    ) {
      throw new Error(`graph occurrence ${occurrence.occurrenceId} selected state is inconsistent`);
    }
    return;
  }
  if (
    selected.policySha256 !== normalizeSha256(selected.policySha256, "selected camera policy digest")
    || selected.requestProvenanceSha256
      !== normalizeSha256(selected.requestProvenanceSha256, "selected camera request provenance digest")
    || selected.staleAfterSeconds !== Math.max(selected.captureCadenceSeconds * 2, 15 * 60)
    || selected.captureStatus !== "selected-generation"
    || selected.state !== "verified"
    || !exactKeys(selected.pointer, CAMERA_POINTER_KEYS)
    || selected.pointer.selectionSha256
      !== normalizeSha256(selected.pointer.selectionSha256, "selected camera pointer digest")
    || !Number.isSafeInteger(selected.pointer.generation)
    || selected.pointer.generation < 1
    || selected.pointer.currentGenerationSha256
      !== normalizeSha256(selected.pointer.currentGenerationSha256, "selected camera generation digest")
    || (
      selected.pointer.previousGenerationSha256 !== null
      && selected.pointer.previousGenerationSha256
        !== normalizeSha256(selected.pointer.previousGenerationSha256, "selected camera previous generation digest")
    )
  ) {
    throw new Error(`camera occurrence ${occurrence.occurrenceId} selected state is inconsistent`);
  }
}

function validateOccurrenceGroup(origin, values, discoveredValues, kind, expectedCount, seenIds, assets) {
  if (!Array.isArray(values) || values.length !== expectedCount) {
    throw new Error(`live occurrence manifest must contain exactly ${expectedCount} ${kind} occurrences`);
  }
  for (let index = 0; index < values.length; index += 1) {
    const occurrence = requireRecord(values[index], `${kind} occurrence ${index}`);
    const occurrenceId = requireOccurrenceId(occurrence.occurrenceId, `${kind} occurrence ${index} ID`);
    if (seenIds.has(occurrenceId)) throw new Error("live occurrence manifest contains a duplicate occurrence ID");
    seenIds.add(occurrenceId);
    if (
      occurrence.selected === null
      || typeof occurrence.selected !== "object"
      || Array.isArray(occurrence.selected)
      || occurrence.selected.fallback === null
      || typeof occurrence.selected.fallback !== "object"
      || Array.isArray(occurrence.selected.fallback)
    ) {
      throw new Error(`${kind} occurrence ${occurrenceId} is pending or lacks a selected fallback`);
    }
    validateSelectedBinding(occurrence, discoveredValues[index], kind);
    const fallback = validateFallback(origin, occurrence.selected.fallback, `${kind} occurrence ${occurrenceId}`);
    const prior = assets.get(fallback.sha256);
    if (prior && !sameValue(prior, fallback)) {
      throw new Error("one occurrence blob digest has conflicting selected metadata");
    }
    assets.set(fallback.sha256, prior ?? fallback);
  }
}

export function validateLiveOccurrenceDocuments({
  origin,
  build,
  occurrenceManifest,
  occurrenceManifestBytes,
}) {
  const normalizedOrigin = normalizeLiveOccurrenceOrigin(origin);
  if (normalizedOrigin !== LIVE_OCCURRENCE_ATTESTED_ORIGIN) {
    throw new Error(`live occurrence acceptance is bound to ${LIVE_OCCURRENCE_ATTESTED_ORIGIN}`);
  }
  requireRecord(build, "static build");
  requireRecord(occurrenceManifest, "occurrence manifest");
  if (!Buffer.isBuffer(occurrenceManifestBytes) || occurrenceManifestBytes.length < 1) {
    throw new Error("occurrence manifest bytes are required for live acceptance");
  }
  if (
    build.contract !== "verdify.lab-astro-stage-build"
    || build.schemaVersion !== 1
    || build.siteOrigin !== normalizedOrigin
    || build.stageGlobalNoindex !== true
  ) {
    throw new Error("static build identity, live origin, or stage noindex binding is invalid");
  }
  if (
    occurrenceManifest.contract !== "verdify.lab-static-occurrence-manifest"
    || occurrenceManifest.schemaVersion !== 1
    || occurrenceManifest.snapshotId !== build.snapshotId
  ) {
    throw new Error("occurrence manifest identity or snapshot binding is invalid");
  }

  const buildSelection = normalizeSha256(
    build.selectedOccurrenceManifestSha256,
    "static build selected occurrence manifest digest",
  );
  const manifestSelection = normalizeSha256(
    occurrenceManifest.selectedManifestSha256,
    "occurrence manifest selected release digest",
  );
  if (
    build.selectedOccurrenceManifestSha256 !== `sha256:${buildSelection}`
    || occurrenceManifest.selectedManifestSha256 !== manifestSelection
  ) {
    throw new Error("selected occurrence release identities do not use their required prefixed/raw representations");
  }
  if (buildSelection !== manifestSelection) {
    throw new Error("static build and occurrence manifest select different releases");
  }

  const occurrenceManifestSha256 = createHash("sha256").update(occurrenceManifestBytes).digest("hex");
  const boundOccurrenceManifestSha256 = normalizeSha256(
    build.occurrenceManifestDigest,
    "static build occurrence manifest digest",
  );
  if (
    build.occurrenceManifestDigest !== `sha256:${boundOccurrenceManifestSha256}`
    || boundOccurrenceManifestSha256 !== occurrenceManifestSha256
  ) {
    throw new Error("served occurrence manifest bytes do not match the static build digest");
  }
  if (
    build.grafanaOccurrenceCount !== LIVE_OCCURRENCE_EXPECTATIONS.graphCount
    || build.currentMediaOccurrenceCount !== LIVE_OCCURRENCE_EXPECTATIONS.currentMediaCount
    || build.cameraOccurrenceCount !== LIVE_OCCURRENCE_EXPECTATIONS.currentMediaCount
  ) {
    throw new Error("static build does not attest the exact 143-graph and two-camera inventory");
  }

  const discovered = validateStaticOccurrenceManifest(occurrenceManifest);
  const seenIds = new Set();
  const assets = new Map();
  validateOccurrenceGroup(
    normalizedOrigin,
    occurrenceManifest.graphs,
    discovered.graphs,
    "graph",
    LIVE_OCCURRENCE_EXPECTATIONS.graphCount,
    seenIds,
    assets,
  );
  validateOccurrenceGroup(
    normalizedOrigin,
    occurrenceManifest.currentMedia,
    discovered.currentMedia,
    "camera",
    LIVE_OCCURRENCE_EXPECTATIONS.currentMediaCount,
    seenIds,
    assets,
  );

  if (build.cameraLocalFallbackCount !== LIVE_OCCURRENCE_EXPECTATIONS.currentMediaCount) {
    throw new Error("static build does not attest both selected camera fallbacks");
  }
  if (build.materializedOccurrenceBlobCount !== assets.size) {
    throw new Error("materialized occurrence blob count differs from the unique selected fallback set");
  }
  const totalBlobBytes = [...assets.values()].reduce((total, asset) => total + asset.bytes, 0);
  if (!Number.isSafeInteger(totalBlobBytes) || totalBlobBytes > LIVE_OCCURRENCE_EXPECTATIONS.maximumTotalBlobBytes) {
    throw new Error("selected occurrence blobs exceed the aggregate live-acceptance byte bound");
  }

  return Object.freeze({
    contract: "verdify.lab-live-occurrence-document-acceptance",
    schemaVersion: 1,
    origin: normalizedOrigin,
    selectedManifestSha256: `sha256:${buildSelection}`,
    occurrenceManifestSha256: `sha256:${occurrenceManifestSha256}`,
    counts: Object.freeze({
      graphs: LIVE_OCCURRENCE_EXPECTATIONS.graphCount,
      currentMedia: LIVE_OCCURRENCE_EXPECTATIONS.currentMediaCount,
      occurrences: seenIds.size,
      materializedBlobs: assets.size,
      blobBytes: totalBlobBytes,
    }),
    assets: Object.freeze([...assets.values()].sort((left, right) => left.sha256.localeCompare(right.sha256))),
  });
}
