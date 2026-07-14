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

import { runExecutableStagePublisher } from "../scripts/run-stage-occurrence-site-publisher.mjs";
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

function sha256(bytes) {
    return createHash("sha256").update(bytes).digest("hex");
}

function canonical(document) {
    return `${JSON.stringify(document, null, 2)}\n`;
}

async function temporaryRoot(context, prefix) {
    const root = await mkdtemp(path.join(tmpdir(), prefix));
    context.after(() => rm(root, { recursive: true, force: true }));
    return root;
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
    const root = await temporaryRoot(context, "verdify-stage-policy-");
    const sourceRoot = path.join(root, "source");
    await mkdir(path.join(sourceRoot, "config"), { recursive: true });
    const policyBytes = await readFile(POLICY_PATH);
    const policyPath = path.join(root, "selected-policy.json");
    await Promise.all([
        writeFile(
            path.join(
                sourceRoot,
                "config/lab-stage-occurrence-export-policy.json",
            ),
            policyBytes,
        ),
        writeFile(policyPath, policyBytes),
    ]);
    const environment = { LAB_RELEASE_STORE: "binding" };
    const paths = {
        sourceRoot,
        snapshotRoot: path.join(sourceRoot, ".snapshot"),
        nodeModulesRoot: "/app/node_modules",
    };
    const runtime = async () => ({ contract: "runtime" });
    let construction = null;
    let invocation = null;
    const argv = [
        "execute",
        "--event",
        "/input/event.json",
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
    const result = await runExecutableStagePublisher(argv, {
        environment,
        paths,
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
    assert.deepEqual(construction, { environment, ...paths });
    assert.deepEqual(invocation.argv, argv);
    assert.equal(invocation.dependencies.createRuntime, runtime);
});

test("executable rejects a different approved policy before runtime construction", async (context) => {
    const root = await temporaryRoot(context, "verdify-stage-policy-drift-");
    const sourceRoot = path.join(root, "source");
    await mkdir(path.join(sourceRoot, "config"), { recursive: true });
    const packaged = JSON.parse(await readFile(POLICY_PATH, "utf8"));
    const selected = structuredClone(packaged);
    selected.reviewedAt = "2026-07-13T07:36:31Z";
    const packagedPath = path.join(
        sourceRoot,
        "config/lab-stage-occurrence-export-policy.json",
    );
    const selectedPath = path.join(root, "selected-policy.json");
    await Promise.all([
        writeFile(packagedPath, canonical(packaged)),
        writeFile(selectedPath, canonical(selected)),
    ]);
    let runtimeConstructions = 0;
    let cliInvocations = 0;
    await assert.rejects(
        runExecutableStagePublisher(
            [
                "execute",
                "--event",
                "/input/event.json",
                "--producer-result",
                "/input/producer.json",
                "--policy",
                selectedPath,
                "--manifest",
                "/input/manifest.json",
                "--candidate-root",
                "/work/candidates",
                "--workspace-root",
                "/work/build",
            ],
            {
                environment: {},
                paths: {
                    sourceRoot,
                    snapshotRoot: path.join(sourceRoot, ".snapshot"),
                    nodeModulesRoot: "/app/node_modules",
                },
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

test("packaged exporter keeps the offline verifier as its no-argument default", async () => {
    const dockerfile = await readFile(
        new URL("../Dockerfile.release-runtime", import.meta.url),
        "utf8",
    );
    const exporter = dockerfile.slice(
        dockerfile.indexOf("FROM dependencies AS occurrence-exporter"),
    );
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
