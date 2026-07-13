import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";

import { runOccurrenceExportCli } from "../scripts/execute-occurrence-export.mjs";
import { executeOccurrenceExportBatch } from "../scripts/lib/occurrence-export-caller.mjs";
import { createOccurrenceExportStoreOperations } from "../scripts/lib/occurrence-export-operation-adapter.mjs";
import {
    draftBlockedOccurrenceExportPolicy,
    occurrenceExportPolicySha256,
    reportingFeedEnvelopeSha256,
} from "../scripts/lib/occurrence-export-contract.mjs";
import {
    LocalOccurrenceReleaseStore,
    S3OccurrenceReleaseStore,
} from "../scripts/lib/occurrence-release-store.mjs";
import {
    discoverCurrentMediaOccurrence,
    discoverGraphOccurrence,
    loadSelectedOccurrenceRelease,
    staticOccurrenceManifest,
} from "../scripts/lib/occurrence-release.mjs";

const REVIEWED_AT = "2026-07-13T11:59:00Z";
const APPROVED_AT = "2026-07-13T12:00:00Z";
const EXPORTED_AT = "2026-07-13T12:10:00Z";
const PROCESSING_AT = "2026-07-13T12:10:30Z";
const BUCKET = "verdify-lab-releases";
const LOCATION = `s3://${BUCKET}/adapter-offline`;

const CRC_TABLE = Array.from({ length: 256 }, (_, value) => {
    let crc = value;
    for (let bit = 0; bit < 8; bit += 1) {
        crc = (crc & 1) === 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
    }
    return crc >>> 0;
});

function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes)
        crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
    const typeBytes = Buffer.from(type);
    const result = Buffer.alloc(12 + data.length);
    result.writeUInt32BE(data.length, 0);
    typeBytes.copy(result, 4);
    data.copy(result, 8);
    result.writeUInt32BE(
        crc32(Buffer.concat([typeBytes, data])),
        8 + data.length,
    );
    return result;
}

function png() {
    const width = 320;
    const height = 180;
    const header = Buffer.alloc(13);
    header.writeUInt32BE(width, 0);
    header.writeUInt32BE(height, 4);
    header[8] = 8;
    header[9] = 6;
    const row = Buffer.alloc(1 + width * 4);
    for (let column = 0; column < width; column += 1) {
        Buffer.from([24, 96, 48, 255]).copy(row, 1 + column * 4);
    }
    return Buffer.concat([
        Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
        chunk("IHDR", header),
        chunk(
            "IDAT",
            deflateSync(
                Buffer.concat(Array.from({ length: height }, () => row)),
            ),
        ),
        chunk("IEND", Buffer.alloc(0)),
    ]);
}

