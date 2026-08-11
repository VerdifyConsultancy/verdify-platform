import { createHash } from "node:crypto";
import {
    constants as fsConstants,
    lstat,
    open,
    readFile,
    readdir,
    realpath,
} from "node:fs/promises";
import path from "node:path";

import { executeOccurrenceExportBatch } from "./occurrence-export-caller.mjs";
import { createOccurrenceExportStoreOperations } from "./occurrence-export-operation-adapter.mjs";
import {
    occurrenceExportPolicySha256,
    validateOccurrenceExportBatch,
    validatePolicyManifestBinding,
} from "./occurrence-export-contract.mjs";
import {
    currentMediaGenerationPayloadSha256,
    loadSelectedCurrentMediaGeneration,
    loadSelectedOccurrenceRelease,
} from "./occurrence-release.mjs";
import { validateOccurrenceProducerResult } from "./occurrence-producer-result-contract.mjs";
import {
    inventoryBuiltSite,
    siteContentIdentitySha256,
    siteReleasePayloadSha256,
    validateSiteReleaseManifest,
} from "./site-release-store.mjs";
import { verifySelectedEvidence } from "../verify-production-output.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const EVENT_ID_RE = /^evt_occurrence_site_[0-9a-f]{32}$/u;
const RELEASE_EVENT_ID_RE = /^evt_[A-Za-z0-9_-]{8,128}$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u;
const COMMIT_RE = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u;
const PROVENANCE_PATH = "occurrence-publish-provenance.json";
const PUBLICATION_PROFILE_KEYS = [
    "siteOrigin",
    "stageGlobalNoindex",
    "policyVersion",
];

const PRODUCTION_PUBLICATION_PROFILE = Object.freeze({
    siteOrigin: "https://lab.verdify.ai",
    stageGlobalNoindex: false,
    policyVersion: "occurrence-selected-production-v1",
});
const STAGE_PUBLICATION_PROFILE = Object.freeze({
    siteOrigin: "https://lab-stage.verdify.ai",
    stageGlobalNoindex: true,
    policyVersion: "occurrence-selected-stage-v1",
});

const EVENT_KEYS = [
    "contract",
    "schemaVersion",
    "eventId",
    "sourceId",
    "sourceWatermark",
    "occurredAt",
    "releasedAt",
    "sourceSnapshotManifestSha256",
    "sourceOccurrenceManifestSha256",
    "occurrencePolicySha256",
    "occurrenceStoreIdentitySha256",
    "producerResultSha256",
    "builderCommit",
    "buildOperationSha256",
    "verificationOperationSha256",
    "siteStoreIdentitySha256",
    "expectedSiteSelectionSha256",
];

const CHECKPOINT_KEYS = [
    "contract",
    "schemaVersion",
    "eventId",
    "eventSha256",
    "producerResultSha256",
    "occurrenceCallResultSha256",
    "sourceSnapshotManifestSha256",
    "sourceOccurrenceManifestSha256",
    "occurrencePolicySha256",
    "occurrenceStoreIdentitySha256",
    "occurrenceSelectionSha256",
    "occurrenceManifestSha256",
    "buildOperationSha256",
    "verificationOperationSha256",
    "siteStoreIdentitySha256",
    "expectedSiteSelectionSha256",
];

const PROVENANCE_KEYS = [
    "contract",
    "schemaVersion",
    "eventId",
    "eventSha256",
    "producerResultSha256",
    "occurrenceCallResultSha256",
    "sourceSnapshotManifestSha256",
    "sourceOccurrenceManifestSha256",
    "occurrencePolicySha256",
    "occurrenceStoreIdentitySha256",
    "occurrenceSelectionSha256",
    "occurrenceManifestSha256",
    "builderCommit",
    "buildOperationSha256",
    "verificationOperationSha256",
    "siteStoreIdentitySha256",
];

