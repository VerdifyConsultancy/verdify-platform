import { lstat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { runOccurrenceSitePublishCli } from "./execute-occurrence-site-publish.mjs";
import {
    createStageAstroBuildOperation,
    createStageOutputVerificationOperation,
} from "./lib/occurrence-site-stage-operations.mjs";
import { createOccurrenceSiteStageRuntimeFactory } from "./lib/occurrence-site-stage-runtime.mjs";

const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_SOURCE_ROOT = path.resolve(SCRIPT_ROOT, "..");

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
