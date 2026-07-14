import path from "node:path";
import { pathToFileURL } from "node:url";

import { main as compileSnapshot } from "./compile-snapshot.mjs";
import { parseOccurrenceReleaseStoreLocation } from "./lib/occurrence-release-store.mjs";
import { createOccurrenceReleaseReaderStore } from "./lib/runtime-s3-binding.mjs";

const S3_ENVIRONMENT_NAMES = Object.freeze([
    "LAB_S3_ENDPOINT_URL",
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
]);

function requiredEnvironment(name) {
    const value = process.env[name];
    if (
        typeof value !== "string" ||
        value.length === 0 ||
        value.length > 16 * 1024 ||
        value.includes("\u0000")
    ) {
        throw new Error(`selected-stage compiler ${name} is invalid`);
    }
    return value;
}

export async function runSelectedStageCompile() {
    const storeRoot = requiredEnvironment("LAB_OCCURRENCE_STORE");
    const location = parseOccurrenceReleaseStoreLocation(storeRoot);
    const occurrenceStoreFactory =
        location.kind === "local"
            ? null
            : (selectedRoot) =>
                  createOccurrenceReleaseReaderStore(selectedRoot, {
                      environment: Object.fromEntries(
                          S3_ENVIRONMENT_NAMES.map((name) => [
                              name,
                              requiredEnvironment(name),
                          ]),
                      ),
                  });
    await compileSnapshot({ occurrenceStoreFactory });
}

const executablePath = process.argv[1]
    ? pathToFileURL(path.resolve(process.argv[1])).href
    : null;
if (executablePath === import.meta.url) {
    runSelectedStageCompile().catch((error) => {
        process.stderr.write(`run-selected-stage-compile: ${error.message}\n`);
        process.exitCode = 1;
    });
}
