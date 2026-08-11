import path from "node:path";
import { pathToFileURL } from "node:url";

import { readCanonicalExportDocument } from "./lib/occurrence-export-contract.mjs";
import { runOccurrenceSitePublisherDelivery } from "./lib/occurrence-site-publisher-runner.mjs";

function usage() {
    return [
        "Usage:",
        "  node scripts/execute-occurrence-site-publish.mjs execute --event EVENT --producer-result PRODUCER_RESULT --policy POLICY --manifest MANIFEST --candidate-root CANDIDATE_ROOT --workspace-root WORKSPACE_ROOT",
        "",
        "The execute command is mutation-capable and requires a canonical source-activated policy.",
        "The source-only executable has no default runtime, store, endpoint, credential, or network client.",
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
        ) {
            throw new Error("invalid command arguments");
        }
        result.values.set(key, value);
    }
    return result;
}

function exactOptions(values) {
    const names = [
        "--event",
        "--producer-result",
        "--policy",
        "--manifest",
        "--candidate-root",
        "--workspace-root",
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

export async function runOccurrenceSitePublishCli(
    argv,
    {
        readDocument = readCanonicalExportDocument,
        createRuntime = null,
        runDelivery = runOccurrenceSitePublisherDelivery,
        processEvent,
    } = {},
) {
    const { command, values } = options(argv);
    if (command !== "execute") throw new Error(usage());
    exactOptions(values);

    const [event, producerResult, policy, manifest] = await Promise.all([
        readDocument(values.get("--event"), "occurrence site event"),
        readDocument(
            values.get("--producer-result"),
            "occurrence producer result",
        ),
        readDocument(values.get("--policy"), "occurrence export policy"),
        readDocument(values.get("--manifest"), "static occurrence manifest"),
    ]);
    return runDelivery(
        {
            event,
            producerResult,
            policy,
            manifest,
            candidateRoot: path.resolve(values.get("--candidate-root")),
            workspaceRoot: path.resolve(values.get("--workspace-root")),
        },
        { createRuntime, processEvent },
    );
}

async function main() {
    const result = await runOccurrenceSitePublishCli(process.argv.slice(2));
    process.stdout.write(canonical(result));
}

const invokedAs = process.argv[1]
    ? pathToFileURL(path.resolve(process.argv[1])).href
    : null;
if (invokedAs === import.meta.url) {
    main().catch((error) => {
        process.stderr.write(
            `execute-occurrence-site-publish: ${error.message}\n`,
        );
        process.exitCode = 1;
    });
}
