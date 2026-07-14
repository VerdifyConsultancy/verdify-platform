import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
    access,
    mkdir,
    mkdtemp,
    readFile,
    readdir,
    rm,
    symlink,
    writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { runOccurrenceSitePublishCli } from "../scripts/execute-occurrence-site-publish.mjs";
import {
    resolvePackagedStagePublisherPaths,
    runExecutableStagePublisher,
} from "../scripts/run-stage-occurrence-site-publisher.mjs";
import { runBoundedChildProcess } from "../scripts/lib/bounded-child-process.mjs";
import { createOccurrenceReleaseStore } from "../scripts/lib/occurrence-release-store.mjs";
import {
    createStageAstroBuildOperation,
    createStageOutputVerificationOperation,
} from "../scripts/lib/occurrence-site-stage-operations.mjs";
import { occurrenceSitePublicationProfiles } from "../scripts/lib/occurrence-site-publisher.mjs";
import { createSiteReleaseCheckpointOperations } from "../scripts/lib/site-release-checkpoint-operations.mjs";
import { createSiteReleaseWriterStore } from "../scripts/lib/runtime-s3-binding.mjs";

const PROFILE = occurrenceSitePublicationProfiles.stage;
const POLICY_PATH = new URL(
    "../config/lab-stage-occurrence-export-policy.json",
    import.meta.url,
);
const BUILDER_COMMIT = "1".repeat(40);

function sha256(bytes) {
    return createHash("sha256").update(bytes).digest("hex");
}

function canonical(document) {
    return `${JSON.stringify(document, null, 2)}\n`;
}

function canonicalDocument(document) {
    const bytes = Buffer.from(canonical(document));
    return { document, bytes, sha256: sha256(bytes) };
}

async function temporaryRoot(context, prefix) {
    const root = await mkdtemp(path.join(tmpdir(), prefix));
    context.after(() => rm(root, { recursive: true, force: true }));
    return root;
}

async function publisherBindingFixture(
    context,
    prefix,
    { eventBuilderCommit = BUILDER_COMMIT } = {},
) {
    const root = await temporaryRoot(context, prefix);
    const sourceRoot = path.join(root, "source");
    await mkdir(path.join(sourceRoot, "config"), { recursive: true });
    const policyBytes = await readFile(POLICY_PATH);
    const policyPath = path.join(root, "selected-policy.json");
    const eventPath = path.join(root, "event.json");
    const metadataPath = path.join(root, "occurrence-exporter-image.json");
    await Promise.all([
        writeFile(
            path.join(
                sourceRoot,
                "config/lab-stage-occurrence-export-policy.json",
            ),
            policyBytes,
        ),
        writeFile(policyPath, policyBytes),
        writeFile(
            eventPath,
            canonical({ builderCommit: eventBuilderCommit }),
        ),
        writeFile(
            metadataPath,
            canonical({
                contract: "verdify.lab-occurrence-exporter-image-metadata",
                schemaVersion: 1,
                builderCommit: BUILDER_COMMIT,
                releasedAt: "2026-07-14T01:34:00Z",
            }),
        ),
    ]);
    const paths = {
        sourceRoot,
        snapshotRoot: path.join(sourceRoot, ".snapshot"),
        nodeModulesRoot: "/app/node_modules",
        metadataPath,
    };
    const argv = [
        "execute",
        "--event",
        eventPath,
        "--producer-result",
        "/input/producer.json",
        "--policy",
        policyPath,
        "--manifest",
        "/input/manifest.json",
        "--candidate-root",
        "/work/candidates",
        "--workspace-root",
        "/work/build",
    ];
    return { root, sourceRoot, policyPath, eventPath, metadataPath, paths, argv };
}

test("bounded child termination kills the complete process group", async (context) => {
    const root = await temporaryRoot(context, "verdify-bounded-child-");
    const marker = path.join(root, "orphan-marker");
    const parent = path.join(root, "parent.mjs");
    await writeFile(
        parent,
        [
            'import { spawn } from "node:child_process";',
            `spawn(process.execPath, ["-e", ${JSON.stringify(`setTimeout(() => require("node:fs").writeFileSync(${JSON.stringify(marker)}, "orphaned"), 350)`) }], { stdio: "ignore" });`,
            'process.on("SIGTERM", () => {});',
            "setInterval(() => {}, 1000);",
            "",
        ].join("\n"),
    );
    await assert.rejects(
        runBoundedChildProcess({
            label: "process-tree-proof",
            executable: process.execPath,
            arguments: [parent],
            cwd: root,
            environment: { PATH: process.env.PATH ?? "/usr/local/bin:/usr/bin" },
            timeoutMs: 75,
            terminationGraceMs: 75,
            maximumOutputBytes: 1024,
            forwardOutput: false,
        }),
        /exceeded 75ms/,
    );
    await new Promise((resolve) => setTimeout(resolve, 450));
    await assert.rejects(access(marker), /ENOENT/);
});

