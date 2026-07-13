import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";

import { OccurrenceReleaseStore } from "./occurrence-release-store.mjs";
import {
    evaluateEventFreshness,
    loadCurrentMediaGeneration,
} from "./occurrence-release.mjs";
import { validatePngFile } from "./png-validation.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const MEDIA_ID_RE = /^media_[0-9a-f]{24}$/u;

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

function digest(value, label) {
    if (typeof value !== "string" || !SHA256_RE.test(value)) {
        throw new Error(`${label} is invalid`);
    }
    return value;
}

function canonicalDocument(value, expected, label) {
    if (
        value === null ||
        typeof value !== "object" ||
        Array.isArray(value) ||
        value.document === null ||
        typeof value.document !== "object" ||
        Array.isArray(value.document) ||
        !Buffer.isBuffer(value.bytes) ||
        !SHA256_RE.test(value.sha256) ||
        !canonicalBytes(value.document).equals(value.bytes) ||
        sha256(value.bytes) !== value.sha256 ||
        !canonicalBytes(value.document).equals(canonicalBytes(expected))
    )
        throw new Error(
            `${label} post-read does not match the exact committed document`,
        );
    return value;
}

function sourceRoot(value) {
    if (
        typeof value !== "string" ||
        value.length === 0 ||
        value.length > 4096 ||
        /[\u0000-\u001f\u007f]/u.test(value)
    )
        throw new Error("occurrence export source root is invalid");
    return path.resolve(value);
}

function fallbackRecord(blob, candidate, policyVersion) {
    return {
        publicPath: `/evidence/blobs/sha256/${blob.sha256}.png`,
        sha256: blob.sha256,
        decodedSha256: blob.decodedSha256,
        decodedBytes: blob.decodedBytes,
        bytes: blob.bytes,
        mediaType: blob.mediaType,
        width: blob.width,
        height: blob.height,
        capturedAt: candidate.capturedAt,
        verifiedAt: candidate.verifiedAt,
        policyVersion,
    };
}

async function publishCandidateBlob({ store, root, candidate, readCandidate }) {
    const verified = await validatePngFile(root, candidate.relativePath);
    if (verified.sha256 !== candidate.expectedSha256) {
        throw new Error("occurrence candidate changed after caller inspection");
    }
    const bytes = await readCandidate(verified.sourcePath);
    if (!Buffer.isBuffer(bytes))
        throw new Error("occurrence candidate reader did not return bytes");
    return store.publishPngBlob(bytes, candidate.expectedSha256);
}

function mediaIntent(
    request,
    storeIdentitySha256,
    generationSha256,
    blobSha256,
) {
    return {
        contract: "verdify.lab-current-media-export-intent",
        schemaVersion: 1,
        eventId: request.event.eventId,
        storeIdentitySha256,
        eventSha256: sha256(canonicalBytes(request.event)),
        payloadSha256: request.event.payloadSha256,
        policySha256: request.policySha256,
        requestProvenanceSha256: request.requestProvenanceSha256,
        occurrenceId: request.occurrence.occurrenceId,
        generationSha256,
        blobSha256,
        expectedSelectionSha256: request.expectedSelectionSha256,
    };
}

function validateStoredMediaIntent(value, request, storeIdentitySha256) {
    if (
        value === null ||
        !exactKeys(value.document, [
            "contract",
            "schemaVersion",
            "eventId",
            "storeIdentitySha256",
            "eventSha256",
            "payloadSha256",
            "policySha256",
            "requestProvenanceSha256",
            "occurrenceId",
            "generationSha256",
            "blobSha256",
            "expectedSelectionSha256",
        ]) ||
        value.document.contract !== "verdify.lab-current-media-export-intent" ||
        value.document.schemaVersion !== 1 ||
        value.document.eventId !== request.event.eventId ||
        value.document.storeIdentitySha256 !== storeIdentitySha256 ||
        value.document.eventSha256 !== sha256(canonicalBytes(request.event)) ||
        value.document.payloadSha256 !== request.event.payloadSha256 ||
        value.document.policySha256 !== request.policySha256 ||
        value.document.requestProvenanceSha256 !==
            request.requestProvenanceSha256 ||
        value.document.occurrenceId !== request.occurrence.occurrenceId ||
        !SHA256_RE.test(value.document.generationSha256) ||
        !SHA256_RE.test(value.document.blobSha256) ||
        value.document.expectedSelectionSha256 !==
            request.expectedSelectionSha256
    )
        throw new Error(
            "current media event intent does not match the exact request",
        );
    canonicalDocument(value, value.document, "current media event intent");
    return value.document;
}