function canonicalBytes(value) {
    return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(value) {
    return createHash("sha256").update(value).digest("hex");
}

function exactKeys(value, keys) {
    return (
        value !== null &&
        typeof value === "object" &&
        !Array.isArray(value) &&
        Object.getPrototypeOf(value) === Object.prototype &&
        Object.keys(value).join(",") === keys.join(",")
    );
}

function validatePublicationProfile(value) {
    if (!exactKeys(value, PUBLICATION_PROFILE_KEYS)) {
        throw new Error(
            "site publication profile does not use the closed v1 shape",
        );
    }
    const canonical = canonicalBytes(value);
    if (
        !canonical.equals(canonicalBytes(PRODUCTION_PUBLICATION_PROFILE)) &&
        !canonical.equals(canonicalBytes(STAGE_PUBLICATION_PROFILE))
    ) {
        throw new Error("site publication profile is not an allowed target");
    }
    return structuredClone(value);
}

export function occurrenceSiteOperationSha256(kind, rawProfile) {
    if (!["build", "verification"].includes(kind)) {
        throw new Error("occurrence site operation kind is invalid");
    }
    const publicationProfile = validatePublicationProfile(rawProfile);
    return sha256(
        canonicalBytes({
            contract: "verdify.lab-occurrence-site-operation-identity",
            schemaVersion: 1,
            kind,
            publicationProfile,
        }),
    );
}

export const occurrenceSitePublicationProfiles = Object.freeze({
    production: PRODUCTION_PUBLICATION_PROFILE,
    stage: STAGE_PUBLICATION_PROFILE,
});

function safeText(value, label, maximum = 512) {
    if (
        typeof value !== "string" ||
        value.length === 0 ||
        value.length > maximum ||
        /[\u0000-\u001f\u007f]/u.test(value)
    )
        throw new Error(`${label} is invalid`);
    return value;
}

function digest(value, label) {
    if (typeof value !== "string" || !SHA256_RE.test(value)) {
        throw new Error(`${label} is invalid`);
    }
    return value;
}

function nullableDigest(value, label) {
    if (value !== null) digest(value, label);
    return value;
}

function instant(value, label) {
    safeText(value, label, 32);
    const milliseconds = Date.parse(value);
    const normalized = Number.isFinite(milliseconds)
        ? new Date(milliseconds).toISOString()
        : "";
    const expected = value.includes(".")
        ? normalized
        : normalized.replace(".000Z", "Z");
    if (!ISO_INSTANT_RE.test(value) || value !== expected)
        throw new Error(`${label} is invalid`);
    return value;
}

function canonicalValue(value, label, expectedDocument = null) {
    if (
        value === null ||
        typeof value !== "object" ||
        Array.isArray(value) ||
        value.document === null ||
        typeof value.document !== "object" ||
        Array.isArray(value.document) ||
        !Buffer.isBuffer(value.bytes) ||
        !SHA256_RE.test(value.sha256)
    )
        throw new Error(`${label} is not a canonical value`);
    const bytes = canonicalBytes(value.document);
    if (
        !bytes.equals(value.bytes) ||
        sha256(bytes) !== value.sha256 ||
        (expectedDocument !== null &&
            !bytes.equals(canonicalBytes(expectedDocument)))
    )
        throw new Error(`${label} canonical identity mismatch`);
    return value;
}

function eventPayload(value) {
    const {
        contract: _contract,
        schemaVersion: _schemaVersion,
        eventId: _eventId,
        ...payload
    } = value;
    return payload;
}

function deterministicEventId(value) {
    return `evt_occurrence_site_${sha256(canonicalBytes(eventPayload(value))).slice(0, 32)}`;
}

export function createOccurrenceSitePublishEvent(input) {
    const event = {
        contract: "verdify.lab-occurrence-site-publish-event",
        schemaVersion: 1,
        eventId: "",
        sourceId: input.sourceId,
        sourceWatermark: input.sourceWatermark,
        occurredAt: input.occurredAt,
        releasedAt: input.releasedAt,
        sourceSnapshotManifestSha256: input.sourceSnapshotManifestSha256,
        sourceOccurrenceManifestSha256: input.sourceOccurrenceManifestSha256,
        occurrencePolicySha256: input.occurrencePolicySha256,
        occurrenceStoreIdentitySha256: input.occurrenceStoreIdentitySha256,
        producerResultSha256: input.producerResultSha256,
        builderCommit: input.builderCommit,
        buildOperationSha256: input.buildOperationSha256,
        verificationOperationSha256: input.verificationOperationSha256,
        siteStoreIdentitySha256: input.siteStoreIdentitySha256,
        expectedSiteSelectionSha256: input.expectedSiteSelectionSha256 ?? null,
    };
    event.eventId = deterministicEventId(event);
    return validateOccurrenceSitePublishEvent(event);
}

export function validateOccurrenceSitePublishEvent(event) {
    if (
        !exactKeys(event, EVENT_KEYS) ||
        event.contract !== "verdify.lab-occurrence-site-publish-event" ||
        event.schemaVersion !== 1 ||
        !EVENT_ID_RE.test(event.eventId) ||
        event.eventId !== deterministicEventId(event) ||
        !COMMIT_RE.test(event.builderCommit)
    )
        throw new Error(
            "occurrence site publish event does not use the closed v1 contract",
        );
    safeText(event.sourceId, "occurrence site event source ID", 256);
    safeText(
        event.sourceWatermark,
        "occurrence site event source watermark",
        512,
    );
    instant(event.occurredAt, "occurrence site event occurrence time");
    instant(event.releasedAt, "occurrence site event release time");
    if (Date.parse(event.releasedAt) < Date.parse(event.occurredAt)) {
        throw new Error(
            "occurrence site event release time predates its source event",
        );
    }
    for (const [value, label] of [
        [event.sourceSnapshotManifestSha256, "source snapshot digest"],
        [
            event.sourceOccurrenceManifestSha256,
            "source occurrence manifest digest",
        ],
        [event.occurrencePolicySha256, "occurrence policy digest"],
        [event.occurrenceStoreIdentitySha256, "occurrence store identity"],
        [event.producerResultSha256, "producer result digest"],
        [event.buildOperationSha256, "build operation identity"],
        [event.verificationOperationSha256, "verification operation identity"],
        [event.siteStoreIdentitySha256, "site store identity"],
    ])
        digest(value, label);
    nullableDigest(
        event.expectedSiteSelectionSha256,
        "site selection precondition",
    );
    return event;
}

function validateProducerResult(
    result,
    event,
    policySha256,
    manifestSha256,
    manifest,
) {
    validateOccurrenceProducerResult(result, {
        policySha256,
        sourceOccurrenceManifestSha256: manifestSha256,
        sourceId: event.sourceId,
        sourceWatermark: event.sourceWatermark,
        sourceWatermarkAt: event.occurredAt,
    });
    if (sha256(canonicalBytes(result)) !== event.producerResultSha256) {
        throw new Error(
            "occurrence producer result does not match the exact publish event",
        );
    }
    const legacyByDashboard = result.datasourceBindingProof.legacyByDashboard;
    const manifestCounts = new Map(
        legacyByDashboard.map(({ uid }) => [uid, 0]),
    );
    let reportingDefaults = 0;
    for (const graph of manifest.graphs ?? []) {
        if (manifestCounts.has(graph.uid)) {
            manifestCounts.set(graph.uid, manifestCounts.get(graph.uid) + 1);
        } else {
            reportingDefaults += 1;
        }
    }
    if (
        reportingDefaults !==
            result.datasourceBindingProof.reportingDefaultCount ||
        legacyByDashboard.some(
            ({ uid, count }) => manifestCounts.get(uid) !== count,
        )
    ) {
        throw new Error(
            "occurrence producer datasource proof does not match the source occurrence plan",
        );
    }
    return result;
}

function validateBuildOperation(operation, event) {
    const legacy =
        exactKeys(operation, [
            "contract",
            "schemaVersion",
            "operationSha256",
            "build",
        ]) && operation.schemaVersion === 1;
    const profiled =
        exactKeys(operation, [
            "contract",
            "schemaVersion",
            "operationSha256",
            "publicationProfile",
            "build",
        ]) && operation.schemaVersion === 2;
    if (
        (!legacy && !profiled) ||
        operation.contract !== "verdify.lab-selected-astro-build-operation" ||
        operation.operationSha256 !== event.buildOperationSha256 ||
        !SHA256_RE.test(operation.operationSha256) ||
        typeof operation.build !== "function"
    ) {
        throw new Error(
            "Astro build operation does not use the bound v1 contract",
        );
    }
    const publicationProfile = profiled
        ? validatePublicationProfile(operation.publicationProfile)
        : structuredClone(PRODUCTION_PUBLICATION_PROFILE);
    if (
        profiled &&
        occurrenceSiteOperationSha256("build", publicationProfile) !==
            operation.operationSha256
    ) {
        throw new Error(
            "Astro build operation identity does not bind its publication profile",
        );
    }
    return { operation, publicationProfile, targetAttested: profiled };
}

function validateVerificationOperation(operation, event) {
    const legacy =
        exactKeys(operation, [
            "contract",
            "schemaVersion",
            "operationSha256",
            "verify",
        ]) &&
        operation.contract ===
            "verdify.lab-production-output-verification-operation" &&
        operation.schemaVersion === 1;
    const profiled =
        exactKeys(operation, [
            "contract",
            "schemaVersion",
            "operationSha256",
            "publicationProfile",
            "verify",
        ]) &&
        operation.contract ===
            "verdify.lab-site-output-verification-operation" &&
        operation.schemaVersion === 2;
    if (
        (!legacy && !profiled) ||
        operation.operationSha256 !== event.verificationOperationSha256 ||
        !SHA256_RE.test(operation.operationSha256) ||
        typeof operation.verify !== "function"
    ) {
        throw new Error("site output verifier does not use a bound contract");
    }
    const publicationProfile = profiled
        ? validatePublicationProfile(operation.publicationProfile)
        : structuredClone(PRODUCTION_PUBLICATION_PROFILE);
    if (
        profiled &&
        occurrenceSiteOperationSha256("verification", publicationProfile) !==
            operation.operationSha256
    ) {
        throw new Error(
            "site output verifier identity does not bind its publication profile",
        );
    }
    return { operation, publicationProfile, targetAttested: profiled };
}

function validateCheckpointOperations(operation, event) {
    if (
        !exactKeys(operation, [
            "contract",
            "schemaVersion",
            "storeIdentitySha256",
            "read",
            "write",
        ]) ||
        operation.contract !==
            "verdify.lab-occurrence-site-checkpoint-operations" ||
        operation.schemaVersion !== 1 ||
        operation.storeIdentitySha256 !== event.siteStoreIdentitySha256 ||
        typeof operation.read !== "function" ||
        typeof operation.write !== "function"
    )
        throw new Error(
            "occurrence site checkpoint operations do not use the bound v1 contract",
        );
    return operation;
}

function validatePublicationOperation(operation, event) {
    const keys = [
        "contract",
        "schemaVersion",
        "storeIdentitySha256",
        "readSelection",
        "readRelease",
        "readBlob",
        "readEventIntent",
        "publish",
    ];
    if (
        !exactKeys(operation, keys) ||
        operation.contract !==
            "verdify.lab-site-release-publication-operation" ||
        operation.schemaVersion !== 1 ||
        operation.storeIdentitySha256 !== event.siteStoreIdentitySha256 ||
        keys.slice(3).some((key) => typeof operation[key] !== "function")
    )
        throw new Error(
            "site release publication does not use the bound v1 contract",
        );
    return operation;
}

function deterministicOccurrenceEventId(prefix, value) {
    return `evt_${prefix}_${sha256(canonicalBytes(value)).slice(0, 32)}`;
}

function recoverableCandidate({
    record,
    kind,
    bounds,
    exportedAt,
    verifiedAt,
}) {
    if (record.candidate === null) return null;
    const match = new RegExp(
        `^${kind}/${record.occurrenceId}/([0-9a-f]{64})\\.png$`,
        "u",
    ).exec(record.candidate.relativePath);
    if (match === null) {
        throw new Error(
            `${kind} recovery candidate path is not content-addressed`,
        );
    }
    if (
        Date.parse(record.candidate.capturedAt) > Date.parse(exportedAt) ||
        Date.parse(verifiedAt) - Date.parse(record.candidate.capturedAt) >
            bounds.maxCandidateAgeSeconds * 1000
    ) {
        throw new Error(`${kind} recovery candidate time is invalid`);
    }
    return {
        relativePath: record.candidate.relativePath,
        expectedSha256: match[1],
        verifiedAt,
        capturedAt: record.candidate.capturedAt,
        ...(kind === "current-media"
            ? {
                  requestProvenanceSha256:
                      record.candidate.requestProvenanceSha256,
              }
            : {}),
    };
}

function fallbackMatchesCandidate(fallback, candidate, bounds, policyVersion) {
    return (
        fallback !== null &&
        fallback.publicPath ===
            `/evidence/blobs/sha256/${candidate.expectedSha256}.png` &&
        fallback.sha256 === candidate.expectedSha256 &&
        fallback.mediaType === "image/png" &&
        fallback.bytes <= bounds.maxBytes &&
        fallback.width >= bounds.minWidth &&
        fallback.width <= bounds.maxWidth &&
        fallback.height >= bounds.minHeight &&
        fallback.height <= bounds.maxHeight &&
        fallback.capturedAt === candidate.capturedAt &&
        fallback.verifiedAt === candidate.verifiedAt &&
        fallback.policyVersion === policyVersion
    );
}

function selectedOccurrenceProof({ producerResult, selected, cameraBindings }) {
    return {
        contract: "verdify.lab-occurrence-export-selected-proof",
        schemaVersion: 1,
        batchId: producerResult.exportBatch.batchId,
        policyVersion: producerResult.exportBatch.policyVersion,
        policySha256: producerResult.policySha256,
        sourceOccurrenceManifestSha256:
            producerResult.sourceOccurrenceManifestSha256,
        reportingFeedSha256: producerResult.reportingFeedSha256,
        media: cameraBindings.map(
            ({
                occurrenceId,
                disposition,
                eventId,
                selectionSha256,
                generationSha256,
                blobSha256,
            }) => ({
                occurrenceId,
                status:
                    disposition === "captured"
                        ? "selected"
                        : "retained-aggregate-lkg",
                eventId,
                selectionSha256,
                generationSha256,
                blobSha256,
            }),
        ),
        aggregate: {
            status: "selected",
            eventId: selected.current.event.eventId,
            manifestSha256: selected.selection.current.manifestSha256,
            selectionSha256: selected.selectionSha256,
        },
    };
}

async function recoverSelectedOccurrenceProof({
    operations,
    policy,
    manifest,
    manifestSha256,
    producerResult,
    processingAt,
}) {
    const selected = await loadSelectedOccurrenceRelease(
        operations.evidenceStore,
    );
    if (selected.selection === null || selected.current === null) return null;

    const batch = producerResult.exportBatch;
    const discovered = validatePolicyManifestBinding(
        policy,
        manifest,
        manifestSha256,
    );
    const feedFreshness = validateOccurrenceExportBatch(
        batch,
        policy,
        processingAt,
    );
    if (
        feedFreshness.status === "alert" ||
        selected.selection.current.manifestSha256 !==
            sha256(canonicalBytes(selected.current)) ||
        selected.selection.current.eventId !== selected.current.event.eventId ||
        selected.current.policyVersion !== policy.policyVersion ||
        selected.current.policySha256 !== producerResult.policySha256 ||
        selected.current.sourceSnapshotManifestSha256 !==
            policy.sourceSnapshotManifestSha256 ||
        selected.current.publishedAt !== feedFreshness.effectiveProcessingAt ||
        selected.current.occurrences.graphs.length !== 143 ||
        selected.current.occurrences.currentMedia.length !== 2
    ) {
        return null;
    }

    const graphBatchById = new Map(
        batch.graphs.map((record) => [record.occurrenceId, record]),
    );
    const priorGraphById = new Map(
        (selected.previous?.occurrences.graphs ?? []).map((record) => [
            record.occurrenceId,
            record,
        ]),
    );
    for (let index = 0; index < discovered.graphs.length; index += 1) {
        const expected = discovered.graphs[index];
        const actual = selected.current.occurrences.graphs[index];
        const batchRecord = graphBatchById.get(expected.occurrenceId);
        const candidate = recoverableCandidate({
            record: batchRecord,
            kind: "graphs",
            bounds: policy.imagePolicy.graphs,
            exportedAt: batch.exportedAt,
            verifiedAt: feedFreshness.effectiveProcessingAt,
        });
        const prior = priorGraphById.get(expected.occurrenceId) ?? null;
        if (
            actual?.occurrenceId !== expected.occurrenceId ||
            batchRecord?.occurrenceId !== expected.occurrenceId ||
            Object.keys(expected).some(
                (key) =>
                    !canonicalBytes(actual[key]).equals(
                        canonicalBytes(expected[key]),
                    ),
            ) ||
            actual.staleAfterSeconds !==
                Math.max(expected.renderCadenceSeconds * 2, 1800) ||
            actual.probeStatus !== batchRecord.probeStatus ||
            (candidate !== null &&
                (actual.state !== "verified" ||
                    !fallbackMatchesCandidate(
                        actual.fallback,
                        candidate,
                        policy.imagePolicy.graphs,
                        policy.policyVersion,
                    ))) ||
            (candidate === null &&
                (prior?.fallback !== null && prior?.fallback !== undefined
                    ? actual.state !== "retained-last-known-good" ||
                      !canonicalBytes(actual.fallback).equals(
                          canonicalBytes(prior.fallback),
                      )
                    : actual.state !== "missing" || actual.fallback !== null))
        ) {
            return null;
        }
    }

    const mediaBatchById = new Map(
        batch.currentMedia.map((record) => [record.occurrenceId, record]),
    );
    const cameraBindings = [];
    for (let index = 0; index < discovered.currentMedia.length; index += 1) {
        const expected = discovered.currentMedia[index];
        const actual = selected.current.occurrences.currentMedia[index];
        const batchRecord = mediaBatchById.get(expected.occurrenceId);
        const candidate = recoverableCandidate({
            record: batchRecord,
            kind: "current-media",
            bounds: policy.imagePolicy.currentMedia,
            exportedAt: batch.exportedAt,
            verifiedAt: feedFreshness.effectiveProcessingAt,
        });
        const selectedMedia = await loadSelectedCurrentMediaGeneration(
            operations.evidenceStore,
            expected.occurrenceId,
        );
        const captured =
            batchRecord?.captureStatus === "success" && candidate !== null;
        const expectedMediaPayloadSha256 = captured
            ? currentMediaGenerationPayloadSha256({
                  policyVersion: policy.policyVersion,
                  policySha256: producerResult.policySha256,
                  requestProvenanceSha256: batchRecord.requestProvenanceSha256,
                  occurrence: expected,
                  candidate,
              })
            : null;
        const expectedMediaEvent = captured
            ? {
                  contract: "verdify.lab-release-trigger",
                  schemaVersion: 1,
                  eventId: deterministicOccurrenceEventId("media", {
                      batchId: batch.batchId,
                      occurrenceId: expected.occurrenceId,
                      payloadSha256: expectedMediaPayloadSha256,
                  }),
                  eventType: "current-media-updated",
                  sourceId: batch.reportingFeed.sourceId,
                  sourceWatermark: batch.reportingFeed.sourceWatermark,
                  occurredAt: batch.reportingFeed.sourceWatermarkAt,
                  payloadSha256: expectedMediaPayloadSha256,
              }
            : null;
        if (
            actual?.occurrenceId !== expected.occurrenceId ||
            batchRecord?.occurrenceId !== expected.occurrenceId ||
            selectedMedia === null ||
            Object.keys(expected).some(
                (key) =>
                    !canonicalBytes(actual[key]).equals(
                        canonicalBytes(expected[key]),
                    ),
            ) ||
            actual.policySha256 !== producerResult.policySha256 ||
            actual.requestProvenanceSha256 !==
                batchRecord.requestProvenanceSha256 ||
            actual.staleAfterSeconds !==
                Math.max(expected.captureCadenceSeconds * 2, 900) ||
            actual.captureStatus !== "selected-generation" ||
            actual.state !== "verified" ||
            actual.fallback === null ||
            actual.pointer?.selectionSha256 !== selectedMedia.selectionSha256 ||
            actual.pointer?.generation !== selectedMedia.selection.generation ||
            actual.pointer?.currentGenerationSha256 !==
                selectedMedia.selection.current.generationSha256 ||
            actual.pointer?.previousGenerationSha256 !==
                (selectedMedia.selection.previous?.generationSha256 ?? null) ||
            !canonicalBytes(actual.fallback).equals(
                canonicalBytes(selectedMedia.current.fallback),
            ) ||
            selectedMedia.current.sourceProvenanceSha256 !==
                expected.sourceProvenanceSha256 ||
            selectedMedia.current.policyVersion !== policy.policyVersion ||
            selectedMedia.current.policySha256 !==
                producerResult.policySha256 ||
            selectedMedia.current.requestProvenanceSha256 !==
                batchRecord.requestProvenanceSha256 ||
            (captured &&
                (!canonicalBytes(selectedMedia.current.event).equals(
                    canonicalBytes(expectedMediaEvent),
                ) ||
                    selectedMedia.current.publishedAt !==
                        feedFreshness.effectiveProcessingAt)) ||
            (candidate !== null &&
                !fallbackMatchesCandidate(
                    selectedMedia.current.fallback,
                    candidate,
                    policy.imagePolicy.currentMedia,
                    policy.policyVersion,
                ))
        ) {
            return null;
        }
        cameraBindings.push({
            occurrenceId: expected.occurrenceId,
            disposition: captured ? "captured" : "retained-aggregate-lkg",
            selectionSha256: selectedMedia.selectionSha256,
            selectionGeneration: selectedMedia.selection.generation,
            generationSha256: selectedMedia.selection.current.generationSha256,
            previousGenerationSha256:
                selectedMedia.selection.previous?.generationSha256 ?? null,
            blobSha256: selectedMedia.selection.current.blobSha256,
            eventId: selectedMedia.current.event.eventId,
            sourceProvenanceSha256: expected.sourceProvenanceSha256,
            policySha256: producerResult.policySha256,
            requestProvenanceSha256: batchRecord.requestProvenanceSha256,
            fallback: selectedMedia.current.fallback,
        });
    }

    const reconciliation = {
        contract: "verdify.lab-exact-occurrence-reconciliation",
        schemaVersion: 1,
        batchId: batch.batchId,
        policyVersion: policy.policyVersion,
        policySha256: producerResult.policySha256,
        sourceSnapshotManifestSha256: policy.sourceSnapshotManifestSha256,
        sourceOccurrenceManifestSha256: manifestSha256,
        reportingFeedSha256: producerResult.reportingFeedSha256,
        graphResultSha256: producerResult.graphResultSha256,
        cameraBindings,
        publishedAt: feedFreshness.effectiveProcessingAt,
    };
    const reconciliationSha256 = sha256(canonicalBytes(reconciliation));
    const expectedEvent = {
        contract: "verdify.lab-release-trigger",
        schemaVersion: 1,
        eventId: deterministicOccurrenceEventId("reconcile", {
            batchId: batch.batchId,
            reconciliationSha256,
        }),
        eventType: "reconciliation",
        sourceId: batch.reportingFeed.sourceId,
        sourceWatermark: batch.reportingFeed.sourceWatermark,
        occurredAt: batch.reportingFeed.sourceWatermarkAt,
        payloadSha256: reconciliationSha256,
    };
    if (
        !canonicalBytes(selected.current.event).equals(
            canonicalBytes(expectedEvent),
        )
    ) {
        return null;
    }

    const intentValue = canonicalValue(
        await operations.readAggregateEventIntent(expectedEvent.eventId),
        "selected aggregate occurrence event intent",
    );
    const intent = intentValue.document;
    if (
        !exactKeys(intent, [
            "contract",
            "schemaVersion",
            "eventId",
            "storeIdentitySha256",
            "eventSha256",
            "payloadSha256",
            "reconciliationSha256",
            "manifestSha256",
            "expectedSelectionSha256",
            "cameraSelections",
        ]) ||
        intent.contract !== "verdify.lab-exact-reconciliation-intent" ||
        intent.schemaVersion !== 1 ||
        intent.eventId !== expectedEvent.eventId ||
        intent.storeIdentitySha256 !== operations.storeIdentitySha256 ||
        intent.eventSha256 !== sha256(canonicalBytes(expectedEvent)) ||
        intent.payloadSha256 !== reconciliationSha256 ||
        intent.reconciliationSha256 !== reconciliationSha256 ||
        intent.manifestSha256 !== selected.selection.current.manifestSha256 ||
        intent.expectedSelectionSha256 !== batch.expectedSelectionSha256 ||
        !canonicalBytes(intent.cameraSelections).equals(
            canonicalBytes(
                cameraBindings.map(({ occurrenceId, selectionSha256 }) => ({
                    occurrenceId,
                    selectionSha256,
                })),
            ),
        )
    ) {
        return null;
    }
    return {
        selected,
        proof: selectedOccurrenceProof({
            producerResult,
            selected,
            cameraBindings,
        }),
    };
}

function checkpointDocument(event, occurrenceProof, selected) {
    return {
        contract: "verdify.lab-occurrence-site-publish-checkpoint",
        schemaVersion: 1,
        eventId: event.eventId,
        eventSha256: sha256(canonicalBytes(event)),
        producerResultSha256: event.producerResultSha256,
        occurrenceCallResultSha256: sha256(canonicalBytes(occurrenceProof)),
        sourceSnapshotManifestSha256: event.sourceSnapshotManifestSha256,
        sourceOccurrenceManifestSha256: event.sourceOccurrenceManifestSha256,
        occurrencePolicySha256: event.occurrencePolicySha256,
        occurrenceStoreIdentitySha256: event.occurrenceStoreIdentitySha256,
        occurrenceSelectionSha256: selected.selectionSha256,
        occurrenceManifestSha256: selected.selection.current.manifestSha256,
        buildOperationSha256: event.buildOperationSha256,
        verificationOperationSha256: event.verificationOperationSha256,
        siteStoreIdentitySha256: event.siteStoreIdentitySha256,
        expectedSiteSelectionSha256: event.expectedSiteSelectionSha256,
    };
}

function validateCheckpoint(document, event) {
    if (
        !exactKeys(document, CHECKPOINT_KEYS) ||
        document.contract !==
            "verdify.lab-occurrence-site-publish-checkpoint" ||
        document.schemaVersion !== 1 ||
        document.eventId !== event.eventId ||
        document.eventSha256 !== sha256(canonicalBytes(event)) ||
        document.producerResultSha256 !== event.producerResultSha256 ||
        document.sourceSnapshotManifestSha256 !==
            event.sourceSnapshotManifestSha256 ||
        document.sourceOccurrenceManifestSha256 !==
            event.sourceOccurrenceManifestSha256 ||
        document.occurrencePolicySha256 !== event.occurrencePolicySha256 ||
        document.occurrenceStoreIdentitySha256 !==
            event.occurrenceStoreIdentitySha256 ||
        document.buildOperationSha256 !== event.buildOperationSha256 ||
        document.verificationOperationSha256 !==
            event.verificationOperationSha256 ||
        document.siteStoreIdentitySha256 !== event.siteStoreIdentitySha256 ||
        document.expectedSiteSelectionSha256 !==
            event.expectedSiteSelectionSha256
    )
        throw new Error(
            "occurrence site checkpoint conflicts with the exact event",
        );
    for (const [value, label] of [
        [document.occurrenceCallResultSha256, "occurrence call result digest"],
        [document.occurrenceSelectionSha256, "occurrence selection digest"],
        [document.occurrenceManifestSha256, "occurrence release digest"],
    ])
        digest(value, label);
    return document;
}

async function commitCheckpoint(operations, document) {
    let writeError = null;
    try {
        const written = await operations.write(structuredClone(document));
        canonicalValue(written, "written occurrence site checkpoint", document);
    } catch (error) {
        writeError = error;
    }
    let observed = null;
    try {
        observed = canonicalValue(
            await operations.read(document.eventId),
            "selected occurrence site checkpoint",
            document,
        );
    } catch (readError) {
        if (writeError !== null) throw writeError;
        throw readError;
    }
    if (writeError !== null && observed === null) throw writeError;
    return observed.document;
}

async function canonicalEmptyWorkspace(root) {
    const absolute = path.resolve(root);
    const metadata = await lstat(absolute, { bigint: true });
    if (
        !metadata.isDirectory() ||
        metadata.isSymbolicLink() ||
        (await realpath(absolute)) !== absolute ||
        (await readdir(absolute)).length !== 0
    )
        throw new Error(
            "occurrence site workspace is not one empty canonical directory",
        );
    return absolute;
}

async function canonicalBuildRoot(workspaceRoot, value) {
    if (
        !exactKeys(value, ["contract", "schemaVersion", "buildRoot"]) ||
        value.contract !== "verdify.lab-selected-astro-build-result" ||
        value.schemaVersion !== 1 ||
        typeof value.buildRoot !== "string"
    )
        throw new Error("Astro build operation returned an invalid result");
    const buildRoot = path.resolve(value.buildRoot);
    const relative = path.relative(workspaceRoot, buildRoot);
    const metadata = await lstat(buildRoot, { bigint: true });
    if (
        relative === "" ||
        relative === ".." ||
        relative.startsWith(`..${path.sep}`) ||
        path.isAbsolute(relative) ||
        !metadata.isDirectory() ||
        metadata.isSymbolicLink() ||
        (await realpath(buildRoot)) !== buildRoot
    )
        throw new Error("Astro build root escapes its exclusive workspace");
    return buildRoot;
}

function provenanceDocument(event, checkpoint) {
    return {
        contract: "verdify.lab-occurrence-site-build-provenance",
        schemaVersion: 1,
        eventId: event.eventId,
        eventSha256: checkpoint.eventSha256,
        producerResultSha256: checkpoint.producerResultSha256,
        occurrenceCallResultSha256: checkpoint.occurrenceCallResultSha256,
        sourceSnapshotManifestSha256: checkpoint.sourceSnapshotManifestSha256,
        sourceOccurrenceManifestSha256:
            checkpoint.sourceOccurrenceManifestSha256,
        occurrencePolicySha256: checkpoint.occurrencePolicySha256,
        occurrenceStoreIdentitySha256: checkpoint.occurrenceStoreIdentitySha256,
        occurrenceSelectionSha256: checkpoint.occurrenceSelectionSha256,
        occurrenceManifestSha256: checkpoint.occurrenceManifestSha256,
        builderCommit: event.builderCommit,
        buildOperationSha256: checkpoint.buildOperationSha256,
        verificationOperationSha256: checkpoint.verificationOperationSha256,
        siteStoreIdentitySha256: checkpoint.siteStoreIdentitySha256,
    };
}

function validateProvenance(document, event, checkpoint) {
    const expected = provenanceDocument(event, checkpoint);
    if (
        !exactKeys(document, PROVENANCE_KEYS) ||
        !canonicalBytes(document).equals(canonicalBytes(expected))
    )
        throw new Error(
            "site build provenance does not bind the exact occurrence event",
        );
    return document;
}

async function writeProvenance(buildRoot, document) {
    const destination = path.join(buildRoot, PROVENANCE_PATH);
    const handle = await open(
        destination,
        fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_EXCL,
        0o644,
    );
    try {
        await handle.writeFile(canonicalBytes(document));
        await handle.sync();
    } finally {
        await handle.close();
    }
    const bytes = await readFile(destination);
    if (!bytes.equals(canonicalBytes(document)))
        throw new Error("site build provenance changed after write");
}

function publicInventory(inventory) {
    return inventory.files.map(
        ({ sourcePath: _sourcePath, ...record }) => record,
    );
}

function inventoryIdentity(inventory) {
    return sha256(
        canonicalBytes({
            contract: "verdify.lab-built-site-inventory",
            schemaVersion: 1,
            totalBytes: inventory.totalBytes,
            files: publicInventory(inventory),
        }),
    );
}

async function readCanonicalJson(file, label) {
    const handle = await open(
        file,
        fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW,
    );
    try {
        const before = await handle.stat({ bigint: true });
        if (
            !before.isFile() ||
            before.nlink !== 1n ||
            before.size < 1n ||
            before.size > 16n * 1024n * 1024n
        ) {
            throw new Error(`${label} is not a bounded single-link file`);
        }
        const bytes = await handle.readFile();
        const after = await handle.stat({ bigint: true });
        if (
            after.dev !== before.dev ||
            after.ino !== before.ino ||
            after.size !== before.size
        ) {
            throw new Error(`${label} changed while being read`);
        }
        const document = JSON.parse(bytes.toString("utf8"));
        if (!canonicalBytes(document).equals(bytes))
            throw new Error(`${label} is not canonical JSON`);
        return { document, bytes, sha256: sha256(bytes) };
    } finally {
        await handle.close();
    }
}

async function verifySelectedBuild({
    buildRoot,
    event,
    checkpoint,
    policy,
    selected,
    inventory,
    publicationProfile,
    targetAttested,
}) {
    const [buildValue, occurrenceValue, provenanceValue] = await Promise.all([
        readCanonicalJson(
            path.join(buildRoot, "static-build.json"),
            "Astro static build record",
        ),
        readCanonicalJson(
            path.join(buildRoot, "occurrence-manifest.json"),
            "Astro occurrence manifest",
        ),
        readCanonicalJson(
            path.join(buildRoot, PROVENANCE_PATH),
            "occurrence publish provenance",
        ),
    ]);
    const build = buildValue.document;
    const occurrenceManifest = occurrenceValue.document;
    if (
        targetAttested &&
        (build.siteOrigin !== publicationProfile.siteOrigin ||
            build.stageGlobalNoindex !== publicationProfile.stageGlobalNoindex)
    ) {
        throw new Error(
            "Astro build does not attest the bound site publication target",
        );
    }
    for (const [relative, value] of [
        ["static-build.json", buildValue],
        ["occurrence-manifest.json", occurrenceValue],
        [PROVENANCE_PATH, provenanceValue],
    ]) {
        const record = inventory.files.find(
            ({ path: name }) => name === relative,
        );
        if (
            record === undefined ||
            record.sha256 !== value.sha256 ||
            record.bytes !== value.bytes.length
        ) {
            throw new Error(
                `Astro semantic file differs from its inventoried bytes: ${relative}`,
            );
        }
    }
    validateProvenance(provenanceValue.document, event, checkpoint);
    if (
        build.snapshotManifestDigest !==
            `sha256:${event.sourceSnapshotManifestSha256}` ||
        build.selectedOccurrenceManifestSha256 !==
            `sha256:${checkpoint.occurrenceManifestSha256}` ||
        occurrenceManifest.selectedManifestSha256 !==
            checkpoint.occurrenceManifestSha256 ||
        build.grafanaOccurrenceCount !== 143 ||
        build.currentMediaOccurrenceCount !== 2 ||
        occurrenceManifest.graphs?.length !== 143 ||
        occurrenceManifest.currentMedia?.length !== 2 ||
        build.materializedOccurrenceBlobCount < 1
    )
        throw new Error(
            "Astro build does not select the exact complete 143+2 occurrence release",
        );
    validatePolicyManifestBinding(
        policy,
        occurrenceManifest,
        checkpoint.sourceOccurrenceManifestSha256,
    );
    for (const [kind, served, released] of [
        [
            "graph",
            occurrenceManifest.graphs,
            selected.current.occurrences.graphs,
        ],
        [
            "current-media",
            occurrenceManifest.currentMedia,
            selected.current.occurrences.currentMedia,
        ],
    ]) {
        if (served.length !== released.length) {
            throw new Error(
                `Astro build has incomplete selected ${kind} evidence`,
            );
        }
        for (let index = 0; index < served.length; index += 1) {
            if (
                served[index].occurrenceId !== released[index].occurrenceId ||
                !canonicalBytes(served[index].selected).equals(
                    canonicalBytes(released[index]),
                )
            ) {
                throw new Error(
                    `Astro build selected ${kind} evidence differs from the exact store release`,
                );
            }
        }
    }
    verifySelectedEvidence(build, occurrenceManifest);
    return {
        buildInventorySha256: inventoryIdentity(inventory),
        buildSha256: buildValue.sha256,
        occurrenceOutputManifestSha256: occurrenceValue.sha256,
        provenanceSha256: provenanceValue.sha256,
    };
}

function validateVerificationResult(value, expected, targetAttested) {
    const identityKeys = [
        "buildInventorySha256",
        "buildContentIdentitySha256",
        "staticBuildSha256",
        "occurrenceOutputManifestSha256",
        "occurrenceSelectionSha256",
        "occurrenceManifestSha256",
        "provenanceSha256",
        "siteEventSha256",
        "sitePayloadSha256",
    ];
    const legacy =
        !targetAttested &&
        exactKeys(value, ["contract", "schemaVersion", ...identityKeys]) &&
        value.contract ===
            "verdify.lab-production-output-verification-result" &&
        value.schemaVersion === 1;
    const profiled =
        targetAttested &&
        exactKeys(value, [
            "contract",
            "schemaVersion",
            "siteOrigin",
            "stageGlobalNoindex",
            "policyVersion",
            ...identityKeys,
        ]) &&
        value.contract === "verdify.lab-site-output-verification-result" &&
        value.schemaVersion === 2 &&
        value.siteOrigin === expected.siteOrigin &&
        value.stageGlobalNoindex === expected.stageGlobalNoindex &&
        value.policyVersion === expected.policyVersion;
    if (
        (!legacy && !profiled) ||
        value.buildInventorySha256 !== expected.buildInventorySha256 ||
        value.buildContentIdentitySha256 !==
            expected.buildContentIdentitySha256 ||
        value.staticBuildSha256 !== expected.staticBuildSha256 ||
        value.occurrenceOutputManifestSha256 !==
            expected.occurrenceOutputManifestSha256 ||
        value.occurrenceSelectionSha256 !==
            expected.occurrenceSelectionSha256 ||
        value.occurrenceManifestSha256 !== expected.occurrenceManifestSha256 ||
        value.provenanceSha256 !== expected.provenanceSha256 ||
        value.siteEventSha256 !== expected.siteEventSha256 ||
        value.sitePayloadSha256 !== expected.sitePayloadSha256
    )
        throw new Error(
            "site output verifier did not attest the exact selected build and target",
        );
    return value;
}

function siteEvent(event, contentIdentitySha256, policyVersion) {
    return {
        contract: "verdify.lab-release-trigger",
        schemaVersion: 1,
        eventId: event.eventId,
        eventType: "reconciliation",
        sourceId: event.sourceId,
        sourceWatermark: event.sourceWatermark,
        occurredAt: event.occurredAt,
        payloadSha256: siteReleasePayloadSha256({
            sourceSnapshotManifestSha256: event.sourceSnapshotManifestSha256,
            policyVersion,
            builderCommit: event.builderCommit,
            contentIdentitySha256,
        }),
    };
}

async function validateSelectedOccurrence(operations, checkpoint, policy) {
    const selected = await loadSelectedOccurrenceRelease(
        operations.evidenceStore,
    );
    if (
        selected.selection === null ||
        selected.current === null ||
        selected.selectionSha256 !== checkpoint.occurrenceSelectionSha256 ||
        selected.selection.current.manifestSha256 !==
            checkpoint.occurrenceManifestSha256 ||
        selected.current.policySha256 !== checkpoint.occurrencePolicySha256 ||
        selected.current.sourceSnapshotManifestSha256 !==
            checkpoint.sourceSnapshotManifestSha256 ||
        selected.current.policyVersion !== policy.policyVersion
    )
        throw new Error(
            "selected occurrence store no longer matches the event checkpoint",
        );
    return selected;
}

function validateSiteSelection(value) {
    if (value === null) return null;
    const document = value?.document;
    const pointer = (candidate) =>
        candidate === null ||
        (exactKeys(candidate, ["releaseSha256", "eventId"]) &&
            SHA256_RE.test(candidate.releaseSha256) &&
            RELEASE_EVENT_ID_RE.test(candidate.eventId));
    if (
        !exactKeys(document, [
            "contract",
            "schemaVersion",
            "generation",
            "current",
            "previous",
            "selectedAt",
            "reason",
        ]) ||
        document.contract !== "verdify.lab-site-release-selection" ||
        document.schemaVersion !== 1 ||
        !Number.isSafeInteger(document.generation) ||
        document.generation < 1 ||
        document.current === null ||
        !pointer(document.current) ||
        !pointer(document.previous) ||
        document.current.releaseSha256 === document.previous?.releaseSha256 ||
        !["publish", "rollback"].includes(document.reason) ||
        typeof value.sha256 !== "string" ||
        sha256(canonicalBytes(document)) !== value.sha256
    ) {
        throw new Error("site publication selection read is invalid");
    }
    instant(document.selectedAt, "site publication selection time");
    return value;
}

async function currentSiteState(publication) {
    const selection = validateSiteSelection(await publication.readSelection());
    if (selection === null) return null;
    const manifest = await publication.readRelease(
        selection.document.current.releaseSha256,
    );
    const releaseBytes = canonicalBytes(manifest);
    validateSiteReleaseManifest(manifest, releaseBytes);
    if (
        sha256(releaseBytes) !== selection.document.current.releaseSha256 ||
        manifest.event.eventId !== selection.document.current.eventId
    ) {
        throw new Error("selected site release digest mismatch");
    }
    return {
        selection,
        manifest,
        releaseSha256: selection.document.current.releaseSha256,
    };
}

function assertEventOrder(event, current) {
    if (current === null) {
        if (event.expectedSiteSelectionSha256 !== null)
            throw new Error("site selection precondition is stale");
        return;
    }
    const comparison =
        Date.parse(event.occurredAt) -
        Date.parse(current.manifest.event.occurredAt);
    if (comparison < 0)
        throw new Error(
            "occurrence site publish event is older than the selected site release",
        );
    if (comparison === 0) {
        if (current.manifest.event.eventId !== event.eventId) {
            throw new Error(
                "occurrence site publish event conflicts at the selected source time",
            );
        }
        // An exact selected event retry is authorized by its immutable event
        // intent, not by the pre-transition selector it necessarily replaced.
        return;
    }
    if (current.selection.sha256 !== event.expectedSiteSelectionSha256) {
        throw new Error("site selection precondition is stale");
    }
}

async function publishedEventState(
    publication,
    event,
    checkpoint,
    publicationProfile,
) {
    const intent = await publication.readEventIntent(event.eventId);
    if (intent === null) return null;
    if (
        !exactKeys(intent, [
            "contract",
            "schemaVersion",
            "storeIdentitySha256",
            "eventId",
            "eventSha256",
            "payloadSha256",
            "releaseSha256",
            "expectedSelectionSha256",
        ]) ||
        intent.contract !== "verdify.lab-site-release-event-intent" ||
        intent.schemaVersion !== 2 ||
        intent.eventId !== event.eventId ||
        intent.storeIdentitySha256 !== event.siteStoreIdentitySha256 ||
        !SHA256_RE.test(intent.storeIdentitySha256) ||
        !SHA256_RE.test(intent.eventSha256) ||
        !SHA256_RE.test(intent.payloadSha256) ||
        !SHA256_RE.test(intent.releaseSha256) ||
        (intent.expectedSelectionSha256 !== null &&
            !SHA256_RE.test(intent.expectedSelectionSha256)) ||
        intent.expectedSelectionSha256 !== event.expectedSiteSelectionSha256
    )
        throw new Error(
            "published site event intent conflicts with the exact event",
        );
    const manifest = await publication.readRelease(intent.releaseSha256);
    const releaseBytes = canonicalBytes(manifest);
    validateSiteReleaseManifest(manifest, releaseBytes);
    if (
        sha256(releaseBytes) !== intent.releaseSha256 ||
        sha256(canonicalBytes(manifest.event)) !== intent.eventSha256 ||
        manifest.event.payloadSha256 !== intent.payloadSha256 ||
        manifest.event.eventId !== event.eventId ||
        manifest.event.sourceId !== event.sourceId ||
        manifest.event.sourceWatermark !== event.sourceWatermark ||
        manifest.event.occurredAt !== event.occurredAt ||
        manifest.sourceSnapshotManifestSha256 !==
            event.sourceSnapshotManifestSha256 ||
        manifest.builderCommit !== event.builderCommit ||
        manifest.policyVersion !== publicationProfile.policyVersion
    )
        throw new Error(
            "published site release does not match the exact event envelope",
        );
    const provenanceRecord = manifest.files.find(
        ({ path: relative }) => relative === PROVENANCE_PATH,
    );
    if (!provenanceRecord)
        throw new Error("published site release has no occurrence provenance");
    const provenanceBlob = await publication.readBlob(provenanceRecord.sha256, {
        maximumBytes: provenanceRecord.bytes,
    });
    if (
        !Buffer.isBuffer(provenanceBlob.body) ||
        provenanceBlob.bytes !== provenanceRecord.bytes ||
        provenanceBlob.sha256 !== provenanceRecord.sha256
    )
        throw new Error("published occurrence provenance blob is invalid");
    const provenance = JSON.parse(provenanceBlob.body.toString("utf8"));
    if (!canonicalBytes(provenance).equals(provenanceBlob.body)) {
        throw new Error("published occurrence provenance is not canonical");
    }
    validateProvenance(provenance, event, checkpoint);
    const selection = validateSiteSelection(await publication.readSelection());
    const isSelected =
        selection?.document.current.releaseSha256 === intent.releaseSha256 &&
        selection.document.current.eventId === event.eventId;
    const isResumable =
        !isSelected &&
        (selection?.sha256 ?? null) === intent.expectedSelectionSha256;
    if (!isSelected && !isResumable) {
        throw new Error(
            "published site event is neither selected nor exactly resumable",
        );
    }
    return {
        intent,
        manifest,
        selection,
        provenance,
        state: isSelected ? "selected" : "resumable",
    };
}

function publicResult({
    event,
    checkpoint,
    manifest,
    selectionSha256,
    status,
}) {
    const releaseSha256 = sha256(canonicalBytes(manifest));
    return {
        contract: "verdify.lab-occurrence-site-publish-result",
        schemaVersion: 1,
        status,
        eventId: event.eventId,
        eventSha256: checkpoint.eventSha256,
        producerResultSha256: checkpoint.producerResultSha256,
        occurrenceCallResultSha256: checkpoint.occurrenceCallResultSha256,
        occurrenceSelectionSha256: checkpoint.occurrenceSelectionSha256,
        occurrenceManifestSha256: checkpoint.occurrenceManifestSha256,
        occurrencePolicySha256: checkpoint.occurrencePolicySha256,
        occurrenceStoreIdentitySha256: checkpoint.occurrenceStoreIdentitySha256,
        buildOperationSha256: checkpoint.buildOperationSha256,
        verificationOperationSha256: checkpoint.verificationOperationSha256,
        buildContentIdentitySha256: manifest.contentIdentitySha256,
        siteStoreIdentitySha256: checkpoint.siteStoreIdentitySha256,
        siteEventSha256: sha256(canonicalBytes(manifest.event)),
        releaseSha256,
        siteSelectionSha256: selectionSha256,
    };
}

/**
 * Join one already-produced occurrence result to the selected occurrence store,
 * a single-use Astro workspace, target-profiled verification, and full-site release.
 * Every I/O-capable dependency is explicit; this module constructs no client,
 * transport, command, environment binding, route, workload, or activation.
 */
export async function processOccurrenceSitePublishEvent({
    event: rawEvent,
    producerResult: rawProducerResult,
    policy: rawPolicy,
    manifest: rawManifest,
    manifestSha256,
    occurrenceStore,
    candidateRoot,
    workspaceRoot,
    buildOperation: rawBuildOperation,
    verificationOperation: rawVerificationOperation,
    checkpointOperations: rawCheckpointOperations,
    publicationOperation: rawPublicationOperation,
}) {
    const event = structuredClone(rawEvent);
    const producerResult = structuredClone(rawProducerResult);
    const policy = structuredClone(rawPolicy);
    const manifest = structuredClone(rawManifest);
    validateOccurrenceSitePublishEvent(event);
    if (sha256(canonicalBytes(manifest)) !== manifestSha256) {
        throw new Error(
            "source occurrence manifest does not match its canonical digest",
        );
    }
    if (
        event.sourceOccurrenceManifestSha256 !== manifestSha256 ||
        occurrenceExportPolicySha256(policy) !== event.occurrencePolicySha256 ||
        policy.sourceSnapshotManifestSha256 !==
            event.sourceSnapshotManifestSha256 ||
        occurrenceStore?.identity?.sha256 !==
            event.occurrenceStoreIdentitySha256
    )
        throw new Error(
            "occurrence source, policy, or store identity conflicts with the event",
        );
    validateProducerResult(
        producerResult,
        event,
        event.occurrencePolicySha256,
        manifestSha256,
        manifest,
    );
    const buildBinding = validateBuildOperation(rawBuildOperation, event);
    const verificationBinding = validateVerificationOperation(
        rawVerificationOperation,
        event,
    );
    if (
        buildBinding.targetAttested !== verificationBinding.targetAttested ||
        !canonicalBytes(buildBinding.publicationProfile).equals(
            canonicalBytes(verificationBinding.publicationProfile),
        )
    ) {
        throw new Error(
            "Astro build and verification operations target different publication profiles",
        );
    }
    const buildOperation = buildBinding.operation;
    const verificationOperation = verificationBinding.operation;
    const publicationProfile = buildBinding.publicationProfile;
    const targetAttested = buildBinding.targetAttested;
    const checkpointOperations = validateCheckpointOperations(
        rawCheckpointOperations,
        event,
    );
    const publication = validatePublicationOperation(
        rawPublicationOperation,
        event,
    );

    const checkpointValue = await checkpointOperations.read(event.eventId);

    // Reject stale/out-of-order site events before selecting any occurrence data.
    const initialSiteState = await currentSiteState(publication);
    assertEventOrder(event, initialSiteState);

    const occurrenceOperations = await createOccurrenceExportStoreOperations({
        store: occurrenceStore,
        sourceRoot: candidateRoot,
    });
    let checkpoint;
    if (checkpointValue === null) {
        let recovered = await recoverSelectedOccurrenceProof({
            operations: occurrenceOperations,
            policy,
            manifest,
            manifestSha256,
            producerResult,
            processingAt: event.releasedAt,
        });
        if (recovered === null) {
            const occurrenceCallResult = await executeOccurrenceExportBatch({
                policy,
                manifest,
                manifestSha256,
                batch: producerResult.exportBatch,
                graphResult: producerResult.graphResult,
                sourceRoot: candidateRoot,
                processingAt: event.releasedAt,
                operations: occurrenceOperations,
            });
            if (occurrenceCallResult.status !== "selected") {
                throw new Error(
                    `occurrence export did not select the exact aggregate: ${occurrenceCallResult.failure?.code ?? occurrenceCallResult.status}`,
                );
            }
            recovered = await recoverSelectedOccurrenceProof({
                operations: occurrenceOperations,
                policy,
                manifest,
                manifestSha256,
                producerResult,
                processingAt: event.releasedAt,
            });
            if (
                recovered === null ||
                recovered.selected.selectionSha256 !==
                    occurrenceCallResult.aggregate.selectionSha256 ||
                recovered.selected.selection.current.manifestSha256 !==
                    occurrenceCallResult.aggregate.manifestSha256 ||
                recovered.selected.selection.current.eventId !==
                    occurrenceCallResult.aggregate.eventId
            ) {
                throw new Error(
                    "occurrence caller result does not match the exact selected aggregate proof",
                );
            }
        }
        checkpoint = await commitCheckpoint(
            checkpointOperations,
            checkpointDocument(event, recovered.proof, recovered.selected),
        );
    } else {
        checkpoint = validateCheckpoint(
            canonicalValue(checkpointValue, "occurrence site checkpoint")
                .document,
            event,
        );
    }
    const selected = await validateSelectedOccurrence(
        occurrenceOperations,
        checkpoint,
        policy,
    );

    const alreadyPublished = await publishedEventState(
        publication,
        event,
        checkpoint,
        publicationProfile,
    );
    if (alreadyPublished?.state === "selected") {
        return publicResult({
            event,
            checkpoint,
            manifest: alreadyPublished.manifest,
            selectionSha256: alreadyPublished.selection.sha256,
            status: "idempotent",
        });
    }

    const workspace = await canonicalEmptyWorkspace(workspaceRoot);
    const buildResult = await buildOperation.build({
        contract: targetAttested
            ? "verdify.lab-profiled-selected-astro-build-request"
            : "verdify.lab-selected-astro-build-request",
        schemaVersion: targetAttested ? 2 : 1,
        ...(targetAttested
            ? { publicationProfile: structuredClone(publicationProfile) }
            : {}),
        event: structuredClone(event),
        checkpoint: structuredClone(checkpoint),
        workspaceRoot: workspace,
        policy: structuredClone(policy),
        manifest: structuredClone(manifest),
        occurrenceStore,
    });
    const buildRoot = await canonicalBuildRoot(workspace, buildResult);
    await validateSelectedOccurrence(occurrenceOperations, checkpoint, policy);
    const provenance = provenanceDocument(event, checkpoint);
    await writeProvenance(buildRoot, provenance);
    const beforeSemanticReads = await inventoryBuiltSite(buildRoot);
    const buildEvidence = await verifySelectedBuild({
        buildRoot,
        event,
        checkpoint,
        policy,
        selected,
        inventory: beforeSemanticReads,
        publicationProfile,
        targetAttested,
    });
    const afterSemanticReads = await inventoryBuiltSite(buildRoot);
    if (
        inventoryIdentity(beforeSemanticReads) !==
        inventoryIdentity(afterSemanticReads)
    ) {
        throw new Error("Astro build changed during semantic verification");
    }
    const beforeVerification = afterSemanticReads;
    const files = publicInventory(beforeVerification);
    const contentIdentitySha256 = siteContentIdentitySha256({
        sourceSnapshotManifestSha256: event.sourceSnapshotManifestSha256,
        policyVersion: publicationProfile.policyVersion,
        builderCommit: event.builderCommit,
        files,
    });
    const releaseEvent = siteEvent(
        event,
        contentIdentitySha256,
        publicationProfile.policyVersion,
    );
    const verificationExpected = {
        siteOrigin: publicationProfile.siteOrigin,
        stageGlobalNoindex: publicationProfile.stageGlobalNoindex,
        policyVersion: publicationProfile.policyVersion,
        buildInventorySha256: inventoryIdentity(beforeVerification),
        buildContentIdentitySha256: contentIdentitySha256,
        staticBuildSha256: buildEvidence.buildSha256,
        occurrenceOutputManifestSha256:
            buildEvidence.occurrenceOutputManifestSha256,
        occurrenceSelectionSha256: checkpoint.occurrenceSelectionSha256,
        occurrenceManifestSha256: checkpoint.occurrenceManifestSha256,
        provenanceSha256: buildEvidence.provenanceSha256,
        siteEventSha256: sha256(canonicalBytes(releaseEvent)),
        sitePayloadSha256: releaseEvent.payloadSha256,
    };
    validateVerificationResult(
        await verificationOperation.verify({
            contract: targetAttested
                ? "verdify.lab-site-output-verification-request"
                : "verdify.lab-production-output-verification-request",
            schemaVersion: targetAttested ? 2 : 1,
            ...(targetAttested
                ? {
                      siteOrigin: publicationProfile.siteOrigin,
                      stageGlobalNoindex: publicationProfile.stageGlobalNoindex,
                      policyVersion: publicationProfile.policyVersion,
                  }
                : {}),
            event: structuredClone(event),
            buildRoot,
            buildContentIdentitySha256: contentIdentitySha256,
            occurrenceSelectionSha256: checkpoint.occurrenceSelectionSha256,
            occurrenceManifestSha256: checkpoint.occurrenceManifestSha256,
            buildInventorySha256: verificationExpected.buildInventorySha256,
            staticBuildSha256: verificationExpected.staticBuildSha256,
            occurrenceOutputManifestSha256:
                verificationExpected.occurrenceOutputManifestSha256,
            provenanceSha256: verificationExpected.provenanceSha256,
            siteEventSha256: verificationExpected.siteEventSha256,
            sitePayloadSha256: verificationExpected.sitePayloadSha256,
        }),
        verificationExpected,
        targetAttested,
    );
    const afterVerification = await inventoryBuiltSite(buildRoot);
    if (
        inventoryIdentity(beforeVerification) !==
        inventoryIdentity(afterVerification)
    ) {
        throw new Error("Astro build changed during site output verification");
    }
    await validateSelectedOccurrence(occurrenceOperations, checkpoint, policy);

    let publicationError = null;
    try {
        await publication.publish({
            buildRoot,
            event: releaseEvent,
            sourceSnapshotManifestSha256: event.sourceSnapshotManifestSha256,
            policyVersion: publicationProfile.policyVersion,
            builderCommit: event.builderCommit,
            releasedAt: event.releasedAt,
            expectedSelectionSha256: event.expectedSiteSelectionSha256,
        });
    } catch (error) {
        publicationError = error;
    }
    let published;
    try {
        published = await publishedEventState(
            publication,
            event,
            checkpoint,
            publicationProfile,
        );
    } catch (readError) {
        if (publicationError !== null) throw publicationError;
        throw readError;
    }
    if (published === null) {
        if (publicationError !== null) throw publicationError;
        throw new Error(
            "site release publication did not persist its exact event intent",
        );
    }
    if (published.state === "resumable") {
        if (publicationError !== null) throw publicationError;
        throw new Error(
            "site release publication persisted an exact resumable intent without selecting it",
        );
    }
    if (
        published.manifest.contentIdentitySha256 !== contentIdentitySha256 ||
        !canonicalBytes(published.manifest.event).equals(
            canonicalBytes(releaseEvent),
        )
    )
        throw new Error(
            "published site release is not the verified immutable build",
        );
    const afterPublication = await inventoryBuiltSite(buildRoot);
    if (
        inventoryIdentity(beforeVerification) !==
        inventoryIdentity(afterPublication)
    ) {
        throw new Error("Astro build changed during site release publication");
    }
    await validateSelectedOccurrence(occurrenceOperations, checkpoint, policy);
    return publicResult({
        event,
        checkpoint,
        manifest: published.manifest,
        selectionSha256: published.selection.sha256,
        status: "published",
    });
}

export const occurrenceSitePublisherContract = Object.freeze({
    event: Object.freeze({
        contract: "verdify.lab-occurrence-site-publish-event",
        schemaVersion: 1,
    }),
    checkpoint: Object.freeze({
        contract: "verdify.lab-occurrence-site-publish-checkpoint",
        schemaVersion: 1,
    }),
    result: Object.freeze({
        contract: "verdify.lab-occurrence-site-publish-result",
        schemaVersion: 1,
        statuses: Object.freeze(["published", "idempotent"]),
    }),
    defaults: Object.freeze({
        buildOperation: null,
        verificationOperation: null,
        checkpointOperations: null,
        publicationOperation: null,
    }),
});
