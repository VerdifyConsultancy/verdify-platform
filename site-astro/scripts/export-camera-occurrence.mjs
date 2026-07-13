import { pathToFileURL } from "node:url";

import {
  captureCameraOccurrence,
} from "./lib/camera-export-producer.mjs";
import { readCanonicalExportDocument } from "./lib/occurrence-export-contract.mjs";

function usage() {
  return "Usage: node scripts/export-camera-occurrence.mjs capture --policy POLICY --request REQUEST --output-root OUTPUT_ROOT [--timeout-ms MILLISECONDS]";
}

function options(argv) {
  const command = argv[0] ?? "";
  const values = new Map();
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined || values.has(key)) throw new Error("invalid command arguments");
    values.set(key, value);
  }
  return { command, values };
}

function validateOptions(command, values) {
  const required = ["--policy", "--request", "--output-root"];
  const allowed = new Set([...required, "--timeout-ms"]);
  if (
    command !== "capture"
    || required.some((name) => !values.has(name))
    || [...values.keys()].some((name) => !allowed.has(name))
  ) throw new Error(usage());
}

function canonical(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

export async function runCameraExportCli(
  argv,
  {
    transport,
    now = () => new Date().toISOString(),
    stdout = process.stdout,
  } = {},
) {
  const { command, values } = options(argv);
  validateOptions(command, values);
  const policy = await readCanonicalExportDocument(values.get("--policy"), "occurrence export policy");
  const request = await readCanonicalExportDocument(values.get("--request"), "camera export request");
  const timeoutText = values.get("--timeout-ms");
  const timeoutMs = timeoutText === undefined || !/^\d+$/.test(timeoutText)
    ? timeoutText === undefined ? undefined : Number.NaN
    : Number(timeoutText);
  const result = await captureCameraOccurrence({
    policy: policy.document,
    request: request.document,
    outputRoot: values.get("--output-root"),
    now,
    ...(transport === undefined ? {} : { transport }),
    ...(timeoutMs === undefined ? {} : { timeoutMs }),
  });
  stdout.write(canonical(result));
  return result;
}

const executedDirectly = process.argv[1]
  && import.meta.url === pathToFileURL(process.argv[1]).href;

if (executedDirectly) {
  runCameraExportCli(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`export-camera-occurrence: ${error.message}\n`);
    process.exitCode = 1;
  });
}
