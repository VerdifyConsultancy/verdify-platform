import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { evaluateEventFreshness } from "../scripts/lib/occurrence-release.mjs";
import { S3ObjectStore } from "../scripts/lib/s3-object-store.mjs";
import {
    LocalSiteReleaseStore,
    S3SiteReleaseStore,
    createSiteReleaseStore,
    parseSiteReleaseStoreLocation,
    siteContentIdentitySha256,
    siteReleasePayloadSha256,
} from "../scripts/lib/site-release-store.mjs";

const BUCKET = "verdify-lab-releases";
const PREFIX = "lab-stage/releases";
const LOCATION = `s3://${BUCKET}/${PREFIX}`;
const SNAPSHOT = "a".repeat(64);
const COMMIT = "b".repeat(40);

function canonicalBytes(value) {
    return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
    return createHash("sha256").update(bytes).digest("hex");
}

function precondition() {
    const error = new Error("conditional write did not match");
    error.name = "PreconditionFailed";
    error.$metadata = { httpStatusCode: 412 };
    return error;
}

function missing() {
    const error = new Error("object is absent");
    error.name = "NoSuchKey";
    error.$metadata = { httpStatusCode: 404 };
    return error;
}

class FakeS3Client {
    constructor({ pageSize = 1000 } = {}) {
        this.objects = new Map();
        this.pageSize = pageSize;
        this.sequence = 0;
        this.commands = [];
        this.beforePut = null;
    }

    identity(input) {
        return `${input.Bucket}/${input.Key}`;
    }

    seed(key, bytes, { etag = null, omitContentLength = false } = {}) {
        this.sequence += 1;
        this.objects.set(`${BUCKET}/${key}`, {
            bytes: Buffer.from(bytes),
            etag: etag ?? `"fake-${this.sequence}"`,
            omitContentLength,
        });
    }

    async send(command) {
        const name = command.constructor.name;
        const input = command.input;
        this.commands.push({ name, input: { ...input } });
        if (name === "PutObjectCommand") {
            if (this.beforePut !== null) await this.beforePut(input, this);
            const identity = this.identity(input);
            const current = this.objects.get(identity);
            if (input.IfNoneMatch === "*" && current !== undefined)
                throw precondition();
            if (input.IfMatch !== undefined && current?.etag !== input.IfMatch)
                throw precondition();
            const bytes = Buffer.from(input.Body);
            assert.equal(input.ContentLength, bytes.length);
            this.sequence += 1;
            const etag = `"fake-${this.sequence}"`;
            this.objects.set(identity, {
                bytes,
                etag,
                omitContentLength: false,
            });
            return { ETag: etag };
        }
        if (name === "GetObjectCommand") {
            const value = this.objects.get(this.identity(input));
            if (value === undefined) throw missing();
            const bytes = value.bytes;
            return {
                ETag: value.etag,
                ...(value.omitContentLength
                    ? {}
                    : { ContentLength: bytes.length }),
                Body: (async function* body() {
                    for (let offset = 0; offset < bytes.length; offset += 3)
                        yield bytes.subarray(offset, offset + 3);
                })(),
            };
        }
        if (name === "ListObjectsV2Command") {
            const keys = [...this.objects.keys()]
                .map((identity) => identity.slice(`${input.Bucket}/`.length))
                .filter((key) => key.startsWith(input.Prefix))
                .sort();
            const offset =
                input.ContinuationToken === undefined
                    ? 0
                    : Number(input.ContinuationToken);
            const limit = Math.min(this.pageSize, input.MaxKeys);
            const page = keys.slice(offset, offset + limit);
            const next = offset + page.length;
            return {
                Contents: page.map((Key) => ({ Key })),
                IsTruncated: next < keys.length,
                ...(next < keys.length
                    ? { NextContinuationToken: String(next) }
                    : {}),
            };
        }
        throw new Error(`unexpected command ${name}`);
    }
}

function selection({
    generation = 1,
    current = "1".repeat(64),
    previous = null,
} = {}) {
    return {
        contract: "verdify.lab-site-release-selection",
        schemaVersion: 1,
        generation,
        current: {
            releaseSha256: current,
            eventId: `evt_site_${String(generation).padStart(4, "0")}`,
        },
        previous,
        selectedAt: `2026-07-12T12:${String(generation).padStart(2, "0")}:00Z`,
        reason: "publish",
    };
}

