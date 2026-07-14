import { createHash } from "node:crypto";
import { lstat, realpath } from "node:fs/promises";
import path from "node:path";

import {
    occurrenceExportPolicySha256,
    validateOccurrenceExportPolicy,
    validatePolicyManifestBinding,
} from "./occurrence-export-contract.mjs";
import { validateOccurrenceProducerResult } from "./occurrence-producer-result-contract.mjs";
import {
    occurrenceSiteOperationSha256,
    occurrenceSitePublicationProfiles,
    processOccurrenceSitePublishEvent,
    validateOccurrenceSitePublishEvent,
} from "./occurrence-site-publisher.mjs";

const SHA256_RE = /^[0-9a-f]{64}$/u;
const STAGE_ORIGIN = "https://lab-stage.verdify.ai";
const STAGE_PROFILE = occurrenceSitePublicationProfiles.stage;

const RUNTIME_KEYS = [
    "contract",
    "schemaVersion",
    "siteOrigin",
    "stageGlobalNoindex",
    "occurrenceStore",
    "buildOperation",
    "verificationOperation",
    "checkpointOperations",
    "publicationOperation",
];

const PUBLICATION_RESULT_KEYS = [
    "contract",
    "schemaVersion",
    "status",
    "eventId",
    "eventSha256",
    "producerResultSha256",
    "occurrenceCallResultSha256",
    "occurrenceSelectionSha256",
    "occurrenceManifestSha256",
    "occurrencePolicySha256",
    "occurrenceStoreIdentitySha256",
    "buildOperationSha256",
    "verificationOperationSha256",
    "buildContentIdentitySha256",
    "siteStoreIdentitySha256",
    "siteEventSha256",
    "releaseSha256",
    "siteSelectionSha256",
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

function canonicalValue(value, label) {
    if (
        !exactKeys(value, ["document", "bytes", "sha256"]) ||
        value.document === null ||
        typeof value.document !== "object" ||
        Array.isArray(value.document) ||
        !Buffer.isBuffer(value.bytes) ||
        !SHA256_RE.test(value.sha256)
    ) {
        throw new Error(`${label} is not a canonical document value`);
    }
    const bytes = canonicalBytes(value.document);
    if (!bytes.equals(value.bytes) || sha256(bytes) !== value.sha256) {
        throw new Error(`${label} canonical identity mismatch`);
    }
    return value;
}

async function canonicalRoot(value, label) {
    if (
        typeof value !== "string" ||
        value.length === 0 ||
        value.length > 4096 ||
        /[\u0000-\u001f\u007f]/u.test(value) ||
        !path.isAbsolute(value) ||
        path.normalize(value) !== value
    ) {
        throw new Error(`${label} must be one canonical absolute path`);
    }
    const metadata = await lstat(value, { bigint: true });
    if (
        !metadata.isDirectory() ||
        metadata.isSymbolicLink() ||
        (await realpath(value)) !== value
    ) {
        throw new Error(`${label} must be one canonical directory`);
    }
    return value;
}

function rootsOverlap(first, second) {
    const relative = path.relative(first, second);
    return (
        relative === "" ||
        (relative !== ".." &&
            !relative.startsWith(`..${path.sep}`) &&
            !path.isAbsolute(relative))
    );
}

function operationProfileMatches(operation, kind, eventSha256) {
    const functionName = kind === "build" ? "build" : "verify";
    const contract =
        kind === "build"
            ? "verdify.lab-selected-astro-build-operation"
            : "verdify.lab-site-output-verification-operation";
    const keys = [
        "contract",
        "schemaVersion",
        "operationSha256",
        "publicationProfile",
        functionName,
    ];
    return (
        exactKeys(operation, keys) &&
        operation.contract === contract &&
        operation.schemaVersion === 2 &&
        typeof operation[functionName] === "function" &&
        canonicalBytes(operation.publicationProfile).equals(
            canonicalBytes(STAGE_PROFILE),
        ) &&
        operation.operationSha256 ===
            occurrenceSiteOperationSha256(kind, STAGE_PROFILE) &&
        operation.operationSha256 === eventSha256
    );
}

function validateApprovedPolicy(policy) {
    validateOccurrenceExportPolicy(policy);
    if (
        policy.activation.state !== "approved" ||
        policy.activation.approvedBy !== "jason" ||
        !policy.activation.approvedAt
    ) {
        throw new Error(
            "occurrence site publication is disabled by the supplied policy",
        );
    }
    return policy;
}

function validateRuntime(runtime, event) {
    if (
        !exactKeys(runtime, RUNTIME_KEYS) ||
        runtime.contract !== "verdify.lab-stage-occurrence-site-runtime" ||
        runtime.schemaVersion !== 1 ||
        runtime.siteOrigin !== STAGE_ORIGIN ||
        runtime.stageGlobalNoindex !== true ||
        runtime.occurrenceStore?.identity?.sha256 !==
            event.occurrenceStoreIdentitySha256 ||
        !operationProfileMatches(
            runtime.buildOperation,
            "build",
            event.buildOperationSha256,
        ) ||
        !operationProfileMatches(
            runtime.verificationOperation,
            "verification",
            event.verificationOperationSha256,
        )
    ) {
        throw new Error(
            "occurrence site runtime does not preserve the closed noindex Lab stage contract",
        );
    }
    return runtime;
}

function validatePublicationResult(result, event) {
    const eventSha256 = sha256(canonicalBytes(event));
    if (
        !exactKeys(result, PUBLICATION_RESULT_KEYS) ||
        result.contract !== "verdify.lab-occurrence-site-publish-result" ||
        result.schemaVersion !== 1 ||
        !["published", "idempotent"].includes(result.status) ||
        result.eventId !== event.eventId ||
        result.eventSha256 !== eventSha256 ||
        result.producerResultSha256 !== event.producerResultSha256 ||
        result.occurrencePolicySha256 !== event.occurrencePolicySha256 ||
        result.occurrenceStoreIdentitySha256 !==
            event.occurrenceStoreIdentitySha256 ||
        result.buildOperationSha256 !== event.buildOperationSha256 ||
        result.verificationOperationSha256 !==
            event.verificationOperationSha256 ||
        result.siteStoreIdentitySha256 !== event.siteStoreIdentitySha256 ||
        PUBLICATION_RESULT_KEYS.slice(4).some(
            (key) => !SHA256_RE.test(result[key]),
        )
    ) {
        throw new Error(
            "occurrence site processor did not return the exact event-bound result",
        );
    }
    return result;
}

/**
 * Consume one canonical, file-transported occurrence publication delivery.
 * The caller must inject every I/O-capable runtime dependency. Policy approval
 * and event identity are checked before the construction-only runtime resolver
 * is requested. That resolver must perform no external I/O; its returned
 * stage-profiled operations are validated before any operation is invoked.
 */
export async function runOccurrenceSitePublisherDelivery(
    {
        event: rawEvent,
        producerResult: rawProducerResult,
        policy: rawPolicy,
        manifest: rawManifest,
        candidateRoot: rawCandidateRoot,
        workspaceRoot: rawWorkspaceRoot,
    },
    {
        createRuntime = null,
        processEvent = processOccurrenceSitePublishEvent,
    } = {},
) {
    const eventValue = canonicalValue(rawEvent, "occurrence site event");
    const producerResultValue = canonicalValue(
        rawProducerResult,
        "occurrence producer result",
    );
    const policyValue = canonicalValue(rawPolicy, "occurrence export policy");
    const manifestValue = canonicalValue(
        rawManifest,
        "static occurrence manifest",
    );
    const event = structuredClone(eventValue.document);
    const producerResult = structuredClone(producerResultValue.document);
    const policy = structuredClone(policyValue.document);
    const manifest = structuredClone(manifestValue.document);
    const candidateRoot = await canonicalRoot(
        rawCandidateRoot,
        "occurrence candidate root",
    );
    const workspaceRoot = await canonicalRoot(
        rawWorkspaceRoot,
        "occurrence publisher workspace root",
    );
    if (
        rootsOverlap(candidateRoot, workspaceRoot) ||
        rootsOverlap(workspaceRoot, candidateRoot)
    ) {
        throw new Error(
            "occurrence candidate and publisher workspace roots must be disjoint",
        );
    }

    validateOccurrenceSitePublishEvent(event);
    validateApprovedPolicy(policy);
    const policySha256 = occurrenceExportPolicySha256(policy);
    if (
        policyValue.sha256 !== policySha256 ||
        event.occurrencePolicySha256 !== policySha256 ||
        event.sourceOccurrenceManifestSha256 !== manifestValue.sha256 ||
        event.sourceSnapshotManifestSha256 !==
            policy.sourceSnapshotManifestSha256 ||
        producerResultValue.sha256 !== event.producerResultSha256
    ) {
        throw new Error(
            "occurrence site event does not bind the exact canonical input documents",
        );
    }
    validatePolicyManifestBinding(policy, manifest, manifestValue.sha256);
    validateOccurrenceProducerResult(producerResult, {
        policySha256,
        sourceOccurrenceManifestSha256: manifestValue.sha256,
        sourceId: event.sourceId,
        sourceWatermark: event.sourceWatermark,
        sourceWatermarkAt: event.occurredAt,
    });

    if (typeof createRuntime !== "function") {
        throw new Error(
            "occurrence site publisher runtime is not configured; no default live action is available",
        );
    }
    if (typeof processEvent !== "function") {
        throw new Error("occurrence site event processor is invalid");
    }
    const runtime = validateRuntime(
        await createRuntime({
            contract: "verdify.lab-stage-occurrence-site-runtime-request",
            schemaVersion: 1,
            siteOrigin: STAGE_ORIGIN,
            stageGlobalNoindex: true,
            publicationProfile: structuredClone(STAGE_PROFILE),
            event: structuredClone(event),
            producerResult: structuredClone(producerResult),
            policy: structuredClone(policy),
            manifest: structuredClone(manifest),
            manifestSha256: manifestValue.sha256,
            candidateRoot,
            workspaceRoot,
        }),
        event,
    );
    const publication = validatePublicationResult(
        await processEvent({
            event,
            producerResult,
            policy,
            manifest,
            manifestSha256: manifestValue.sha256,
            occurrenceStore: runtime.occurrenceStore,
            candidateRoot,
            workspaceRoot,
            buildOperation: runtime.buildOperation,
            verificationOperation: runtime.verificationOperation,
            checkpointOperations: runtime.checkpointOperations,
            publicationOperation: runtime.publicationOperation,
        }),
        event,
    );
    return {
        contract: "verdify.lab-stage-occurrence-site-execution-result",
        schemaVersion: 1,
        siteOrigin: STAGE_ORIGIN,
        stageGlobalNoindex: true,
        publicationProfile: STAGE_PROFILE,
        publication,
    };
}

export const occurrenceSitePublisherRunnerContract = Object.freeze({
    runtime: Object.freeze({
        contract: "verdify.lab-stage-occurrence-site-runtime",
        schemaVersion: 1,
        siteOrigin: STAGE_ORIGIN,
        stageGlobalNoindex: true,
    }),
    result: Object.freeze({
        contract: "verdify.lab-stage-occurrence-site-execution-result",
        schemaVersion: 1,
        publicationProfile: STAGE_PROFILE,
    }),
    defaults: Object.freeze({
        createRuntime: null,
    }),
});