test("bounded child keeps its group KILL deadline after the direct child exits", async (context) => {
    const root = await temporaryRoot(context, "verdify-bounded-descendant-");
    const marker = path.join(root, "term-resistant-marker");
    const parent = path.join(root, "exiting-parent.mjs");
    const grandchild = [
        'process.on("SIGTERM", () => {});',
        `setTimeout(() => require("node:fs").writeFileSync(${JSON.stringify(marker)}, "survived"), 350);`,
        "setInterval(() => {}, 1000);",
    ].join("\n");
    await writeFile(
        parent,
        [
            'import { spawn } from "node:child_process";',
            `spawn(process.execPath, ["-e", ${JSON.stringify(grandchild)}], { stdio: "ignore" });`,
            'process.on("SIGTERM", () => process.exit(0));',
            "setInterval(() => {}, 1000);",
            "",
        ].join("\n"),
    );
    await assert.rejects(
        runBoundedChildProcess({
            label: "exiting-parent-process-tree-proof",
            executable: process.execPath,
            arguments: [parent],
            cwd: root,
            environment: {
                PATH: process.env.PATH ?? "/usr/local/bin:/usr/bin",
            },
            timeoutMs: 75,
            terminationGraceMs: 75,
            maximumOutputBytes: 1024,
            forwardOutput: false,
        }),
        /exceeded 75ms/,
    );
    await new Promise((resolve) => setTimeout(resolve, 350));
    await assert.rejects(access(marker), /ENOENT/);
});

test("stage build removes only its owned failed workspace and can retry", async (context) => {
    const root = await temporaryRoot(context, "verdify-stage-operation-");
    const sourceRoot = path.join(root, "source");
    const snapshotRoot = path.join(root, "snapshot");
    const nodeModulesRoot = path.join(root, "node_modules");
    const workspaceRoot = path.join(root, "workspace");
    const occurrenceStoreRoot = path.join(root, "occurrences");
    await Promise.all([
        mkdir(sourceRoot),
        mkdir(snapshotRoot),
        mkdir(nodeModulesRoot),
        mkdir(workspaceRoot),
        mkdir(occurrenceStoreRoot),
    ]);
    for (const directory of ["scripts", "src", "vendor"]) {
        await mkdir(path.join(sourceRoot, directory));
    }
    for (const file of [
        "astro.config.mjs",
        "package-lock.json",
        "package.json",
        "postcss.config.mjs",
        "tsconfig.json",
    ]) {
        await writeFile(path.join(sourceRoot, file), "{}\n");
    }
    await writeFile(path.join(snapshotRoot, "attestation.json"), "{}\n");

    let fail = true;
    const labels = [];
    const operation = createStageAstroBuildOperation({
        sourceRoot,
        snapshotRoot,
        nodeModulesRoot,
        environment: { LAB_OCCURRENCE_STORE: occurrenceStoreRoot },
        runCommand: async ({ label, cwd }) => {
            labels.push(label);
            if (fail) throw new Error("injected build interruption");
            if (label === "Astro selected-stage build") {
                await mkdir(path.join(cwd, "dist"));
            }
        },
    });
    const sourceSnapshotManifestSha256 = "1".repeat(64);
    const request = {
        contract: "verdify.lab-profiled-selected-astro-build-request",
        schemaVersion: 2,
        publicationProfile: structuredClone(PROFILE),
        event: { sourceSnapshotManifestSha256 },
        checkpoint: {},
        workspaceRoot,
        policy: { sourceSnapshotManifestSha256 },
        manifest: {},
        occurrenceStore: createOccurrenceReleaseStore(occurrenceStoreRoot),
    };
    await assert.rejects(operation.build(request), /injected build interruption/);
    assert.deepEqual(await readdir(workspaceRoot), []);

    fail = false;
    const result = await operation.build(request);
    assert.equal(result.contract, "verdify.lab-selected-astro-build-result");
    assert.equal(result.buildRoot, path.join(workspaceRoot, "site-astro", "dist"));
    assert.equal((await readdir(workspaceRoot)).join(","), "site-astro");
    assert.ok(labels.includes("index selected-stage Pagefind"));
    assert.ok(labels.includes("verify selected global-noindex stage output"));
});