function canonicalBytes(value) {
    return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(value) {
    return createHash("sha256").update(value).digest("hex");
}

function missing() {
    const error = new Error("object is absent");
    error.name = "NoSuchKey";
    error.$metadata = { httpStatusCode: 404 };
    return error;
}

function precondition() {
    const error = new Error("conditional write did not match");
    error.name = "PreconditionFailed";
    error.$metadata = { httpStatusCode: 412 };
    return error;
}

class FakeS3Client {
    constructor() {
        this.objects = new Map();
        this.commands = [];
        this.sequence = 0;
    }

    identity(input) {
        return `${input.Bucket}/${input.Key}`;
    }

    async send(command) {
        const name = command.constructor.name;
        const input = command.input;
        this.commands.push({ name, key: input.Key });
        if (name === "GetObjectCommand") {
            const value = this.objects.get(this.identity(input));
            if (value === undefined) throw missing();
            return {
                ETag: value.etag,
                ContentLength: value.bytes.length,
                Body: (async function* body() {
                    yield value.bytes;
                })(),
            };
        }
        if (name === "PutObjectCommand") {
            const identity = this.identity(input);
            const current = this.objects.get(identity);
            if (input.IfNoneMatch === "*" && current !== undefined)
                throw precondition();
            if (input.IfMatch !== undefined && current?.etag !== input.IfMatch)
                throw precondition();
            this.sequence += 1;
            const value = {
                bytes: Buffer.from(input.Body),
                etag: `"fake-${this.sequence}"`,
            };
            this.objects.set(identity, value);
            return { ETag: value.etag };
        }
        throw new Error(`unexpected command ${name}`);
    }
}

function graphResultFor(batch) {
    return {
        contract: "verdify.lab-graph-export-result",
        schemaVersion: 3,
        policyVersion: batch.policyVersion,
        policySha256: batch.policySha256,
        sourceOccurrenceManifestSha256: batch.sourceOccurrenceManifestSha256,
        reportingFeedSha256: reportingFeedEnvelopeSha256(batch.reportingFeed),
        rendererContract: {
            contract: "verdify.lab-graph-renderer-runtime-status",
            schemaVersion: 1,
            status: "satisfied",
            failure: null,
        },
        graphs: structuredClone(batch.graphs),
    };
}

async function fixture(context) {
    const root = await mkdtemp(
        path.join(tmpdir(), "verdify-operation-adapter-"),
    );
    const sourceRoot = path.join(root, "source");
    const storeRoot = path.join(root, "store");
    const documentsRoot = path.join(root, "documents");
    await Promise.all([
        mkdir(sourceRoot),
        mkdir(storeRoot),
        mkdir(documentsRoot),
    ]);
    context.after(() => rm(root, { recursive: true, force: true }));

    const graphs = Array.from({ length: 143 }, (_, index) =>
        discoverGraphOccurrence({
            route: `/evidence/adapter-graph-${String(index).padStart(3, "0")}`,
            ordinal: index,
            liveUrl: `https://graphs.verdify.ai/d-solo/public-reporting/adapter?orgId=1&panelId=${index + 1}&from=now-24h&to=now`,
            title: `Adapter graph ${index + 1}`,
        }),
    );
    const cameraUrls = [
        "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
        "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080",
    ];
    const currentMedia = cameraUrls.map((sourceUrl, index) =>
        discoverCurrentMediaOccurrence({
            route: `/evidence/adapter-camera-${index + 1}`,
            ordinal: index,
            sourceUrl,
            semanticRole: `Adapter camera ${index + 1}`,
        }),
    );
    const sourceSnapshotManifestSha256 = sha256(
        Buffer.from("adapter-snapshot"),
    );
    const manifest = staticOccurrenceManifest({
        snapshotId: `sanitized-content-sha256:${sourceSnapshotManifestSha256}`,
        discoveredGraphs: graphs,
        discoveredCurrentMedia: currentMedia,
    });
    const manifestSha256 = sha256(canonicalBytes(manifest));
    const blockedPolicy = draftBlockedOccurrenceExportPolicy({
        manifest,
        manifestSha256,
        policyVersion: "operation-adapter-offline-v1",
        approvedAt: REVIEWED_AT,
        cameraSources: currentMedia.map((occurrence, index) => ({
            occurrenceId: occurrence.occurrenceId,
            url: cameraUrls[index],
        })),
    });
    const policy = structuredClone(blockedPolicy);
    policy.activation = {
        ...policy.activation,
        state: "approved",
        approvedBy: "jason",
        approvedAt: APPROVED_AT,
    };
    const policySha256 = occurrenceExportPolicySha256(policy);
    const image = png();
    const imageSha256 = sha256(image);

    async function candidate(
        kind,
        occurrenceId,
        requestProvenanceSha256 = null,
    ) {
        const relativePath = `${kind}/${occurrenceId}/${imageSha256}.png`;
        await mkdir(path.join(sourceRoot, kind, occurrenceId), {
            recursive: true,
        });
        await writeFile(
            path.join(sourceRoot, ...relativePath.split("/")),
            image,
        );
        return {
            relativePath,
            mediaType: "image/png",
            capturedAt: "2026-07-13T12:05:00Z",
            ...(requestProvenanceSha256 === null
                ? {}
                : { requestProvenanceSha256 }),
        };
    }

    const graphRecords = [];
    for (const occurrence of graphs) {
        graphRecords.push({
            occurrenceId: occurrence.occurrenceId,
            probeStatus: "success",
            candidate: await candidate("graphs", occurrence.occurrenceId),
        });
    }
    const approvedMedia = new Map(
        policy.currentMedia.map((record) => [record.occurrenceId, record]),
    );
    const mediaRecords = [];
    for (const occurrence of currentMedia) {
        const requestProvenanceSha256 = approvedMedia.get(
            occurrence.occurrenceId,
        ).requestProvenanceSha256;
        mediaRecords.push({
            occurrenceId: occurrence.occurrenceId,
            captureStatus: "success",
            requestProvenanceSha256,
            candidate: await candidate(
                "current-media",
                occurrence.occurrenceId,
                requestProvenanceSha256,
            ),
            expectedSelectionSha256: null,
        });
    }
    const batch = {
        contract: "verdify.lab-occurrence-export-batch",
        schemaVersion: 2,
        batchId: "batch_operation_adapter_offline_0001",
        policyVersion: policy.policyVersion,
        policySha256,
        sourceOccurrenceManifestSha256: manifestSha256,
        reportingFeed: {
            contract: "verdify.operator-public-reporting-feed",
            schemaVersion: 1,
            sourceId: "operator-public-reporting-feed-adapter-offline",
            sourceClass: "public-reporting-projection",
            credentialClass: "reporting-read-only",
            direction: "one-way-read-only",
            sourceWatermark: "wm_operation_adapter_offline_0001",
            sourceWatermarkAt: APPROVED_AT,
        },
        exportedAt: EXPORTED_AT,
        expectedSelectionSha256: null,
        graphs: graphRecords,
        currentMedia: mediaRecords,
    };
    const graphResult = graphResultFor(batch);
    const documentPaths = {
        manifest: path.join(documentsRoot, "manifest.json"),
        policy: path.join(documentsRoot, "policy.json"),
        blockedPolicy: path.join(documentsRoot, "blocked-policy.json"),
        batch: path.join(documentsRoot, "batch.json"),
        graphResult: path.join(documentsRoot, "graph-result.json"),
    };
    await Promise.all([
        writeFile(documentPaths.manifest, canonicalBytes(manifest)),
        writeFile(documentPaths.policy, canonicalBytes(policy)),
        writeFile(documentPaths.blockedPolicy, canonicalBytes(blockedPolicy)),
        writeFile(documentPaths.batch, canonicalBytes(batch)),
        writeFile(documentPaths.graphResult, canonicalBytes(graphResult)),
    ]);
    return {
        sourceRoot,
        storeRoot,
        manifest,
        manifestSha256,
        policy,
        blockedPolicy,
        batch,
        graphResult,
        currentMedia,
        documentPaths,
    };
}

function callerInput(value, operations) {
    return {
        policy: value.policy,
        manifest: value.manifest,
        manifestSha256: value.manifestSha256,
        batch: value.batch,
        graphResult: value.graphResult,
        sourceRoot: value.sourceRoot,
        processingAt: PROCESSING_AT,
        operations,
    };
}

test("concrete local operations select the exact 143+2 aggregate", async (context) => {
    const value = await fixture(context);
    const store = new LocalOccurrenceReleaseStore(value.storeRoot);
    const operations = await createOccurrenceExportStoreOperations({
        store,
        sourceRoot: value.sourceRoot,
    });
    const result = await executeOccurrenceExportBatch(
        callerInput(value, operations),
    );
    assert.equal(result.status, "selected");
    assert.equal(result.media.length, 2);
    const selected = await loadSelectedOccurrenceRelease(store);
    assert.equal(selected.current.occurrences.graphs.length, 143);
    assert.equal(selected.current.occurrences.currentMedia.length, 2);
    assert.equal(selected.selectionSha256, result.aggregate.selectionSha256);
});

test("the concrete adapter drives injected S3 conditional APIs without network", async (context) => {
    const value = await fixture(context);
    const client = new FakeS3Client();
    const store = new S3OccurrenceReleaseStore(LOCATION, { client });
    const operations = await createOccurrenceExportStoreOperations({
        store,
        sourceRoot: value.sourceRoot,
    });
    assert.equal(
        client.commands.length,
        0,
        "initialization does not make a request",
    );
    const result = await executeOccurrenceExportBatch(
        callerInput(value, operations),
    );
    assert.equal(result.status, "selected");
    assert.ok(client.commands.some(({ name }) => name === "PutObjectCommand"));
    assert.ok(
        client.commands.every(({ key }) =>
            key.startsWith("adapter-offline/occurrence-releases/v1/"),
        ),
    );
    const selected = await loadSelectedOccurrenceRelease(store);
    assert.equal(selected.current.occurrences.graphs.length, 143);
    assert.equal(selected.current.occurrences.currentMedia.length, 2);
});

test("a committed aggregate response failure is resolved by the exact post-read", async (context) => {
    const value = await fixture(context);
    const store = new LocalOccurrenceReleaseStore(value.storeRoot);
    const operations = await createOccurrenceExportStoreOperations({
        store,
        sourceRoot: value.sourceRoot,
    });
    const writeAggregateSelection = store.writeAggregateSelection.bind(store);
    store.writeAggregateSelection = async (...args) => {
        await writeAggregateSelection(...args);
        throw new Error("injected response loss after aggregate commit");
    };
    const result = await executeOccurrenceExportBatch(
        callerInput(value, operations),
    );
    assert.equal(result.status, "selected");
    assert.equal(
        (await store.readAggregateSelection()).sha256,
        result.aggregate.selectionSha256,
    );
});

test("committed camera response failures are resolved by exact selector post-reads", async (context) => {
    const value = await fixture(context);
    const store = new LocalOccurrenceReleaseStore(value.storeRoot);
    const operations = await createOccurrenceExportStoreOperations({
        store,
        sourceRoot: value.sourceRoot,
    });
    const writeCurrentMediaSelection =
        store.writeCurrentMediaSelection.bind(store);
    store.writeCurrentMediaSelection = async (...args) => {
        await writeCurrentMediaSelection(...args);
        throw new Error("injected response loss after camera commit");
    };
    const result = await executeOccurrenceExportBatch(
        callerInput(value, operations),
    );
    assert.equal(result.status, "selected");
    assert.ok(result.media.every(({ status }) => status === "selected"));
});

test("camera preconditions stop aggregate CAS before changing its selector", async (context) => {
    const value = await fixture(context);
    const store = new LocalOccurrenceReleaseStore(value.storeRoot);
    const operations = await createOccurrenceExportStoreOperations({
        store,
        sourceRoot: value.sourceRoot,
    });
    const result = await executeOccurrenceExportBatch(
        callerInput(value, operations),
    );
    assert.equal(result.status, "selected");
    const before = await store.readAggregateSelection();
    const secondCamera = await store.readCurrentMediaSelection(
        value.currentMedia[1].occurrenceId,
    );
    await assert.rejects(
        operations.compareAndSwapAggregateSelection({
            selection: before.document,
            expectedSelectionSha256: before.sha256,
            cameraSelectionPreconditions: [
                {
                    occurrenceId: value.currentMedia[0].occurrenceId,
                    selectionSha256: "f".repeat(64),
                },
                {
                    occurrenceId: value.currentMedia[1].occurrenceId,
                    selectionSha256: secondCamera.sha256,
                },
            ],
        }),
        /camera selection precondition failed/,
    );
    assert.equal((await store.readAggregateSelection()).sha256, before.sha256);
    await assert.rejects(
        operations.compareAndSwapAggregateSelection({
            selection: before.document,
            expectedSelectionSha256: before.sha256,
            cameraSelectionPreconditions: [],
        }),
        /aggregate selection command is invalid/,
    );
});

test("CLI requires an explicit command and approved policy before store construction", async (context) => {
    const value = await fixture(context);
    let createCalls = 0;
    const createStore = () => {
        createCalls += 1;
        throw new Error("store construction must remain unreachable");
    };
    await assert.rejects(runOccurrenceExportCli([], { createStore }), /Usage:/);
    assert.equal(createCalls, 0);
    await assert.rejects(
        runOccurrenceExportCli(
            [
                "execute",
                "--manifest",
                value.documentPaths.manifest,
                "--policy",
                value.documentPaths.blockedPolicy,
                "--batch",
                value.documentPaths.batch,
                "--graph-result",
                value.documentPaths.graphResult,
                "--source",
                value.sourceRoot,
                "--store",
                value.storeRoot,
            ],
            { createStore },
        ),
        /disabled by the supplied policy/,
    );
    assert.equal(createCalls, 0);
});

test("CLI consumes canonical local documents and one explicit local store", async (context) => {
    const value = await fixture(context);
    let requestedLocation = null;
    const result = await runOccurrenceExportCli(
        [
            "execute",
            "--manifest",
            value.documentPaths.manifest,
            "--policy",
            value.documentPaths.policy,
            "--batch",
            value.documentPaths.batch,
            "--graph-result",
            value.documentPaths.graphResult,
            "--source",
            value.sourceRoot,
            "--store",
            value.storeRoot,
        ],
        {
            createStore: (location) => {
                requestedLocation = location;
                return new LocalOccurrenceReleaseStore(location);
            },
            now: () => PROCESSING_AT,
        },
    );
    assert.equal(requestedLocation, value.storeRoot);
    assert.equal(result.status, "selected");
    assert.doesNotMatch(
        JSON.stringify(result),
        /s3:|sourceRoot|relativePath|credential|endpoint/i,
    );
});
