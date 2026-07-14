import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
    S3OccurrenceReleaseStore,
    createOccurrenceReleaseStore,
} from "../scripts/lib/occurrence-release-store.mjs";
import {
    createOccurrenceSitePublishEvent,
    occurrenceSitePublicationProfiles,
} from "../scripts/lib/occurrence-site-publisher.mjs";
import {
    createOccurrenceSiteStageRuntimeFactory,
    occurrenceSiteStageRuntimeContract,
} from "../scripts/lib/occurrence-site-stage-runtime.mjs";
import {
    createOccurrenceReleaseReaderStore,
    createSiteReleaseReaderStore,
    createSiteReleaseWriterStore,
} from "../scripts/lib/runtime-s3-binding.mjs";
import { createSiteReleasePublicationOperation } from "../scripts/lib/site-release-publication-operation.mjs";
import {
    S3SiteReleaseStore,
    createSiteReleaseStore,
} from "../scripts/lib/site-release-store.mjs";

const OCCURRENCE_STORE_ROOT =
    "s3://verdify-lab-releases/stage/occurrences";
const SITE_STORE_ROOT = "s3://verdify-lab-releases/stage/site";
const OCCURRENCE_IDENTITY = createOccurrenceReleaseStore(
    OCCURRENCE_STORE_ROOT,
).identity.sha256;
const SITE_IDENTITY = createSiteReleaseStore(SITE_STORE_ROOT).identity.sha256;
const ENVIRONMENT = {
    LAB_OCCURRENCE_STORE: OCCURRENCE_STORE_ROOT,
    LAB_RELEASE_STORE: SITE_STORE_ROOT,
    LAB_S3_ENDPOINT_URL: "https://s3-hdd.vallery.net",
    AWS_DEFAULT_REGION: "garage",
    AWS_ACCESS_KEY_ID: "writer-access",
    AWS_SECRET_ACCESS_KEY: "writer-secret",
};

function event() {
    return createOccurrenceSitePublishEvent({
        sourceId: "stage-runtime-test",
        sourceWatermark: "wm_stage_runtime_0001",
        occurredAt: "2026-07-13T12:00:00Z",
        releasedAt: "2026-07-13T12:01:00Z",
        sourceSnapshotManifestSha256: "3".repeat(64),
        sourceOccurrenceManifestSha256: "4".repeat(64),
        occurrencePolicySha256: "5".repeat(64),
        occurrenceStoreIdentitySha256: OCCURRENCE_IDENTITY,
        producerResultSha256: "6".repeat(64),
        builderCommit: "7".repeat(40),
        buildOperationSha256: "8".repeat(64),
        verificationOperationSha256: "9".repeat(64),
        siteStoreIdentitySha256: SITE_IDENTITY,
        expectedSiteSelectionSha256: null,
    });
}

function request(overrides = {}) {
    const profile = occurrenceSitePublicationProfiles.stage;
    return {
        contract: "verdify.lab-stage-occurrence-site-runtime-request",
        schemaVersion: 1,
        siteOrigin: profile.siteOrigin,
        stageGlobalNoindex: profile.stageGlobalNoindex,
        publicationProfile: structuredClone(profile),
        event: event(),
        producerResult: {},
        policy: {},
        manifest: {},
        manifestSha256: "a".repeat(64),
        candidateRoot: "/tmp/candidates",
        workspaceRoot: "/tmp/workspace",
        ...overrides,
    };
}

function dependencies(overrides = {}) {
    return {
        buildOperation: { build: async () => null },
        verificationOperation: { verify: async () => null },
        checkpointOperations: {
            storeIdentitySha256: SITE_IDENTITY,
            read: async () => null,
            write: async () => null,
        },
        ...overrides,
    };
}

test("stage runtime resolves exact writer stores without invoking an operation", async () => {
    const clients = [];
    let unrelatedReads = 0;
    const environment = {
        ...ENVIRONMENT,
        get AWS_SESSION_TOKEN() {
            unrelatedReads += 1;
            throw new Error("unrelated environment key was read");
        },
    };
    const configured = dependencies();
    const resolver = createOccurrenceSiteStageRuntimeFactory({
        environment,
        ...configured,
        clientFactory: (config) => {
            const client = {
                config,
                requests: 0,
                async send() {
                    this.requests += 1;
                    throw new Error("stage runtime construction made a request");
                },
            };
            clients.push(client);
            return client;
        },
    });
    const runtime = await resolver(request());
    assert.equal(runtime.contract, occurrenceSiteStageRuntimeContract.contract);
    assert.equal(runtime.siteOrigin, "https://lab-stage.verdify.ai");
    assert.equal(runtime.stageGlobalNoindex, true);
    assert.ok(runtime.occurrenceStore instanceof S3OccurrenceReleaseStore);
    assert.equal(runtime.occurrenceStore.accessMode, "writer");
    assert.equal(runtime.buildOperation, configured.buildOperation);
    assert.equal(runtime.verificationOperation, configured.verificationOperation);
    assert.equal(runtime.checkpointOperations, configured.checkpointOperations);
    assert.equal(
        runtime.publicationOperation.storeIdentitySha256,
        SITE_IDENTITY,
    );
    assert.equal(clients.length, 2);
    assert.deepEqual(
        clients.map(({ requests }) => requests),
        [0, 0],
    );
    assert.equal(unrelatedReads, 0);
});

