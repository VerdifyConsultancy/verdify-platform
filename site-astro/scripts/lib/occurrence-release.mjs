import { createHash, randomUUID } from "node:crypto";
import { constants as fsConstants, copyFile, link, lstat, mkdir, open, readdir, realpath, rename, unlink } from "node:fs/promises";
import path from "node:path";

import { validatePngFile } from "./png-validation.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/;
const OCCURRENCE_ID_RE = /^(?:graph|media)_[0-9a-f]{24}$/;
const EVENT_ID_RE = /^evt_[A-Za-z0-9_-]{8,128}$/;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const MAX_MANIFEST_BYTES = 8 * 1024 * 1024;
const MAX_RELEASE_ENCODED_BYTES = 1024 * 1024 * 1024;
const MAX_RELEASE_DECODED_BYTES = 1024 * 1024 * 1024;
const GRAPH_MIN_STALE_SECONDS = 30 * 60;
const CURRENT_MEDIA_MIN_STALE_SECONDS = 15 * 60;
const ARCHIVAL_MEDIA_MIN_STALE_SECONDS = 24 * 60 * 60;
const EVENT_FRESHNESS = {
  "planner-completed": { targetSeconds: 5 * 60, alertAfterSeconds: 15 * 60 },
  "forecast-published": { targetSeconds: 10 * 60, alertAfterSeconds: 15 * 60 },
  "dataset-published": { targetSeconds: 15 * 60, alertAfterSeconds: 30 * 60 },
  "graph-fallback-updated": { targetSeconds: 30 * 60, alertAfterSeconds: 30 * 60 },
  "current-media-updated": { targetSeconds: 15 * 60, alertAfterSeconds: 15 * 60 },
  // A reconciliation carries the reporting-feed source watermark. Keep the
  // per-release target aligned with the <=15 minute public-evidence SLO while
  // reserving alert state for the >30 minute stale boundary.
  reconciliation: { targetSeconds: 15 * 60, alertAfterSeconds: 30 * 60 },
};

class CandidateImageError extends Error {}

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function readBoundedSingleLink(file, maximumBytes, label) {
  const handle = await open(file, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  try {
    const metadata = await handle.stat({ bigint: true });
    if (!metadata.isFile() || metadata.nlink !== 1n || metadata.size < 1n || metadata.size > BigInt(maximumBytes)) {
      throw new Error(`${label} file is invalid`);
    }
    const bytes = await handle.readFile();
    const after = await handle.stat({ bigint: true });
    if (
      bytes.length !== Number(metadata.size)
      || after.dev !== metadata.dev
      || after.ino !== metadata.ino
      || after.size !== metadata.size
      || after.nlink !== 1n
    ) {
      throw new Error(`${label} changed while being read`);
    }
    return bytes;
  } finally {
    await handle.close();
  }
}

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).join(",") === keys.join(",");
}