test("stage build rejects nested source and snapshot links before copying", async (context) => {
    for (const selectedTree of ["source", "snapshot"]) {
        const root = await temporaryRoot(
            context,
            `verdify-stage-linked-${selectedTree}-`,
        );
        const sourceRoot = path.join(root, "source");
        const snapshotRoot = path.join(root, "snapshot");
        const nodeModulesRoot = path.join(root, "node_modules");
        const workspaceRoot = path.join(root, "workspace");
        const occurrenceStoreRoot = path.join(root, "occurrences");
        await Promise.all([
            mkdir(sourceRoot),
            mkdir(snapshotRoot),
            mkdir(nodeModulesRoot),
            mkdir(workspaceRoot),
            mkdir(occurrenceStoreRoot),
        ]);
        for (const directory of ["scripts", "src", "vendor"]) {
            await mkdir(path.join(sourceRoot, directory));
        }
        for (const file of [
            "astro.config.mjs",
            "package-lock.json",
            "package.json",
            "postcss.config.mjs",
            "tsconfig.json",
        ]) {
            await writeFile(path.join(sourceRoot, file), "{}\n");
        }
        await writeFile(path.join(snapshotRoot, "attestation.json"), "{}\n");
        const linkedRoot =
            selectedTree === "source"
                ? path.join(sourceRoot, "scripts")
                : snapshotRoot;
        await mkdir(path.join(linkedRoot, "nested"));
        await symlink(
            path.join(root, "outside"),
            path.join(linkedRoot, "nested", "linked"),
        );

        const operation = createStageAstroBuildOperation({
            sourceRoot,
            snapshotRoot,
            nodeModulesRoot,
            environment: { LAB_OCCURRENCE_STORE: occurrenceStoreRoot },
            runCommand: async () => {
                assert.fail("linked packaged trees must fail before a command");
            },
        });
        const sourceSnapshotManifestSha256 = "1".repeat(64);
        await assert.rejects(
            operation.build({
                contract: "verdify.lab-profiled-selected-astro-build-request",
                schemaVersion: 2,
                publicationProfile: structuredClone(PROFILE),
                event: { sourceSnapshotManifestSha256 },
                checkpoint: {},
                workspaceRoot,
                policy: { sourceSnapshotManifestSha256 },
                manifest: {},
                occurrenceStore:
                    createOccurrenceReleaseStore(occurrenceStoreRoot),
            }),
            /contains a link/,
        );
        assert.deepEqual(await readdir(workspaceRoot), []);
    }
});

test("stage verifier runs the real verifier boundary and returns only bound identities", async (context) => {
    const root = await temporaryRoot(context, "verdify-stage-verifier-");
    const projectRoot = path.join(root, "site-astro");
    const buildRoot = path.join(projectRoot, "dist");
    await Promise.all([
        mkdir(path.join(projectRoot, "scripts"), { recursive: true }),
        mkdir(path.join(projectRoot, ".home"), { recursive: true }),
        mkdir(path.join(projectRoot, ".tmp"), { recursive: true }),
        mkdir(buildRoot, { recursive: true }),
    ]);
    const files = {
        "static-build.json": Buffer.from('{"build":true}\n'),
        "occurrence-manifest.json": Buffer.from('{"occurrences":true}\n'),
        "occurrence-publish-provenance.json": Buffer.from(
            '{"provenance":true}\n',
        ),
    };
    await Promise.all(
        Object.entries(files).map(([name, bytes]) =>
            writeFile(path.join(buildRoot, name), bytes),
        ),
    );
    let invocation = null;
    const operation = createStageOutputVerificationOperation({
        runCommand: async (request) => {
            invocation = request;
        },
    });
    const identities = {
        buildInventorySha256: "1".repeat(64),
        buildContentIdentitySha256: "2".repeat(64),
        staticBuildSha256: sha256(files["static-build.json"]),
        occurrenceOutputManifestSha256: sha256(
            files["occurrence-manifest.json"],
        ),
        occurrenceSelectionSha256: "3".repeat(64),
        occurrenceManifestSha256: "4".repeat(64),
        provenanceSha256: sha256(
            files["occurrence-publish-provenance.json"],
        ),
        siteEventSha256: "5".repeat(64),
        sitePayloadSha256: "6".repeat(64),
    };
    const result = await operation.verify({
        contract: "verdify.lab-site-output-verification-request",
        schemaVersion: 2,
        siteOrigin: PROFILE.siteOrigin,
        stageGlobalNoindex: true,
        policyVersion: PROFILE.policyVersion,
        event: {},
        buildRoot,
        buildContentIdentitySha256: identities.buildContentIdentitySha256,
        occurrenceSelectionSha256: identities.occurrenceSelectionSha256,
        occurrenceManifestSha256: identities.occurrenceManifestSha256,
        buildInventorySha256: identities.buildInventorySha256,
        staticBuildSha256: identities.staticBuildSha256,
        occurrenceOutputManifestSha256:
            identities.occurrenceOutputManifestSha256,
        provenanceSha256: identities.provenanceSha256,
        siteEventSha256: identities.siteEventSha256,
        sitePayloadSha256: identities.sitePayloadSha256,
    });
    assert.equal(
        invocation.arguments[0],
        path.join(projectRoot, "scripts", "verify-static.mjs"),
    );
    assert.deepEqual(result, {
        contract: "verdify.lab-site-output-verification-result",
        schemaVersion: 2,
        siteOrigin: PROFILE.siteOrigin,
        stageGlobalNoindex: true,
        policyVersion: PROFILE.policyVersion,
        ...identities,
    });
});

