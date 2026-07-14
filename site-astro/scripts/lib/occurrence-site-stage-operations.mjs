import { createHash } from "node:crypto";
import {
    cp,
    lstat,
    mkdir,
    readFile,
    readdir,
    realpath,
    rm,
    symlink,
    writeFile,
} from "node:fs/promises";
import path from "node:path";

import { runBoundedChildProcess } from "./bounded-child-process.mjs";
import { parseOccurrenceReleaseStoreLocation } from "./occurrence-release-store.mjs";
import {
    occurrenceSiteOperationSha256,
    occurrenceSitePublicationProfiles,
} from "./occurrence-site-publisher.mjs";

const STAGE_PROFILE = occurrenceSitePublicationProfiles.stage;
const SHA256_RE = /^[0-9a-f]{64}$/u;
const SOURCE_ENTRIES = Object.freeze([
    "astro.config.mjs",
    "package-lock.json",
    "package.json",
    "postcss.config.mjs",
    "scripts",
    "src",
    "tsconfig.json",
    "vendor",
]);
const BUILD_REQUEST_KEYS = Object.freeze([
    "contract",
    "schemaVersion",
    "publicationProfile",
    "event",
    "checkpoint",
    "workspaceRoot",
    "policy",
    "manifest",
    "occurrenceStore",
]);
const VERIFY_REQUEST_KEYS = Object.freeze([
    "contract",
    "schemaVersion",
    "siteOrigin",
    "stageGlobalNoindex",
    "policyVersion",
    "event",
    "buildRoot",
    "buildContentIdentitySha256",
    "occurrenceSelectionSha256",
    "occurrenceManifestSha256",
    "buildInventorySha256",
    "staticBuildSha256",
    "occurrenceOutputManifestSha256",
    "provenanceSha256",
    "siteEventSha256",
    "sitePayloadSha256",
]);
const VERIFY_RESULT_IDENTITY_KEYS = Object.freeze([
    "buildInventorySha256",
    "buildContentIdentitySha256",
    "staticBuildSha256",
    "occurrenceOutputManifestSha256",
    "occurrenceSelectionSha256",
    "occurrenceManifestSha256",
    "provenanceSha256",
    "siteEventSha256",
    "sitePayloadSha256",
]);

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

function sha256(bytes) {
    return createHash("sha256").update(bytes).digest("hex");
}

