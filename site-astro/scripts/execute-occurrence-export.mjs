import path from "node:path";
import { pathToFileURL } from "node:url";

import { executeOccurrenceExportBatch } from "./lib/occurrence-export-caller.mjs";
import { createOccurrenceExportStoreOperations } from "./lib/occurrence-export-operation-adapter.mjs";
import {
    readCanonicalExportDocument,
    validateOccurrenceExportPolicy,
    validatePolicyManifestBinding,
} from "./lib/occurrence-export-contract.mjs";
import { createOccurrenceReleaseStore } from "./lib/occurrence-release-store.mjs";

function usage() {
    return [
        "Usage:",
        "  node scripts/execute-occurrence-export.mjs execute --manifest MANIFEST --policy POLICY --batch BATCH --graph-result GRAPH_RESULT --source SOURCE_ROOT --store STORE_LOCATION",
        "",
        "The execute command is mutation-capable and requires a canonical source-activated policy.",
        "STORE_LOCATION must be an explicit canonical local path or s3://bucket/non-empty-prefix URI.",
    ].join("\n");
}

function options(argv) {
    const result = { command: argv[0] ?? "", values: new Map() };
    for (let index = 1; index < argv.length; index += 2) {
        const key = argv[index];
        const value = argv[index + 1];
        if (
            !key?.startsWith("--") ||
            value === undefined ||
            result.values.has(key)
        )
            throw new Error("invalid command arguments");
        result.values.set(key, value);
    }
    return result;
}

function exactOptions(values) {
    const names = [
        "--manifest",
        "--policy",
        "--batch",
        "--graph-result",
        "--source",
        "--store",
    ];
    if (
        values.size !== names.length ||
        names.some((name) => !values.has(name))
    ) {
        throw new Error(usage());
    }
}

function canonical(value) {
    return `${JSON.stringify(value, null, 2)}\n`;
}

export async function runOccurrenceExportCli(
    argv,
    {
        readDocument = readCanonicalExportDocument,
        createStore = createOccurrenceReleaseStore,
        createOperations = createOccurrenceExportStoreOperations,
        executeBatch = executeOccurrenceExportBatch,
        now = () => new Date().toISOString(),
    } = {},
) {
    const { command, values } = options(argv);
    if (command !== "execute") throw new Error(usage());
    exactOptions(values);

    // Read and validate every authority-bearing local document before store
    // construction. In particular, a blocked policy cannot initialize even a
    // local store or instantiate an S3 client.
    const manifest = await readDocument(
        values.get("--manifest"),
        "static occurrence manifest",
    );
    const policy = await readDocument(
        values.get("--policy"),
        "occurrence export policy",
    );
    const batch = await readDocument(
        values.get("--batch"),
        "occurrence export batch",
    );
    const graphResult = await readDocument(
        values.get("--graph-result"),
        "graph export result",
    );
    validateOccurrenceExportPolicy(policy.document);
    if (
        policy.document.activation.state !== "active" ||
        policy.document.activation.activatedBy !== "direct-task" ||
        !policy.document.activation.activatedAt
    )
        throw new Error(
            "occurrence export execution is disabled by the supplied policy",
        );
    validatePolicyManifestBinding(
        policy.document,
        manifest.document,
        manifest.sha256,
    );

    const store = createStore(values.get("--store"));
    const operations = await createOperations({
        store,
        sourceRoot: values.get("--source"),
    });
    return executeBatch({
        policy: policy.document,
        manifest: manifest.document,
        manifestSha256: manifest.sha256,
        batch: batch.document,
        graphResult: graphResult.document,
        sourceRoot: path.resolve(values.get("--source")),
        processingAt: now(),
        operations,
    });
}

async function main() {
    const result = await runOccurrenceExportCli(process.argv.slice(2));
    process.stdout.write(canonical(result));
    if (result.status !== "selected") process.exitCode = 1;
}

const invokedAs = process.argv[1]
    ? pathToFileURL(path.resolve(process.argv[1])).href
    : null;
if (invokedAs === import.meta.url) {
    main().catch((error) => {
        process.stderr.write(`execute-occurrence-export: ${error.message}\n`);
        process.exitCode = 1;
    });
}