class MemoryS3Client {
    constructor() {
        this.objects = new Map();
    }

    async send(command) {
        const name = command.constructor.name;
        const input = command.input;
        if (name === "GetObjectCommand") {
            const body = this.objects.get(input.Key);
            if (body === undefined) throw Object.assign(new Error("missing"), { name: "NoSuchKey" });
            return {
                Body: Buffer.from(body),
                ContentLength: body.length,
                ETag: `"${sha256(body)}"`,
            };
        }
        if (name === "PutObjectCommand") {
            if (input.IfNoneMatch === "*" && this.objects.has(input.Key)) {
                throw Object.assign(new Error("exists"), {
                    name: "PreconditionFailed",
                    $metadata: { httpStatusCode: 412 },
                });
            }
            const body = Buffer.from(input.Body);
            this.objects.set(input.Key, body);
            return { ETag: `"${sha256(body)}"` };
        }
        throw new Error(`unexpected command ${name}`);
    }
}

test("site checkpoints are immutable and idempotent in the exact S3 release store", async () => {
    const client = new MemoryS3Client();
    const store = await createSiteReleaseWriterStore(
        "s3://verdify-lab-releases/stage/site",
        {
            environment: {
                LAB_S3_ENDPOINT_URL: "https://s3-hdd.vallery.net",
                AWS_DEFAULT_REGION: "garage",
                AWS_ACCESS_KEY_ID: "writer-access",
                AWS_SECRET_ACCESS_KEY: "writer-secret",
            },
            clientFactory: () => client,
        },
    );
    const operation = createSiteReleaseCheckpointOperations({ store });
    const document = {
        eventId: `evt_occurrence_site_${"a".repeat(32)}`,
        proof: "checkpoint",
    };
    const first = await operation.write(document);
    const second = await operation.write(document);
    assert.deepEqual(first.document, document);
    assert.equal(first.sha256, second.sha256);
    assert.deepEqual((await operation.read(document.eventId)).document, document);
    await assert.rejects(
        operation.write({ ...document, proof: "conflict" }),
        /event ID was reused/,
    );
});

test("explicit stage publisher composition selects a runtime without hiding the command", async (context) => {
    const fixture = await publisherBindingFixture(
        context,
        "verdify-stage-policy-",
    );
    const environment = { LAB_RELEASE_STORE: "binding" };
    const runtime = async () => ({ contract: "runtime" });
    let construction = null;
    let invocation = null;
    const result = await runExecutableStagePublisher(fixture.argv, {
        environment,
        paths: fixture.paths,
        createRuntime: (options) => {
            construction = options;
            return runtime;
        },
        runCli: async (argv, dependencies) => {
            invocation = { argv, dependencies };
            return { status: "selected" };
        },
    });
    assert.deepEqual(result, { status: "selected" });
    assert.deepEqual(construction, { environment, ...fixture.paths });
    assert.deepEqual(invocation.argv, fixture.argv);
    assert.equal(invocation.dependencies.createRuntime, runtime);
    assert.equal(
        invocation.dependencies.boundDocuments.event.document.builderCommit,
        BUILDER_COMMIT,
    );
    assert.equal(
        invocation.dependencies.boundDocuments.policy.sha256,
        sha256(await readFile(POLICY_PATH)),
    );
});

