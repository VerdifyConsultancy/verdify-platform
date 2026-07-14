import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
    LocalOccurrenceReleaseStore,
    S3OccurrenceReleaseStore,
} from "../scripts/lib/occurrence-release-store.mjs";
import {
    RUNTIME_S3_ENDPOINT_URL,
    RUNTIME_S3_REGION,
    createOccurrenceReleaseReaderStore,
    createOccurrenceReleaseWriterStore,
    createSiteReleaseReaderStore,
    createSiteReleaseWriterStore,
    siteReleaseCliEnvironment,
} from "../scripts/lib/runtime-s3-binding.mjs";
import { S3ObjectStore } from "../scripts/lib/s3-object-store.mjs";
import {
    LocalSiteReleaseStore,
    S3SiteReleaseStore,
} from "../scripts/lib/site-release-store.mjs";

const STORE_ROOT = "s3://verdify-lab-releases/lab-stage/releases";
const ENVIRONMENT = Object.freeze({
    LAB_S3_ENDPOINT_URL: RUNTIME_S3_ENDPOINT_URL,
    AWS_DEFAULT_REGION: RUNTIME_S3_REGION,
    AWS_ACCESS_KEY_ID: "test-access-key",
    AWS_SECRET_ACCESS_KEY: "test-secret-access-key",
});

class NoRequestClient {
    constructor() {
        this.commands = [];
    }

    async send(command) {
        this.commands.push(command);
        return { ETag: '"written"' };
    }
}

test("runtime S3 factories bind fixed explicit configuration and distinct access modes", async () => {
    const cases = [
        [createSiteReleaseReaderStore, S3SiteReleaseStore, "reader"],
        [createSiteReleaseWriterStore, S3SiteReleaseStore, "writer"],
        [
            createOccurrenceReleaseReaderStore,
            S3OccurrenceReleaseStore,
            "reader",
        ],
        [
            createOccurrenceReleaseWriterStore,
            S3OccurrenceReleaseStore,
            "writer",
        ],
    ];
    for (const [factory, StoreClass, accessMode] of cases) {
        let constructions = 0;
        let capturedConfig = null;
        const client = new NoRequestClient();
        const storePromise = factory(STORE_ROOT, {
            environment: ENVIRONMENT,
            clientFactory(config) {
                constructions += 1;
                capturedConfig = config;
                return client;
            },
        });
        const store = await storePromise;
        assert.ok(store instanceof StoreClass);
        assert.equal(store.accessMode, accessMode);
        assert.equal(store.objects.accessMode, accessMode);
        assert.equal(constructions, 1);
        assert.deepEqual(capturedConfig, {
            endpoint: RUNTIME_S3_ENDPOINT_URL,
            region: RUNTIME_S3_REGION,
            forcePathStyle: true,
            credentials: {
                accessKeyId: ENVIRONMENT.AWS_ACCESS_KEY_ID,
                secretAccessKey: ENVIRONMENT.AWS_SECRET_ACCESS_KEY,
            },
        });
        assert.equal(client.commands.length, 0, "initialize performs no request");
    }
});

test("local factories and CLI forwarding do not inspect an environment", async (t) => {
    const root = await mkdtemp(path.join(tmpdir(), "verdify-runtime-s3-local-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    const unreadableEnvironment = new Proxy(
        {},
        {
            get() {
                throw new Error("environment value was read");
            },
            getOwnPropertyDescriptor() {
                throw new Error("environment metadata was read");
            },
            has() {
                throw new Error("environment membership was read");
            },
            ownKeys() {
                throw new Error("environment names were read");
            },
        },
    );
    const options = { environment: unreadableEnvironment, create: true };
    const siteWriter = await createSiteReleaseWriterStore(root, options);
    const occurrenceWriter = await createOccurrenceReleaseWriterStore(
        root,
        options,
    );
    const siteReader = await createSiteReleaseReaderStore(root, {
        environment: unreadableEnvironment,
    });
    const occurrenceReader = await createOccurrenceReleaseReaderStore(root, {
        environment: unreadableEnvironment,
    });
    assert.ok(siteWriter instanceof LocalSiteReleaseStore);
    assert.ok(siteReader instanceof LocalSiteReleaseStore);
    assert.ok(occurrenceWriter instanceof LocalOccurrenceReleaseStore);
    assert.ok(occurrenceReader instanceof LocalOccurrenceReleaseStore);
    const forwarded = siteReleaseCliEnvironment(root, {
        environment: unreadableEnvironment,
    });
    assert.deepEqual(forwarded, {});
    assert.equal(Object.isFrozen(forwarded), true);
});

test("S3 CLI forwarding is exact and frozen", () => {
    const forwarded = siteReleaseCliEnvironment(STORE_ROOT, {
        environment: ENVIRONMENT,
    });
    assert.deepEqual(forwarded, ENVIRONMENT);
    assert.deepEqual(Object.keys(forwarded), [
        "LAB_S3_ENDPOINT_URL",
        "AWS_DEFAULT_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ]);
    assert.equal(Object.isFrozen(forwarded), true);
});

test("S3 bindings require fixed metadata and bounded explicit credentials without reflecting values", async () => {
    const marker = "do-not-reflect-this-value";
    const invalidEnvironments = [
        {},
        { ...ENVIRONMENT, LAB_S3_ENDPOINT_URL: `https://${marker}.invalid` },
        { ...ENVIRONMENT, AWS_DEFAULT_REGION: marker },
        { ...ENVIRONMENT, AWS_ACCESS_KEY_ID: `${marker}\u0000` },
        {
            ...ENVIRONMENT,
            AWS_SECRET_ACCESS_KEY: marker.repeat(100),
        },
        Object.create(ENVIRONMENT),
        Object.defineProperty({ ...ENVIRONMENT }, "AWS_ACCESS_KEY_ID", {
            enumerable: true,
            get() {
                throw new Error(marker);
            },
        }),
    ];
    for (const environment of invalidEnvironments) {
        await assert.rejects(
            createSiteReleaseReaderStore(STORE_ROOT, { environment }),
            (error) => {
                assert.doesNotMatch(error.message, new RegExp(marker, "u"));
                assert.match(error.message, /runtime S3/u);
                return true;
            },
        );
    }
    await assert.rejects(
        createOccurrenceReleaseWriterStore(STORE_ROOT),
        /runtime S3 environment is required/u,
    );
});

test("reader object stores reject writes before issuing a request while writer remains the compatibility default", async () => {
    const client = new NoRequestClient();
    const reader = await new S3ObjectStore({
        bucket: "verdify-lab-releases",
        prefix: "lab-stage/releases",
        accessMode: "reader",
        client,
    }).initialize();
    await assert.rejects(
        reader.putIfAbsent("selection.json", Buffer.from("value")),
        /not configured for writes/u,
    );
    await assert.rejects(
        reader.putIfMatch(
            "selection.json",
            Buffer.from("value"),
            '"prior"',
        ),
        /not configured for writes/u,
    );
    assert.equal(client.commands.length, 0);

    const writer = await new S3ObjectStore({
        bucket: "verdify-lab-releases",
        prefix: "lab-stage/releases",
        client,
    }).initialize();
    assert.equal(writer.accessMode, "writer");
    assert.deepEqual(
        await writer.putIfAbsent("selection.json", Buffer.from("value")),
        { written: true, etag: '"written"' },
    );
    assert.equal(client.commands.length, 1);
    assert.throws(
        () =>
            new S3ObjectStore({
                bucket: "verdify-lab-releases",
                prefix: "lab-stage/releases",
                accessMode: "publisher",
                client,
            }),
        /access mode is invalid/u,
    );
});
