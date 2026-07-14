import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
    cp,
    mkdir,
    mkdtemp,
    readFile,
    readdir,
    readlink,
    rename,
    rm,
    symlink,
    writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { deflateSync } from "node:zlib";

import {
    createOccurrenceSitePublishEvent,
    occurrenceSiteOperationSha256,
    occurrenceSitePublicationProfiles,
    occurrenceSitePublisherContract,
    processOccurrenceSitePublishEvent,
} from "../scripts/lib/occurrence-site-publisher.mjs";
import {
    occurrenceSitePublisherRunnerContract,
    runOccurrenceSitePublisherDelivery,
} from "../scripts/lib/occurrence-site-publisher-runner.mjs";
import { runOccurrenceSitePublishCli } from "../scripts/execute-occurrence-site-publish.mjs";
import {
    draftBlockedOccurrenceExportPolicy,
    occurrenceExportPolicySha256,
    reportingFeedEnvelopeSha256,
} from "../scripts/lib/occurrence-export-contract.mjs";
import { S3OccurrenceReleaseStore } from "../scripts/lib/occurrence-release-store.mjs";
import {
    discoverCurrentMediaOccurrence,
    discoverGraphOccurrence,
    loadSelectedOccurrenceRelease,
    materializeOccurrenceBlobs,
    staticOccurrenceManifest,
} from "../scripts/lib/occurrence-release.mjs";
import { hydrateSiteCache } from "../scripts/lib/site-release-cache.mjs";
import {
    S3SiteReleaseStore,
    inventoryBuiltSite,
    publishSiteRelease,
    siteContentIdentitySha256,
    siteReleasePayloadSha256,
} from "../scripts/lib/site-release-store.mjs";
import { verifySelectedEvidence } from "../scripts/verify-production-output.mjs";

const BUCKET = "verdify-lab-releases";
const OCCURRENCE_LOCATION = `s3://${BUCKET}/publisher-offline/occurrences`;
const SITE_LOCATION = `s3://${BUCKET}/publisher-offline/site`;
const REVIEWED_AT = "2026-07-13T11:59:00Z";
const APPROVED_AT = "2026-07-13T12:00:00Z";
const EXPORTED_AT = "2026-07-13T12:10:00Z";
const RELEASED_AT = "2026-07-13T12:10:30Z";
const BUILDER_COMMIT = "b".repeat(40);
const BUILD_OPERATION_SHA256 = "c".repeat(64);
const VERIFICATION_OPERATION_SHA256 = "d".repeat(64);
const SITE_ROOT = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "..",
);
const LEGACY_DATASOURCE_PROOF = Object.freeze([
    Object.freeze({ uid: "greenhouse-equipment", count: 5 }),
    Object.freeze({ uid: "greenhouse-hydroponics", count: 5 }),
    Object.freeze({ uid: "greenhouse-lighting", count: 13 }),
    Object.freeze({ uid: "greenhouse-soil", count: 10 }),
    Object.freeze({ uid: "greenhouse-weather", count: 7 }),
]);

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

