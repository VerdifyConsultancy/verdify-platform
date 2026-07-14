import { lstat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { runOccurrenceSitePublishCli } from "./execute-occurrence-site-publish.mjs";
import { readCanonicalExportDocument } from "./lib/occurrence-export-contract.mjs";
import {
    createStageAstroBuildOperation,
    createStageOutputVerificationOperation,
} from "./lib/occurrence-site-stage-operations.mjs";
import { createOccurrenceSiteStageRuntimeFactory } from "./lib/occurrence-site-stage-runtime.mjs";

const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_SOURCE_ROOT = path.resolve(SCRIPT_ROOT, "..");
const PACKAGED_POLICY = path.join(
    "config",
    "lab-stage-occurrence-export-policy.json",
);
const EXECUTE_OPTIONS = Object.freeze([
    "--event",
    "--producer-result",
    "--policy",
    "--manifest",
    "--candidate-root",
    "--workspace-root",
]);

async function directoryExists(value) {
    try {
        const metadata = await lstat(value);
        return metadata.isDirectory() && !metadata.isSymbolicLink();
    } catch (error) {
        if (error.code === "ENOENT") return false;
        throw error;
    }
}

export async function resolvePackagedStagePublisherPaths(
    sourceRoot = DEFAULT_SOURCE_ROOT,
) {
    const canonicalSource = path.resolve(sourceRoot);
    const candidates = [
        path.join(canonicalSource, "node_modules"),
        path.resolve(canonicalSource, "..", "node_modules"),
    ];
    for (const nodeModulesRoot of candidates) {
        if (await directoryExists(nodeModulesRoot)) {
            return {
                sourceRoot: canonicalSource,
                snapshotRoot: path.join(canonicalSource, ".snapshot"),
                nodeModulesRoot,
            };
        }
    }
    throw new Error("packaged stage publisher Node modules are unavailable");
}

function policyArgument(argv) {
    if (
        !Array.isArray(argv) ||
        argv[0] !== "execute" ||
        argv.length !== 1 + EXECUTE_OPTIONS.length * 2
    ) {
        throw new Error("stage publisher arguments are invalid");
    }
    const values = new Map();
    for (let index = 1; index < argv.length; index += 2) {
        const name = argv[index];
        const value = argv[index + 1];
        if (
            !EXECUTE_OPTIONS.includes(name) ||
            typeof value !== "string" ||
            value.length === 0 ||
            values.has(name)
        ) {
            throw new Error("stage publisher arguments are invalid");
        }
        values.set(name, value);
    }
    if (EXECUTE_OPTIONS.some((name) => !values.has(name))) {
        throw new Error("stage publisher arguments are invalid");
    }
    return values.get("--policy");
}

/** Bind a delivery to the exact canonical policy packaged in this image. */
export async function assertPackagedStagePolicy(argv, sourceRoot) {
    const selectedPolicyPath = policyArgument(argv);
    const packagedPolicyPath = path.join(
        path.resolve(sourceRoot),
        PACKAGED_POLICY,
    );
    const [packaged, selected] = await Promise.all([
        readCanonicalExportDocument(
            packagedPolicyPath,
            "packaged stage occurrence policy",
        ),
        readCanonicalExportDocument(
            selectedPolicyPath,
            "selected stage occurrence policy",
        ),
    ]);
    if (selected.sha256 !== packaged.sha256) {
        throw new Error(
            "selected stage occurrence policy does not match the packaged policy",
        );
    }
    return packaged.sha256;
}

/** Construct the approved stage runtime without invoking an operation. */
export function createExecutableStagePublisherRuntime({
    environment,
    sourceRoot,
    snapshotRoot,
    nodeModulesRoot,
    nodeExecutable = process.execPath,
    timeoutMs,
    terminationGraceMs,
    clientFactory,
} = {}) {
    const buildOperation = createStageAstroBuildOperation({
        sourceRoot,
        snapshotRoot,
        nodeModulesRoot,
        environment,
        nodeExecutable,
        ...(timeoutMs === undefined ? {} : { timeoutMs }),
        ...(terminationGraceMs === undefined
            ? {}
            : { terminationGraceMs }),
    });
    const verificationOperation = createStageOutputVerificationOperation({
        environment,
        nodeExecutable,
        ...(timeoutMs === undefined ? {} : { timeoutMs }),
        ...(terminationGraceMs === undefined
            ? {}
            : { terminationGraceMs }),
    });
    return createOccurrenceSiteStageRuntimeFactory({
        environment,
        buildOperation,
        verificationOperation,
        ...(clientFactory === undefined ? {} : { clientFactory }),
    });
}

export async function runExecutableStagePublisher(
    argv,
    {
        environment,
        paths = null,
        createRuntime = createExecutableStagePublisherRuntime,
        runCli = runOccurrenceSitePublishCli,
    } = {},
) {
    if (typeof createRuntime !== "function" || typeof runCli !== "function") {
        throw new Error("stage publisher composition is invalid");
    }
    const selectedPaths =
        paths ?? (await resolvePackagedStagePublisherPaths());
    await assertPackagedStagePolicy(argv, selectedPaths.sourceRoot);
    const runtime = createRuntime({
        environment,
        ...selectedPaths,
    });
    return runCli(argv, { createRuntime: runtime });
}

async function main() {
    const result = await runExecutableStagePublisher(process.argv.slice(2), {
        environment: process.env,
    });
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

const executablePath = process.argv[1]
    ? pathToFileURL(path.resolve(process.argv[1])).href
    : null;
if (executablePath === import.meta.url) {
    main().catch((error) => {
        process.stderr.write(
            `run-stage-occurrence-site-publisher: ${error.message}\n`,
        );
        process.exitCode = 1;
    });
}