function validateReplayGeneration(value, request, intent) {
    const generation = value?.generation;
    if (
        value?.generationSha256 !== intent.generationSha256 ||
        generation?.occurrenceId !== request.occurrence.occurrenceId ||
        generation.sourceProvenanceSha256 !==
            request.occurrence.sourceProvenanceSha256 ||
        generation.policyVersion !== request.policyVersion ||
        generation.policySha256 !== request.policySha256 ||
        generation.requestProvenanceSha256 !==
            request.requestProvenanceSha256 ||
        !canonicalBytes(generation.event).equals(
            canonicalBytes(request.event),
        ) ||
        generation.publishedAt !== request.publishedAt ||
        generation.fallback.sha256 !== intent.blobSha256 ||
        generation.fallback.sha256 !== request.candidate.expectedSha256 ||
        generation.fallback.capturedAt !== request.candidate.capturedAt ||
        generation.fallback.verifiedAt !== request.candidate.verifiedAt ||
        generation.fallback.policyVersion !== request.policyVersion
    ) {
        throw new Error(
            "current media replay generation does not match the exact request and intent",
        );
    }
    return generation;
}

function mediaSelection(request, selected, generationSha256, blobSha256) {
    return {
        contract: "verdify.lab-current-media-selection",
        schemaVersion: 1,
        occurrenceId: request.occurrence.occurrenceId,
        generation: (selected?.document.generation ?? 0) + 1,
        current: { generationSha256, blobSha256 },
        previous: selected?.document.current ?? null,
        selectedAt: request.publishedAt,
        reason: "publish",
    };
}

async function writeMediaSelectionExact(
    store,
    request,
    selected,
    generationSha256,
    blobSha256,
) {
    const occurrenceId = request.occurrence.occurrenceId;
    const next = mediaSelection(
        request,
        selected,
        generationSha256,
        blobSha256,
    );
    let writeError = null;
    try {
        await store.writeCurrentMediaSelection(
            occurrenceId,
            next,
            selected?.sha256 ?? null,
        );
    } catch (error) {
        writeError = error;
    }
    let observed = null;
    try {
        observed = await store.readCurrentMediaSelection(occurrenceId);
        canonicalDocument(observed, next, "current media selection");
    } catch (readError) {
        if (writeError !== null) throw writeError;
        throw readError;
    }
    if (writeError !== null && observed === null) throw writeError;
    return observed;
}