async function releaseFixture() {
    const root = await mkdtemp(path.join(tmpdir(), "verdify-s3-release-"));
    const build = path.join(root, "build");
    await mkdir(build);
    const sourcePath = path.join(build, "index.html");
    const source = Buffer.from("<!doctype html><title>S3 fixture</title>\n");
    await writeFile(sourcePath, source);
    const file = {
        path: "index.html",
        sha256: sha256(source),
        bytes: source.length,
        mediaType: "text/html; charset=utf-8",
    };
    const contentIdentitySha256 = siteContentIdentitySha256({
        sourceSnapshotManifestSha256: SNAPSHOT,
        policyVersion: "verdify-site-v1",
        builderCommit: COMMIT,
        files: [file],
    });
    const event = {
        contract: "verdify.lab-release-trigger",
        schemaVersion: 1,
        eventId: "evt_site_s3_0001",
        eventType: "planner-completed",
        sourceId: "planner/public-snapshot",
        sourceWatermark: "planner-s3-1",
        occurredAt: "2026-07-12T12:00:00Z",
        payloadSha256: siteReleasePayloadSha256({
            sourceSnapshotManifestSha256: SNAPSHOT,
            policyVersion: "verdify-site-v1",
            builderCommit: COMMIT,
            contentIdentitySha256,
        }),
    };
    const releasedAt = "2026-07-12T12:01:00Z";
    return {
        root,
        source: { ...file, sourcePath },
        manifest: {
            contract: "verdify.lab-site-release",
            schemaVersion: 1,
            sourceSnapshotManifestSha256: SNAPSHOT,
            policyVersion: "verdify-site-v1",
            builderCommit: COMMIT,
            event,
            releasedAt,
            freshness: evaluateEventFreshness(event, releasedAt),
            contentIdentitySha256,
            fileCount: 1,
            totalBytes: source.length,
            files: [file],
        },
    };
}

test("store location parsing separates canonical local paths from strict S3 prefixes", () => {
    assert.deepEqual(parseSiteReleaseStoreLocation(LOCATION), {
        kind: "s3",
        bucket: BUCKET,
        prefix: PREFIX,
    });
    assert.deepEqual(parseSiteReleaseStoreLocation("./release-cache"), {
        kind: "local",
        root: path.resolve("./release-cache"),
    });
    for (const invalid of [
        "s3://verdify-lab-releases",
        "s3://Verdify-lab/releases",
        "s3://verdify-lab-releases/",
        "s3://verdify-lab-releases/lab//releases",
        "s3://verdify-lab-releases/lab/../releases",
        "s3://verdify-lab-releases/lab/releases?version=1",
        "s3://verdify-lab-releases/lab/%72eleases",
        "s3://verdify-lab-releases/lab/releases with spaces",
        "s3://verdify-lab-releases/lab/releases:mutable",
        "https://verdify.invalid/releases",
        "file:///tmp/releases",
        "//remote/releases",
        "relative\\windows",
        "relative\u0000path",
    ])
        assert.throws(
            () => parseSiteReleaseStoreLocation(invalid),
            /invalid|neither/,
        );
});

test("store factory preserves local behavior and injects S3 client construction without I/O", async () => {
    assert.ok(
        createSiteReleaseStore("./release-cache") instanceof
            LocalSiteReleaseStore,
    );
    const client = new FakeS3Client();
    assert.ok(
        createSiteReleaseStore(LOCATION, { client }) instanceof
            S3SiteReleaseStore,
    );
    let constructions = 0;
    const store = new S3SiteReleaseStore(LOCATION, {
        clientFactory: () => {
            constructions += 1;
            return client;
        },
    });
    assert.equal(constructions, 0);
    await store.initialize();
    assert.equal(constructions, 1);
    assert.equal(client.commands.length, 0);
});

test("S3 release adapter creates immutable blobs and manifests and detects differing stored bytes", async (t) => {
    const value = await releaseFixture();
    t.after(() => rm(value.root, { recursive: true, force: true }));
    const client = new FakeS3Client();
    const store = await new S3SiteReleaseStore(LOCATION, {
        client,
    }).initialize();
    await store.publishBlob(value.source);
    await store.publishBlob(value.source);
    const releaseSha256 = await store.publishRelease(value.manifest);
    assert.equal(await store.publishRelease(value.manifest), releaseSha256);
    assert.deepEqual(await store.readRelease(releaseSha256), value.manifest);
    assert.ok(
        client.commands
            .filter((command) => command.name === "PutObjectCommand")
            .every((command) => command.input.IfNoneMatch === "*"),
    );

    client.seed(
        `${PREFIX}/blobs/sha256/${value.source.sha256}`,
        Buffer.alloc(value.source.bytes, 0x78),
    );
    await assert.rejects(
        store.publishBlob(value.source),
        /content-addressed site blob collision/,
    );
});

