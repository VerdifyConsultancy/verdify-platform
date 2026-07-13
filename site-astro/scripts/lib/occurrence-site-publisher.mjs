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
    reportingFeedEnvelopeSha256,
} from "./occurrence-export-contract.mjs";
import { loadSelectedOccurrenceRelease } from "./occurrence-release.mjs";
import {
    inventoryBuiltSite,
    siteContentIdentitySha256,
    siteReleasePayloadSha256,
    validateSiteReleaseManifest,
} from "./site-release-store.mjs";
import { verifyCompleteSelectedOccurrenceEvidence } from "../compile-snapshot.mjs";
import { verifySelectedEvidence } from "../verify-production-output.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const EVENT_ID_RE = /^evt_occurrence_site_[0-9a-f]{32}$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u;
const COMMIT_RE = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u;
const PROVENANCE_PATH = "occurrence-publish-provenance.json";

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

const PRODUCER_RESULT_KEYS = [
    "contract",
    "schemaVersion",
    "policySha256",
    "sourceOccurrenceManifestSha256",
    "reportingFeedSha256",
    "selectorPreconditionsSha256",
    "datasourceBindingProof",
    "executionBounds",
    "cameraAttempts",
    "graphResult",
    "graphResultSha256",
    "exportBatch",
    "exportBatchSha256",
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

function validateProducerResult(result, event, policySha256, manifestSha256) {
    const batch = result?.exportBatch;
    const selectorPreconditions = {
        contract: "verdify.lab-occurrence-export-selector-preconditions",
        schemaVersion: 1,
        aggregateExpectedSelectionSha256: batch?.expectedSelectionSha256,
        currentMedia: (batch?.currentMedia ?? []).map(
            ({ occurrenceId, expectedSelectionSha256 }) => ({
                occurrenceId,
                expectedSelectionSha256,
            }),
        ),
    };
    if (
        !exactKeys(result, PRODUCER_RESULT_KEYS) ||
        result.contract !== "verdify.lab-occurrence-producer-run" ||
        result.schemaVersion !== 1 ||
        result.policySha256 !== policySha256 ||
        result.sourceOccurrenceManifestSha256 !== manifestSha256 ||
        sha256(canonicalBytes(result.graphResult)) !==
            result.graphResultSha256 ||
        sha256(canonicalBytes(result.exportBatch)) !==
            result.exportBatchSha256 ||
        reportingFeedEnvelopeSha256(batch?.reportingFeed) !==
            result.reportingFeedSha256 ||
        sha256(canonicalBytes(selectorPreconditions)) !==
            result.selectorPreconditionsSha256 ||
        sha256(canonicalBytes(result)) !== event.producerResultSha256 ||
        batch?.reportingFeed?.sourceId !== event.sourceId ||
        batch?.reportingFeed?.sourceWatermark !== event.sourceWatermark ||
        batch?.reportingFeed?.sourceWatermarkAt !== event.occurredAt ||
        batch?.policySha256 !== policySha256 ||
        batch?.sourceOccurrenceManifestSha256 !== manifestSha256 ||
        batch?.graphs?.length !== 143 ||
        batch?.currentMedia?.length !== 2 ||
        !exactKeys(result.datasourceBindingProof, [
            "contract",
            "schemaVersion",
            "graphCount",
            "legacyOverrideCount",
            "reportingDefaultCount",
            "legacyByDashboard",
            "planSha256",
        ]) ||
        result.datasourceBindingProof.contract !==
            "verdify.lab-graph-datasource-binding-proof" ||
        result.datasourceBindingProof.schemaVersion !== 1 ||
        result.datasourceBindingProof.graphCount !== 143 ||
        result.datasourceBindingProof.legacyOverrideCount !== 40 ||
        result.datasourceBindingProof.reportingDefaultCount !== 103 ||
        !Array.isArray(result.datasourceBindingProof.legacyByDashboard) ||
        !SHA256_RE.test(result.datasourceBindingProof.planSha256) ||
        !exactKeys(result.executionBounds, [
            "graphConcurrency",
            "graphTimeoutMs",
            "graphSettlementGraceMs",
            "graphMaxAttempts",
            "cameraConcurrency",
            "cameraTimeoutMs",
            "cameraMaxAttempts",
        ]) ||
        result.executionBounds.graphMaxAttempts !== 1 ||
        !Array.isArray(result.cameraAttempts) ||
        result.cameraAttempts.length !== 2 ||
        result.cameraAttempts.some(
            (attempt, index) =>
                !exactKeys(attempt, [
                    "occurrenceId",
                    "attempts",
                    "captureStatus",
                ]) ||
                attempt.occurrenceId !==
                    batch.currentMedia[index].occurrenceId ||
                !Number.isSafeInteger(attempt.attempts) ||
                attempt.attempts < 1 ||
                attempt.attempts > result.executionBounds.cameraMaxAttempts ||
                attempt.captureStatus !==
                    batch.currentMedia[index].captureStatus,
        )
    )
        throw new Error(
            "occurrence producer result does not match the exact publish event",
        );
    return result;
}

function validateBuildOperation(operation, event) {
    if (
        !exactKeys(operation, [
            "contract",
            "schemaVersion",
            "operationSha256",
            "build",
        ]) ||
        operation.contract !== "verdify.lab-selected-astro-build-operation" ||
        operation.schemaVersion !== 1 ||
        operation.operationSha256 !== event.buildOperationSha256 ||
        !SHA256_RE.test(operation.operationSha256) ||
        typeof operation.build !== "function"
    )
        throw new Error(
            "Astro build operation does not use the bound v1 contract",
        );
    return operation;
}

function validateVerificationOperation(operation, event) {
    if (
        !exactKeys(operation, [
            "contract",
            "schemaVersion",
            "operationSha256",
            "verify",
        ]) ||
        operation.contract !==
            "verdify.lab-production-output-verification-operation" ||
        operation.schemaVersion !== 1 ||
        operation.operationSha256 !== event.verificationOperationSha256 ||
        !SHA256_RE.test(operation.operationSha256) ||
        typeof operation.verify !== "function"
    )
        throw new Error(
            "production output verifier does not use the bound v1 contract",
        );
    return operation;
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

function checkpointDocument(event, occurrenceCallResult, selected) {
    return {
        contract: "verdify.lab-occurrence-site-publish-checkpoint",
        schemaVersion: 1,
        eventId: event.eventId,
        eventSha256: sha256(canonicalBytes(event)),
        producerResultSha256: event.producerResultSha256,
        occurrenceCallResultSha256: sha256(
            canonicalBytes(occurrenceCallResult),
        ),
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
    verifyCompleteSelectedOccurrenceEvidence(
        selected,
        occurrenceManifest,
        policy,
        event.occurrencePolicySha256,
    );
    verifySelectedEvidence(build, occurrenceManifest);
    return {
        buildSha256: buildValue.sha256,
        occurrenceOutputManifestSha256: occurrenceValue.sha256,
        provenanceSha256: provenanceValue.sha256,
    };
}

function validateVerificationResult(value, expected) {
    if (
        !exactKeys(value, [
            "contract",
            "schemaVersion",
            "buildContentIdentitySha256",
            "occurrenceSelectionSha256",
            "occurrenceManifestSha256",
        ]) ||
        value.contract !==
            "verdify.lab-production-output-verification-result" ||
        value.schemaVersion !== 1 ||
        value.buildContentIdentitySha256 !==
            expected.buildContentIdentitySha256 ||
        value.occurrenceSelectionSha256 !==
            expected.occurrenceSelectionSha256 ||
        value.occurrenceManifestSha256 !== expected.occurrenceManifestSha256
    )
        throw new Error(
            "production output verifier did not attest the exact selected build",
        );
    return value;
}

function siteEvent(event, contentIdentitySha256) {
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
            policyVersion: "occurrence-selected-production-v1",
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
    if (
        typeof value !== "object" ||
        value.document === null ||
        typeof value.document !== "object" ||
        !SHA256_RE.test(value.sha256) ||
        value.document.current === null ||
        !SHA256_RE.test(value.document.current.releaseSha256)
    )
        throw new Error("site publication selection read is invalid");
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
    if (sha256(releaseBytes) !== selection.document.current.releaseSha256) {
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

async function publishedEventState(publication, event, checkpoint) {
    const intent = await publication.readEventIntent(event.eventId);
    if (intent === null) return null;
    if (
        intent.eventId !== event.eventId ||
        intent.storeIdentitySha256 !== event.siteStoreIdentitySha256 ||
        !SHA256_RE.test(intent.releaseSha256)
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
        manifest.builderCommit !== event.builderCommit
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
    if (
        selection === null ||
        selection.document.current.releaseSha256 !== intent.releaseSha256 ||
        selection.document.current.eventId !== event.eventId
    )
        throw new Error(
            "published event is no longer the selected site release",
        );
    return { intent, manifest, selection, provenance };
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
 * a single-use Astro workspace, production verification, and full-site release.
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
    );
    const buildOperation = validateBuildOperation(rawBuildOperation, event);
    const verificationOperation = validateVerificationOperation(
        rawVerificationOperation,
        event,
    );
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
    if (
        checkpointValue === null &&
        initialSiteState?.manifest.event.eventId === event.eventId
    ) {
        throw new Error(
            "selected site event has no exact occurrence publish checkpoint",
        );
    }

    const occurrenceOperations = await createOccurrenceExportStoreOperations({
        store: occurrenceStore,
        sourceRoot: candidateRoot,
    });
    let checkpoint;
    if (checkpointValue === null) {
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
        const selected = await loadSelectedOccurrenceRelease(
            occurrenceOperations.evidenceStore,
        );
        if (
            selected.selectionSha256 !==
                occurrenceCallResult.aggregate.selectionSha256 ||
            selected.selection.current.manifestSha256 !==
                occurrenceCallResult.aggregate.manifestSha256
        )
            throw new Error(
                "occurrence caller result does not match the selected store post-read",
            );
        checkpoint = await commitCheckpoint(
            checkpointOperations,
            checkpointDocument(event, occurrenceCallResult, selected),
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
    );
    if (alreadyPublished !== null) {
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
        contract: "verdify.lab-selected-astro-build-request",
        schemaVersion: 1,
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
    const buildEvidence = await verifySelectedBuild({
        buildRoot,
        event,
        checkpoint,
        policy,
        selected,
    });
    const beforeVerification = await inventoryBuiltSite(buildRoot);
    const files = publicInventory(beforeVerification);
    const contentIdentitySha256 = siteContentIdentitySha256({
        sourceSnapshotManifestSha256: event.sourceSnapshotManifestSha256,
        policyVersion: "occurrence-selected-production-v1",
        builderCommit: event.builderCommit,
        files,
    });
    validateVerificationResult(
        await verificationOperation.verify({
            contract: "verdify.lab-production-output-verification-request",
            schemaVersion: 1,
            event: structuredClone(event),
            buildRoot,
            buildContentIdentitySha256: contentIdentitySha256,
            occurrenceSelectionSha256: checkpoint.occurrenceSelectionSha256,
            occurrenceManifestSha256: checkpoint.occurrenceManifestSha256,
            buildEvidence,
        }),
        {
            buildContentIdentitySha256: contentIdentitySha256,
            occurrenceSelectionSha256: checkpoint.occurrenceSelectionSha256,
            occurrenceManifestSha256: checkpoint.occurrenceManifestSha256,
        },
    );
    const afterVerification = await inventoryBuiltSite(buildRoot);
    if (
        inventoryIdentity(beforeVerification) !==
        inventoryIdentity(afterVerification)
    ) {
        throw new Error("Astro build changed during production verification");
    }
    await validateSelectedOccurrence(occurrenceOperations, checkpoint, policy);

    const releaseEvent = siteEvent(event, contentIdentitySha256);
    let publicationError = null;
    try {
        await publication.publish({
            buildRoot,
            event: releaseEvent,
            sourceSnapshotManifestSha256: event.sourceSnapshotManifestSha256,
            policyVersion: "occurrence-selected-production-v1",
            builderCommit: event.builderCommit,
            releasedAt: event.releasedAt,
            expectedSelectionSha256: event.expectedSiteSelectionSha256,
        });
    } catch (error) {
        publicationError = error;
    }
    let published;
    try {
        published = await publishedEventState(publication, event, checkpoint);
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