test("packaged path resolution includes the image metadata beside runtime source", async (context) => {
    const root = await temporaryRoot(context, "verdify-stage-paths-");
    const sourceRoot = path.join(root, "runtime-source");
    await mkdir(path.join(sourceRoot, "node_modules"), { recursive: true });
    assert.deepEqual(await resolvePackagedStagePublisherPaths(sourceRoot), {
        sourceRoot,
        snapshotRoot: path.join(sourceRoot, ".snapshot"),
        nodeModulesRoot: path.join(sourceRoot, "node_modules"),
        metadataPath: path.join(root, "occurrence-exporter-image.json"),
    });
});

test("executable rejects a different approved policy before runtime construction", async (context) => {
    const fixture = await publisherBindingFixture(
        context,
        "verdify-stage-policy-drift-",
    );
    const packaged = JSON.parse(await readFile(POLICY_PATH, "utf8"));
    const selected = structuredClone(packaged);
    selected.reviewedAt = "2026-07-13T07:36:31Z";
    await writeFile(fixture.policyPath, canonical(selected));
    let runtimeConstructions = 0;
    let cliInvocations = 0;
    await assert.rejects(
        runExecutableStagePublisher(
            fixture.argv,
            {
                environment: {},
                paths: fixture.paths,
                createRuntime: () => {
                    runtimeConstructions += 1;
                    return async () => ({});
                },
                runCli: async () => {
                    cliInvocations += 1;
                    return {};
                },
            },
        ),
        /does not match the packaged policy/,
    );
    assert.equal(runtimeConstructions, 0);
    assert.equal(cliInvocations, 0);
});

test("executable delivers its single-read policy when the path changes after binding", async (context) => {
    const fixture = await publisherBindingFixture(
        context,
        "verdify-stage-policy-single-read-",
    );
    const packaged = JSON.parse(await readFile(POLICY_PATH, "utf8"));
    const changed = structuredClone(packaged);
    changed.reviewedAt = "2026-07-13T07:36:31Z";
    let runtimeRequests = 0;
    let deliveredPolicy = null;
    const result = await runExecutableStagePublisher(fixture.argv, {
        environment: {},
        paths: fixture.paths,
        createRuntime: () => async (request) => {
            runtimeRequests += 1;
            assert.deepEqual(request.policy, packaged);
            return { contract: "unused-runtime" };
        },
        runCli: async (argv, dependencies) => {
            await writeFile(fixture.policyPath, canonical(changed));
            return runOccurrenceSitePublishCli(argv, {
                ...dependencies,
                readDocument: async (_file, label) =>
                    canonicalDocument({ label }),
                runDelivery: async (inputs, deliveryDependencies) => {
                    deliveredPolicy = inputs.policy;
                    await deliveryDependencies.createRuntime({
                        policy: inputs.policy.document,
                    });
                    return { status: "single-read" };
                },
            });
        },
    });
    assert.deepEqual(result, { status: "single-read" });
    assert.deepEqual(deliveredPolicy.document, packaged);
    assert.equal(deliveredPolicy.sha256, sha256(Buffer.from(canonical(packaged))));
    assert.notEqual(
        deliveredPolicy.sha256,
        sha256(await readFile(fixture.policyPath)),
    );
    assert.equal(runtimeRequests, 1);
});

test("executable rejects an event from a different builder before runtime construction", async (context) => {
    const fixture = await publisherBindingFixture(
        context,
        "verdify-stage-builder-drift-",
        { eventBuilderCommit: "2".repeat(40) },
    );
    let runtimeConstructions = 0;
    let cliInvocations = 0;
    await assert.rejects(
        runExecutableStagePublisher(fixture.argv, {
            environment: {},
            paths: fixture.paths,
            createRuntime: () => {
                runtimeConstructions += 1;
                return async () => ({});
            },
            runCli: async () => {
                cliInvocations += 1;
                return {};
            },
        }),
        /builder commit does not match the packaged image/,
    );
    assert.equal(runtimeConstructions, 0);
    assert.equal(cliInvocations, 0);
});

test("packaged exporter keeps the offline verifier as its no-argument default", async () => {
    const dockerfile = await readFile(
        new URL("../Dockerfile.occurrence-exporter", import.meta.url),
        "utf8",
    );
    const exporter = dockerfile;
    assert.match(
        exporter,
        /CMD \["node", "\/app\/scripts\/verify-occurrence-exporter-image\.mjs"\]\s*$/u,
    );
    assert.doesNotMatch(exporter, /^ENTRYPOINT/imu);
    assert.match(
        exporter,
        /COPY \.snapshot\/ \/app\/runtime-source\/\.snapshot\//u,
    );
    assert.match(
        exporter,
        /run-stage-occurrence-site-publisher\.mjs/u,
    );
    assert.match(
        exporter,
        /ai\.verdify\.release-authority="explicit-stage-only"/u,
    );
});
