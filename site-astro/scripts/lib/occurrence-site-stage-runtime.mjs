import {
    occurrenceSitePublicationProfiles,
    validateOccurrenceSitePublishEvent,
} from "./occurrence-site-publisher.mjs";
import {
    S3OccurrenceReleaseStore,
    createOccurrenceReleaseStore,
    parseOccurrenceReleaseStoreLocation,
} from "./occurrence-release-store.mjs";
import {
    createOccurrenceReleaseWriterStore,
    createSiteReleaseWriterStore,
} from "./runtime-s3-binding.mjs";
import { createSiteReleasePublicationOperation } from "./site-release-publication-operation.mjs";
import { createSiteReleaseCheckpointOperations } from "./site-release-checkpoint-operations.mjs";
import {
    S3SiteReleaseStore,
    createSiteReleaseStore,
    parseSiteReleaseStoreLocation,
} from "./site-release-store.mjs";

const CONTROL_RE = /[\u0000-\u001f\u007f]/u;
const SHA256_RE = /^[0-9a-f]{64}$/u;
const STAGE_PROFILE = occurrenceSitePublicationProfiles.stage;

const REQUEST_KEYS = [
    "contract",
    "schemaVersion",
    "siteOrigin",
    "stageGlobalNoindex",
    "publicationProfile",
    "event",
    "producerResult",
    "policy",
    "manifest",
    "manifestSha256",
    "candidateRoot",
    "workspaceRoot",
];

function exactKeys(value, keys) {
    return (
        value !== null &&
        typeof value === "object" &&
        !Array.isArray(value) &&
        Object.getPrototypeOf(value) === Object.prototype &&
        Object.keys(value).join(",") === keys.join(",")
    );
}

function sameDocument(first, second) {
    return JSON.stringify(first) === JSON.stringify(second);
}

function location(environment, name) {
    if (
        environment === null ||
        typeof environment !== "object" ||
        Array.isArray(environment) ||
        !Object.prototype.hasOwnProperty.call(environment, name)
    ) {
        throw new Error("occurrence site runtime environment is invalid");
    }
    const value = environment[name];
    if (
        typeof value !== "string" ||
        value.length === 0 ||
        value.length > 4096 ||
        CONTROL_RE.test(value)
    ) {
        throw new Error(`occurrence site runtime ${name} is invalid`);
    }
    return value;
}

function validateRequest(request) {
    if (
        !exactKeys(request, REQUEST_KEYS) ||
        request.contract !==
            "verdify.lab-stage-occurrence-site-runtime-request" ||
        request.schemaVersion !== 1 ||
        request.siteOrigin !== STAGE_PROFILE.siteOrigin ||
        request.stageGlobalNoindex !== STAGE_PROFILE.stageGlobalNoindex ||
        !sameDocument(request.publicationProfile, STAGE_PROFILE) ||
        !SHA256_RE.test(request.manifestSha256)
    ) {
        throw new Error(
            "occurrence site runtime request does not use the closed stage contract",
        );
    }
    validateOccurrenceSitePublishEvent(request.event);
    return request;
}

function dependency(value, method, label) {
    if (
        value === null ||
        typeof value !== "object" ||
        typeof value[method] !== "function"
    ) {
        throw new Error(`${label} is not configured`);
    }
    return value;
}

function sharedS3Location(storeRoot, parser, label) {
    const location = parser(storeRoot);
    if (location.kind !== "s3") {
        throw new Error(`${label} must use the shared S3 store`);
    }
    return location;
}

/**
 * Build a construction-only resolver for the file-transported stage command.
 * It reads only the two explicit store locations and delegates all credential
 * handling to the strict writer factories. No operation is invoked here.
 */