test("stage runtime rejects target, local stores, and store drift before returning a runtime", async () => {
    let clientConstructions = 0;
    const options = {
        environment: ENVIRONMENT,
        ...dependencies(),
        clientFactory: () => {
            clientConstructions += 1;
            return { send: async () => null };
        },
    };
    const resolver = createOccurrenceSiteStageRuntimeFactory(options);
    await assert.rejects(
        resolver(request({ siteOrigin: "https://lab.verdify.ai" })),
        /does not use the closed stage contract/,
    );
    assert.equal(clientConstructions, 0);

    const localResolver = createOccurrenceSiteStageRuntimeFactory({
        ...options,
        environment: {
            LAB_OCCURRENCE_STORE: "/tmp/occurrences",
            LAB_RELEASE_STORE: "/tmp/site",
        },
    });
    await assert.rejects(
        localResolver(request()),
        /must use the shared S3 store/,
    );
    assert.equal(clientConstructions, 0);

    const inheritedLocations = Object.assign(
        Object.create({
            LAB_OCCURRENCE_STORE: OCCURRENCE_STORE_ROOT,
            LAB_RELEASE_STORE: SITE_STORE_ROOT,
        }),
        {
            LAB_S3_ENDPOINT_URL: ENVIRONMENT.LAB_S3_ENDPOINT_URL,
            AWS_DEFAULT_REGION: ENVIRONMENT.AWS_DEFAULT_REGION,
            AWS_ACCESS_KEY_ID: ENVIRONMENT.AWS_ACCESS_KEY_ID,
            AWS_SECRET_ACCESS_KEY: ENVIRONMENT.AWS_SECRET_ACCESS_KEY,
        },
    );
    const inheritedResolver = createOccurrenceSiteStageRuntimeFactory({
        ...options,
        environment: inheritedLocations,
    });
    await assert.rejects(
        inheritedResolver(request()),
        /environment is invalid/,
    );
    assert.equal(clientConstructions, 0);

    const wrongSite = createOccurrenceSiteStageRuntimeFactory({
        ...options,
        createSiteStore: (_storeRoot, storeOptions) =>
            createSiteReleaseWriterStore(
                "s3://verdify-lab-releases/stage/wrong-site",
                storeOptions,
            ),
    });
    await assert.rejects(
        wrongSite(request()),
        /stores do not match the exact event identities/,
    );

    const occurrenceReader = createOccurrenceSiteStageRuntimeFactory({
        ...options,
        createOccurrenceStore: createOccurrenceReleaseReaderStore,
    });
    await assert.rejects(
        occurrenceReader(request()),
        /stores do not match the exact event identities/,
    );
});

test("stage runtime has no implicit operation or environment defaults", async () => {
    assert.throws(
        () => createOccurrenceSiteStageRuntimeFactory(),
        /build operation is not configured/,
    );
    const source = await readFile(
        new URL(
            "../scripts/lib/occurrence-site-stage-runtime.mjs",
            import.meta.url,
        ),
        "utf8",
    );
    assert.doesNotMatch(
        source,
        /process\.env|@aws-sdk|fetch\s*\(|node:child_process|kubectl|argocd/iu,
    );
    assert.deepEqual(occurrenceSiteStageRuntimeContract.defaults, {
        buildOperation: null,
        verificationOperation: null,
        checkpointOperations: null,
    });
});

test("site publication adapter exposes one identity-bound writer operation", async () => {
    const calls = [];
    const localStoreRoot = "/tmp/verdify-site-publication-operation";
    const store = createSiteReleaseStore(localStoreRoot);
    store.readSelection = async () => {
        calls.push("selection");
        return null;
    };
    store.readRelease = async (value) => {
        calls.push(["release", value]);
        return null;
    };
    store.readBlob = async (value, options) => {
        calls.push(["blob", value, options]);
        return null;
    };
    store.readEventIntent = async (value) => {
        calls.push(["intent", value]);
        return null;
    };
    const operation = createSiteReleasePublicationOperation({
        storeRoot: localStoreRoot,
        store,
    });
    assert.deepEqual(Object.keys(operation), [
        "contract",
        "schemaVersion",
        "storeIdentitySha256",
        "readSelection",
        "readRelease",
        "readBlob",
        "readEventIntent",
        "publish",
    ]);
    assert.equal(operation.storeIdentitySha256, store.identity.sha256);
    await operation.readSelection();
    await operation.readRelease("c".repeat(64));
    await operation.readBlob("d".repeat(64), { maximumBytes: 1 });
    await operation.readEventIntent("evt_runtime_adapter");
    assert.deepEqual(calls, [
        "selection",
        ["release", "c".repeat(64)],
        ["blob", "d".repeat(64), { maximumBytes: 1 }],
        ["intent", "evt_runtime_adapter"],
    ]);
});

test("site publication adapter rejects a mismatched root and S3 reader", async () => {
    const clientFactory = () => ({ send: async () => null });
    const writer = await createSiteReleaseWriterStore(SITE_STORE_ROOT, {
        environment: ENVIRONMENT,
        clientFactory,
    });
    assert.throws(
        () =>
            createSiteReleasePublicationOperation({
                storeRoot: "s3://verdify-lab-releases/stage/other-site",
                store: writer,
            }),
        /publication writer is invalid/,
    );

    const reader = await createSiteReleaseReaderStore(SITE_STORE_ROOT, {
        environment: ENVIRONMENT,
        clientFactory,
    });
    assert.ok(reader instanceof S3SiteReleaseStore);
    assert.equal(reader.accessMode, "reader");
    assert.throws(
        () =>
            createSiteReleasePublicationOperation({
                storeRoot: SITE_STORE_ROOT,
                store: reader,
            }),
        /publication writer is invalid/,
    );
});