test("S3 selection updates use entity-tag compare-and-swap and reject stale and racing writers", async () => {
    const first = selection();
    const initialClient = new FakeS3Client();
    const initialStore = await new S3SiteReleaseStore(LOCATION, {
        client: initialClient,
    }).initialize();
    initialClient.beforePut = async (input, fake) => {
        if (input.IfNoneMatch !== "*") return;
        fake.beforePut = null;
        fake.seed(input.Key, canonicalBytes(first), {
            etag: '"initial-competing-writer"',
        });
    };
    await assert.rejects(
        initialStore.writeSelection(first, null),
        /site selection precondition failed/,
    );
    assert.equal((await initialStore.readSelection()).document.generation, 1);

    const client = new FakeS3Client();
    const store = await new S3SiteReleaseStore(LOCATION, {
        client,
    }).initialize();
    const firstSha256 = await store.writeSelection(first, null);
    const selected = await store.readSelection();
    assert.equal(selected.sha256, firstSha256);
    assert.match(selected.etag, /^"fake-/u);

    const second = selection({
        generation: 2,
        current: "2".repeat(64),
        previous: first.current,
    });
    await assert.rejects(
        store.writeSelection(second, "f".repeat(64)),
        /site selection precondition failed/,
    );
    client.beforePut = async (input, fake) => {
        if (input.IfMatch === undefined) return;
        fake.beforePut = null;
        fake.seed(
            input.Key,
            fake.objects.get(`${input.Bucket}/${input.Key}`).bytes,
            { etag: '"competing-writer"' },
        );
    };
    await assert.rejects(
        store.writeSelection(second, firstSha256),
        /site selection precondition failed/,
    );
    const afterRace = await store.readSelection();
    assert.equal(afterRace.document.generation, 1);
});

test("S3 reads enforce declared and streamed byte bounds", async () => {
    const client = new FakeS3Client();
    const objects = await new S3ObjectStore({
        bucket: BUCKET,
        prefix: PREFIX,
        client,
    }).initialize();
    client.seed(`${PREFIX}/bounded/declared.bin`, Buffer.from("12345"));
    await assert.rejects(
        objects.read("bounded/declared.bin", {
            maximumBytes: 4,
            label: "declared fixture",
        }),
        /exceeds its byte limit/,
    );
    client.seed(`${PREFIX}/bounded/streamed.bin`, Buffer.from("12345"), {
        omitContentLength: true,
    });
    await assert.rejects(
        objects.read("bounded/streamed.bin", {
            maximumBytes: 4,
            label: "streamed fixture",
        }),
        /exceeds its byte limit/,
    );
});

test("S3 release listing consumes continuation pages and bounds membership", async () => {
    const client = new FakeS3Client({ pageSize: 2 });
    for (const digest of ["1".repeat(64), "2".repeat(64), "3".repeat(64)]) {
        client.seed(
            `${PREFIX}/releases/sha256/${digest}.json`,
            canonicalBytes({ digest }),
        );
    }
    const store = await new S3SiteReleaseStore(LOCATION, {
        client,
    }).initialize();
    assert.deepEqual(await store.listReleaseDigests(), [
        "1".repeat(64),
        "2".repeat(64),
        "3".repeat(64),
    ]);
    assert.equal(
        client.commands.filter(
            (command) => command.name === "ListObjectsV2Command",
        ).length,
        2,
    );
    const objects = await new S3ObjectStore({
        bucket: BUCKET,
        prefix: PREFIX,
        client,
    }).initialize();
    await assert.rejects(
        objects.list("releases/sha256/", { maximumObjects: 2 }),
        /exceeds its membership limit/,
    );
    client.seed(
        `${PREFIX}/releases/sha256/not-a-release.json`,
        Buffer.from("{}\n"),
    );
    await assert.rejects(
        store.listReleaseDigests(),
        /manifest membership is invalid/,
    );
});

test("S3 event intents are absent-only and retain canonical identity", async () => {
    const client = new FakeS3Client();
    const store = await new S3SiteReleaseStore(LOCATION, {
        client,
    }).initialize();
    const intent = {
        contract: "verdify.lab-site-release-event-intent",
        schemaVersion: 1,
        eventId: "evt_site_s3_0002",
        eventSha256: "1".repeat(64),
        payloadSha256: "2".repeat(64),
        releaseSha256: "3".repeat(64),
        expectedSelectionSha256: null,
    };
    await store.publishEventIntent(intent);
    await store.publishEventIntent(intent);
    assert.deepEqual(await store.readEventIntent(intent.eventId), intent);
    const key = `${PREFIX}/events/sha256/${sha256(Buffer.from(intent.eventId))}.json`;
    client.seed(
        key,
        canonicalBytes({ ...intent, releaseSha256: "4".repeat(64) }),
    );
    await assert.rejects(
        store.publishEventIntent(intent),
        /content-addressed site JSON collision/,
    );
});