async function publishCurrentMedia({ store, root, readCandidate }, request) {
    const occurrenceId = request?.occurrence?.occurrenceId;
    if (!MEDIA_ID_RE.test(occurrenceId ?? "")) {
        throw new Error("current media publication request is invalid");
    }
    let intentValue = await store.readCurrentMediaEventIntent(
        occurrenceId,
        request.event.eventId,
    );
    if (intentValue !== null) {
        const intent = validateStoredMediaIntent(
            intentValue,
            request,
            store.identity.sha256,
        );
        const generation = await loadCurrentMediaGeneration(
            store,
            occurrenceId,
            intent.generationSha256,
        );
        validateReplayGeneration(generation, request, intent);
        const selected = await store.readCurrentMediaSelection(occurrenceId);
        if (
            selected?.document.current.generationSha256 ===
            intent.generationSha256
        )
            return selected;
        if ((selected?.sha256 ?? null) !== intent.expectedSelectionSha256) {
            throw new Error("current media selection precondition failed");
        }
        return writeMediaSelectionExact(
            store,
            request,
            selected,
            intent.generationSha256,
            intent.blobSha256,
        );
    }

    const selected = await store.readCurrentMediaSelection(occurrenceId);
    if ((selected?.sha256 ?? null) !== request.expectedSelectionSha256) {
        throw new Error("current media selection precondition failed");
    }
    const blob = await publishCandidateBlob({
        store,
        root,
        candidate: request.candidate,
        readCandidate,
    });
    const fallback = fallbackRecord(
        blob,
        request.candidate,
        request.policyVersion,
    );
    const generation = {
        contract: "verdify.lab-current-media-generation",
        schemaVersion: 3,
        occurrenceId,
        sourceProvenanceSha256: request.occurrence.sourceProvenanceSha256,
        policySha256: request.policySha256,
        requestProvenanceSha256: request.requestProvenanceSha256,
        event: request.event,
        policyVersion: request.policyVersion,
        publishedAt: request.publishedAt,
        fallback,
    };
    const generationSha256 = await store.publishCurrentMediaGeneration(
        occurrenceId,
        generation,
    );
    const intent = mediaIntent(
        request,
        store.identity.sha256,
        generationSha256,
        fallback.sha256,
    );
    let intentWriteError = null;
    try {
        await store.publishCurrentMediaEventIntent(
            occurrenceId,
            request.event.eventId,
            intent,
        );
    } catch (error) {
        intentWriteError = error;
    }
    intentValue = await store
        .readCurrentMediaEventIntent(occurrenceId, request.event.eventId)
        .catch(() => null);
    if (intentValue === null) {
        if (intentWriteError !== null) throw intentWriteError;
        throw new Error(
            "current media event intent is unavailable after publication",
        );
    }
    canonicalDocument(intentValue, intent, "current media event intent");
    return writeMediaSelectionExact(
        store,
        request,
        selected,
        generationSha256,
        fallback.sha256,
    );
}

async function graphRecord(
    { store, root, readCandidate },
    input,
    prior,
    policyVersion,
) {
    if (input.candidate === undefined) {
        const { probeStatus, ...discovered } = input;
        if (prior?.fallback) {
            return {
                ...discovered,
                staleAfterSeconds: Math.max(
                    input.renderCadenceSeconds * 2,
                    1800,
                ),
                probeStatus,
                state: "retained-last-known-good",
                fallback: prior.fallback,
            };
        }
        return {
            ...discovered,
            staleAfterSeconds: Math.max(input.renderCadenceSeconds * 2, 1800),
            probeStatus,
            state: "missing",
            fallback: null,
        };
    }
    const { candidate, probeStatus, ...discovered } = input;
    const blob = await publishCandidateBlob({
        store,
        root,
        candidate,
        readCandidate,
    });
    return {
        ...discovered,
        staleAfterSeconds: Math.max(input.renderCadenceSeconds * 2, 1800),
        probeStatus,
        state: "verified",
        fallback: fallbackRecord(blob, candidate, policyVersion),
    };
}

function reconciliationIntent(command, storeIdentitySha256, manifestSha256) {
    return {
        contract: "verdify.lab-exact-reconciliation-intent",
        schemaVersion: 1,
        eventId: command.event.eventId,
        storeIdentitySha256,
        eventSha256: sha256(canonicalBytes(command.event)),
        payloadSha256: command.event.payloadSha256,
        reconciliationSha256: command.reconciliationSha256,
        manifestSha256,
        expectedSelectionSha256: command.expectedSelectionSha256,
        cameraSelections: command.reconciliation.cameraBindings.map(
            ({ occurrenceId, selectionSha256 }) => ({
                occurrenceId,
                selectionSha256,
            }),
        ),
    };
}