function requireSafeText(value, label, maximum = 256) {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum || /[\u0000-\u001f\u007f]/u.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function requireInstant(value, label) {
  requireSafeText(value, label, 32);
  const milliseconds = Date.parse(value);
  const normalized = Number.isFinite(milliseconds) ? new Date(milliseconds).toISOString() : "";
  const expected = value.includes(".") ? normalized : normalized.replace(".000Z", "Z");
  if (!ISO_INSTANT_RE.test(value) || value !== expected) throw new Error(`${label} is invalid`);
  return value;
}

function occurrenceId(kind, identity) {
  return `${kind}_${sha256(Buffer.from(JSON.stringify(identity))).slice(0, 24)}`;
}

export function occurrenceReleasePayloadSha256({
  sourceSnapshotManifestSha256,
  policyVersion,
  policySha256,
  graphs = [],
  currentMedia = [],
}) {
  return sha256(canonicalBytes({
    contract: "verdify.lab-occurrence-release-payload",
    schemaVersion: 2,
    sourceSnapshotManifestSha256,
    policyVersion,
    policySha256,
    graphs,
    currentMedia,
  }));
}

export function currentMediaGenerationPayloadSha256({
  policyVersion,
  policySha256,
  requestProvenanceSha256,
  occurrence,
  candidate,
}) {
  return sha256(canonicalBytes({
    contract: "verdify.lab-current-media-generation-payload",
    schemaVersion: 3,
    policyVersion,
    policySha256,
    requestProvenanceSha256,
    occurrence,
    candidate,
  }));
}

function safeRoute(route) {
  requireSafeText(route, "route", 2048);
  if (!route.startsWith("/") || route.includes("\\") || route.includes("?") || route.includes("#")) {
    throw new Error("route is invalid");
  }
  const normalized = path.posix.normalize(route);
  if (normalized !== route && `${normalized}/` !== route) throw new Error("route is invalid");
  return route;
}

function boundedPositiveInteger(value, label, maximum = 86_400) {
  if (!Number.isSafeInteger(value) || value <= 0 || value > maximum) throw new Error(`${label} is invalid`);
  return value;
}

function normalizedQuery(parsed) {
  const result = {};
  const names = [...new Set(parsed.searchParams.keys())].sort();
  for (const name of names) result[name] = parsed.searchParams.getAll(name);
  return result;
}

function graphTarget(liveUrl) {
  const parsed = new URL(liveUrl);
  if (
    parsed.protocol !== "https:"
    || parsed.hostname !== "graphs.verdify.ai"
    || parsed.username
    || parsed.password
    || parsed.hash
  ) {
    throw new Error("graph live target is outside the approved origin");
  }
  const route = /^\/(?:d-solo|d)\/([^/]+)(?:\/[^/?]*)?$/.exec(parsed.pathname);
  if (!route) throw new Error("graph live target is invalid");
  const uid = decodeURIComponent(route[1]);
  requireSafeText(uid, "graph dashboard UID", 128);
  const panelValues = parsed.searchParams.getAll("panelId");
  if (panelValues.length > 1 || (panelValues.length === 1 && !/^\d+$/.test(panelValues[0]))) {
    throw new Error("graph panel selection is invalid");
  }
  const panelId = panelValues[0] ?? "";
  const query = normalizedQuery(parsed);
  if (Object.keys(query).some((name) => /(?:token|secret|password|credential|api[-_]?key|auth)/i.test(name))) {
    throw new Error("graph live target contains a credential-like query key");
  }
  const variables = Object.fromEntries(Object.entries(query).filter(([name]) => name.startsWith("var-")));
  const timeRange = {
    from: query.from?.at(0) ?? "",
    to: query.to?.at(0) ?? "",
  };
  return { liveUrl: parsed.toString(), uid, panelId, query, variables, timeRange };
}

export function discoverGraphOccurrence({ route, ordinal, liveUrl, title = "Greenhouse evidence graph", renderCadenceSeconds = 900 }) {
  safeRoute(route);
  if (!Number.isSafeInteger(ordinal) || ordinal < 0 || ordinal > 10_000) throw new Error("graph ordinal is invalid");
  const target = graphTarget(liveUrl);
  const id = occurrenceId("graph", { route, ordinal, liveUrl: target.liveUrl });
  return {
    occurrenceId: id,
    route,
    ordinal,
    semanticRole: requireSafeText(title, "graph semantic role", 512),
    uid: target.uid,
    panelId: target.panelId,
    query: target.query,
    variables: target.variables,
    timeRange: target.timeRange,
    liveUrl: target.liveUrl,
    renderCadenceSeconds: boundedPositiveInteger(renderCadenceSeconds, "graph render cadence"),
  };
}

export function discoverCurrentMediaOccurrence({ route, ordinal, sourceUrl, semanticRole = "Current greenhouse camera", captureCadenceSeconds = 300 }) {
  safeRoute(route);
  if (!Number.isSafeInteger(ordinal) || ordinal < 0 || ordinal > 10_000) throw new Error("media ordinal is invalid");
  let parsed;
  try {
    parsed = new URL(sourceUrl);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" || parsed.hostname !== "api.verdify.ai") return null;
  if (
    parsed.username
    || parsed.password
    || parsed.hash
    || !/^\/api\/v1\/public\/cameras\/[^/]+\/latest\.(?:jpg|png)$/.test(parsed.pathname)
  ) throw new Error("current media source is invalid");
  const queryNames = [...new Set(parsed.searchParams.keys())];
  if (
    queryNames.some((name) => name !== "h")
    || parsed.searchParams.getAll("h").length > 1
    || (parsed.searchParams.has("h") && !/^(?:[1-9]\d{0,2}|1\d{3}|20\d{2}|21[0-5]\d|2160)$/.test(parsed.searchParams.get("h")))
  ) {
    throw new Error("current media source query is invalid");
  }
  const id = occurrenceId("media", { route, ordinal, classification: "current-still" });
  return {
    occurrenceId: id,
    route,
    ordinal,
    classification: "current-still",
    semanticRole: requireSafeText(semanticRole, "media semantic role", 512),
    sourceProvenanceSha256: sha256(Buffer.from(`approved-current-still:${id}`)),
    stableTarget: `/evidence/current/${id}`,
    captureCadenceSeconds: boundedPositiveInteger(captureCadenceSeconds, "media capture cadence"),
  };
}

function validateDiscoveredCurrentMediaOccurrence(occurrence) {
  if (
    !exactKeys(occurrence, [
      "occurrenceId",
      "route",
      "ordinal",
      "classification",
      "semanticRole",
      "sourceProvenanceSha256",
      "stableTarget",
      "captureCadenceSeconds",
    ])
    || occurrence.classification !== "current-still"
    || !SHA256_RE.test(occurrence.sourceProvenanceSha256)
    || !Number.isSafeInteger(occurrence.ordinal)
    || occurrence.ordinal < 0
    || occurrence.ordinal > 10_000
  ) {
    throw new Error("current media occurrence does not use the closed v1 shape");
  }
  safeRoute(occurrence.route);
  requireSafeText(occurrence.semanticRole, "media semantic role", 512);
  boundedPositiveInteger(occurrence.captureCadenceSeconds, "media capture cadence");
  const expectedId = occurrenceId("media", {
    route: occurrence.route,
    ordinal: occurrence.ordinal,
    classification: occurrence.classification,
  });
  if (
    occurrence.occurrenceId !== expectedId
    || occurrence.stableTarget !== `/evidence/current/${occurrence.occurrenceId}`
  ) {
    throw new Error("current media occurrence identity is invalid");
  }
  return occurrence;
}

function validateCurrentMediaReleaseInput(input) {
  if (
    !exactKeys(input, ["discovered", "requestProvenanceSha256"])
    || !SHA256_RE.test(input.requestProvenanceSha256)
  ) {
    throw new Error("current media release input does not use the closed v2 shape");
  }
  validateDiscoveredCurrentMediaOccurrence(input.discovered);
  return input;
}

function validateEvent(event) {
  const keys = [
    "contract",
    "schemaVersion",
    "eventId",
    "eventType",
    "sourceId",
    "sourceWatermark",
    "occurredAt",
    "payloadSha256",
  ];
  if (!exactKeys(event, keys) || event.contract !== "verdify.lab-release-trigger" || event.schemaVersion !== 1) {
    throw new Error("release event does not use the closed v1 contract");
  }
  if (!EVENT_ID_RE.test(event.eventId) || !Object.hasOwn(EVENT_FRESHNESS, event.eventType) || !SHA256_RE.test(event.payloadSha256)) {
    throw new Error("release event identity is invalid");
  }
  requireSafeText(event.sourceId, "release event source ID", 256);
  requireSafeText(event.sourceWatermark, "release event source watermark", 512);
  requireInstant(event.occurredAt, "release event occurrence time");
  return event;
}

export function evaluateEventFreshness(event, publishedAt) {
  validateEvent(event);
  requireInstant(publishedAt, "release publication time");
  const elapsedSeconds = Math.floor((Date.parse(publishedAt) - Date.parse(event.occurredAt)) / 1000);
  if (elapsedSeconds < 0) throw new Error("release publication precedes its event");
  const thresholds = EVENT_FRESHNESS[event.eventType];
  return {
    completedAt: event.occurredAt,
    publishedAt,
    elapsedSeconds,
    targetSeconds: thresholds.targetSeconds,
    alertAfterSeconds: thresholds.alertAfterSeconds,
    status: elapsedSeconds >= thresholds.alertAfterSeconds
      ? "alert"
      : elapsedSeconds > thresholds.targetSeconds
        ? "late"
        : "fresh",
  };
}

export function evaluateOccurrenceFreshness(manifest, asOf) {
  requireInstant(asOf, "occurrence freshness evaluation time");
  if (!manifest?.occurrences || !Array.isArray(manifest.occurrences.graphs) || !Array.isArray(manifest.occurrences.currentMedia)) {
    throw new Error("occurrence freshness manifest is invalid");
  }
  const evaluatedAt = Date.parse(asOf);
  function result(occurrence, timestamp) {
    if (occurrence.fallback === null) {
      return {
        occurrenceId: occurrence.occurrenceId,
        ageSeconds: null,
        staleAfterSeconds: occurrence.staleAfterSeconds,
        status: "missing",
      };
    }
    requireInstant(timestamp, "occurrence evidence time");
    const ageSeconds = Math.floor((evaluatedAt - Date.parse(timestamp)) / 1000);
    if (ageSeconds < 0) throw new Error("occurrence freshness evaluation precedes its evidence");
    return {
      occurrenceId: occurrence.occurrenceId,
      ageSeconds,
      staleAfterSeconds: occurrence.staleAfterSeconds,
      status: ageSeconds > occurrence.staleAfterSeconds ? "alert" : "fresh",
    };
  }
  return {
    evaluatedAt: asOf,
    graphs: manifest.occurrences.graphs.map((occurrence) => result(occurrence, occurrence.fallback?.verifiedAt)),
    currentMedia: manifest.occurrences.currentMedia.map((occurrence) => result(occurrence, occurrence.fallback?.capturedAt)),
  };
}

export function summarizeOccurrenceFreshness(manifest, asOf) {
  const evaluated = evaluateOccurrenceFreshness(manifest, asOf);
  function group(items) {
    const counts = { fresh: 0, alert: 0, missing: 0 };
    for (const item of items) counts[item.status] += 1;
    return { total: items.length, ...counts };
  }
  const graphs = group(evaluated.graphs);
  const currentMedia = group(evaluated.currentMedia);
  return {
    evaluatedAt: evaluated.evaluatedAt,
    status: graphs.alert + graphs.missing + currentMedia.alert + currentMedia.missing > 0 ? "alert" : "fresh",
    graphs,
    currentMedia,
  };
}

async function canonicalStoreRoot(root) {
  const absolute = path.resolve(root);
  const metadata = await lstat(absolute, { bigint: true });
  if (!metadata.isDirectory() || metadata.isSymbolicLink() || (await realpath(absolute)) !== absolute) {
    throw new Error("occurrence store root is invalid");
  }
  return absolute;
}

async function secureDirectory(root, relative, { create = false, leafMode = 0o755 } = {}) {
  let current = root;
  const segments = relative.split("/");
  for (let index = 0; index < segments.length; index += 1) {
    current = path.join(current, segments[index]);
    if (create) {
      try {
        await mkdir(current, { mode: index === segments.length - 1 ? leafMode : 0o755 });
      } catch (error) {
        if (error.code !== "EEXIST") throw error;
      }
    }
    const metadata = await lstat(current, { bigint: true });
    if (!metadata.isDirectory() || metadata.isSymbolicLink() || (await realpath(current)) !== current) {
      throw new Error("occurrence store layout is invalid");
    }
  }
  return current;
}

async function ensureStore(root) {
  const resolved = await canonicalStoreRoot(root);
  for (const relative of ["blobs/sha256", "manifests/sha256", "events/sha256"]) {
    await secureDirectory(resolved, relative, { create: true });
  }
  await secureDirectory(resolved, ".quarantine", { create: true, leafMode: 0o700 });
  return resolved;
}

async function openStore(root) {
  const resolved = await canonicalStoreRoot(root);
  for (const relative of ["blobs/sha256", "manifests/sha256", "events/sha256", ".quarantine"]) {
    await secureDirectory(resolved, relative);
  }
  return resolved;
}

async function syncDirectory(directory) {
  const handle = await open(directory, fsConstants.O_RDONLY);
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function atomicCanonicalWrite(destination, value) {
  const directory = path.dirname(destination);
  const candidate = path.join(directory, `.candidate-${randomUUID()}`);
  const bytes = canonicalBytes(value);
  const handle = await open(candidate, "wx", 0o644);
  try {
    await handle.writeFile(bytes);
    await handle.sync();
  } finally {
    await handle.close();
  }
  await rename(candidate, destination);
  await syncDirectory(directory);
  return sha256(bytes);
}

async function publishCanonicalAbsent(destination, value) {
  const directory = path.dirname(destination);
  const candidate = path.join(directory, `.candidate-${randomUUID()}`);
  const bytes = canonicalBytes(value);
  const handle = await open(candidate, "wx", 0o600);
  try {
    await handle.writeFile(bytes);
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await link(candidate, destination);
    await syncDirectory(directory);
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    const existing = await readBoundedSingleLink(destination, bytes.length, "content-addressed JSON");
    if (!existing.equals(bytes)) throw new Error("content-addressed JSON collision");
  } finally {
    await unlink(candidate).catch(() => {});
  }
  return sha256(bytes);
}

async function importVerifiedPng(storeRoot, sourceRoot, candidate, requireRequestProvenance = false) {
  const keys = requireRequestProvenance
    ? ["relativePath", "expectedSha256", "verifiedAt", "capturedAt", "requestProvenanceSha256"]
    : ["relativePath", "expectedSha256", "verifiedAt", "capturedAt"];
  if (!exactKeys(candidate, keys)) {
    throw new Error("image candidate does not use its closed shape");
  }
  requireInstant(candidate.verifiedAt, "image verification time");
  requireInstant(candidate.capturedAt, "image capture time");
  if (!SHA256_RE.test(candidate.expectedSha256)) throw new Error("image candidate expected digest is invalid");
  if (requireRequestProvenance && !SHA256_RE.test(candidate.requestProvenanceSha256)) {
    throw new Error("current media candidate request provenance is invalid");
  }
  if (Date.parse(candidate.capturedAt) > Date.parse(candidate.verifiedAt)) {
    throw new Error("image verification precedes capture");
  }
  let verified;
  try {
    verified = await validatePngFile(sourceRoot, candidate.relativePath);
  } catch {
    throw new CandidateImageError("candidate image validation failed");
  }
  if (verified.sha256 !== candidate.expectedSha256) {
    throw new CandidateImageError("candidate image changed after prepared verification");
  }
  const filename = `${verified.sha256}.png`;
  const relativeBlob = path.posix.join("blobs", "sha256", filename);
  const destination = path.join(storeRoot, ...relativeBlob.split("/"));
  const temporary = path.join(storeRoot, ".quarantine", `${randomUUID()}.png`);
  try {
    await copyFile(verified.sourcePath, temporary, fsConstants.COPYFILE_EXCL);
    const copied = await validatePngFile(path.dirname(temporary), path.basename(temporary));
    if (copied.sha256 !== verified.sha256 || copied.decodedSha256 !== verified.decodedSha256) {
      throw new CandidateImageError("candidate image changed during import");
    }
    try {
      await link(temporary, destination);
      await syncDirectory(path.dirname(destination));
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      const existing = await validatePngFile(path.dirname(destination), path.basename(destination));
      if (existing.sha256 !== verified.sha256 || existing.decodedSha256 !== verified.decodedSha256) {
        throw new Error("content-addressed image collision");
      }
    }
  } finally {
    await unlink(temporary).catch(() => {});
  }
  return {
    publicPath: `/evidence/blobs/sha256/${filename}`,
    sha256: verified.sha256,
    decodedSha256: verified.decodedSha256,
    decodedBytes: verified.decodedBytes,
    bytes: verified.bytes,
    mediaType: verified.mediaType,
    width: verified.width,
    height: verified.height,
    capturedAt: candidate.capturedAt,
    verifiedAt: candidate.verifiedAt,
  };
}

function priorOccurrence(priorRelease, group, id) {
  return priorRelease?.occurrences?.[group]?.find((occurrence) => occurrence.occurrenceId === id) ?? null;
}

async function resolveGraph(storeRoot, sourceRoot, input, priorRelease, policyVersion) {
  const discovered = discoverGraphOccurrence(input);
  const prior = priorOccurrence(priorRelease, "graphs", discovered.occurrenceId);
  let fallback = null;
  let state = "missing";
  let probeStatus = input.probeStatus;
  if (!["success", "timeout", "http-error", "decode-error", "missing", "policy-rejected"].includes(probeStatus)) {
    throw new Error("graph probe status is invalid");
  }
  if (probeStatus === "success") {
    try {
      fallback = {
        ...(await importVerifiedPng(storeRoot, sourceRoot, input.candidate)),
        policyVersion,
      };
      state = "verified";
    } catch (error) {
      if (!(error instanceof CandidateImageError)) throw error;
      probeStatus = "decode-error";
    }
  }
  if (fallback === null && prior?.fallback) {
    fallback = prior.fallback;
    state = "retained-last-known-good";
  }
  return {
    ...discovered,
    staleAfterSeconds: Math.max(discovered.renderCadenceSeconds * 2, GRAPH_MIN_STALE_SECONDS),
    probeStatus,
    state,
    fallback,
  };
}

function currentMediaGenerationMatches(generation, {
  sourceProvenanceSha256,
  policyVersion,
  policySha256,
  requestProvenanceSha256,
}) {
  return generation?.sourceProvenanceSha256 === sourceProvenanceSha256
    && generation.policyVersion === policyVersion
    && generation.policySha256 === policySha256
    && generation.requestProvenanceSha256 === requestProvenanceSha256;
}

async function resolveSelectedCurrentMedia(storeRoot, input, policyVersion, policySha256) {
  validateCurrentMediaReleaseInput(input);
  const { discovered, requestProvenanceSha256 } = input;
  const pointer = await selectedCurrentMediaPointer(storeRoot, discovered.occurrenceId);
  const identity = {
    sourceProvenanceSha256: discovered.sourceProvenanceSha256,
    policyVersion,
    policySha256,
    requestProvenanceSha256,
  };
  const selected = currentMediaGenerationMatches(pointer?.current, identity)
    ? pointer.current
    : null;
  return {
    ...discovered,
    policySha256,
    requestProvenanceSha256,
    staleAfterSeconds: Math.max(discovered.captureCadenceSeconds * 2, CURRENT_MEDIA_MIN_STALE_SECONDS),
    captureStatus: selected ? "selected-generation" : "missing",
    state: selected ? "verified" : "missing",
    fallback: selected?.fallback ?? null,
    pointer: publicCurrentMediaPointer(selected ? pointer : null),
  };
}

function selectionRecord(current, previous, generation, selectedAt, reason) {
  return {
    contract: "verdify.lab-occurrence-selection",
    schemaVersion: 1,
    generation,
    current,
    previous,
    selectedAt,
    reason,
  };
}

function validatePointer(pointer) {
  if (pointer === null) return null;
  if (!exactKeys(pointer, ["manifestSha256", "eventId"]) || !SHA256_RE.test(pointer.manifestSha256) || !EVENT_ID_RE.test(pointer.eventId)) {
    throw new Error("occurrence selection pointer is invalid");
  }
  return pointer;
}

function currentMediaPointerRecord(occurrenceIdValue, current, previous, generation, selectedAt, reason) {
  return {
    contract: "verdify.lab-current-media-selection",
    schemaVersion: 1,
    occurrenceId: occurrenceIdValue,
    generation,
    current,
    previous,
    selectedAt,
    reason,
  };
}

function validateMediaGenerationPointer(pointer) {
  if (pointer === null) return null;
  if (
    !exactKeys(pointer, ["generationSha256", "blobSha256"])
    || !SHA256_RE.test(pointer.generationSha256)
    || !SHA256_RE.test(pointer.blobSha256)
  ) {
    throw new Error("current media generation pointer is invalid");
  }
  return pointer;
}

function currentMediaDirectory(storeRoot, occurrenceIdValue) {
  if (!/^media_[0-9a-f]{24}$/.test(occurrenceIdValue)) throw new Error("current media occurrence identity is invalid");
  return path.join(storeRoot, "occurrences", occurrenceIdValue);
}

async function readCurrentMediaSelectionFromRoot(storeRoot, occurrenceIdValue) {
  const directory = currentMediaDirectory(storeRoot, occurrenceIdValue);
  try {
    await secureDirectory(storeRoot, `occurrences/${occurrenceIdValue}`);
    await secureDirectory(storeRoot, `occurrences/${occurrenceIdValue}/generations/sha256`);
    await secureDirectory(storeRoot, `occurrences/${occurrenceIdValue}/events/sha256`);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  let bytes;
  try {
    bytes = await readBoundedSingleLink(path.join(directory, "selection.json"), 64 * 1024, "current media selection");
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  let selection;
  try {
    selection = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("current media selection is not valid JSON");
  }
  if (
    !exactKeys(selection, [
      "contract",
      "schemaVersion",
      "occurrenceId",
      "generation",
      "current",
      "previous",
      "selectedAt",
      "reason",
    ])
    || selection.contract !== "verdify.lab-current-media-selection"
    || selection.schemaVersion !== 1
    || selection.occurrenceId !== occurrenceIdValue
    || !Number.isSafeInteger(selection.generation)
    || selection.generation < 1
    || !["publish", "rollback"].includes(selection.reason)
    || canonicalBytes(selection).compare(bytes) !== 0
  ) {
    throw new Error("current media selection does not use the canonical v1 contract");
  }
  validateMediaGenerationPointer(selection.current);
  validateMediaGenerationPointer(selection.previous);
  if (selection.current === null || selection.current.generationSha256 === selection.previous?.generationSha256) {
    throw new Error("current media selection pointers are inconsistent");
  }
  requireInstant(selection.selectedAt, "current media selection time");
  return { selection, selectionSha256: sha256(bytes), directory };
}

async function loadCurrentMediaGenerationFromRoot(storeRoot, occurrenceIdValue, digest) {
  if (!SHA256_RE.test(digest)) throw new Error("current media generation digest is invalid");
  const directory = currentMediaDirectory(storeRoot, occurrenceIdValue);
  await secureDirectory(storeRoot, `occurrences/${occurrenceIdValue}/generations/sha256`);
  const bytes = await readBoundedSingleLink(
    path.join(directory, "generations", "sha256", `${digest}.json`),
    128 * 1024,
    "current media generation",
  );
  if (sha256(bytes) !== digest) throw new Error("current media generation digest mismatch");
  let generation;
  try {
    generation = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("current media generation is not valid JSON");
  }
  if (
    !exactKeys(generation, [
      "contract",
      "schemaVersion",
      "occurrenceId",
      "sourceProvenanceSha256",
      "policySha256",
      "requestProvenanceSha256",
      "event",
      "policyVersion",
      "publishedAt",
      "fallback",
    ])
    || generation.contract !== "verdify.lab-current-media-generation"
    || generation.schemaVersion !== 3
    || generation.occurrenceId !== occurrenceIdValue
    || !SHA256_RE.test(generation.sourceProvenanceSha256)
    || !SHA256_RE.test(generation.policySha256)
    || !SHA256_RE.test(generation.requestProvenanceSha256)
    || canonicalBytes(generation).compare(bytes) !== 0
  ) {
    throw new Error("current media generation does not use the canonical v3 contract");
  }
  validateEvent(generation.event);
  if (generation.event.eventType !== "current-media-updated") throw new Error("current media generation event type is invalid");
  requireSafeText(generation.policyVersion, "current media policy version", 256);
  requireInstant(generation.publishedAt, "current media generation publication time");
  await verifyFallbackBlob(storeRoot, generation.fallback);
  if (Date.parse(generation.fallback.verifiedAt) > Date.parse(generation.publishedAt)) {
    throw new Error("current media fallback verification is newer than its generation");
  }
  return generation;
}

async function selectedCurrentMediaPointer(storeRoot, occurrenceIdValue) {
  const selected = await readCurrentMediaSelectionFromRoot(storeRoot, occurrenceIdValue);
  if (selected === null) return null;
  const current = await loadCurrentMediaGenerationFromRoot(
    storeRoot,
    occurrenceIdValue,
    selected.selection.current.generationSha256,
  );
  const previous = selected.selection.previous
    ? await loadCurrentMediaGenerationFromRoot(storeRoot, occurrenceIdValue, selected.selection.previous.generationSha256)
    : null;
  if (
    current.fallback.sha256 !== selected.selection.current.blobSha256
    || (previous && previous.fallback.sha256 !== selected.selection.previous.blobSha256)
  ) {
    throw new Error("current media selection does not match its generations");
  }
  return { ...selected, current, previous };
}

async function pruneCurrentMediaGenerations(directory, selection, priorSelection = null) {
  const generationDirectory = path.join(directory, "generations", "sha256");
  const names = await readdir(generationDirectory);
  if (names.length > 1000 || names.some((name) => !/^[0-9a-f]{64}\.json$/.test(name))) {
    throw new Error("current media generation store membership is invalid");
  }
  const retained = new Set([
    selection.current.generationSha256,
    selection.previous?.generationSha256,
    priorSelection?.current.generationSha256,
    priorSelection?.previous?.generationSha256,
  ].filter(Boolean));
  for (const name of names) {
    if (!retained.has(name.slice(0, -5))) await unlink(path.join(generationDirectory, name));
  }
  await syncDirectory(generationDirectory);
}

export async function publishCurrentMediaGeneration({
  storeRoot,
  sourceRoot,
  event,
  policyVersion,
  policySha256,
  requestProvenanceSha256,
  publishedAt,
  occurrence,
  candidate,
  expectedSelectionSha256 = null,
  ...unexpected
}) {
  if (Object.keys(unexpected).length > 0) {
    throw new Error("current media generation request does not use the closed v3 shape");
  }
  validateEvent(event);
  if (event.eventType !== "current-media-updated") throw new Error("current media generation event type is invalid");
  requireSafeText(policyVersion, "current media policy version", 256);
  if (!SHA256_RE.test(policySha256)) throw new Error("current media policy digest is invalid");
  if (!SHA256_RE.test(requestProvenanceSha256)) throw new Error("current media request provenance is invalid");
  requireInstant(publishedAt, "current media generation publication time");
  evaluateEventFreshness(event, publishedAt);
  if (expectedSelectionSha256 !== null && !SHA256_RE.test(expectedSelectionSha256)) {
    throw new Error("current media selection precondition is invalid");
  }
  validateDiscoveredCurrentMediaOccurrence(occurrence);
  if (candidate?.requestProvenanceSha256 !== requestProvenanceSha256) {
    throw new Error("current media candidate does not match its expected camera request provenance");
  }
  if (event.payloadSha256 !== currentMediaGenerationPayloadSha256({
    policyVersion,
    policySha256,
    requestProvenanceSha256,
    occurrence,
    candidate,
  })) {
    throw new Error("current media event payload digest mismatch");
  }
  const generationIdentity = {
    sourceProvenanceSha256: occurrence.sourceProvenanceSha256,
    policyVersion,
    policySha256,
    requestProvenanceSha256,
  };
  return withStoreLock(storeRoot, async (root) => {
    const directory = currentMediaDirectory(root, occurrence.occurrenceId);
    await secureDirectory(root, `occurrences/${occurrence.occurrenceId}/generations/sha256`, { create: true });
    await secureDirectory(root, `occurrences/${occurrence.occurrenceId}/events/sha256`, { create: true });
    const selected = await selectedCurrentMediaPointer(root, occurrence.occurrenceId);
    const eventPath = path.join(directory, "events", "sha256", `${sha256(Buffer.from(event.eventId))}.json`);
    try {
      const existing = await readIdempotencyRecord(eventPath);
      if (existing.eventId !== event.eventId || existing.payloadSha256 !== event.payloadSha256) {
        throw new Error("release event ID was reused with different payload");
      }
      if (existing.eventSha256 !== sha256(canonicalBytes(event))) {
        throw new Error("release event ID was reused with a different envelope");
      }
      let generation;
      try {
        generation = await loadCurrentMediaGenerationFromRoot(root, occurrence.occurrenceId, existing.manifestSha256);
      } catch (error) {
        if (error.code === "ENOENT") return { selected, idempotent: true, retained: false };
        throw error;
      }
      if (selected?.selection.current.generationSha256 === existing.manifestSha256) {
        return { selected, idempotent: true, retained: true };
      }
      if ((selected?.selectionSha256 ?? null) === existing.expectedSelectionSha256) {
        const pointer = { generationSha256: existing.manifestSha256, blobSha256: generation.fallback.sha256 };
        const previous = currentMediaGenerationMatches(selected?.current, generation)
          ? selected.selection.current
          : null;
        const next = currentMediaPointerRecord(
          occurrence.occurrenceId,
          pointer,
          previous,
          (selected?.selection.generation ?? 0) + 1,
          generation.publishedAt,
          "publish",
        );
        await pruneCurrentMediaGenerations(directory, next, selected?.selection ?? null);
        await atomicCanonicalWrite(path.join(directory, "selection.json"), next);
        return { selected: await selectedCurrentMediaPointer(root, occurrence.occurrenceId), idempotent: true, retained: true };
      }
      return { selected, idempotent: true, retained: true, ignoredStaleReplay: true };
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (selected && Date.parse(event.occurredAt) < Date.parse(selected.current.event.occurredAt)) {
      throw new Error("current media event is older than the selected generation");
    }
    if (selected && expectedSelectionSha256 === null) throw new Error("current media selection precondition is required");
    if ((selected?.selectionSha256 ?? null) !== expectedSelectionSha256) {
      throw new Error("current media selection precondition failed");
    }
    const fallback = {
      ...(await importVerifiedPng(root, sourceRoot, candidate, true)),
      policyVersion,
    };
    const generation = {
      contract: "verdify.lab-current-media-generation",
      schemaVersion: 3,
      occurrenceId: occurrence.occurrenceId,
      sourceProvenanceSha256: occurrence.sourceProvenanceSha256,
      policySha256,
      requestProvenanceSha256,
      event,
      policyVersion,
      publishedAt,
      fallback,
    };
    const generationSha256 = sha256(canonicalBytes(generation));
    await publishCanonicalAbsent(
      path.join(directory, "generations", "sha256", `${generationSha256}.json`),
      generation,
    );
    const pointer = { generationSha256, blobSha256: fallback.sha256 };
    const previous = currentMediaGenerationMatches(selected?.current, generationIdentity)
      ? selected.selection.current
      : null;
    const next = currentMediaPointerRecord(
      occurrence.occurrenceId,
      pointer,
      previous,
      (selected?.selection.generation ?? 0) + 1,
      publishedAt,
      "publish",
    );
    await publishCanonicalAbsent(eventPath, {
      contract: "verdify.lab-release-idempotency",
      schemaVersion: 1,
      eventId: event.eventId,
      eventSha256: sha256(canonicalBytes(event)),
      payloadSha256: event.payloadSha256,
      manifestSha256: generationSha256,
      expectedSelectionSha256,
    });
    await pruneCurrentMediaGenerations(directory, next, selected?.selection ?? null);
    await atomicCanonicalWrite(path.join(directory, "selection.json"), next);
    return {
      selected: await selectedCurrentMediaPointer(root, occurrence.occurrenceId),
      idempotent: false,
      retained: true,
    };
  });
}

function publicCurrentMediaPointer(selected) {
  if (selected === null) return null;
  return {
    selectionSha256: selected.selectionSha256,
    generation: selected.selection.generation,
    currentGenerationSha256: selected.selection.current.generationSha256,
    previousGenerationSha256: selected.selection.previous?.generationSha256 ?? null,
  };
}

async function readSelection(storeRoot) {
  let bytes;
  try {
    bytes = await readBoundedSingleLink(path.join(storeRoot, "selection.json"), 64 * 1024, "occurrence selection");
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  let selection;
  try {
    selection = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("occurrence selection is not valid JSON");
  }
  if (
    !exactKeys(selection, ["contract", "schemaVersion", "generation", "current", "previous", "selectedAt", "reason"])
    || selection.contract !== "verdify.lab-occurrence-selection"
    || selection.schemaVersion !== 1
    || !Number.isSafeInteger(selection.generation)
    || selection.generation < 1
    || canonicalBytes(selection).compare(bytes) !== 0
  ) {
    throw new Error("occurrence selection does not use the canonical v1 contract");
  }
  if (selection.current === null) throw new Error("occurrence selection has no current release");
  validatePointer(selection.current);
  validatePointer(selection.previous);
  if (selection.previous?.manifestSha256 === selection.current.manifestSha256) {
    throw new Error("occurrence selection current and previous are identical");
  }
  requireInstant(selection.selectedAt, "occurrence selection time");
  if (!["publish", "rollback"].includes(selection.reason)) throw new Error("occurrence selection reason is invalid");
  return selection;
}

async function readIdempotencyRecord(file) {
  const bytes = await readBoundedSingleLink(file, 16 * 1024, "release idempotency record");
  let record;
  try {
    record = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("release idempotency record is not valid JSON");
  }
  if (
    !exactKeys(record, [
      "contract",
      "schemaVersion",
      "eventId",
      "eventSha256",
      "payloadSha256",
      "manifestSha256",
      "expectedSelectionSha256",
    ])
    || record.contract !== "verdify.lab-release-idempotency"
    || record.schemaVersion !== 1
    || !EVENT_ID_RE.test(record.eventId)
    || !SHA256_RE.test(record.eventSha256)
    || !SHA256_RE.test(record.payloadSha256)
    || !SHA256_RE.test(record.manifestSha256)
    || (record.expectedSelectionSha256 !== null && !SHA256_RE.test(record.expectedSelectionSha256))
    || canonicalBytes(record).compare(bytes) !== 0
  ) {
    throw new Error("release idempotency record does not use the canonical v1 contract");
  }
  return record;
}

async function pruneOccurrenceHistory(storeRoot, selection, retain = 10, priorSelection = null) {
  const directory = path.join(storeRoot, "manifests", "sha256");
  const names = await readdir(directory);
  if (names.length > 1000 || names.some((name) => !/^[0-9a-f]{64}\.json$/.test(name))) {
    throw new Error("occurrence manifest store membership is invalid");
  }
  if (names.length <= retain) return;
  const protectedDigests = new Set([
    selection.current.manifestSha256,
    selection.previous?.manifestSha256,
    priorSelection?.current.manifestSha256,
    priorSelection?.previous?.manifestSha256,
  ].filter(Boolean));
  const records = [];
  for (const name of names) {
    const digest = name.slice(0, -5);
    const manifest = await loadManifest(storeRoot, digest);
    records.push({ digest, manifest });
  }
  records.sort((left, right) =>
    right.manifest.publishedAt.localeCompare(left.manifest.publishedAt) || right.digest.localeCompare(left.digest));
  const keep = new Set(protectedDigests);
  for (const record of records) {
    if (keep.size >= retain) break;
    keep.add(record.digest);
  }
  for (const record of records) {
    if (keep.has(record.digest)) continue;
    await unlink(path.join(directory, `${record.digest}.json`));
  }
  await syncDirectory(directory);
}

async function loadManifest(storeRoot, digest) {
  if (!SHA256_RE.test(digest)) throw new Error("occurrence manifest digest is invalid");
  const file = path.join(storeRoot, "manifests", "sha256", `${digest}.json`);
  const bytes = await readBoundedSingleLink(file, MAX_MANIFEST_BYTES, "occurrence manifest");
  if (sha256(bytes) !== digest) throw new Error("occurrence manifest digest mismatch");
  let manifest;
  try {
    manifest = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("occurrence manifest is not valid JSON");
  }
  if (
    !exactKeys(manifest, [
      "contract",
      "schemaVersion",
      "event",
      "policyVersion",
      "policySha256",
      "sourceSnapshotManifestSha256",
      "publishedAt",
      "freshness",
      "occurrences",
    ])
    || manifest.contract !== "verdify.lab-specialist-occurrence-release"
    || manifest.schemaVersion !== 2
    || canonicalBytes(manifest).compare(bytes) !== 0
    || !exactKeys(manifest.occurrences, ["graphs", "currentMedia"])
    || !Array.isArray(manifest.occurrences.graphs)
    || !Array.isArray(manifest.occurrences.currentMedia)
  ) {
    throw new Error("occurrence manifest does not use the canonical v2 contract");
  }
  validateEvent(manifest.event);
  requireSafeText(manifest.policyVersion, "occurrence policy version", 256);
  if (!SHA256_RE.test(manifest.policySha256)) throw new Error("occurrence policy digest is invalid");
  if (!SHA256_RE.test(manifest.sourceSnapshotManifestSha256)) throw new Error("source snapshot manifest digest is invalid");
  requireInstant(manifest.publishedAt, "occurrence manifest publication time");
  return manifest;
}

async function verifyFallbackBlob(storeRoot, fallback) {
  if (fallback === null) return;
  const keys = [
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
  if (
    !exactKeys(fallback, keys)
    || !SHA256_RE.test(fallback.sha256)
    || !SHA256_RE.test(fallback.decodedSha256)
    || fallback.mediaType !== "image/png"
    || fallback.publicPath !== `/evidence/blobs/sha256/${fallback.sha256}.png`
    || !Number.isSafeInteger(fallback.decodedBytes)
    || fallback.decodedBytes <= 0
  ) {
    throw new Error("occurrence fallback does not use the closed v1 shape");
  }
  const relative = path.posix.join("blobs", "sha256", `${fallback.sha256}.png`);
  const verified = await validatePngFile(storeRoot, relative);
  if (
    verified.sha256 !== fallback.sha256
    || verified.decodedSha256 !== fallback.decodedSha256
    || verified.decodedBytes !== fallback.decodedBytes
    || verified.bytes !== fallback.bytes
    || verified.width !== fallback.width
    || verified.height !== fallback.height
  ) {
    throw new Error("occurrence fallback metadata does not match decoded bytes");
  }
  requireInstant(fallback.capturedAt, "fallback capture time");
  requireInstant(fallback.verifiedAt, "fallback verification time");
  requireSafeText(fallback.policyVersion, "fallback policy version", 256);
}

async function verifyReleaseBlobs(storeRoot, manifest) {
  const occurrences = [...manifest.occurrences.graphs, ...manifest.occurrences.currentMedia];
  if (occurrences.length > 10_000) throw new Error("occurrence release exceeds its record limit");
  if (JSON.stringify(manifest.freshness) !== JSON.stringify(evaluateEventFreshness(manifest.event, manifest.publishedAt))) {
    throw new Error("occurrence release freshness does not match its event");
  }
  const seen = new Set();
  for (const occurrence of manifest.occurrences.graphs) {
    const keys = [
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
    if (!exactKeys(occurrence, keys)) throw new Error("graph occurrence does not use the closed v1 shape");
    const normalized = discoverGraphOccurrence({
      route: occurrence.route,
      ordinal: occurrence.ordinal,
      liveUrl: occurrence.liveUrl,
      title: occurrence.semanticRole,
      renderCadenceSeconds: occurrence.renderCadenceSeconds,
    });
    for (const key of Object.keys(normalized)) {
      if (JSON.stringify(occurrence[key]) !== JSON.stringify(normalized[key])) {
        throw new Error("graph occurrence normalization mismatch");
      }
    }
    if (
      occurrence.staleAfterSeconds !== Math.max(occurrence.renderCadenceSeconds * 2, GRAPH_MIN_STALE_SECONDS)
      || !["success", "timeout", "http-error", "decode-error", "missing", "policy-rejected"].includes(occurrence.probeStatus)
      || !["verified", "retained-last-known-good", "missing"].includes(occurrence.state)
      || (occurrence.state === "missing") !== (occurrence.fallback === null)
      || (occurrence.state === "verified" && occurrence.probeStatus !== "success")
      || (occurrence.state === "verified" && occurrence.fallback?.policyVersion !== manifest.policyVersion)
      || (occurrence.state === "retained-last-known-good" && occurrence.fallback === null)
    ) {
      throw new Error("graph occurrence state is inconsistent");
    }
  }
  for (const occurrence of manifest.occurrences.currentMedia) {
    const keys = [
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
    if (
      !exactKeys(occurrence, keys)
      || occurrence.classification !== "current-still"
      || !SHA256_RE.test(occurrence.sourceProvenanceSha256)
      || occurrence.policySha256 !== manifest.policySha256
      || !SHA256_RE.test(occurrence.requestProvenanceSha256)
      || occurrence.stableTarget !== `/evidence/current/${occurrence.occurrenceId}`
      || occurrence.staleAfterSeconds !== Math.max(occurrence.captureCadenceSeconds * 2, CURRENT_MEDIA_MIN_STALE_SECONDS)
      || !["selected-generation", "missing"].includes(occurrence.captureStatus)
      || !["verified", "missing"].includes(occurrence.state)
      || (occurrence.state === "missing") !== (occurrence.fallback === null)
      || (occurrence.state === "missing") !== (occurrence.pointer === null)
      || (occurrence.state === "verified" && occurrence.captureStatus !== "selected-generation")
      || (occurrence.state === "verified" && occurrence.fallback?.policyVersion !== manifest.policyVersion)
    ) {
      throw new Error("current media occurrence state is inconsistent");
    }
    if (occurrence.pointer !== null) {
      if (
        !exactKeys(occurrence.pointer, [
          "selectionSha256",
          "generation",
          "currentGenerationSha256",
          "previousGenerationSha256",
        ])
        || !SHA256_RE.test(occurrence.pointer.selectionSha256)
        || !Number.isSafeInteger(occurrence.pointer.generation)
        || occurrence.pointer.generation < 1
        || !SHA256_RE.test(occurrence.pointer.currentGenerationSha256)
        || (occurrence.pointer.previousGenerationSha256 !== null && !SHA256_RE.test(occurrence.pointer.previousGenerationSha256))
      ) {
        throw new Error("current media occurrence pointer is invalid");
      }
    }
    safeRoute(occurrence.route);
    requireSafeText(occurrence.semanticRole, "media semantic role", 512);
    boundedPositiveInteger(occurrence.captureCadenceSeconds, "media capture cadence");
    if (!Number.isSafeInteger(occurrence.ordinal) || occurrence.ordinal < 0 || occurrence.ordinal > 10_000) {
      throw new Error("media ordinal is invalid");
    }
    if (occurrence.occurrenceId !== occurrenceId("media", {
      route: occurrence.route,
      ordinal: occurrence.ordinal,
      classification: occurrence.classification,
    })) {
      throw new Error("current media occurrence identity mismatch");
    }
  }
  const uniqueFallbacks = new Map();
  for (const occurrence of occurrences) {
    if (!OCCURRENCE_ID_RE.test(occurrence.occurrenceId) || seen.has(occurrence.occurrenceId)) {
      throw new Error("occurrence release has an invalid or duplicate occurrence ID");
    }
    seen.add(occurrence.occurrenceId);
    if (occurrence.fallback && Date.parse(occurrence.fallback.verifiedAt) > Date.parse(manifest.publishedAt)) {
      throw new Error("occurrence fallback verification is newer than its release");
    }
    if (occurrence.fallback) {
      const existing = uniqueFallbacks.get(occurrence.fallback.sha256);
      if (existing && JSON.stringify(existing) !== JSON.stringify(occurrence.fallback)) {
        throw new Error("occurrence release has conflicting metadata for one fallback digest");
      }
      uniqueFallbacks.set(occurrence.fallback.sha256, occurrence.fallback);
    }
  }
  let encodedBytes = 0;
  let decodedBytes = 0;
  for (const fallback of uniqueFallbacks.values()) {
    encodedBytes += fallback.bytes;
    decodedBytes += fallback.decodedBytes;
    if (encodedBytes > MAX_RELEASE_ENCODED_BYTES || decodedBytes > MAX_RELEASE_DECODED_BYTES) {
      throw new Error("occurrence release exceeds its aggregate image byte budget");
    }
    await verifyFallbackBlob(storeRoot, fallback);
  }
}

export async function loadSelectedOccurrenceRelease(storeRoot) {
  const root = await openStore(storeRoot);
  const selection = await readSelection(root);
  if (selection === null) return { root, selection: null, selectionSha256: null, current: null, previous: null };
  const current = await loadManifest(root, selection.current.manifestSha256);
  const previous = selection.previous ? await loadManifest(root, selection.previous.manifestSha256) : null;
  await verifyReleaseBlobs(root, current);
  if (previous) await verifyReleaseBlobs(root, previous);
  return { root, selection, selectionSha256: sha256(canonicalBytes(selection)), current, previous };
}

async function withStoreLock(storeRoot, callback) {
  const root = await ensureStore(storeRoot);
  const lockPath = path.join(root, ".publish.lock");
  let handle;
  try {
    handle = await open(lockPath, "wx", 0o600);
  } catch (error) {
    if (error.code === "EEXIST") throw new Error("another cooperating occurrence publisher is active");
    throw error;
  }
  const identity = await handle.stat({ bigint: true });
  try {
    return await callback(root);
  } finally {
    await handle.close().catch(() => {});
    try {
      const selected = await lstat(lockPath, { bigint: true });
      if (selected.isFile() && selected.dev === identity.dev && selected.ino === identity.ino) await unlink(lockPath);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
}

export async function publishOccurrenceRelease({
  storeRoot,
  sourceRoot,
  event,
  sourceSnapshotManifestSha256,
  policyVersion,
  policySha256,
  publishedAt,
  graphs = [],
  currentMedia = [],
  expectedSelectionSha256 = null,
  ...unexpected
}) {
  if (Object.keys(unexpected).length > 0) {
    throw new Error("occurrence release request does not use the closed v2 shape");
  }
  validateEvent(event);
  requireInstant(publishedAt, "release publication time");
  requireSafeText(policyVersion, "occurrence policy version", 256);
  if (!SHA256_RE.test(policySha256)) throw new Error("occurrence policy digest is invalid");
  if (!SHA256_RE.test(sourceSnapshotManifestSha256)) throw new Error("source snapshot manifest digest is invalid");
  if (expectedSelectionSha256 !== null && !SHA256_RE.test(expectedSelectionSha256)) throw new Error("expected selection digest is invalid");
  if (!Array.isArray(graphs) || !Array.isArray(currentMedia) || graphs.length + currentMedia.length > 10_000) {
    throw new Error("occurrence candidates exceed their record limit");
  }
  for (const media of currentMedia) validateCurrentMediaReleaseInput(media);
  if (event.eventType === "current-media-updated") {
    throw new Error("current media updates require the independent generation publisher");
  }
  if (event.eventType === "graph-fallback-updated" && currentMedia.length > 0) {
    throw new Error("graph fallback events cannot mutate current media occurrences");
  }
  if (event.payloadSha256 !== occurrenceReleasePayloadSha256({
    sourceSnapshotManifestSha256,
    policyVersion,
    policySha256,
    graphs,
    currentMedia,
  })) {
    throw new Error("occurrence release event payload digest mismatch");
  }
  return withStoreLock(storeRoot, async (root) => {
    const selected = await loadSelectedOccurrenceRelease(root);
    const eventKey = sha256(Buffer.from(event.eventId));
    const eventPath = path.join(root, "events", "sha256", `${eventKey}.json`);
    try {
      const existing = await readIdempotencyRecord(eventPath);
      if (existing.eventId !== event.eventId || existing.payloadSha256 !== event.payloadSha256) {
        throw new Error("release event ID was reused with different payload");
      }
      if (existing.eventSha256 !== sha256(canonicalBytes(event))) {
        throw new Error("release event ID was reused with a different envelope");
      }
      let manifest;
      try {
        manifest = await loadManifest(root, existing.manifestSha256);
      } catch (error) {
        if (error.code === "ENOENT") {
          return { manifestSha256: existing.manifestSha256, manifest: null, idempotent: true, retained: false };
        }
        throw error;
      }
      if (selected.selection?.current.manifestSha256 === existing.manifestSha256) {
        return { manifestSha256: existing.manifestSha256, manifest, idempotent: true, retained: true };
      }
      if (selected.selectionSha256 === existing.expectedSelectionSha256) {
        const pointer = { manifestSha256: existing.manifestSha256, eventId: event.eventId };
        const next = selectionRecord(
          pointer,
          selected.selection?.current ?? null,
          (selected.selection?.generation ?? 0) + 1,
          manifest.publishedAt,
          "publish",
        );
        await pruneOccurrenceHistory(root, next, 10, selected.selection);
        const selectionSha256 = await atomicCanonicalWrite(path.join(root, "selection.json"), next);
        return { manifestSha256: existing.manifestSha256, selectionSha256, manifest, idempotent: true, retained: true };
      }
      return { manifestSha256: existing.manifestSha256, manifest, idempotent: true, retained: true, ignoredStaleReplay: true };
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }

    for (const selectedManifest of [selected.current, selected.previous].filter(Boolean)) {
      if (selectedManifest.event.eventId !== event.eventId) continue;
      if (selectedManifest.event.payloadSha256 !== event.payloadSha256) {
        throw new Error("release event ID was reused with different payload");
      }
      const selectedDigest = selectedManifest === selected.current
        ? selected.selection.current.manifestSha256
        : selected.selection.previous.manifestSha256;
      await publishCanonicalAbsent(eventPath, {
        contract: "verdify.lab-release-idempotency",
        schemaVersion: 1,
        eventId: event.eventId,
        eventSha256: sha256(canonicalBytes(event)),
        payloadSha256: event.payloadSha256,
        manifestSha256: selectedDigest,
        expectedSelectionSha256: null,
      });
      return { manifestSha256: selectedDigest, manifest: selectedManifest, idempotent: true };
    }

    // Reconciliation output depends on the independently selected per-camera
    // generations, so an identical request payload may resolve differently after
    // media-first publication. Graph-only requests remain safely change-gated.
    if (currentMedia.length === 0 && selected.current?.event.payloadSha256 === event.payloadSha256) {
      await publishCanonicalAbsent(eventPath, {
        contract: "verdify.lab-release-idempotency",
        schemaVersion: 1,
        eventId: event.eventId,
        eventSha256: sha256(canonicalBytes(event)),
        payloadSha256: event.payloadSha256,
        manifestSha256: selected.selection.current.manifestSha256,
        expectedSelectionSha256: selected.selectionSha256,
      });
      return {
        manifestSha256: selected.selection.current.manifestSha256,
        manifest: selected.current,
        idempotent: false,
        unchanged: true,
        retained: true,
      };
    }

    if (selected.current && Date.parse(event.occurredAt) < Date.parse(selected.current.event.occurredAt)) {
      throw new Error("occurrence release event is older than the selected release");
    }

    if (selected.selection !== null && expectedSelectionSha256 === null) {
      throw new Error("occurrence selection precondition is required");
    }
    if (selected.selectionSha256 !== expectedSelectionSha256) {
      throw new Error("occurrence selection precondition failed");
    }

    const graphById = new Map((selected.current?.occurrences.graphs ?? []).map((occurrence) => [
      occurrence.occurrenceId,
      occurrence.fallback && occurrence.fallback.policyVersion !== policyVersion
        ? { ...occurrence, probeStatus: "missing", state: "retained-last-known-good" }
        : occurrence,
    ]));
    for (const graph of graphs) {
      const occurrence = await resolveGraph(root, sourceRoot, graph, selected.current, policyVersion);
      graphById.set(occurrence.occurrenceId, occurrence);
    }
    const mediaById = new Map((selected.current?.occurrences.currentMedia ?? []).map((occurrence) => [occurrence.occurrenceId, occurrence]));
    for (const media of currentMedia) {
      const occurrence = await resolveSelectedCurrentMedia(root, media, policyVersion, policySha256);
      mediaById.set(occurrence.occurrenceId, occurrence);
    }
    const resolvedGraphs = [...graphById.values()];
    const resolvedMedia = [...mediaById.values()];
    resolvedGraphs.sort((left, right) => left.occurrenceId.localeCompare(right.occurrenceId));
    resolvedMedia.sort((left, right) => left.occurrenceId.localeCompare(right.occurrenceId));
    const manifest = {
      contract: "verdify.lab-specialist-occurrence-release",
      schemaVersion: 2,
      event,
      policyVersion,
      policySha256,
      sourceSnapshotManifestSha256,
      publishedAt,
      freshness: evaluateEventFreshness(event, publishedAt),
      occurrences: {
        graphs: resolvedGraphs,
        currentMedia: resolvedMedia,
      },
    };
    await verifyReleaseBlobs(root, manifest);
    const manifestDigest = sha256(canonicalBytes(manifest));
    await publishCanonicalAbsent(path.join(root, "manifests", "sha256", `${manifestDigest}.json`), manifest);
    const pointer = { manifestSha256: manifestDigest, eventId: event.eventId };
    const next = selectionRecord(pointer, selected.selection?.current ?? null, (selected.selection?.generation ?? 0) + 1, publishedAt, "publish");
    await publishCanonicalAbsent(eventPath, {
      contract: "verdify.lab-release-idempotency",
      schemaVersion: 1,
      eventId: event.eventId,
      eventSha256: sha256(canonicalBytes(event)),
      payloadSha256: event.payloadSha256,
      manifestSha256: manifestDigest,
      expectedSelectionSha256,
    });
    await pruneOccurrenceHistory(root, next, 10, selected.selection);
    const selectionSha256 = await atomicCanonicalWrite(path.join(root, "selection.json"), next);
    return { manifestSha256: manifestDigest, selectionSha256, manifest, idempotent: false };
  });
}

export async function rollbackOccurrenceRelease({ storeRoot, expectedSelectionSha256, rolledBackAt }) {
  requireInstant(rolledBackAt, "rollback time");
  if (!SHA256_RE.test(expectedSelectionSha256)) throw new Error("expected selection digest is invalid");
  return withStoreLock(storeRoot, async (root) => {
    const selected = await loadSelectedOccurrenceRelease(root);
    if (selected.selection === null || selected.selectionSha256 !== expectedSelectionSha256) {
      throw new Error("occurrence rollback precondition failed");
    }
    if (selected.selection.previous === null) throw new Error("occurrence rollback has no previous release");
    const next = selectionRecord(
      selected.selection.previous,
      selected.selection.current,
      selected.selection.generation + 1,
      rolledBackAt,
      "rollback",
    );
    const selectionSha256 = await atomicCanonicalWrite(path.join(root, "selection.json"), next);
    return { selection: next, selectionSha256 };
  });
}

export async function loadSelectedCurrentMediaGeneration(storeRoot, occurrenceIdValue) {
  const root = await openStore(storeRoot);
  return selectedCurrentMediaPointer(root, occurrenceIdValue);
}

export async function rollbackCurrentMediaGeneration({
  storeRoot,
  occurrenceId: occurrenceIdValue,
  expectedSelectionSha256,
  rolledBackAt,
}) {
  requireInstant(rolledBackAt, "current media rollback time");
  if (!SHA256_RE.test(expectedSelectionSha256)) throw new Error("current media rollback precondition is invalid");
  return withStoreLock(storeRoot, async (root) => {
    const selected = await selectedCurrentMediaPointer(root, occurrenceIdValue);
    if (selected === null || selected.selectionSha256 !== expectedSelectionSha256) {
      throw new Error("current media rollback precondition failed");
    }
    if (selected.selection.previous === null) throw new Error("current media rollback has no previous generation");
    const next = currentMediaPointerRecord(
      occurrenceIdValue,
      selected.selection.previous,
      selected.selection.current,
      selected.selection.generation + 1,
      rolledBackAt,
      "rollback",
    );
    const selectionSha256 = await atomicCanonicalWrite(
      path.join(selected.directory, "selection.json"),
      next,
    );
    const result = await selectedCurrentMediaPointer(root, occurrenceIdValue);
    if (result.selectionSha256 !== selectionSha256) throw new Error("current media rollback selection changed unexpectedly");
    return result;
  });
}

export async function materializeOccurrenceBlobs(storeRoot, manifest, destination) {
  const root = await openStore(storeRoot);
  await verifyReleaseBlobs(root, manifest);
  await mkdir(path.join(destination, "evidence", "blobs", "sha256"), { recursive: true, mode: 0o755 });
  const fallbacks = [...manifest.occurrences.graphs, ...manifest.occurrences.currentMedia]
    .map((occurrence) => occurrence.fallback)
    .filter(Boolean);
  const digests = [...new Set(fallbacks.map((fallback) => fallback.sha256))].sort();
  for (const digest of digests) {
    const source = path.join(root, "blobs", "sha256", `${digest}.png`);
    const target = path.join(destination, "evidence", "blobs", "sha256", `${digest}.png`);
    await copyFile(source, target, fsConstants.COPYFILE_EXCL);
    const verified = await validatePngFile(path.dirname(target), path.basename(target));
    if (verified.sha256 !== digest) throw new Error("materialized occurrence blob digest mismatch");
  }
  return digests.length;
}

export function occurrenceStateIndex(manifest) {
  const graphs = new Map((manifest?.occurrences?.graphs ?? []).map((item) => [item.occurrenceId, item]));
  const currentMedia = new Map((manifest?.occurrences?.currentMedia ?? []).map((item) => [item.occurrenceId, item]));
  return { graphs, currentMedia };
}

export function staticOccurrenceManifest({ snapshotId, selectedManifestSha256 = null, discoveredGraphs, discoveredCurrentMedia, selectedManifest = null }) {
  requireSafeText(snapshotId, "snapshot ID", 512);
  if (selectedManifestSha256 !== null && !SHA256_RE.test(selectedManifestSha256)) throw new Error("selected occurrence manifest digest is invalid");
  if ((selectedManifestSha256 === null) !== (selectedManifest === null)) {
    throw new Error("selected occurrence manifest identity is incomplete");
  }
  const selected = occurrenceStateIndex(selectedManifest);
  const seen = new Set();
  for (const item of [...discoveredGraphs, ...discoveredCurrentMedia]) {
    if (seen.has(item.occurrenceId)) throw new Error("static occurrence manifest has a duplicate occurrence ID");
    seen.add(item.occurrenceId);
  }
  const graphs = discoveredGraphs.map((item) => ({
    ...item,
    selected: selected.graphs.get(item.occurrenceId) ?? null,
  }));
  const currentMedia = discoveredCurrentMedia.map((item) => ({
    ...item,
    selected: selected.currentMedia.get(item.occurrenceId)?.sourceProvenanceSha256 === item.sourceProvenanceSha256
      ? selected.currentMedia.get(item.occurrenceId)
      : null,
  }));
  return {
    contract: "verdify.lab-static-occurrence-manifest",
    schemaVersion: 1,
    snapshotId,
    selectedManifestSha256,
    graphs,
    currentMedia,
  };
}

export const freshnessThresholds = {
  graphMinimumSeconds: GRAPH_MIN_STALE_SECONDS,
  currentMediaMinimumSeconds: CURRENT_MEDIA_MIN_STALE_SECONDS,
  archivalMediaMinimumSeconds: ARCHIVAL_MEDIA_MIN_STALE_SECONDS,
  event: EVENT_FRESHNESS,
};
