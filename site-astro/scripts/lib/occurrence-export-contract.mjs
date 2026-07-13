import { createHash } from "node:crypto";
import { constants as fsConstants, open } from "node:fs/promises";
import path from "node:path";

import {
  currentMediaGenerationPayloadSha256,
  discoverGraphOccurrence,
  occurrenceReleasePayloadSha256,
} from "./occurrence-release.mjs";
import { validatePngFile } from "./png-validation.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/;
const GRAPH_ID_RE = /^graph_[0-9a-f]{24}$/;
const MEDIA_ID_RE = /^media_[0-9a-f]{24}$/;
const BATCH_ID_RE = /^batch_[A-Za-z0-9_-]{8,128}$/;
const SOURCE_ID_RE = /^operator-public-reporting-feed(?:-[a-z0-9-]{1,64})?$/;
const SOURCE_WATERMARK_RE = /^wm_[A-Za-z0-9_-]{8,128}$/;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const MAX_DOCUMENT_BYTES = 8 * 1024 * 1024;
const MAX_OCCURRENCES = 10_000;
const REPORTING_TARGET_SECONDS = 15 * 60;
const REPORTING_STALE_SECONDS = 30 * 60;
const REQUIRED_ACTIVATION_GATES = [
  "jason-approval",
  "operator-owned-reporting-feed",
  "least-privilege-reporting-credential",
];

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

function safeText(value, label, maximum = 512) {
  if (
    typeof value !== "string"
    || value.length === 0
    || value.length > maximum
    || /[\u0000-\u001f\u007f]/u.test(value)
  ) throw new Error(`${label} is invalid`);
  return value;
}

function instant(value, label) {
  safeText(value, label, 32);
  if (!ISO_INSTANT_RE.test(value) || !Number.isFinite(Date.parse(value))) throw new Error(`${label} is invalid`);
  return value;
}

function positiveInteger(value, label, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value <= 0 || value > maximum) throw new Error(`${label} is invalid`);
  return value;
}

function nullableDigest(value, label) {
  if (value !== null && !SHA256_RE.test(value)) throw new Error(`${label} is invalid`);
  return value;
}

function safeRoute(route) {
  safeText(route, "occurrence route", 2048);
  if (
    !route.startsWith("/")
    || route.includes("\\")
    || route.includes("?")
    || route.includes("#")
    || (path.posix.normalize(route) !== route && `${path.posix.normalize(route)}/` !== route)
  ) throw new Error("occurrence route is invalid");
  return route;
}

