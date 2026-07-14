import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

import {
  cameraExportProducerContract,
  graphExportProducerContract,
  occurrenceProducerRunnerContract,
} from "./lib/occurrence-producer-contracts.mjs";

function requireContract(condition, label) {
  if (!condition) throw new Error(`packaged occurrence exporter ${label} contract is invalid`);
}

const METADATA_PATH = "/app/occurrence-exporter-image.json";
const SOURCE_REVISION_RE = /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/u;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u;

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).join(",") === keys.join(",");
}

function validateMetadata(metadata) {
  requireContract(
    exactKeys(metadata, ["contract", "schemaVersion", "builderCommit", "releasedAt"])
      && metadata.contract === "verdify.lab-occurrence-exporter-image-metadata"
      && metadata.schemaVersion === 1
      && SOURCE_REVISION_RE.test(metadata.builderCommit)
      && ISO_INSTANT_RE.test(metadata.releasedAt),
    "source metadata",
  );
  const parsed = Date.parse(metadata.releasedAt);
  const normalized = Number.isFinite(parsed) ? new Date(parsed).toISOString() : "";
  const expected = metadata.releasedAt.includes(".") ? normalized : normalized.replace(".000Z", "Z");
  requireContract(metadata.releasedAt === expected, "source metadata time");
  return metadata;
}

export function verifyOccurrenceExporterImage(metadata) {
  validateMetadata(metadata);
  requireContract(
    graphExportProducerContract.expectedGraphCount === 143
      && graphExportProducerContract.renderer.contract === "verdify.lab-graph-renderer"
      && graphExportProducerContract.renderer.schemaVersion === 3
      && graphExportProducerContract.renderer.sourceClass === "operator-owned-reporting-tier"
      && graphExportProducerContract.renderer.anonymousAccess === false,
    "graph producer",
  );
  requireContract(
    cameraExportProducerContract.approvedOccurrenceIds.length === 2
      && new Set(cameraExportProducerContract.approvedOccurrenceIds).size === 2
      && cameraExportProducerContract.defaultTimeoutMs > 0
      && cameraExportProducerContract.defaultTimeoutMs <= cameraExportProducerContract.maxTimeoutMs,
    "camera producer",
  );
  requireContract(
    occurrenceProducerRunnerContract.expectedGraphCount === 143
      && occurrenceProducerRunnerContract.expectedCurrentMediaCount === 2
      && occurrenceProducerRunnerContract.expectedLegacyOverrideCount
        + occurrenceProducerRunnerContract.expectedReportingDefaultCount === 143
      && occurrenceProducerRunnerContract.result.contract === "verdify.lab-occurrence-producer-run"
      && occurrenceProducerRunnerContract.result.schemaVersion === 1,
    "runner",
  );

  return Object.freeze({
    contract: "verdify.lab-occurrence-exporter-image-status",
    schemaVersion: 1,
    status: "packaged",
    runtime: "runtime-unbound",
    source: Object.freeze({
      builderCommit: metadata.builderCommit,
      releasedAt: metadata.releasedAt,
    }),
    authorities: Object.freeze({
      release: "explicit-stage-only",
      device: "none",
      live: "none",
    }),
    operations: Object.freeze({
      network: "not-invoked",
      store: "not-invoked",
      capture: "not-invoked",
      render: "not-invoked",
    }),
    producerContracts: Object.freeze({
      graphs: "verified-143",
      cameras: "verified-2",
      runner: "verified-143-plus-2",
    }),
  });
}

export async function verifyPackagedOccurrenceExporterImage(metadataPath = METADATA_PATH) {
  const bytes = await readFile(metadataPath);
  requireContract(bytes.length > 0 && bytes.length <= 1024, "source metadata bytes");
  let metadata;
  try {
    metadata = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("packaged occurrence exporter source metadata is not JSON");
  }
  requireContract(
    Buffer.from(`${JSON.stringify(metadata, null, 2)}\n`).compare(bytes) === 0,
    "source metadata canonical bytes",
  );
  return verifyOccurrenceExporterImage(metadata);
}

async function main() {
  process.stdout.write(`${JSON.stringify(await verifyPackagedOccurrenceExporterImage(), null, 2)}\n`);
}

const executablePath = process.argv[1];
if (executablePath && import.meta.url === pathToFileURL(executablePath).href) {
  main().catch((error) => {
    process.stderr.write(`verify-occurrence-exporter-image: ${error.message}\n`);
    process.exitCode = 1;
  });
}