export function createOccurrenceSiteStageRuntimeFactory({
    environment,
    buildOperation,
    verificationOperation,
    checkpointOperations = null,
    clientFactory,
    createOccurrenceStore = createOccurrenceReleaseWriterStore,
    createSiteStore = createSiteReleaseWriterStore,
    createPublicationOperation = createSiteReleasePublicationOperation,
    createCheckpointOperations = createSiteReleaseCheckpointOperations,
} = {}) {
    dependency(buildOperation, "build", "stage Astro build operation");
    dependency(
        verificationOperation,
        "verify",
        "stage output verification operation",
    );
    if (checkpointOperations !== null) {
        dependency(
            checkpointOperations,
            "read",
            "occurrence site checkpoint operations",
        );
        if (typeof checkpointOperations.write !== "function") {
            throw new Error(
                "occurrence site checkpoint operations are not configured",
            );
        }
    }
    if (
        typeof createOccurrenceStore !== "function" ||
        typeof createSiteStore !== "function" ||
        typeof createPublicationOperation !== "function" ||
        typeof createCheckpointOperations !== "function"
    ) {
        throw new Error("occurrence site runtime store factories are invalid");
    }

    return async (rawRequest) => {
        const request = validateRequest(rawRequest);
        const occurrenceStoreRoot = location(
            environment,
            "LAB_OCCURRENCE_STORE",
        );
        const siteStoreRoot = location(environment, "LAB_RELEASE_STORE");
        sharedS3Location(
            occurrenceStoreRoot,
            parseOccurrenceReleaseStoreLocation,
            "occurrence site evidence store",
        );
        sharedS3Location(
            siteStoreRoot,
            parseSiteReleaseStoreLocation,
            "occurrence site release store",
        );
        const expectedOccurrenceIdentity = createOccurrenceReleaseStore(
            occurrenceStoreRoot,
        ).identity.sha256;
        const expectedSiteIdentity =
            createSiteReleaseStore(siteStoreRoot).identity.sha256;
        const storeOptions = {
            environment,
            ...(clientFactory === undefined ? {} : { clientFactory }),
        };
        const [occurrenceStore, siteStore] = await Promise.all([
            createOccurrenceStore(occurrenceStoreRoot, storeOptions),
            createSiteStore(siteStoreRoot, storeOptions),
        ]);
        if (
            !(occurrenceStore instanceof S3OccurrenceReleaseStore) ||
            occurrenceStore.accessMode !== "writer" ||
            !(siteStore instanceof S3SiteReleaseStore) ||
            siteStore.accessMode !== "writer" ||
            occurrenceStore.identity.sha256 !== expectedOccurrenceIdentity ||
            siteStore.identity.sha256 !== expectedSiteIdentity ||
            occurrenceStore?.identity?.sha256 !==
                request.event.occurrenceStoreIdentitySha256 ||
            siteStore?.identity?.sha256 !==
                request.event.siteStoreIdentitySha256 ||
            (checkpointOperations !== null &&
                checkpointOperations.storeIdentitySha256 !==
                    request.event.siteStoreIdentitySha256)
        ) {
            throw new Error(
                "occurrence site runtime stores do not match the exact event identities",
            );
        }
        const publicationOperation = createPublicationOperation({
            storeRoot: siteStoreRoot,
            store: siteStore,
        });
        const selectedCheckpointOperations =
            checkpointOperations ??
            createCheckpointOperations({ store: siteStore });
        if (
            publicationOperation?.storeIdentitySha256 !==
                request.event.siteStoreIdentitySha256 ||
            selectedCheckpointOperations?.storeIdentitySha256 !==
                request.event.siteStoreIdentitySha256 ||
            typeof selectedCheckpointOperations?.read !== "function" ||
            typeof selectedCheckpointOperations?.write !== "function"
        ) {
            throw new Error(
                "occurrence site publication does not match the exact event store",
            );
        }
        return {
            contract: "verdify.lab-stage-occurrence-site-runtime",
            schemaVersion: 1,
            siteOrigin: STAGE_PROFILE.siteOrigin,
            stageGlobalNoindex: STAGE_PROFILE.stageGlobalNoindex,
            occurrenceStore,
            buildOperation,
            verificationOperation,
            checkpointOperations: selectedCheckpointOperations,
            publicationOperation,
        };
    };
}

export const occurrenceSiteStageRuntimeContract = Object.freeze({
    contract: "verdify.lab-stage-occurrence-site-runtime",
    schemaVersion: 1,
    siteOrigin: STAGE_PROFILE.siteOrigin,
    stageGlobalNoindex: STAGE_PROFILE.stageGlobalNoindex,
    requiredStoreLocations: Object.freeze([
        "LAB_OCCURRENCE_STORE",
        "LAB_RELEASE_STORE",
    ]),
    defaults: Object.freeze({
        buildOperation: null,
        verificationOperation: null,
        checkpointOperations: null,
    }),
});