export async function readCanonicalExportDocument(file, label = "export document") {
  const absolute = path.resolve(file);
  const handle = await open(absolute, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  let bytes;
  try {
    const metadata = await handle.stat({ bigint: true });
    if (
      !metadata.isFile()
      || metadata.nlink !== 1n
      || metadata.size < 1n
      || metadata.size > BigInt(MAX_DOCUMENT_BYTES)
    ) throw new Error(`${label} is not a bounded single-link regular file`);
    bytes = await handle.readFile();
    const after = await handle.stat({ bigint: true });
    if (
      after.dev !== metadata.dev
      || after.ino !== metadata.ino
      || after.size !== metadata.size
      || after.nlink !== 1n
    ) throw new Error(`${label} changed while being read`);
  } finally {
    await handle.close();
  }
  let document;
  try {
    document = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error(`${label} is not valid JSON`);
  }
  if (!canonicalBytes(document).equals(bytes)) throw new Error(`${label} is not canonical JSON`);
  return { document, bytes, sha256: sha256(bytes) };
}

function discoveredGraph(record) {
  if (!exactKeys(record, [
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
    "selected",
  ])) throw new Error("static graph occurrence does not use the closed v1 shape");
  const { selected: _selected, ...candidate } = record;
  const normalized = discoverGraphOccurrence({
    route: candidate.route,
    ordinal: candidate.ordinal,
    liveUrl: candidate.liveUrl,
    title: candidate.semanticRole,
    renderCadenceSeconds: candidate.renderCadenceSeconds,
  });
  if (JSON.stringify(candidate) !== JSON.stringify(normalized)) {
    throw new Error("static graph occurrence is not canonical");
  }
  return candidate;
}

function discoveredCurrentMedia(record) {
  if (!exactKeys(record, [
    "occurrenceId",
    "route",
    "ordinal",
    "classification",
    "semanticRole",
    "sourceProvenanceSha256",
    "stableTarget",
    "captureCadenceSeconds",
    "selected",
  ])) throw new Error("static current-media occurrence does not use the closed v1 shape");
  const { selected: _selected, ...candidate } = record;
  if (
    !MEDIA_ID_RE.test(candidate.occurrenceId)
    || candidate.classification !== "current-still"
    || !SHA256_RE.test(candidate.sourceProvenanceSha256)
    || candidate.stableTarget !== `/evidence/current/${candidate.occurrenceId}`
    || !Number.isSafeInteger(candidate.ordinal)
    || candidate.ordinal < 0
    || candidate.ordinal > MAX_OCCURRENCES
  ) throw new Error("static current-media occurrence identity is invalid");
  safeRoute(candidate.route);
  safeText(candidate.semanticRole, "current-media semantic role", 512);
  positiveInteger(candidate.captureCadenceSeconds, "current-media capture cadence", 86_400);
  return candidate;
}

export function validateStaticOccurrenceManifest(manifest) {
  if (
    !exactKeys(manifest, [
      "contract",
      "schemaVersion",
      "snapshotId",
      "selectedManifestSha256",
      "graphs",
      "currentMedia",
    ])
    || manifest.contract !== "verdify.lab-static-occurrence-manifest"
    || manifest.schemaVersion !== 1
    || !Array.isArray(manifest.graphs)
    || !Array.isArray(manifest.currentMedia)
    || manifest.graphs.length + manifest.currentMedia.length > MAX_OCCURRENCES
  ) throw new Error("static occurrence manifest does not use the closed v1 shape");
  safeText(manifest.snapshotId, "snapshot identity", 512);
  nullableDigest(manifest.selectedManifestSha256, "selected occurrence manifest digest");
  const graphs = manifest.graphs.map(discoveredGraph);
  const currentMedia = manifest.currentMedia.map(discoveredCurrentMedia);
  const seen = new Set();
  for (const occurrence of [...graphs, ...currentMedia]) {
    if (seen.has(occurrence.occurrenceId)) throw new Error("static occurrence manifest has duplicate occurrence IDs");
    seen.add(occurrence.occurrenceId);
  }
  return { graphs, currentMedia };
}

function occurrenceFingerprint(occurrence) {
  return sha256(canonicalBytes(occurrence));
}

function snapshotManifestDigest(snapshotId) {
  const match = /^(?:sanitized-content|snapshot)-sha256:([0-9a-f]{64})$/.exec(snapshotId);
  if (!match) throw new Error("snapshot identity does not expose a supported manifest digest");
  return match[1];
}

export function draftBlockedOccurrenceExportPolicy({
  manifest,
  manifestSha256,
  policyVersion,
  approvedAt,
  cameraSources = [],
}) {
  if (!SHA256_RE.test(manifestSha256)) throw new Error("source occurrence manifest digest is invalid");
  safeText(policyVersion, "occurrence export policy version", 256);
  instant(approvedAt, "policy review time");
  const { graphs, currentMedia } = validateStaticOccurrenceManifest(manifest);
  const policy = {
    contract: "verdify.lab-occurrence-export-policy",
    schemaVersion: 1,
    policyVersion,
    activation: {
      state: "blocked",
      approvedBy: null,
      approvedAt: null,
      requiredGates: REQUIRED_ACTIVATION_GATES,
    },
    sourceSnapshotManifestSha256: snapshotManifestDigest(manifest.snapshotId),
    sourceOccurrenceManifestSha256: manifestSha256,
    reviewedAt: approvedAt,
    reportingFeed: {
      contract: "verdify.operator-public-reporting-feed",
      authority: "operator-owned",
      direction: "one-way-read-only",
      sourceClass: "public-reporting-projection",
      credentialClass: "reporting-read-only",
      existingAnonymousGraphsAllowed: false,
      trackAPrimaryRoleAllowed: false,
      sourceWatermarkRequired: true,
      p95TargetSeconds: REPORTING_TARGET_SECONDS,
      staleAfterSeconds: REPORTING_STALE_SECONDS,
    },
    cameraUpstream: {
      contract: "verdify.public-camera-read-feed",
      method: "GET",
      origin: "https://api.verdify.ai",
      redirectsAllowed: false,
      authorization: "forbidden",
      cookies: "forbidden",
      allowedResponseMediaType: "image/jpeg",
      sanitization: "decode-reencode-rgb-or-rgba-png-metadata-free",
      forbiddenNetworkClasses: ["device-vlan", "frigate", "go2rtc", "control-plane"],
      sources: [...cameraSources].sort((left, right) => left.occurrenceId.localeCompare(right.occurrenceId)),
    },
    imagePolicy: {
      mediaType: "image/png",
      graphs: {
        minWidth: 320,
        minHeight: 180,
        maxWidth: 4096,
        maxHeight: 2160,
        maxBytes: 8 * 1024 * 1024,
        maxCandidateAgeSeconds: REPORTING_STALE_SECONDS,
      },
      currentMedia: {
        minWidth: 320,
        minHeight: 180,
        maxWidth: 2160,
        maxHeight: 2160,
        maxBytes: 8 * 1024 * 1024,
        maxCandidateAgeSeconds: 15 * 60,
      },
    },
    graphs: graphs
      .map((occurrence) => ({
        occurrenceId: occurrence.occurrenceId,
        occurrenceSha256: occurrenceFingerprint(occurrence),
      }))
      .sort((left, right) => left.occurrenceId.localeCompare(right.occurrenceId)),
    currentMedia: currentMedia
      .map((occurrence) => ({
        occurrenceId: occurrence.occurrenceId,
        occurrenceSha256: occurrenceFingerprint(occurrence),
      }))
      .sort((left, right) => left.occurrenceId.localeCompare(right.occurrenceId)),
  };
  validateOccurrenceExportPolicy(policy);
  return policy;
}

function validateCameraUpstream(cameraUpstream, approvedCurrentMedia) {
  if (!exactKeys(cameraUpstream, [
    "contract",
    "method",
    "origin",
    "redirectsAllowed",
    "authorization",
    "cookies",
    "allowedResponseMediaType",
    "sanitization",
    "forbiddenNetworkClasses",
    "sources",
  ])) throw new Error("camera upstream policy does not use the closed v1 shape");
  if (
    cameraUpstream.contract !== "verdify.public-camera-read-feed"
    || cameraUpstream.method !== "GET"
    || cameraUpstream.origin !== "https://api.verdify.ai"
    || cameraUpstream.redirectsAllowed !== false
    || cameraUpstream.authorization !== "forbidden"
    || cameraUpstream.cookies !== "forbidden"
    || cameraUpstream.allowedResponseMediaType !== "image/jpeg"
    || cameraUpstream.sanitization !== "decode-reencode-rgb-or-rgba-png-metadata-free"
    || JSON.stringify(cameraUpstream.forbiddenNetworkClasses) !== JSON.stringify([
      "device-vlan",
      "frigate",
      "go2rtc",
      "control-plane",
    ])
  ) throw new Error("camera upstream policy weakens the public read-only handoff");
  if (!Array.isArray(cameraUpstream.sources) || cameraUpstream.sources.length !== approvedCurrentMedia.length) {
    throw new Error("camera upstream allowlist is incomplete");
  }
  const approvedIds = new Set(approvedCurrentMedia.map((record) => record.occurrenceId));
  const approvedUrls = new Set([
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
    "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080",
  ]);
  const seenIds = new Set();
  const seenUrls = new Set();
  for (const source of cameraUpstream.sources) {
    if (!exactKeys(source, ["occurrenceId", "url"]) || !approvedIds.has(source.occurrenceId)) {
      throw new Error("camera upstream allowlist entry is invalid");
    }
    let parsed;
    try {
      parsed = new URL(source.url);
    } catch {
      throw new Error("camera upstream URL is invalid");
    }
    if (
      !approvedUrls.has(source.url)
      || parsed.origin !== cameraUpstream.origin
      || parsed.username
      || parsed.password
      || parsed.hash
      || seenIds.has(source.occurrenceId)
      || seenUrls.has(source.url)
    ) throw new Error("camera upstream URL is outside the exact public allowlist");
    seenIds.add(source.occurrenceId);
    seenUrls.add(source.url);
  }
  const sorted = [...cameraUpstream.sources].sort((left, right) => left.occurrenceId.localeCompare(right.occurrenceId));
  if (JSON.stringify(sorted) !== JSON.stringify(cameraUpstream.sources)) throw new Error("camera upstream allowlist is not sorted");
}

function validateActivation(activation) {
  if (!exactKeys(activation, ["state", "approvedBy", "approvedAt", "requiredGates"])) {
    throw new Error("occurrence export activation does not use the closed v1 shape");
  }
  if (JSON.stringify(activation.requiredGates) !== JSON.stringify(REQUIRED_ACTIVATION_GATES)) {
    throw new Error("occurrence export activation gates are incomplete");
  }
  if (activation.state === "blocked") {
    if (activation.approvedBy !== null || activation.approvedAt !== null) {
      throw new Error("blocked occurrence export policy carries approval metadata");
    }
    return;
  }
  if (activation.state !== "approved" || activation.approvedBy !== "jason") {
    throw new Error("occurrence export activation is invalid");
  }
  instant(activation.approvedAt, "occurrence export approval time");
}

function validateFeedPolicy(feed) {
  if (!exactKeys(feed, [
    "contract",
    "authority",
    "direction",
    "sourceClass",
    "credentialClass",
    "existingAnonymousGraphsAllowed",
    "trackAPrimaryRoleAllowed",
    "sourceWatermarkRequired",
    "p95TargetSeconds",
    "staleAfterSeconds",
  ])) throw new Error("reporting feed policy does not use the closed v1 shape");
  if (
    feed.contract !== "verdify.operator-public-reporting-feed"
    || feed.authority !== "operator-owned"
    || feed.direction !== "one-way-read-only"
    || feed.sourceClass !== "public-reporting-projection"
    || feed.credentialClass !== "reporting-read-only"
    || feed.existingAnonymousGraphsAllowed !== false
    || feed.trackAPrimaryRoleAllowed !== false
    || feed.sourceWatermarkRequired !== true
    || feed.p95TargetSeconds !== REPORTING_TARGET_SECONDS
    || feed.staleAfterSeconds !== REPORTING_STALE_SECONDS
  ) throw new Error("reporting feed policy weakens the required isolation or freshness contract");
}

function validateImageBounds(bounds, label) {
  if (!exactKeys(bounds, ["minWidth", "minHeight", "maxWidth", "maxHeight", "maxBytes", "maxCandidateAgeSeconds"])) {
    throw new Error(`${label} image policy does not use the closed v1 shape`);
  }
  for (const key of Object.keys(bounds)) positiveInteger(bounds[key], `${label} ${key}`, 128 * 1024 * 1024);
  if (
    bounds.minWidth > bounds.maxWidth
    || bounds.minHeight > bounds.maxHeight
    || bounds.maxBytes > 8 * 1024 * 1024
    || bounds.maxCandidateAgeSeconds > REPORTING_STALE_SECONDS
  ) {
    throw new Error(`${label} image policy exceeds its hard bounds`);
  }
}

function validateAllowlist(records, pattern, label) {
  if (!Array.isArray(records) || records.length > MAX_OCCURRENCES) throw new Error(`${label} allowlist is invalid`);
  const seen = new Set();
  for (const record of records) {
    if (
      !exactKeys(record, ["occurrenceId", "occurrenceSha256"])
      || !pattern.test(record.occurrenceId)
      || !SHA256_RE.test(record.occurrenceSha256)
      || seen.has(record.occurrenceId)
    ) throw new Error(`${label} allowlist entry is invalid`);
    seen.add(record.occurrenceId);
  }
  const sorted = [...records].sort((left, right) => left.occurrenceId.localeCompare(right.occurrenceId));
  if (JSON.stringify(sorted) !== JSON.stringify(records)) throw new Error(`${label} allowlist is not sorted`);
}

export function validateOccurrenceExportPolicy(policy) {
  if (!exactKeys(policy, [
    "contract",
    "schemaVersion",
    "policyVersion",
    "activation",
    "sourceSnapshotManifestSha256",
    "sourceOccurrenceManifestSha256",
    "reviewedAt",
    "reportingFeed",
    "cameraUpstream",
    "imagePolicy",
    "graphs",
    "currentMedia",
  ]) || policy.contract !== "verdify.lab-occurrence-export-policy" || policy.schemaVersion !== 1) {
    throw new Error("occurrence export policy does not use the closed v1 shape");
  }
  safeText(policy.policyVersion, "occurrence export policy version", 256);
  validateActivation(policy.activation);
  if (!SHA256_RE.test(policy.sourceSnapshotManifestSha256) || !SHA256_RE.test(policy.sourceOccurrenceManifestSha256)) {
    throw new Error("occurrence export policy source digest is invalid");
  }
  instant(policy.reviewedAt, "occurrence export policy review time");
  if (
    policy.activation.state === "approved"
    && Date.parse(policy.activation.approvedAt) < Date.parse(policy.reviewedAt)
  ) throw new Error("occurrence export approval predates policy review");
  validateFeedPolicy(policy.reportingFeed);
  validateCameraUpstream(policy.cameraUpstream, policy.currentMedia);
  if (!exactKeys(policy.imagePolicy, ["mediaType", "graphs", "currentMedia"]) || policy.imagePolicy.mediaType !== "image/png") {
    throw new Error("occurrence export image policy is invalid");
  }
  validateImageBounds(policy.imagePolicy.graphs, "graph");
  validateImageBounds(policy.imagePolicy.currentMedia, "current-media");
  validateAllowlist(policy.graphs, GRAPH_ID_RE, "graph");
  validateAllowlist(policy.currentMedia, MEDIA_ID_RE, "current-media");
  return policy;
}

export function validatePolicyManifestBinding(policy, manifest, manifestSha256) {
  validateOccurrenceExportPolicy(policy);
  if (policy.sourceOccurrenceManifestSha256 !== manifestSha256) {
    throw new Error("occurrence export policy does not match the supplied manifest bytes");
  }
  const discovered = validateStaticOccurrenceManifest(manifest);
  if (policy.sourceSnapshotManifestSha256 !== snapshotManifestDigest(manifest.snapshotId)) {
    throw new Error("occurrence export policy does not match the snapshot manifest");
  }
  for (const [label, occurrences, approved] of [
    ["graph", discovered.graphs, policy.graphs],
    ["current-media", discovered.currentMedia, policy.currentMedia],
  ]) {
    if (occurrences.length !== approved.length) throw new Error(`${label} allowlist is not complete`);
    const approvedById = new Map(approved.map((item) => [item.occurrenceId, item.occurrenceSha256]));
    for (const occurrence of occurrences) {
      if (approvedById.get(occurrence.occurrenceId) !== occurrenceFingerprint(occurrence)) {
        throw new Error(`${label} occurrence is not exactly allowlisted`);
      }
    }
  }
  return discovered;
}

export function evaluateReportingFeedFreshness(batch) {
  const elapsedSeconds = Math.floor((Date.parse(batch.exportedAt) - Date.parse(batch.reportingFeed.sourceWatermarkAt)) / 1000);
  if (elapsedSeconds < 0) throw new Error("reporting feed watermark is newer than the export batch");
  return {
    sourceWatermarkAt: batch.reportingFeed.sourceWatermarkAt,
    exportedAt: batch.exportedAt,
    elapsedSeconds,
    p95TargetSeconds: REPORTING_TARGET_SECONDS,
    staleAfterSeconds: REPORTING_STALE_SECONDS,
    status: elapsedSeconds > REPORTING_STALE_SECONDS
      ? "alert"
      : elapsedSeconds > REPORTING_TARGET_SECONDS
        ? "late"
        : "fresh",
  };
}

function validateBatchFeed(feed) {
  if (!exactKeys(feed, [
    "contract",
    "schemaVersion",
    "sourceId",
    "sourceClass",
    "credentialClass",
    "direction",
    "sourceWatermark",
    "sourceWatermarkAt",
  ])) throw new Error("reporting feed batch does not use the closed v1 shape");
  if (
    feed.contract !== "verdify.operator-public-reporting-feed"
    || feed.schemaVersion !== 1
    || feed.sourceClass !== "public-reporting-projection"
    || feed.credentialClass !== "reporting-read-only"
    || feed.direction !== "one-way-read-only"
  ) throw new Error("reporting feed batch is not from the isolated read-only contract");
  if (!SOURCE_ID_RE.test(feed.sourceId)) throw new Error("reporting feed source ID is invalid");
  if (!SOURCE_WATERMARK_RE.test(feed.sourceWatermark)) throw new Error("reporting feed source watermark is not opaque");
  instant(feed.sourceWatermarkAt, "reporting feed source watermark time");
}

function validateCandidate(candidate, label) {
  if (!exactKeys(candidate, ["relativePath", "mediaType", "capturedAt"])) {
    throw new Error(`${label} candidate does not use the closed v1 shape`);
  }
  safeText(candidate.relativePath, `${label} candidate path`, 1024);
  if (candidate.mediaType !== "image/png") throw new Error(`${label} candidate MIME type is not image/png`);
  instant(candidate.capturedAt, `${label} capture time`);
}

function validateBatchRecords(records, approved, pattern, statusKey, allowedStatuses, label) {
  if (!Array.isArray(records) || records.length !== approved.length) throw new Error(`${label} export batch is not complete`);
  const approvedIds = new Set(approved.map((record) => record.occurrenceId));
  const seen = new Set();
  for (const record of records) {
    const keys = label === "current-media"
      ? ["occurrenceId", statusKey, "candidate", "expectedSelectionSha256"]
      : ["occurrenceId", statusKey, "candidate"];
    if (
      !exactKeys(record, keys)
      || !pattern.test(record.occurrenceId)
      || !approvedIds.has(record.occurrenceId)
      || seen.has(record.occurrenceId)
      || !allowedStatuses.includes(record[statusKey])
    ) throw new Error(`${label} export batch entry is invalid`);
    seen.add(record.occurrenceId);
    if (label === "current-media") nullableDigest(record.expectedSelectionSha256, "current-media selection precondition");
    if (record[statusKey] === "success") validateCandidate(record.candidate, label);
    else if (record.candidate !== null) throw new Error(`${label} failed probe carries a candidate`);
  }
}

export function validateOccurrenceExportBatch(batch, policy) {
  validateOccurrenceExportPolicy(policy);
  if (!exactKeys(batch, [
    "contract",
    "schemaVersion",
    "batchId",
    "policyVersion",
    "sourceOccurrenceManifestSha256",
    "reportingFeed",
    "exportedAt",
    "expectedSelectionSha256",
    "graphs",
    "currentMedia",
  ]) || batch.contract !== "verdify.lab-occurrence-export-batch" || batch.schemaVersion !== 1) {
    throw new Error("occurrence export batch does not use the closed v1 shape");
  }
  if (!BATCH_ID_RE.test(batch.batchId)) throw new Error("occurrence export batch ID is invalid");
  if (batch.policyVersion !== policy.policyVersion || batch.sourceOccurrenceManifestSha256 !== policy.sourceOccurrenceManifestSha256) {
    throw new Error("occurrence export batch is not bound to the approved policy and manifest");
  }
  validateBatchFeed(batch.reportingFeed);
  instant(batch.exportedAt, "occurrence export time");
  nullableDigest(batch.expectedSelectionSha256, "occurrence release selection precondition");
  validateBatchRecords(
    batch.graphs,
    policy.graphs,
    GRAPH_ID_RE,
    "probeStatus",
    ["success", "timeout", "http-error", "decode-error", "missing", "policy-rejected"],
    "graph",
  );
  validateBatchRecords(
    batch.currentMedia,
    policy.currentMedia,
    MEDIA_ID_RE,
    "captureStatus",
    ["success", "timeout", "http-error", "decode-error", "missing", "policy-rejected"],
    "current-media",
  );
  return evaluateReportingFeedFreshness(batch);
}

async function verifiedCandidate(sourceRoot, record, bounds, kind, exportedAt) {
  if (record.candidate === null) return null;
  const expected = new RegExp(`^${kind}/${record.occurrenceId}/([0-9a-f]{64})\\.png$`).exec(record.candidate.relativePath);
  if (!expected) throw new Error(`${kind} candidate path is not opaque and content-addressed`);
  const verified = await validatePngFile(sourceRoot, record.candidate.relativePath);
  if (verified.sha256 !== expected[1]) throw new Error(`${kind} candidate filename does not match its bytes`);
  if (
    verified.mediaType !== "image/png"
    || verified.bytes > bounds.maxBytes
    || verified.width < bounds.minWidth
    || verified.width > bounds.maxWidth
    || verified.height < bounds.minHeight
    || verified.height > bounds.maxHeight
  ) throw new Error(`${kind} candidate is outside the approved MIME, byte, or dimension bounds`);
  if (Date.parse(record.candidate.capturedAt) > Date.parse(exportedAt)) {
    throw new Error(`${kind} candidate was captured after the export batch`);
  }
  const candidateAgeSeconds = Math.floor((Date.parse(exportedAt) - Date.parse(record.candidate.capturedAt)) / 1000);
  if (candidateAgeSeconds > bounds.maxCandidateAgeSeconds) {
    throw new Error(`${kind} candidate is stale; retain last-known-good evidence`);
  }
  return {
    relativePath: record.candidate.relativePath,
    verifiedAt: exportedAt,
    capturedAt: record.candidate.capturedAt,
  };
}

function releaseEvent({ eventId, eventType, batch, payloadSha256 }) {
  return {
    contract: "verdify.lab-release-trigger",
    schemaVersion: 1,
    eventId,
    eventType,
    sourceId: batch.reportingFeed.sourceId,
    sourceWatermark: batch.reportingFeed.sourceWatermark,
    occurredAt: batch.reportingFeed.sourceWatermarkAt,
    payloadSha256,
  };
}

function deterministicEventId(prefix, value) {
  return `evt_${prefix}_${sha256(canonicalBytes(value)).slice(0, 32)}`;
}

export async function inspectOccurrenceExportCandidates({ policy, batch, sourceRoot }) {
  const feedFreshness = validateOccurrenceExportBatch(batch, policy);
  const graphCandidates = new Map();
  for (const record of batch.graphs) {
    graphCandidates.set(record.occurrenceId, await verifiedCandidate(
      sourceRoot,
      record,
      policy.imagePolicy.graphs,
      "graphs",
      batch.exportedAt,
    ));
  }
  const currentMediaCandidates = new Map();
  for (const record of batch.currentMedia) {
    currentMediaCandidates.set(record.occurrenceId, await verifiedCandidate(
      sourceRoot,
      record,
      policy.imagePolicy.currentMedia,
      "current-media",
      batch.exportedAt,
    ));
  }
  return { feedFreshness, graphCandidates, currentMediaCandidates };
}

export async function prepareOccurrenceExportRequests({
  policy,
  manifest,
  manifestSha256,
  batch,
  sourceRoot,
  storeRoot,
}) {
  const discovered = validatePolicyManifestBinding(policy, manifest, manifestSha256);
  if (
    policy.activation.state !== "approved"
    || policy.activation.approvedBy !== "jason"
    || !policy.activation.approvedAt
  ) throw new Error("occurrence export policy is blocked pending separate Jason-gated feed, tier, and credential work");
  if (Date.parse(batch.exportedAt) < Date.parse(policy.activation.approvedAt)) {
    throw new Error("occurrence export batch predates its Jason-gated activation");
  }
  safeText(path.resolve(sourceRoot), "occurrence export source root", 4096);
  safeText(path.resolve(storeRoot), "occurrence export store root", 4096);
  const inspected = await inspectOccurrenceExportCandidates({ policy, batch, sourceRoot });
  if (inspected.feedFreshness.status === "alert") {
    throw new Error("reporting feed source watermark is more than 30 minutes stale; retain last-known-good evidence");
  }
  const graphById = new Map(batch.graphs.map((record) => [record.occurrenceId, record]));
  const mediaById = new Map(batch.currentMedia.map((record) => [record.occurrenceId, record]));
  const mediaRequests = [];
  for (const occurrence of discovered.currentMedia) {
    const record = mediaById.get(occurrence.occurrenceId);
    const candidate = inspected.currentMediaCandidates.get(occurrence.occurrenceId);
    if (record.captureStatus !== "success" || candidate === null) continue;
    const request = {
      storeRoot: path.resolve(storeRoot),
      sourceRoot: path.resolve(sourceRoot),
      event: null,
      policyVersion: policy.policyVersion,
      publishedAt: batch.exportedAt,
      occurrence,
      candidate,
      expectedSelectionSha256: record.expectedSelectionSha256,
    };
    const payloadSha256 = currentMediaGenerationPayloadSha256(request);
    request.event = releaseEvent({
      eventId: deterministicEventId("media", { batchId: batch.batchId, occurrenceId: occurrence.occurrenceId, payloadSha256 }),
      eventType: "current-media-updated",
      batch,
      payloadSha256,
    });
    mediaRequests.push(request);
  }
  const graphs = discovered.graphs.map((occurrence) => {
    const record = graphById.get(occurrence.occurrenceId);
    const candidate = inspected.graphCandidates.get(occurrence.occurrenceId);
    return candidate
      ? { ...occurrence, probeStatus: record.probeStatus, candidate }
      : { ...occurrence, probeStatus: record.probeStatus };
  });
  const releaseRequest = {
    storeRoot: path.resolve(storeRoot),
    sourceRoot: path.resolve(sourceRoot),
    event: null,
    sourceSnapshotManifestSha256: policy.sourceSnapshotManifestSha256,
    policyVersion: policy.policyVersion,
    publishedAt: batch.exportedAt,
    graphs,
    currentMedia: discovered.currentMedia,
    expectedSelectionSha256: batch.expectedSelectionSha256,
  };
  const payloadSha256 = occurrenceReleasePayloadSha256(releaseRequest);
  releaseRequest.event = releaseEvent({
    eventId: deterministicEventId("reconcile", { batchId: batch.batchId, payloadSha256 }),
    eventType: "reconciliation",
    batch,
    payloadSha256,
  });
  return {
    feedFreshness: inspected.feedFreshness,
    mediaRequests,
    releaseRequest,
  };
}

export const occurrenceExportContract = {
  p95TargetSeconds: REPORTING_TARGET_SECONDS,
  staleAfterSeconds: REPORTING_STALE_SECONDS,
  requiredActivationGates: REQUIRED_ACTIVATION_GATES,
};