function pngChunk(type, data) {
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

function fixturePng(rgba = [24, 96, 48, 255]) {
    const width = 320;
    const height = 180;
    const header = Buffer.alloc(13);
    header.writeUInt32BE(width, 0);
    header.writeUInt32BE(height, 4);
    header[8] = 8;
    header[9] = 6;
    const row = Buffer.alloc(1 + width * 4);
    for (let column = 0; column < width; column += 1) {
        Buffer.from(rgba).copy(row, 1 + column * 4);
    }
    return Buffer.concat([
        Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
        pngChunk("IHDR", header),
        pngChunk(
            "IDAT",
            deflateSync(
                Buffer.concat(Array.from({ length: height }, () => row)),
            ),
        ),
        pngChunk("IEND", Buffer.alloc(0)),
    ]);
}

function canonicalBytes(value) {
    return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(value) {
    return createHash("sha256").update(value).digest("hex");
}

function canonicalDocument(document) {
    const bytes = canonicalBytes(document);
    return {
        document: structuredClone(document),
        bytes,
        sha256: sha256(bytes),
    };
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
        this.sequence = 0;
        this.commands = [];
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
        if (name === "ListObjectsV2Command") {
            const keys = [...this.objects.keys()]
                .map((identity) => identity.slice(`${input.Bucket}/`.length))
                .filter((key) => key.startsWith(input.Prefix))
                .sort();
            return {
                Contents: keys.map((Key) => ({ Key })),
                IsTruncated: false,
            };
        }
        throw new Error(`unexpected fake S3 command ${name}`);
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

async function compilerSnapshot(root, graphs, cameraUrls) {
    const snapshotRoot = path.join(root, "snapshot");
    const contentRoot = path.join(snapshotRoot, "content");
    await Promise.all([
        mkdir(contentRoot, { recursive: true }),
        mkdir(path.join(snapshotRoot, "manifests"), { recursive: true }),
        mkdir(path.join(snapshotRoot, "evidence"), { recursive: true }),
        mkdir(path.join(contentRoot, "static", "video"), {
            recursive: true,
        }),
        mkdir(path.join(contentRoot, "static", "integration"), {
            recursive: true,
        }),
    ]);
    const markdown = [
        "---",
        "title: Publisher integration proof",
        "description: Complete selected occurrence publication proof.",
        "---",
        "",
        "# Publisher integration proof",
        "",
        ...graphs.map(
            (occurrence) =>
                `<iframe src="${occurrence.liveUrl.replaceAll("&", "&amp;")}" title="${occurrence.semanticRole}"></iframe>`,
        ),
        ...cameraUrls.map(
            (sourceUrl, index) =>
                `<img src="${sourceUrl.replaceAll("&", "&amp;")}" alt="Publisher camera ${index + 1}">`,
        ),
        "",
    ].join("\n");
    const files = { "index.md": sha256(Buffer.from(markdown)) };
    const writes = [
        [path.join(contentRoot, "index.md"), Buffer.from(markdown)],
    ];
    for (let index = 0; index < 179; index += 1) {
        const relative = `static/video/publisher-${String(index).padStart(3, "0")}.ts`;
        const bytes = Buffer.from(`video-${index}\n`);
        files[relative] = sha256(bytes);
        writes.push([path.join(contentRoot, relative), bytes]);
    }
    for (let index = 0; index < 249; index += 1) {
        const relative = `static/integration/publisher-${String(index).padStart(3, "0")}.txt`;
        const bytes = Buffer.from(`filler-${index}\n`);
        files[relative] = sha256(bytes);
        writes.push([path.join(contentRoot, relative), bytes]);
    }
    await Promise.all(writes.map(([file, bytes]) => writeFile(file, bytes)));
    const manifestBytes = canonicalBytes({ files, version: 1 });
    const manifestSha256 = sha256(manifestBytes);
    await writeFile(
        path.join(snapshotRoot, "manifests", "content.json"),
        manifestBytes,
    );
    const guardBytes = canonicalBytes({
        findings: [],
        missing_roots: [],
        roots: [
            {
                identity: sha256(Buffer.from("publisher-content")),
                label: "content",
            },
        ],
        routes: [],
        schema_version: 2,
    });
    await Promise.all([
        writeFile(
            path.join(snapshotRoot, "evidence", "public-output-guard.json"),
            guardBytes,
        ),
        writeFile(
            path.join(snapshotRoot, "attestation.json"),
            canonicalBytes({
                contract: "verdify.lab-stage-sanitized-snapshot",
                schemaVersion: 1,
                evidenceStatus: "provisional-only",
                approvalEligible: false,
                sourceManifestSha256:
                    "05d4373ebf59bef3a7899c5e94514971d663fd7264db09b2b5cb26fec78410b1",
                sanitizedManifestSha256: manifestSha256,
                sourceFileCount: 429,
                sanitizedFileCount: 429,
                policyVersion: "verdify-public-output-stage-v1",
                guardReportSha256: sha256(guardBytes),
                guardSchemaVersion: 2,
                guardFindings: 0,
                transformations: {
                    changedFiles: 8,
                    textRedactionFiles: 3,
                    invalidValueRepairFiles: 3,
                    pngReencodeFiles: 3,
                    hlsFilesPreserved: 179,
                },
            }),
        ),
    ]);
    return {
        snapshotRoot,
        sourceSnapshotManifestSha256: manifestSha256,
    };
}

async function fixture(context, { realCompiler = false } = {}) {
    const root = await mkdtemp(
        path.join(tmpdir(), "verdify-occurrence-site-publisher-"),
    );
    context.after(() => rm(root, { recursive: true, force: true }));
    const candidateRoot = path.join(root, "candidates");
    await mkdir(candidateRoot);
    const dashboardUids = LEGACY_DATASOURCE_PROOF.flatMap(({ uid, count }) =>
        Array.from({ length: count }, () => uid),
    );
    dashboardUids.push(
        ...Array.from({ length: 103 }, () => "site-public-reporting"),
    );
    const graphs = dashboardUids.map((uid, index) =>
        discoverGraphOccurrence({
            route: "/",
            ordinal: index,
            liveUrl: `https://graphs.verdify.ai/d-solo/${uid}/site?orgId=1&panelId=${index + 1}&from=now-24h&to=now`,
            title: `Publisher graph ${index + 1}`,
        }),
    );
    const cameraUrls = [
        "https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080",
        "https://api.verdify.ai/api/v1/public/cameras/greenhouse_2/latest.jpg?h=1080",
    ];
    const currentMedia = cameraUrls.map((sourceUrl, index) =>
        discoverCurrentMediaOccurrence({
            route: "/",
            ordinal: index,
            sourceUrl,
            semanticRole: `Publisher camera ${index + 1}`,
        }),
    );
    const snapshot = realCompiler
        ? await compilerSnapshot(root, graphs, cameraUrls)
        : {
              snapshotRoot: null,
              sourceSnapshotManifestSha256: sha256(
                  Buffer.from("publisher-snapshot"),
              ),
          };
    const { snapshotRoot, sourceSnapshotManifestSha256 } = snapshot;
    const manifest = staticOccurrenceManifest({
        snapshotId: `sanitized-content-sha256:${sourceSnapshotManifestSha256}`,
        discoveredGraphs: graphs,
        discoveredCurrentMedia: currentMedia,
    });
    const manifestSha256 = sha256(canonicalBytes(manifest));
    const blocked = draftBlockedOccurrenceExportPolicy({
        manifest,
        manifestSha256,
        policyVersion: "occurrence-site-publisher-offline-v1",
        approvedAt: REVIEWED_AT,
        cameraSources: currentMedia.map((occurrence, index) => ({
            occurrenceId: occurrence.occurrenceId,
            url: cameraUrls[index],
        })),
    });
    const policy = structuredClone(blocked);
    policy.activation = {
        ...policy.activation,
        state: "approved",
        approvedBy: "jason",
        approvedAt: APPROVED_AT,
    };
    const policySha256 = occurrenceExportPolicySha256(policy);
    const image = fixturePng();
    const imageSha256 = sha256(image);
    async function candidate(
        kind,
        occurrenceId,
        requestProvenanceSha256 = null,
    ) {
        const directory = path.join(candidateRoot, kind, occurrenceId);
        await mkdir(directory, { recursive: true });
        await writeFile(path.join(directory, `${imageSha256}.png`), image);
        return {
            relativePath: `${kind}/${occurrenceId}/${imageSha256}.png`,
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
    const policyMedia = new Map(
        policy.currentMedia.map((entry) => [entry.occurrenceId, entry]),
    );
    const mediaRecords = [];
    for (const occurrence of currentMedia) {
        const requestProvenanceSha256 = policyMedia.get(
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
    const reportingFeed = {
        contract: "verdify.operator-public-reporting-feed",
        schemaVersion: 1,
        sourceId: "operator-public-reporting-feed-publisher-offline",
        sourceClass: "public-reporting-projection",
        credentialClass: "reporting-read-only",
        direction: "one-way-read-only",
        sourceWatermark: "wm_occurrence_site_publisher_0001",
        sourceWatermarkAt: APPROVED_AT,
    };
    const batch = {
        contract: "verdify.lab-occurrence-export-batch",
        schemaVersion: 2,
        batchId: "batch_occurrence_site_publisher_0001",
        policyVersion: policy.policyVersion,
        policySha256,
        sourceOccurrenceManifestSha256: manifestSha256,
        reportingFeed,
        exportedAt: EXPORTED_AT,
        expectedSelectionSha256: null,
        graphs: graphRecords,
        currentMedia: mediaRecords,
    };
    const graphResult = graphResultFor(batch);
    const selectorPreconditions = {
        contract: "verdify.lab-occurrence-export-selector-preconditions",
        schemaVersion: 1,
        aggregateExpectedSelectionSha256: null,
        currentMedia: currentMedia.map(({ occurrenceId }) => ({
            occurrenceId,
            expectedSelectionSha256: null,
        })),
    };
    const producerResult = {
        contract: "verdify.lab-occurrence-producer-run",
        schemaVersion: 1,
        policySha256,
        sourceOccurrenceManifestSha256: manifestSha256,
        reportingFeedSha256: reportingFeedEnvelopeSha256(reportingFeed),
        selectorPreconditionsSha256: sha256(
            canonicalBytes(selectorPreconditions),
        ),
        datasourceBindingProof: {
            contract: "verdify.lab-graph-datasource-binding-proof",
            schemaVersion: 1,
            graphCount: 143,
            legacyOverrideCount: 40,
            reportingDefaultCount: 103,
            legacyByDashboard: structuredClone(LEGACY_DATASOURCE_PROOF),
            planSha256: "e".repeat(64),
        },
        executionBounds: {
            graphConcurrency: 2,
            graphTimeoutMs: 1_000,
            graphSettlementGraceMs: 50,
            graphMaxAttempts: 1,
            cameraConcurrency: 2,
            cameraTimeoutMs: 1_000,
            cameraMaxAttempts: 2,
        },
        cameraAttempts: currentMedia.map(({ occurrenceId }) => ({
            occurrenceId,
            attempts: 1,
            captureStatus: "success",
        })),
        graphResult,
        graphResultSha256: sha256(canonicalBytes(graphResult)),
        exportBatch: batch,
        exportBatchSha256: sha256(canonicalBytes(batch)),
    };
    const client = new FakeS3Client();
    const occurrenceStore = new S3OccurrenceReleaseStore(OCCURRENCE_LOCATION, {
        client,
    });
    const siteStore = await new S3SiteReleaseStore(SITE_LOCATION, {
        client,
    }).initialize();
    return {
        root,
        candidateRoot,
        snapshotRoot,
        manifest,
        manifestSha256,
        policy,
        policySha256,
        producerResult,
        occurrenceStore,
        siteStore,
        client,
        sourceSnapshotManifestSha256,
        graphs,
        currentMedia,
    };
}

function checkpointOperations(
    siteStoreIdentitySha256,
    { failWriteAndPostRead = null } = {},
) {
    const records = new Map();
    const value = (document) => {
        const bytes = canonicalBytes(document);
        return {
            document: structuredClone(document),
            bytes,
            sha256: sha256(bytes),
        };
    };
    return {
        contract: "verdify.lab-occurrence-site-checkpoint-operations",
        schemaVersion: 1,
        storeIdentitySha256: siteStoreIdentitySha256,
        read: async (eventId) => {
            if (failWriteAndPostRead?.postReadUnavailable) {
                failWriteAndPostRead.postReadUnavailable = false;
                throw new Error(
                    "simulated checkpoint post-read unavailability",
                );
            }
            return records.has(eventId) ? value(records.get(eventId)) : null;
        },
        write: async (document) => {
            if (failWriteAndPostRead?.armed) {
                failWriteAndPostRead.armed = false;
                failWriteAndPostRead.postReadUnavailable = true;
                throw new Error("simulated checkpoint write outage");
            }
            const existing = records.get(document.eventId);
            if (
                existing &&
                !canonicalBytes(existing).equals(canonicalBytes(document))
            ) {
                throw new Error("checkpoint event ID conflict");
            }
            records.set(document.eventId, structuredClone(document));
            return value(document);
        },
    };
}

function publicationOperation(
    siteStore,
    { failBeforePublish = null, failAfterIntent = null } = {},
) {
    return {
        contract: "verdify.lab-site-release-publication-operation",
        schemaVersion: 1,
        storeIdentitySha256: siteStore.identity.sha256,
        readSelection: () => siteStore.readSelection(),
        readRelease: (releaseSha256) => siteStore.readRelease(releaseSha256),
        readBlob: (blobSha256, options) =>
            siteStore.readBlob(blobSha256, options),
        readEventIntent: (eventId) => siteStore.readEventIntent(eventId),
        publish: async (request) => {
            if (failBeforePublish?.armed) {
                failBeforePublish.armed = false;
                throw new Error("simulated downstream site-store outage");
            }
            try {
                return await publishSiteRelease({
                    ...request,
                    storeRoot: SITE_LOCATION,
                    store: siteStore,
                    testHooks: failAfterIntent?.armed
                        ? {
                              afterIntent: async () => {
                                  throw new Error(
                                      "simulated failure after site intent",
                                  );
                              },
                          }
                        : null,
                });
            } finally {
                if (failAfterIntent?.armed) failAfterIntent.armed = false;
            }
        },
    };
}

function buildOperation(
    publicationProfile = null,
    { targetOverride = null } = {},
) {
    const profiled = publicationProfile !== null;
    return {
        contract: "verdify.lab-selected-astro-build-operation",
        schemaVersion: profiled ? 2 : 1,
        operationSha256: profiled
            ? occurrenceSiteOperationSha256("build", publicationProfile)
            : BUILD_OPERATION_SHA256,
        ...(profiled
            ? { publicationProfile: structuredClone(publicationProfile) }
            : {}),
        build: async (request) => {
            const buildRoot = path.join(
                request.workspaceRoot,
                "selected-build",
            );
            await mkdir(buildRoot);
            const selected = await loadSelectedOccurrenceRelease(
                request.occurrenceStore,
            );
            const occurrenceManifest = staticOccurrenceManifest({
                snapshotId: request.manifest.snapshotId,
                selectedManifestSha256:
                    selected.selection.current.manifestSha256,
                discoveredGraphs: request.manifest.graphs,
                discoveredCurrentMedia: request.manifest.currentMedia,
                selectedManifest: selected.current,
            });
            const materializedOccurrenceBlobCount =
                await materializeOccurrenceBlobs(
                    request.occurrenceStore,
                    selected.current,
                    buildRoot,
                );
            await Promise.all([
                writeFile(
                    path.join(buildRoot, "index.html"),
                    profiled
                        ? '<!doctype html><meta name="robots" content="noindex,follow"><title>Selected Astro stage proof</title>\n'
                        : "<!doctype html><title>Selected Astro proof</title>\n",
                ),
                writeFile(
                    path.join(buildRoot, "occurrence-manifest.json"),
                    canonicalBytes(occurrenceManifest),
                ),
                writeFile(
                    path.join(buildRoot, "static-build.json"),
                    canonicalBytes({
                        ...(profiled
                            ? {
                                  contract: "verdify.lab-astro-stage-build",
                                  schemaVersion: 1,
                                  siteOrigin:
                                      targetOverride?.siteOrigin ??
                                      publicationProfile.siteOrigin,
                                  stageGlobalNoindex:
                                      targetOverride?.stageGlobalNoindex ??
                                      publicationProfile.stageGlobalNoindex,
                              }
                            : {}),
                        snapshotManifestDigest: `sha256:${request.event.sourceSnapshotManifestSha256}`,
                        selectedOccurrenceManifestSha256: `sha256:${selected.selection.current.manifestSha256}`,
                        grafanaOccurrenceCount:
                            occurrenceManifest.graphs.length,
                        currentMediaOccurrenceCount:
                            occurrenceManifest.currentMedia.length,
                        materializedOccurrenceBlobCount,
                    }),
                ),
            ]);
            return {
                contract: "verdify.lab-selected-astro-build-result",
                schemaVersion: 1,
                buildRoot,
            };
        },
    };
}

function runWorkspace(projectRoot, script, args, environment) {
    const result = spawnSync(process.execPath, [script, ...args], {
        cwd: projectRoot,
        env: environment,
        encoding: "utf8",
        timeout: 120_000,
    });
    if (result.error) throw result.error;
    if (result.status !== 0) {
        throw new Error(
            `${path.relative(projectRoot, script)} failed: ${result.stderr || result.stdout}`,
        );
    }
}

function realCompilerBuildOperation(value, calls) {
    return {
        contract: "verdify.lab-selected-astro-build-operation",
        schemaVersion: 1,
        operationSha256: BUILD_OPERATION_SHA256,
        build: async (request) => {
            calls.builds += 1;
            const projectRoot = path.join(request.workspaceRoot, "site-astro");
            await mkdir(projectRoot);
            await Promise.all(
                ["config", "nginx", "scripts", "src", "vendor"].map(
                    (relative) =>
                        cp(
                            path.join(SITE_ROOT, relative),
                            path.join(projectRoot, relative),
                            { recursive: true },
                        ),
                ),
            );
            await Promise.all(
                [
                    "astro.config.mjs",
                    "package.json",
                    "postcss.config.mjs",
                    "tsconfig.json",
                ].map((relative) =>
                    cp(
                        path.join(SITE_ROOT, relative),
                        path.join(projectRoot, relative),
                    ),
                ),
            );
            await Promise.all([
                cp(value.snapshotRoot, path.join(projectRoot, ".snapshot"), {
                    recursive: true,
                }),
                symlink(
                    path.join(SITE_ROOT, "node_modules"),
                    path.join(projectRoot, "node_modules"),
                    "dir",
                ),
                writeFile(
                    path.join(projectRoot, "occurrence-policy.json"),
                    canonicalBytes(request.policy),
                ),
            ]);
            // Offline production-verifier proof only. This is deliberately
            // not the stage build operation and supplies no runtime CLI/S3
            // activation path; stage origin + global noindex remain separate.
            const environment = {
                ...process.env,
                LAB_SNAPSHOT: path.join(projectRoot, ".snapshot"),
                ALLOW_SYNTHETIC_FIXTURE: "false",
                SITE_ORIGIN: "https://lab.verdify.ai",
                STAGE_GLOBAL_NOINDEX: "false",
                LAB_OCCURRENCE_STORE: OCCURRENCE_LOCATION,
                LAB_OCCURRENCE_POLICY: path.join(
                    projectRoot,
                    "occurrence-policy.json",
                ),
            };
            runWorkspace(
                projectRoot,
                path.join(projectRoot, "scripts", "prepare-site-shell.mjs"),
                [],
                environment,
            );

            const names = [
                "LAB_SNAPSHOT",
                "ALLOW_SYNTHETIC_FIXTURE",
                "SITE_ORIGIN",
                "STAGE_GLOBAL_NOINDEX",
                "LAB_OCCURRENCE_STORE",
                "LAB_OCCURRENCE_POLICY",
            ];
            const previous = new Map(
                names.map((name) => [name, process.env[name]]),
            );
            Object.assign(process.env, environment);
            try {
                const compiler = await import(
                    pathToFileURL(
                        path.join(
                            projectRoot,
                            "scripts",
                            "compile-snapshot.mjs",
                        ),
                    )
                );
                const stores = await import(
                    pathToFileURL(
                        path.join(
                            projectRoot,
                            "scripts",
                            "lib",
                            "occurrence-release-store.mjs",
                        ),
                    )
                );
                await compiler.main({
                    occurrenceStoreFactory: (location) =>
                        new stores.S3OccurrenceReleaseStore(location, {
                            client: value.client,
                        }),
                });
                // Production snapshots are intentionally provisional today,
                // so the production verifier cannot accept them as a release.
                // Keep this offline adapter visibly fixture-only while still
                // exercising the real selected compiler and every verifier
                // check that is available to fixtures.
                const generatedBuildPath = path.join(
                    projectRoot,
                    ".generated",
                    "build.json",
                );
                const generatedPublicBuildPath = path.join(
                    projectRoot,
                    ".generated",
                    "public",
                    "static-build.json",
                );
                const compiledBuild = JSON.parse(
                    await readFile(generatedBuildPath, "utf8"),
                );
                compiledBuild.sanitization.fixtureOnly = true;
                const fixtureBuildBytes = canonicalBytes(compiledBuild);
                await Promise.all([
                    writeFile(generatedBuildPath, fixtureBuildBytes),
                    writeFile(generatedPublicBuildPath, fixtureBuildBytes),
                ]);
            } finally {
                for (const [name, prior] of previous) {
                    if (prior === undefined) delete process.env[name];
                    else process.env[name] = prior;
                }
            }

            runWorkspace(
                projectRoot,
                path.join(
                    projectRoot,
                    "node_modules",
                    "astro",
                    "bin",
                    "astro.mjs",
                ),
                ["build"],
                environment,
            );
            runWorkspace(
                projectRoot,
                path.join(projectRoot, "scripts", "finalize-output.mjs"),
                [],
                environment,
            );
            runWorkspace(
                projectRoot,
                path.join(
                    projectRoot,
                    "node_modules",
                    "pagefind",
                    "lib",
                    "runner",
                    "bin.cjs",
                ),
                ["--site", "dist"],
                environment,
            );
            runWorkspace(
                projectRoot,
                path.join(projectRoot, "scripts", "prune-pagefind-output.mjs"),
                [],
                environment,
            );
            return {
                contract: "verdify.lab-selected-astro-build-result",
                schemaVersion: 1,
                buildRoot: path.join(projectRoot, "dist"),
            };
        },
    };
}

function verificationOperation(
    publicationProfile = null,
    { resultOverride = null } = {},
) {
    const profiled = publicationProfile !== null;
    return {
        contract: profiled
            ? "verdify.lab-site-output-verification-operation"
            : "verdify.lab-production-output-verification-operation",
        schemaVersion: profiled ? 2 : 1,
        operationSha256: profiled
            ? occurrenceSiteOperationSha256("verification", publicationProfile)
            : VERIFICATION_OPERATION_SHA256,
        ...(profiled
            ? { publicationProfile: structuredClone(publicationProfile) }
            : {}),
        verify: async (request) => {
            const build = JSON.parse(
                await readFile(
                    path.join(request.buildRoot, "static-build.json"),
                    "utf8",
                ),
            );
            const occurrenceManifest = JSON.parse(
                await readFile(
                    path.join(request.buildRoot, "occurrence-manifest.json"),
                    "utf8",
                ),
            );
            verifySelectedEvidence(build, occurrenceManifest);
            if (
                profiled &&
                (request.siteOrigin !== publicationProfile.siteOrigin ||
                    request.stageGlobalNoindex !==
                        publicationProfile.stageGlobalNoindex ||
                    request.policyVersion !==
                        publicationProfile.policyVersion ||
                    build.siteOrigin !== publicationProfile.siteOrigin ||
                    build.stageGlobalNoindex !==
                        publicationProfile.stageGlobalNoindex ||
                    !(
                        await readFile(
                            path.join(request.buildRoot, "index.html"),
                            "utf8",
                        )
                    ).includes('<meta name="robots" content="noindex,follow">'))
            ) {
                throw new Error(
                    "stage verifier did not observe the bound origin and noindex output",
                );
            }
            return {
                contract: profiled
                    ? "verdify.lab-site-output-verification-result"
                    : "verdify.lab-production-output-verification-result",
                schemaVersion: profiled ? 2 : 1,
                ...(profiled
                    ? {
                          siteOrigin:
                              resultOverride?.siteOrigin ??
                              publicationProfile.siteOrigin,
                          stageGlobalNoindex:
                              resultOverride?.stageGlobalNoindex ??
                              publicationProfile.stageGlobalNoindex,
                          policyVersion:
                              resultOverride?.policyVersion ??
                              publicationProfile.policyVersion,
                      }
                    : {}),
                buildInventorySha256: request.buildInventorySha256,
                buildContentIdentitySha256: request.buildContentIdentitySha256,
                staticBuildSha256: request.staticBuildSha256,
                occurrenceOutputManifestSha256:
                    request.occurrenceOutputManifestSha256,
                occurrenceSelectionSha256: request.occurrenceSelectionSha256,
                occurrenceManifestSha256: request.occurrenceManifestSha256,
                provenanceSha256: request.provenanceSha256,
                siteEventSha256: request.siteEventSha256,
                sitePayloadSha256: request.sitePayloadSha256,
            };
        },
    };
}

function realProductionVerificationOperation(calls) {
    return {
        contract: "verdify.lab-production-output-verification-operation",
        schemaVersion: 1,
        operationSha256: VERIFICATION_OPERATION_SHA256,
        verify: async (request) => {
            calls.verifications += 1;
            const projectRoot = path.dirname(request.buildRoot);
            const verifier = await import(
                pathToFileURL(
                    path.join(
                        projectRoot,
                        "scripts",
                        "verify-production-output.mjs",
                    ),
                )
            );
            await verifier.verifyProductionOutput({
                dist: request.buildRoot,
                allowFixture: true,
            });
            return {
                contract: "verdify.lab-production-output-verification-result",
                schemaVersion: 1,
                buildInventorySha256: request.buildInventorySha256,
                buildContentIdentitySha256: request.buildContentIdentitySha256,
                staticBuildSha256: request.staticBuildSha256,
                occurrenceOutputManifestSha256:
                    request.occurrenceOutputManifestSha256,
                occurrenceSelectionSha256: request.occurrenceSelectionSha256,
                occurrenceManifestSha256: request.occurrenceManifestSha256,
                provenanceSha256: request.provenanceSha256,
                siteEventSha256: request.siteEventSha256,
                sitePayloadSha256: request.sitePayloadSha256,
            };
        },
    };
}

async function seedSiteLkg(value) {
    const buildRoot = path.join(value.root, "site-lkg");
    await mkdir(buildRoot);
    await writeFile(
        path.join(buildRoot, "index.html"),
        "<!doctype html><title>Prior LKG</title>\n",
    );
    const inventory = await inventoryBuiltSite(buildRoot);
    const files = inventory.files.map(
        ({ sourcePath: _sourcePath, ...record }) => record,
    );
    const contentIdentitySha256 = siteContentIdentitySha256({
        sourceSnapshotManifestSha256: "1".repeat(64),
        policyVersion: "prior-site-lkg-v1",
        builderCommit: "2".repeat(40),
        files,
    });
    const event = {
        contract: "verdify.lab-release-trigger",
        schemaVersion: 1,
        eventId: "evt_prior_site_lkg_0001",
        eventType: "planner-completed",
        sourceId: "prior-site-lkg",
        sourceWatermark: "wm_prior_site_lkg_0001",
        occurredAt: "2026-07-13T11:00:00Z",
        payloadSha256: siteReleasePayloadSha256({
            sourceSnapshotManifestSha256: "1".repeat(64),
            policyVersion: "prior-site-lkg-v1",
            builderCommit: "2".repeat(40),
            contentIdentitySha256,
        }),
    };
    return publishSiteRelease({
        storeRoot: SITE_LOCATION,
        store: value.siteStore,
        buildRoot,
        event,
        sourceSnapshotManifestSha256: "1".repeat(64),
        policyVersion: "prior-site-lkg-v1",
        builderCommit: "2".repeat(40),
        releasedAt: "2026-07-13T11:01:00Z",
        expectedSelectionSha256: null,
    });
}

function eventFor(
    value,
    expectedSiteSelectionSha256,
    producerResult = value.producerResult,
    operationIdentities = {
        build: BUILD_OPERATION_SHA256,
        verification: VERIFICATION_OPERATION_SHA256,
    },
) {
    return createOccurrenceSitePublishEvent({
        sourceId: producerResult.exportBatch.reportingFeed.sourceId,
        sourceWatermark:
            producerResult.exportBatch.reportingFeed.sourceWatermark,
        occurredAt: producerResult.exportBatch.reportingFeed.sourceWatermarkAt,
        releasedAt: RELEASED_AT,
        sourceSnapshotManifestSha256: value.sourceSnapshotManifestSha256,
        sourceOccurrenceManifestSha256: value.manifestSha256,
        occurrencePolicySha256: value.policySha256,
        occurrenceStoreIdentitySha256: value.occurrenceStore.identity.sha256,
        producerResultSha256: sha256(canonicalBytes(producerResult)),
        builderCommit: BUILDER_COMMIT,
        buildOperationSha256: operationIdentities.build,
        verificationOperationSha256: operationIdentities.verification,
        siteStoreIdentitySha256: value.siteStore.identity.sha256,
        expectedSiteSelectionSha256,
    });
}

function processorInput(
    value,
    event,
    checkpoint,
    publication,
    workspaceRoot,
    producerResult = value.producerResult,
) {
    return {
        event,
        producerResult,
        policy: value.policy,
        manifest: value.manifest,
        manifestSha256: value.manifestSha256,
        occurrenceStore: value.occurrenceStore,
        candidateRoot: value.candidateRoot,
        workspaceRoot,
        buildOperation: buildOperation(),
        verificationOperation: verificationOperation(),
        checkpointOperations: checkpoint,
        publicationOperation: publication,
    };
}

test("143+2 fake-S3 publish retries across a downstream outage and two caches converge", async (context) => {
    const value = await fixture(context);
    const prior = await seedSiteLkg(value);
    const cacheOne = path.join(value.root, "cache-one");
    const cacheTwo = path.join(value.root, "cache-two");
    await Promise.all([mkdir(cacheOne), mkdir(cacheTwo)]);
    for (const cacheRoot of [cacheOne, cacheTwo]) {
        const hydrated = await hydrateSiteCache({
            storeRoot: SITE_LOCATION,
            store: value.siteStore,
            cacheRoot,
            asOf: "2026-07-13T11:02:00Z",
        });
        assert.equal(hydrated.releaseSha256, prior.releaseSha256);
    }
    const priorLinks = await Promise.all(
        [cacheOne, cacheTwo].map((root) =>
            readlink(path.join(root, "current")),
        ),
    );
    const priorBytes = await Promise.all(
        [cacheOne, cacheTwo].map((root, index) =>
            readFile(path.join(root, priorLinks[index], "tree", "index.html")),
        ),
    );

    const event = eventFor(value, prior.selectionSha256);
    const checkpoints = checkpointOperations(value.siteStore.identity.sha256);
    const outage = { armed: true };
    const publication = publicationOperation(value.siteStore, {
        failBeforePublish: outage,
    });
    const failedWorkspace = path.join(value.root, "failed-workspace");
    await mkdir(failedWorkspace);
    await assert.rejects(
        processOccurrenceSitePublishEvent(
            processorInput(
                value,
                event,
                checkpoints,
                publication,
                failedWorkspace,
            ),
        ),
        /simulated downstream site-store outage/,
    );
    const afterFailure = await value.siteStore.readSelection();
    assert.equal(afterFailure.sha256, prior.selectionSha256);
    for (const [index, cacheRoot] of [cacheOne, cacheTwo].entries()) {
        assert.equal(
            await readlink(path.join(cacheRoot, "current")),
            priorLinks[index],
        );
        assert.deepEqual(
            await readFile(
                path.join(cacheRoot, priorLinks[index], "tree", "index.html"),
            ),
            priorBytes[index],
        );
    }

    const selectedAfterFailure = await loadSelectedOccurrenceRelease(
        value.occurrenceStore,
    );
    assert.equal(selectedAfterFailure.current.occurrences.graphs.length, 143);
    assert.equal(
        selectedAfterFailure.current.occurrences.currentMedia.length,
        2,
    );

    const retryWorkspace = path.join(value.root, "retry-workspace");
    await mkdir(retryWorkspace);
    const published = await processOccurrenceSitePublishEvent(
        processorInput(value, event, checkpoints, publication, retryWorkspace),
    );
    assert.equal(published.status, "published");
    assert.equal(
        published.occurrenceSelectionSha256,
        selectedAfterFailure.selectionSha256,
    );
    assert.match(published.releaseSha256, /^[0-9a-f]{64}$/u);
    assert.doesNotMatch(
        JSON.stringify(published),
        /https?:|endpoint|credential|candidateRoot|workspaceRoot/iu,
    );

    for (const cacheRoot of [cacheOne, cacheTwo]) {
        const hydrated = await hydrateSiteCache({
            storeRoot: SITE_LOCATION,
            store: value.siteStore,
            cacheRoot,
            asOf: "2026-07-13T12:11:00Z",
        });
        assert.equal(hydrated.releaseSha256, published.releaseSha256);
    }
    const convergedLinks = await Promise.all(
        [cacheOne, cacheTwo].map((root) =>
            readlink(path.join(root, "current")),
        ),
    );
    assert.match(
        convergedLinks[0],
        new RegExp(`^generations/${published.releaseSha256}-`),
    );
    assert.match(
        convergedLinks[1],
        new RegExp(`^generations/${published.releaseSha256}-`),
    );
    const convergedBytes = await Promise.all(
        [cacheOne, cacheTwo].map((root, index) =>
            readFile(
                path.join(
                    root,
                    convergedLinks[index],
                    "tree",
                    "occurrence-manifest.json",
                ),
            ),
        ),
    );
    assert.deepEqual(convergedBytes[0], convergedBytes[1]);

    const idempotentWorkspace = path.join(value.root, "idempotent-workspace");
    await mkdir(idempotentWorkspace);
    const idempotent = await processOccurrenceSitePublishEvent(
        processorInput(
            value,
            event,
            checkpoints,
            publication,
            idempotentWorkspace,
        ),
    );
    assert.equal(idempotent.status, "idempotent");
    assert.equal(idempotent.releaseSha256, published.releaseSha256);
    assert.deepEqual(await readdir(idempotentWorkspace), []);

    const conflictingEnvelope = {
        ...event,
        sourceWatermark: "wm_conflicting_reuse_0001",
    };
    const conflictWorkspace = path.join(value.root, "conflict-workspace");
    await mkdir(conflictWorkspace);
    await assert.rejects(
        processOccurrenceSitePublishEvent(
            processorInput(
                value,
                conflictingEnvelope,
                checkpoints,
                publication,
                conflictWorkspace,
            ),
        ),
        /closed v1 contract/,
    );
    assert.deepEqual(await readdir(conflictWorkspace), []);

    const staleProducer = structuredClone(value.producerResult);
    staleProducer.exportBatch.reportingFeed.sourceWatermark =
        "wm_occurrence_site_stale_0001";
    staleProducer.exportBatch.reportingFeed.sourceWatermarkAt =
        "2026-07-13T10:00:00Z";
    staleProducer.reportingFeedSha256 = reportingFeedEnvelopeSha256(
        staleProducer.exportBatch.reportingFeed,
    );
    staleProducer.exportBatchSha256 = sha256(
        canonicalBytes(staleProducer.exportBatch),
    );
    const staleEvent = eventFor(
        value,
        published.siteSelectionSha256,
        staleProducer,
    );
    const staleWorkspace = path.join(value.root, "stale-workspace");
    await mkdir(staleWorkspace);
    await assert.rejects(
        processOccurrenceSitePublishEvent(
            processorInput(
                value,
                staleEvent,
                checkpoints,
                publication,
                staleWorkspace,
                staleProducer,
            ),
        ),
        /older than the selected site release/,
    );
    assert.deepEqual(await readdir(staleWorkspace), []);

    const equalTimeProducer = structuredClone(value.producerResult);
    equalTimeProducer.exportBatch.reportingFeed.sourceWatermark =
        "wm_occurrence_site_equal_time_conflict_0001";
    equalTimeProducer.reportingFeedSha256 = reportingFeedEnvelopeSha256(
        equalTimeProducer.exportBatch.reportingFeed,
    );
    equalTimeProducer.graphResult.reportingFeedSha256 =
        equalTimeProducer.reportingFeedSha256;
    equalTimeProducer.graphResultSha256 = sha256(
        canonicalBytes(equalTimeProducer.graphResult),
    );
    equalTimeProducer.exportBatchSha256 = sha256(
        canonicalBytes(equalTimeProducer.exportBatch),
    );
    const equalTimeEvent = eventFor(
        value,
        published.siteSelectionSha256,
        equalTimeProducer,
    );
    const equalTimeWorkspace = path.join(
        value.root,
        "equal-time-conflict-workspace",
    );
    await mkdir(equalTimeWorkspace);
    await assert.rejects(
        processOccurrenceSitePublishEvent(
            processorInput(
                value,
                equalTimeEvent,
                checkpoints,
                publication,
                equalTimeWorkspace,
                equalTimeProducer,
            ),
        ),
        /conflicts at the selected source time/,
    );
    assert.deepEqual(await readdir(equalTimeWorkspace), []);

    assert.ok(
        value.client.commands.some(({ name }) => name === "PutObjectCommand"),
    );
    assert.ok(
        value.client.commands.every(({ name }) =>
            [
                "GetObjectCommand",
                "PutObjectCommand",
                "ListObjectsV2Command",
            ].includes(name),
        ),
    );
});

test("selected occurrence CAS restores a missing checkpoint without the candidate workspace", async (context) => {
    const value = await fixture(context);
    const event = eventFor(value, null);
    const outage = { armed: true, postReadUnavailable: false };
    const checkpoints = checkpointOperations(value.siteStore.identity.sha256, {
        failWriteAndPostRead: outage,
    });
    const publication = publicationOperation(value.siteStore);
    const failedWorkspace = path.join(
        value.root,
        "checkpoint-outage-workspace",
    );
    await mkdir(failedWorkspace);
    await assert.rejects(
        processOccurrenceSitePublishEvent(
            processorInput(
                value,
                event,
                checkpoints,
                publication,
                failedWorkspace,
            ),
        ),
        /simulated checkpoint write outage/,
    );
    assert.deepEqual(await readdir(failedWorkspace), []);

    const aggregateSelectionKey = "/occurrence-releases/v1/selection.json";
    const aggregateSelectionWrites = () =>
        value.client.commands.filter(
            ({ name, key }) =>
                name === "PutObjectCommand" &&
                key.endsWith(aggregateSelectionKey),
        ).length;
    assert.equal(aggregateSelectionWrites(), 1);
    const selected = await loadSelectedOccurrenceRelease(value.occurrenceStore);
    assert.equal(selected.current.occurrences.graphs.length, 143);
    assert.equal(selected.current.occurrences.currentMedia.length, 2);
    await rm(value.candidateRoot, { recursive: true, force: true });
    await assert.rejects(readdir(value.candidateRoot), { code: "ENOENT" });

    const retryWorkspace = path.join(
        value.root,
        "checkpoint-recovery-workspace",
    );
    await mkdir(retryWorkspace);
    const published = await processOccurrenceSitePublishEvent(
        processorInput(value, event, checkpoints, publication, retryWorkspace),
    );
    assert.equal(published.status, "published");
    assert.equal(published.occurrenceSelectionSha256, selected.selectionSha256);
    assert.equal(
        aggregateSelectionWrites(),
        1,
        "checkpoint recovery must not replay the consumed occurrence CAS",
    );
});

test("exact site intent and release resume publication after selector interruption", async (context) => {
    const value = await fixture(context);
    const prior = await seedSiteLkg(value);
    const event = eventFor(value, prior.selectionSha256);
    const checkpoints = checkpointOperations(value.siteStore.identity.sha256);
    const interruption = { armed: true };
    const publication = publicationOperation(value.siteStore, {
        failAfterIntent: interruption,
    });
    const failedWorkspace = path.join(value.root, "intent-failure-workspace");
    await mkdir(failedWorkspace);
    await assert.rejects(
        processOccurrenceSitePublishEvent(
            processorInput(
                value,
                event,
                checkpoints,
                publication,
                failedWorkspace,
            ),
        ),
        /simulated failure after site intent/,
    );
    const intent = await value.siteStore.readEventIntent(event.eventId);
    assert.equal(intent.eventId, event.eventId);
    assert.match(intent.releaseSha256, /^[0-9a-f]{64}$/u);
    assert.equal(
        (await value.siteStore.readSelection()).sha256,
        prior.selectionSha256,
        "the interrupted publication must leave the prior site selected",
    );

    const retryWorkspace = path.join(value.root, "intent-resume-workspace");
    await mkdir(retryWorkspace);
    const published = await processOccurrenceSitePublishEvent(
        processorInput(value, event, checkpoints, publication, retryWorkspace),
    );
    assert.equal(published.status, "published");
    assert.equal(published.releaseSha256, intent.releaseSha256);
    const selected = await value.siteStore.readSelection();
    assert.equal(selected.document.current.eventId, event.eventId);
    assert.equal(selected.document.current.releaseSha256, intent.releaseSha256);
});

test("publisher rejects malformed producer proof and every out-of-contract execution bound before mutation", async (context) => {
    const value = await fixture(context);
    const mutations = [
        {
            label: "datasource proof",
            mutate(result) {
                result.datasourceBindingProof.legacyByDashboard[0].count = 4;
            },
        },
        {
            label: "graph concurrency",
            mutate(result) {
                result.executionBounds.graphConcurrency = 5;
            },
        },
        {
            label: "graph timeout",
            mutate(result) {
                result.executionBounds.graphTimeoutMs = 15_001;
            },
        },
        {
            label: "graph settlement grace",
            mutate(result) {
                result.executionBounds.graphSettlementGraceMs = 251;
            },
        },
        {
            label: "graph attempts",
            mutate(result) {
                result.executionBounds.graphMaxAttempts = 2;
            },
        },
        {
            label: "camera concurrency",
            mutate(result) {
                result.executionBounds.cameraConcurrency = 3;
            },
        },
        {
            label: "camera timeout",
            mutate(result) {
                result.executionBounds.cameraTimeoutMs = 15_001;
            },
        },
        {
            label: "camera attempts",
            mutate(result) {
                result.executionBounds.cameraMaxAttempts = 4;
            },
        },
    ];
    for (const [index, mutation] of mutations.entries()) {
        const producerResult = structuredClone(value.producerResult);
        mutation.mutate(producerResult);
        const event = eventFor(value, null, producerResult);
        const workspace = path.join(value.root, `malformed-producer-${index}`);
        await mkdir(workspace);
        await assert.rejects(
            processOccurrenceSitePublishEvent(
                processorInput(
                    value,
                    event,
                    checkpointOperations(value.siteStore.identity.sha256),
                    publicationOperation(value.siteStore),
                    workspace,
                    producerResult,
                ),
            ),
            /exact canonical runner proof/,
            mutation.label,
        );
        assert.deepEqual(await readdir(workspace), []);
    }
    assert.equal(
        value.client.commands.some(({ name }) => name === "PutObjectCommand"),
        false,
    );
});

test("publisher rejects selector digest and pointer bindings before occurrence mutation", async (context) => {
    const value = await fixture(context);
    const prior = await seedSiteLkg(value);
    const event = eventFor(value, prior.selectionSha256);
    for (const [label, mutate] of [
        ["digest", (selected) => ({ ...selected, sha256: "0".repeat(64) })],
        [
            "pointer",
            (selected) => {
                const document = structuredClone(selected.document);
                document.current.eventId = "evt_mismatched_pointer_0001";
                return {
                    ...selected,
                    document,
                    sha256: sha256(canonicalBytes(document)),
                };
            },
        ],
    ]) {
        const base = publicationOperation(value.siteStore);
        const publication = {
            ...base,
            readSelection: async () => mutate(await base.readSelection()),
        };
        const workspace = path.join(value.root, `selector-${label}`);
        await mkdir(workspace);
        await assert.rejects(
            processOccurrenceSitePublishEvent(
                processorInput(
                    value,
                    event,
                    checkpointOperations(value.siteStore.identity.sha256),
                    publication,
                    workspace,
                ),
            ),
            label === "digest"
                ? /selection read is invalid/
                : /selected site release digest mismatch/,
        );
        assert.deepEqual(await readdir(workspace), []);
    }
    assert.equal(
        value.client.commands.some(
            ({ name, key }) =>
                name === "PutObjectCommand" &&
                key.includes("occurrence-releases/v1"),
        ),
        false,
    );
});

test("publisher drives the real selected compiler and full production verifier inside its exclusive workspace", async (context) => {
    const value = await fixture(context, { realCompiler: true });
    const event = eventFor(value, null);
    const workspace = path.join(value.root, "real-compiler-workspace");
    await mkdir(workspace);
    const calls = { builds: 0, verifications: 0 };
    const input = processorInput(
        value,
        event,
        checkpointOperations(value.siteStore.identity.sha256),
        publicationOperation(value.siteStore),
        workspace,
    );
    input.buildOperation = realCompilerBuildOperation(value, calls);
    input.verificationOperation = realProductionVerificationOperation(calls);
    const published = await processOccurrenceSitePublishEvent(input);
    assert.equal(published.status, "published");
    assert.deepEqual(calls, { builds: 1, verifications: 1 });
    const dist = path.join(workspace, "site-astro", "dist");
    const build = JSON.parse(
        await readFile(path.join(dist, "static-build.json"), "utf8"),
    );
    assert.equal(build.contract, "verdify.lab-astro-stage-build");
    assert.equal(build.siteOrigin, "https://lab.verdify.ai");
    assert.equal(build.stageGlobalNoindex, false);
    assert.equal(build.grafanaOccurrenceCount, 143);
    assert.equal(build.currentMediaOccurrenceCount, 2);
    assert.equal(
        build.selectedOccurrenceManifestSha256,
        `sha256:${published.occurrenceManifestSha256}`,
    );
    const routeManifest = JSON.parse(
        await readFile(path.join(dist, "route-manifest.json"), "utf8"),
    );
    assert.equal(
        routeManifest.build.selectedOccurrenceManifestSha256,
        build.selectedOccurrenceManifestSha256,
    );
});

test("publisher has no default client, command, environment, or activation surface", async () => {
    const source = await readFile(
        new URL(
            "../scripts/lib/occurrence-site-publisher.mjs",
            import.meta.url,
        ),
        "utf8",
    );
    assert.doesNotMatch(
        source,
        /process\.env|node:child_process|spawnSync|execFile|fetch\s*\(|@aws-sdk|kubectl|kubernetes|argocd|replicas?:/iu,
    );
    assert.deepEqual(occurrenceSitePublisherContract.defaults, {
        buildOperation: null,
        verificationOperation: null,
        checkpointOperations: null,
        publicationOperation: null,
    });
});

test("stage execution wrapper publishes one canonical delivery and rejects unbound execution", async (context) => {
    const value = await fixture(context);
    const profile = occurrenceSitePublicationProfiles.stage;
    const stageBuild = buildOperation(profile);
    const stageVerification = verificationOperation(profile);
    const event = eventFor(value, null, value.producerResult, {
        build: stageBuild.operationSha256,
        verification: stageVerification.operationSha256,
    });
    const canonicalInputs = {
        event: canonicalDocument(event),
        producerResult: canonicalDocument(value.producerResult),
        policy: canonicalDocument(value.policy),
        manifest: canonicalDocument(value.manifest),
        candidateRoot: value.candidateRoot,
    };
    const inputsFor = async (name) => {
        const workspaceRoot = path.join(value.root, name);
        await mkdir(workspaceRoot);
        return { ...canonicalInputs, workspaceRoot };
    };
    const checkpoints = checkpointOperations(value.siteStore.identity.sha256);
    const publication = publicationOperation(value.siteStore);
    let runtimeCalls = 0;
    const runtimeFactory =
        ({
            build = stageBuild,
            verification = stageVerification,
            siteOrigin = profile.siteOrigin,
        } = {}) =>
        async (request) => {
            runtimeCalls += 1;
            assert.equal(
                request.contract,
                "verdify.lab-stage-occurrence-site-runtime-request",
            );
            assert.deepEqual(request.publicationProfile, profile);
            return {
                contract: "verdify.lab-stage-occurrence-site-runtime",
                schemaVersion: 1,
                siteOrigin,
                stageGlobalNoindex: true,
                occurrenceStore: value.occurrenceStore,
                buildOperation: build,
                verificationOperation: verification,
                checkpointOperations: checkpoints,
                publicationOperation: publication,
            };
        };

    await assert.rejects(
        runOccurrenceSitePublisherDelivery(await inputsFor("wrong-target"), {
            createRuntime: runtimeFactory({
                build: buildOperation(profile, {
                    targetOverride: {
                        siteOrigin: "https://lab.verdify.ai",
                        stageGlobalNoindex: false,
                    },
                }),
            }),
        }),
        /build does not attest the bound site publication target/,
    );

    await assert.rejects(
        runOccurrenceSitePublisherDelivery(await inputsFor("wrong-proof"), {
            createRuntime: runtimeFactory({
                verification: verificationOperation(profile, {
                    resultOverride: {
                        siteOrigin: "https://lab.verdify.ai",
                    },
                }),
            }),
        }),
        /verifier did not attest the exact selected build and target/,
    );

    const inputs = await inputsFor("stage-execution-workspace");
    const createRuntime = runtimeFactory();
    const result = await runOccurrenceSitePublisherDelivery(inputs, {
        createRuntime,
    });
    assert.equal(
        result.contract,
        occurrenceSitePublisherRunnerContract.result.contract,
    );
    assert.equal(result.siteOrigin, "https://lab-stage.verdify.ai");
    assert.equal(result.stageGlobalNoindex, true);
    assert.deepEqual(result.publicationProfile, profile);
    assert.equal(result.publication.status, "published");
    assert.equal(result.publication.eventSha256, inputs.event.sha256);
    assert.equal(runtimeCalls, 3);

    await assert.rejects(
        runOccurrenceSitePublisherDelivery(inputs),
        /runtime is not configured; no default live action is available/,
    );
    assert.equal(runtimeCalls, 3);

    const blockedPolicy = structuredClone(value.policy);
    blockedPolicy.activation = {
        ...blockedPolicy.activation,
        state: "blocked",
        approvedBy: null,
        approvedAt: null,
    };
    await assert.rejects(
        runOccurrenceSitePublisherDelivery(
            { ...inputs, policy: canonicalDocument(blockedPolicy) },
            { createRuntime },
        ),
        /publication is disabled by the supplied policy/,
    );
    assert.equal(runtimeCalls, 3);

    await assert.rejects(
        runOccurrenceSitePublisherDelivery(
            { ...inputs, event: { ...inputs.event, sha256: "f".repeat(64) } },
            { createRuntime },
        ),
        /canonical identity mismatch/,
    );
    assert.equal(runtimeCalls, 3);

    const nestedWorkspace = path.join(value.candidateRoot, "nested-workspace");
    await mkdir(nestedWorkspace);
    await assert.rejects(
        runOccurrenceSitePublisherDelivery(
            { ...canonicalInputs, workspaceRoot: nestedWorkspace },
            { createRuntime },
        ),
        /roots must be disjoint/,
    );
    assert.equal(runtimeCalls, 3);

    await assert.rejects(
        runOccurrenceSitePublisherDelivery(inputs, {
            createRuntime: runtimeFactory({
                siteOrigin: "https://lab.verdify.ai",
            }),
        }),
        /does not preserve the closed noindex Lab stage contract/,
    );

    await assert.rejects(
        runOccurrenceSitePublisherDelivery(inputs, {
            createRuntime,
            processEvent: async () => ({
                ...result.publication,
                eventSha256: "e".repeat(64),
            }),
        }),
        /did not return the exact event-bound result/,
    );

    const buildForInjectedFailure = (request) =>
        request.buildOperation.build({
            contract: "verdify.lab-profiled-selected-astro-build-request",
            schemaVersion: 2,
            publicationProfile: structuredClone(profile),
            event: structuredClone(request.event),
            checkpoint: {},
            workspaceRoot: request.workspaceRoot,
            policy: structuredClone(request.policy),
            manifest: structuredClone(request.manifest),
            occurrenceStore: request.occurrenceStore,
        });
    for (const [label, message] of [
        ["semantic-verifier", "simulated post-build semantic verifier failure"],
        ["publication-read", "simulated post-build publication read failure"],
    ]) {
        const retryInputs = await inputsFor(`${label}-retry-workspace`);
        let fail = true;
        const processor = async (request) => {
            await buildForInjectedFailure(request);
            if (fail) {
                fail = false;
                throw new Error(message);
            }
            return structuredClone(result.publication);
        };
        await assert.rejects(
            runOccurrenceSitePublisherDelivery(retryInputs, {
                createRuntime,
                processEvent: processor,
            }),
            new RegExp(message),
        );
        assert.deepEqual(await readdir(retryInputs.workspaceRoot), []);
        const retried = await runOccurrenceSitePublisherDelivery(retryInputs, {
            createRuntime,
            processEvent: processor,
        });
        assert.equal(retried.publication.eventSha256, inputs.event.sha256);
        assert.deepEqual(await readdir(retryInputs.workspaceRoot), [
            "selected-build",
        ]);
    }

    const replacedInputs = await inputsFor("replaced-build-workspace");
    const replacementMarker = path.join(
        replacedInputs.workspaceRoot,
        "selected-build",
        "replacement-marker",
    );
    await assert.rejects(
        runOccurrenceSitePublisherDelivery(replacedInputs, {
            createRuntime,
            processEvent: async (request) => {
                const built = await buildForInjectedFailure(request);
                await rename(
                    built.buildRoot,
                    path.join(request.workspaceRoot, "displaced-owned-build"),
                );
                await mkdir(built.buildRoot);
                await writeFile(replacementMarker, "unowned replacement\n");
                throw new Error("simulated failure after build replacement");
            },
        }),
        /refused to clean a replaced build tree/,
    );
    assert.equal(
        await readFile(replacementMarker, "utf8"),
        "unowned replacement\n",
    );
});

test("stage execution CLI keeps document reads and runtime creation explicit", async () => {
    const paths = new Map([
        ["event.json", { name: "event" }],
        ["producer.json", { name: "producer" }],
        ["policy.json", { name: "policy" }],
        ["manifest.json", { name: "manifest" }],
    ]);
    const reads = [];
    const runtime = async () => ({ configured: true });
    let delivery;
    const result = await runOccurrenceSitePublishCli(
        [
            "execute",
            "--event",
            "event.json",
            "--producer-result",
            "producer.json",
            "--policy",
            "policy.json",
            "--manifest",
            "manifest.json",
            "--candidate-root",
            "candidate",
            "--workspace-root",
            "workspace",
        ],
        {
            readDocument: async (file, label) => {
                reads.push([file, label]);
                return paths.get(file);
            },
            createRuntime: runtime,
            runDelivery: async (inputs, dependencies) => {
                delivery = { inputs, dependencies };
                return { status: "accepted" };
            },
        },
    );
    assert.deepEqual(result, { status: "accepted" });
    assert.deepEqual(
        reads.map(([file]) => file),
        ["event.json", "producer.json", "policy.json", "manifest.json"],
    );
    assert.equal(delivery.inputs.event.name, "event");
    assert.equal(delivery.inputs.producerResult.name, "producer");
    assert.equal(delivery.dependencies.createRuntime, runtime);
    assert.equal(delivery.dependencies.processEvent, undefined);
    assert.equal(delivery.inputs.candidateRoot, path.resolve("candidate"));
    assert.equal(delivery.inputs.workspaceRoot, path.resolve("workspace"));

    await assert.rejects(runOccurrenceSitePublishCli(["status"]), /Usage:/);
    await assert.rejects(
        runOccurrenceSitePublishCli(["execute", "--event", "event.json"]),
        /Usage:/,
    );
});

test("stage execution source has no implicit live integration surface", async () => {
    const source = await Promise.all([
        readFile(
            new URL(
                "../scripts/lib/occurrence-site-publisher-runner.mjs",
                import.meta.url,
            ),
            "utf8",
        ),
        readFile(
            new URL(
                "../scripts/execute-occurrence-site-publish.mjs",
                import.meta.url,
            ),
            "utf8",
        ),
    ]);
    assert.doesNotMatch(
        source.join("\n"),
        /process\.env|node:child_process|spawnSync|execFile|fetch\s*\(|@aws-sdk|kubectl|kubernetes|argocd|replicas?:/iu,
    );
    assert.deepEqual(occurrenceSitePublisherRunnerContract.defaults, {
        createRuntime: null,
    });
});