function canonicalBytes(value) {
    return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function safeString(value, label, maximum = 4096) {
    if (
        typeof value !== "string" ||
        value.length === 0 ||
        value.length > maximum ||
        /[\u0000-\u001f\u007f]/u.test(value)
    ) {
        throw new Error(`${label} is invalid`);
    }
    return value;
}

function positiveInteger(value, label, maximum) {
    if (!Number.isSafeInteger(value) || value < 1 || value > maximum) {
        throw new Error(`${label} is invalid`);
    }
    return value;
}

function ownEnvironmentValue(environment, name, { required = true } = {}) {
    if (
        environment === null ||
        typeof environment !== "object" ||
        Array.isArray(environment) ||
        !Object.prototype.hasOwnProperty.call(environment, name)
    ) {
        if (!required) return null;
        throw new Error(`stage build environment ${name} is not configured`);
    }
    const value = environment[name];
    if (
        typeof value !== "string" ||
        value.length === 0 ||
        value.length > 16 * 1024 ||
        value.includes("\u0000")
    ) {
        throw new Error(`stage build environment ${name} is invalid`);
    }
    return value;
}

function sameStoreLocation(first, second) {
    return (
        first.kind === second.kind &&
        (first.kind === "local"
            ? first.root === second.root
            : first.bucket === second.bucket && first.prefix === second.prefix)
    );
}

function storeLocation(store) {
    const location = store?.location;
    if (location?.kind === "local" && typeof location.root === "string") {
        return location.root;
    }
    if (
        location?.kind === "s3" &&
        typeof location.bucket === "string" &&
        typeof location.prefix === "string"
    ) {
        return `s3://${location.bucket}/${location.prefix}`;
    }
    throw new Error("stage build occurrence store location is invalid");
}

async function canonicalDirectory(value, label) {
    safeString(value, label);
    if (!path.isAbsolute(value) || path.normalize(value) !== value) {
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
    return { path: value, metadata };
}

async function emptyWorkspace(value) {
    const selected = await canonicalDirectory(
        value,
        "stage build workspace root",
    );
    if ((await readdir(selected.path)).length !== 0) {
        throw new Error("stage build workspace root is not empty");
    }
    return selected;
}

async function copyEntry(sourceRoot, projectRoot, relative) {
    const source = path.join(sourceRoot, relative);
    const metadata = await lstat(source);
    if (metadata.isSymbolicLink()) {
        throw new Error(`stage build source entry is a link: ${relative}`);
    }
    await cp(source, path.join(projectRoot, relative), {
        recursive: metadata.isDirectory(),
        dereference: false,
        errorOnExist: true,
        force: false,
        preserveTimestamps: false,
    });
}

async function removeOwnedProject(projectRoot, identity) {
    let current;
    try {
        current = await lstat(projectRoot, { bigint: true });
    } catch (error) {
        if (error.code === "ENOENT") return;
        throw error;
    }
    if (
        !current.isDirectory() ||
        current.isSymbolicLink() ||
        current.dev !== identity.dev ||
        current.ino !== identity.ino
    ) {
        throw new Error("stage build refused to remove an unowned workspace");
    }
    await rm(projectRoot, { recursive: true, force: false });
}

function validateBuildRequest(request) {
    if (
        !exactKeys(request, BUILD_REQUEST_KEYS) ||
        request.contract !==
            "verdify.lab-profiled-selected-astro-build-request" ||
        request.schemaVersion !== 2 ||
        !sameDocument(request.publicationProfile, STAGE_PROFILE) ||
        request.event?.sourceSnapshotManifestSha256 !==
            request.policy?.sourceSnapshotManifestSha256
    ) {
        throw new Error("stage Astro build request is invalid");
    }
    return request;
}

function selectedChildEnvironment({
    environment,
    projectRoot,
    occurrenceStoreRoot,
    policyPath,
    allowSyntheticFixture,
}) {
    const store = parseOccurrenceReleaseStoreLocation(occurrenceStoreRoot);
    const configuredStore = parseOccurrenceReleaseStoreLocation(
        ownEnvironmentValue(environment, "LAB_OCCURRENCE_STORE"),
    );
    if (!sameStoreLocation(store, configuredStore)) {
        throw new Error(
            "stage build occurrence store differs from its explicit binding",
        );
    }
    const result = {
        PATH:
            ownEnvironmentValue(environment, "PATH", { required: false }) ??
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        HOME: path.join(projectRoot, ".home"),
        TMPDIR: path.join(projectRoot, ".tmp"),
        NODE_ENV: "production",
        LAB_SNAPSHOT: path.join(projectRoot, ".snapshot"),
        ALLOW_SYNTHETIC_FIXTURE: allowSyntheticFixture ? "true" : "false",
        SITE_ORIGIN: STAGE_PROFILE.siteOrigin,
        STAGE_GLOBAL_NOINDEX: "true",
        LAB_OCCURRENCE_STORE: occurrenceStoreRoot,
        LAB_OCCURRENCE_POLICY: policyPath,
    };
    if (store.kind === "s3") {
        for (const name of [
            "LAB_S3_ENDPOINT_URL",
            "AWS_DEFAULT_REGION",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
        ]) {
            result[name] = ownEnvironmentValue(environment, name);
        }
    }
    return result;
}

async function command(runCommand, common, label, script, args = []) {
    await runCommand({
        ...common,
        label,
        executable: common.nodeExecutable,
        arguments: [script, ...args],
    });
}

/**
 * Build a selected, global-noindex Lab stage tree from the immutable source
 * and snapshot packaged in the occurrence-exporter image.
 */
export function createStageAstroBuildOperation({
    sourceRoot,
    snapshotRoot = path.join(sourceRoot ?? "", ".snapshot"),
    nodeModulesRoot = path.join(sourceRoot ?? "", "node_modules"),
    environment,
    allowSyntheticFixture = false,
    nodeExecutable = process.execPath,
    timeoutMs = 15 * 60 * 1000,
    terminationGraceMs = 10 * 1000,
    runCommand = runBoundedChildProcess,
} = {}) {
    safeString(sourceRoot, "stage build packaged source root");
    safeString(snapshotRoot, "stage build packaged snapshot root");
    safeString(nodeModulesRoot, "stage build Node modules root");
    safeString(nodeExecutable, "stage build Node executable");
    if (typeof allowSyntheticFixture !== "boolean") {
        throw new Error("stage build fixture flag is invalid");
    }
    positiveInteger(timeoutMs, "stage build command timeout", 60 * 60 * 1000);
    positiveInteger(
        terminationGraceMs,
        "stage build termination grace",
        60 * 1000,
    );
    if (typeof runCommand !== "function") {
        throw new Error("stage build command runner is invalid");
    }

    return {
        contract: "verdify.lab-selected-astro-build-operation",
        schemaVersion: 2,
        operationSha256: occurrenceSiteOperationSha256(
            "build",
            STAGE_PROFILE,
        ),
        publicationProfile: structuredClone(STAGE_PROFILE),
        async build(rawRequest) {
            const request = validateBuildRequest(rawRequest);
            const workspace = await emptyWorkspace(request.workspaceRoot);
            const source = await canonicalDirectory(
                path.resolve(sourceRoot),
                "stage build packaged source root",
            );
            const snapshot = await canonicalDirectory(
                path.resolve(snapshotRoot),
                "stage build packaged snapshot root",
            );
            const modules = await canonicalDirectory(
                path.resolve(nodeModulesRoot),
                "stage build Node modules root",
            );
            const projectRoot = path.join(workspace.path, "site-astro");
            await mkdir(projectRoot, { mode: 0o700 });
            const projectIdentity = await lstat(projectRoot, { bigint: true });
            let completed = false;
            try {
                for (const relative of SOURCE_ENTRIES) {
                    await copyEntry(source.path, projectRoot, relative);
                }
                await cp(snapshot.path, path.join(projectRoot, ".snapshot"), {
                    recursive: true,
                    dereference: false,
                    errorOnExist: true,
                    force: false,
                    preserveTimestamps: false,
                });
                await symlink(
                    modules.path,
                    path.join(projectRoot, "node_modules"),
                    "dir",
                );
                await Promise.all([
                    mkdir(path.join(projectRoot, ".home"), { mode: 0o700 }),
                    mkdir(path.join(projectRoot, ".tmp"), { mode: 0o700 }),
                    mkdir(path.join(projectRoot, ".runtime"), { mode: 0o700 }),
                ]);
                const policyPath = path.join(
                    projectRoot,
                    ".runtime",
                    "occurrence-policy.json",
                );
                await writeFile(policyPath, canonicalBytes(request.policy), {
                    flag: "wx",
                    mode: 0o600,
                });
                const occurrenceStoreRoot = storeLocation(
                    request.occurrenceStore,
                );
                const childEnvironment = selectedChildEnvironment({
                    environment,
                    projectRoot,
                    occurrenceStoreRoot,
                    policyPath,
                    allowSyntheticFixture,
                });
                const common = {
                    cwd: projectRoot,
                    environment: childEnvironment,
                    nodeExecutable,
                    timeoutMs,
                    terminationGraceMs,
                };
                await command(
                    runCommand,
                    common,
                    "prepare reviewed site shell",
                    path.join(projectRoot, "scripts", "prepare-site-shell.mjs"),
                );
                await command(
                    runCommand,
                    common,
                    "compile selected stage snapshot",
                    path.join(
                        projectRoot,
                        "scripts",
                        "run-selected-stage-compile.mjs",
                    ),
                );
                await command(
                    runCommand,
                    common,
                    "Astro selected-stage diagnostics",
                    path.join(
                        projectRoot,
                        "node_modules",
                        "astro",
                        "bin",
                        "astro.mjs",
                    ),
                    ["check"],
                );
                await command(
                    runCommand,
                    common,
                    "Astro selected-stage build",
                    path.join(
                        projectRoot,
                        "node_modules",
                        "astro",
                        "bin",
                        "astro.mjs",
                    ),
                    ["build"],
                );
                await command(
                    runCommand,
                    common,
                    "finalize selected-stage routes",
                    path.join(projectRoot, "scripts", "finalize-output.mjs"),
                );
                await command(
                    runCommand,
                    common,
                    "index selected-stage Pagefind",
                    path.join(
                        projectRoot,
                        "node_modules",
                        "pagefind",
                        "lib",
                        "runner",
                        "bin.cjs",
                    ),
                    ["--site", "dist"],
                );
                await command(
                    runCommand,
                    common,
                    "prune selected-stage Pagefind output",
                    path.join(
                        projectRoot,
                        "scripts",
                        "prune-pagefind-output.mjs",
                    ),
                );
                await command(
                    runCommand,
                    common,
                    "verify selected global-noindex stage output",
                    path.join(projectRoot, "scripts", "verify-static.mjs"),
                );
                completed = true;
                return {
                    contract: "verdify.lab-selected-astro-build-result",
                    schemaVersion: 1,
                    buildRoot: path.join(projectRoot, "dist"),
                };
            } finally {
                if (!completed) {
                    await removeOwnedProject(projectRoot, projectIdentity);
                }
            }
        },
    };
}

function validateVerificationRequest(request) {
    if (
        !exactKeys(request, VERIFY_REQUEST_KEYS) ||
        request.contract !== "verdify.lab-site-output-verification-request" ||
        request.schemaVersion !== 2 ||
        request.siteOrigin !== STAGE_PROFILE.siteOrigin ||
        request.stageGlobalNoindex !== true ||
        request.policyVersion !== STAGE_PROFILE.policyVersion ||
        VERIFY_RESULT_IDENTITY_KEYS.some(
            (name) => !SHA256_RE.test(request[name]),
        )
    ) {
        throw new Error("stage output verification request is invalid");
    }
    return request;
}

async function assertFileDigest(file, expected, label) {
    const bytes = await readFile(file);
    if (sha256(bytes) !== expected) {
        throw new Error(`${label} differs from its bound digest`);
    }
}

export function createStageOutputVerificationOperation({
    environment = {},
    nodeExecutable = process.execPath,
    timeoutMs = 15 * 60 * 1000,
    terminationGraceMs = 10 * 1000,
    runCommand = runBoundedChildProcess,
} = {}) {
    safeString(nodeExecutable, "stage verifier Node executable");
    positiveInteger(timeoutMs, "stage verifier timeout", 60 * 60 * 1000);
    positiveInteger(
        terminationGraceMs,
        "stage verifier termination grace",
        60 * 1000,
    );
    if (typeof runCommand !== "function") {
        throw new Error("stage verifier command runner is invalid");
    }
    return {
        contract: "verdify.lab-site-output-verification-operation",
        schemaVersion: 2,
        operationSha256: occurrenceSiteOperationSha256(
            "verification",
            STAGE_PROFILE,
        ),
        publicationProfile: structuredClone(STAGE_PROFILE),
        async verify(rawRequest) {
            const request = validateVerificationRequest(rawRequest);
            const build = await canonicalDirectory(
                path.resolve(request.buildRoot),
                "stage verifier build root",
            );
            if (path.basename(build.path) !== "dist") {
                throw new Error("stage verifier build root is not Astro dist");
            }
            const projectRoot = path.dirname(build.path);
            const project = await canonicalDirectory(
                projectRoot,
                "stage verifier project root",
            );
            if (!build.path.startsWith(`${project.path}${path.sep}`)) {
                throw new Error("stage verifier build root escapes its project");
            }
            await runCommand({
                label: "verify published global-noindex stage output",
                executable: nodeExecutable,
                arguments: [
                    path.join(project.path, "scripts", "verify-static.mjs"),
                ],
                cwd: project.path,
                environment: {
                    PATH:
                        ownEnvironmentValue(environment, "PATH", {
                            required: false,
                        }) ??
                        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                    HOME: path.join(project.path, ".home"),
                    TMPDIR: path.join(project.path, ".tmp"),
                    NODE_ENV: "production",
                    SITE_ORIGIN: STAGE_PROFILE.siteOrigin,
                    STAGE_GLOBAL_NOINDEX: "true",
                },
                timeoutMs,
                terminationGraceMs,
            });
            await Promise.all([
                assertFileDigest(
                    path.join(build.path, "static-build.json"),
                    request.staticBuildSha256,
                    "stage static build record",
                ),
                assertFileDigest(
                    path.join(build.path, "occurrence-manifest.json"),
                    request.occurrenceOutputManifestSha256,
                    "stage occurrence output manifest",
                ),
                assertFileDigest(
                    path.join(
                        build.path,
                        "occurrence-publish-provenance.json",
                    ),
                    request.provenanceSha256,
                    "stage occurrence publication provenance",
                ),
            ]);
            return {
                contract: "verdify.lab-site-output-verification-result",
                schemaVersion: 2,
                siteOrigin: STAGE_PROFILE.siteOrigin,
                stageGlobalNoindex: true,
                policyVersion: STAGE_PROFILE.policyVersion,
                ...Object.fromEntries(
                    VERIFY_RESULT_IDENTITY_KEYS.map((name) => [
                        name,
                        request[name],
                    ]),
                ),
            };
        },
    };
}

export const occurrenceSiteStageOperationsContract = Object.freeze({
    contract: "verdify.lab-stage-occurrence-site-operations",
    schemaVersion: 1,
    publicationProfile: STAGE_PROFILE,
    sourceEntries: SOURCE_ENTRIES,
    processTermination: Object.freeze(["SIGTERM", "SIGKILL"]),
    defaults: Object.freeze({
        allowSyntheticFixture: false,
        timeoutMs: 15 * 60 * 1000,
        terminationGraceMs: 10 * 1000,
    }),
});