function validateStoredReconciliationIntent(
    value,
    command,
    storeIdentitySha256,
) {
    if (
        value === null ||
        !exactKeys(value.document, [
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
        value.document.contract !== "verdify.lab-exact-reconciliation-intent" ||
        value.document.schemaVersion !== 1 ||
        value.document.eventId !== command.event.eventId ||
        value.document.storeIdentitySha256 !== storeIdentitySha256 ||
        value.document.eventSha256 !== sha256(canonicalBytes(command.event)) ||
        value.document.payloadSha256 !== command.event.payloadSha256 ||
        value.document.reconciliationSha256 !== command.reconciliationSha256 ||
        !SHA256_RE.test(value.document.manifestSha256) ||
        value.document.expectedSelectionSha256 !==
            command.expectedSelectionSha256 ||
        JSON.stringify(value.document.cameraSelections) !==
            JSON.stringify(
                command.reconciliation.cameraBindings.map(
                    ({ occurrenceId, selectionSha256 }) => ({
                        occurrenceId,
                        selectionSha256,
                    }),
                ),
            )
    )
        throw new Error(
            "aggregate event intent does not match the exact reconciliation",
        );
    canonicalDocument(value, value.document, "aggregate event intent");
    return value.document;
}

async function publishAggregateReconciliation(context, command) {
    const { store } = context;
    let intentValue = await store.readAggregateEventIntent(
        command.event.eventId,
    );
    if (intentValue !== null) {
        const intent = validateStoredReconciliationIntent(
            intentValue,
            command,
            store.identity.sha256,
        );
        await store.readAggregateManifest(intent.manifestSha256);
        return intent;
    }

    const selected = await store.readAggregateSelection();
    if ((selected?.sha256 ?? null) !== command.expectedSelectionSha256) {
        throw new Error(
            "aggregate selection precondition failed before immutable publication",
        );
    }
    const priorManifest =
        selected === null
            ? null
            : (
                  await store.readAggregateManifest(
                      selected.document.current.manifestSha256,
                  )
              ).document;
    const priorGraphs = new Map(
        (priorManifest?.occurrences.graphs ?? []).map((record) => [
            record.occurrenceId,
            record,
        ]),
    );
    const graphs = [];
    for (const input of command.release.graphs) {
        graphs.push(
            await graphRecord(
                context,
                input,
                priorGraphs.get(input.occurrenceId),
                command.release.policyVersion,
            ),
        );
    }
    const mediaById = new Map(
        command.release.currentMedia.map((entry) => [
            entry.discovered.occurrenceId,
            entry,
        ]),
    );
    const currentMedia = command.reconciliation.cameraBindings.map(
        (binding) => {
            const entry = mediaById.get(binding.occurrenceId);
            if (entry === undefined)
                throw new Error(
                    "camera binding is absent from the reconciliation release",
                );
            return {
                ...entry.discovered,
                policySha256: binding.policySha256,
                requestProvenanceSha256: binding.requestProvenanceSha256,
                staleAfterSeconds: Math.max(
                    entry.discovered.captureCadenceSeconds * 2,
                    900,
                ),
                captureStatus: "selected-generation",
                state: "verified",
                fallback: binding.fallback,
                pointer: {
                    selectionSha256: binding.selectionSha256,
                    generation: binding.selectionGeneration,
                    currentGenerationSha256: binding.generationSha256,
                    previousGenerationSha256: binding.previousGenerationSha256,
                },
            };
        },
    );
    const manifest = {
        contract: "verdify.lab-specialist-occurrence-release",
        schemaVersion: 2,
        event: command.event,
        policyVersion: command.release.policyVersion,
        policySha256: command.release.policySha256,
        sourceSnapshotManifestSha256:
            command.release.sourceSnapshotManifestSha256,
        publishedAt: command.release.publishedAt,
        freshness: evaluateEventFreshness(
            command.event,
            command.release.publishedAt,
        ),
        occurrences: { graphs, currentMedia },
    };
    const manifestSha256 = await store.publishAggregateManifest(manifest);
    const intent = reconciliationIntent(
        command,
        store.identity.sha256,
        manifestSha256,
    );
    let intentWriteError = null;
    try {
        await store.publishAggregateEventIntent(command.event.eventId, intent);
    } catch (error) {
        intentWriteError = error;
    }
    intentValue = await store
        .readAggregateEventIntent(command.event.eventId)
        .catch(() => null);
    if (intentValue === null) {
        if (intentWriteError !== null) throw intentWriteError;
        throw new Error(
            "aggregate event intent is unavailable after publication",
        );
    }
    canonicalDocument(intentValue, intent, "aggregate event intent");
    return intent;
}

async function compareAndSwapAggregateSelection(store, command) {
    if (
        !exactKeys(command, [
            "selection",
            "expectedSelectionSha256",
            "cameraSelectionPreconditions",
        ]) ||
        !Array.isArray(command.cameraSelectionPreconditions) ||
        command.cameraSelectionPreconditions.length !== 2
    )
        throw new Error("aggregate selection command is invalid");
    const seen = new Set();
    for (const precondition of command.cameraSelectionPreconditions) {
        if (
            !exactKeys(precondition, ["occurrenceId", "selectionSha256"]) ||
            !MEDIA_ID_RE.test(precondition.occurrenceId) ||
            !SHA256_RE.test(precondition.selectionSha256) ||
            seen.has(precondition.occurrenceId)
        )
            throw new Error("camera selection precondition is invalid");
        seen.add(precondition.occurrenceId);
        const selected = await store.readCurrentMediaSelection(
            precondition.occurrenceId,
        );
        if (selected?.sha256 !== precondition.selectionSha256) {
            throw new Error("camera selection precondition failed");
        }
    }
    if (command.expectedSelectionSha256 !== null) {
        digest(
            command.expectedSelectionSha256,
            "aggregate selection precondition",
        );
    }
    let writeError = null;
    try {
        await store.writeAggregateSelection(
            command.selection,
            command.expectedSelectionSha256,
        );
    } catch (error) {
        writeError = error;
    }
    let observed = null;
    try {
        observed = await store.readAggregateSelection();
        canonicalDocument(observed, command.selection, "aggregate selection");
    } catch (readError) {
        if (writeError !== null) throw writeError;
        throw readError;
    }
    if (writeError !== null && observed === null) throw writeError;
    return observed;
}

/**
 * Bind the caller's closed v1 operation surface to one explicitly injected
 * occurrence store. This factory has no default location or client.
 */
export async function createOccurrenceExportStoreOperations({
    store,
    sourceRoot: candidateRoot,
    readCandidate = readFile,
}) {
    if (!(store instanceof OccurrenceReleaseStore)) {
        throw new Error(
            "occurrence export operations require an explicit occurrence store adapter",
        );
    }
    if (typeof readCandidate !== "function") {
        throw new Error("occurrence candidate reader is invalid");
    }
    const root = sourceRoot(candidateRoot);
    const expectedStoreIdentitySha256 = store.identity?.sha256;
    digest(expectedStoreIdentitySha256, "occurrence store identity");
    const initialized = await store.initialize({ create: true });
    if (
        initialized !== store ||
        initialized.identity?.sha256 !== expectedStoreIdentitySha256
    ) {
        throw new Error(
            "occurrence store initialization changed its adapter identity",
        );
    }
    const context = {
        store,
        root,
        readCandidate,
    };
    return Object.freeze({
        contract: "verdify.lab-occurrence-export-store-operations",
        schemaVersion: 1,
        storeIdentitySha256: store.identity.sha256,
        evidenceStore: store,
        publishCurrentMedia: (request) => publishCurrentMedia(context, request),
        readCurrentMediaEventIntent: (occurrenceId, eventId) =>
            store.readCurrentMediaEventIntent(occurrenceId, eventId),
        publishAggregateReconciliation: (command) =>
            publishAggregateReconciliation(context, command),
        readAggregateEventIntent: (eventId) =>
            store.readAggregateEventIntent(eventId),
        compareAndSwapAggregateSelection: (command) =>
            compareAndSwapAggregateSelection(store, command),
    });
}

export const occurrenceExportOperationAdapterContract = Object.freeze({
    contract: "verdify.lab-occurrence-export-store-operations",
    schemaVersion: 1,
    storeRequired: true,
    defaultStore: null,
    aggregateCas: "camera-selection-preconditions-required",
});
