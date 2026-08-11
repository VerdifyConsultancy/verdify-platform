import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import {
  draftBlockedOccurrenceExportPolicy,
  inspectOccurrenceExportCandidates,
  prepareOccurrenceExportRequests,
  readCanonicalExportDocument,
  validatePolicyManifestBinding,
} from "./lib/occurrence-export-contract.mjs";

function usage() {
  return [
    "Usage:",
    "  node scripts/prepare-occurrence-export.mjs draft-policy --manifest MANIFEST --policy-version VERSION --validated-at ISO --camera-map MAP",
    "  node scripts/prepare-occurrence-export.mjs validate --manifest MANIFEST --policy POLICY --batch BATCH --source SOURCE_ROOT",
    "  node scripts/prepare-occurrence-export.mjs prepare --manifest MANIFEST --policy POLICY --batch BATCH --source SOURCE_ROOT --store STORE_ROOT --output OUTPUT_ROOT",
  ].join("\n");
}

function options(argv) {
  const result = { command: argv[0] ?? "", values: new Map() };
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined || result.values.has(key)) throw new Error("invalid command arguments");
    result.values.set(key, value);
  }
  return result;
}

function exactOptions(values, names) {
  if (values.size !== names.length || names.some((name) => !values.has(name))) throw new Error(usage());
}

function canonical(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

async function documents(values, includeBatch = true) {
  const manifest = await readCanonicalExportDocument(values.get("--manifest"), "static occurrence manifest");
  const policy = await readCanonicalExportDocument(values.get("--policy"), "occurrence export policy");
  const batch = includeBatch
    ? await readCanonicalExportDocument(values.get("--batch"), "occurrence export batch")
    : null;
  return { manifest, policy, batch };
}

async function writePreparedOutput(outputRoot, prepared, batchId) {
  const root = path.resolve(outputRoot);
  await mkdir(root, { mode: 0o700 });
  const mediaDirectory = path.join(root, "media");
  await mkdir(mediaDirectory, { mode: 0o700 });
  const mediaFiles = [];
  for (const request of prepared.mediaRequests) {
    const relative = `media/${request.occurrence.occurrenceId}.request.json`;
    await writeFile(path.join(root, ...relative.split("/")), canonical(request), { flag: "wx", mode: 0o600 });
    mediaFiles.push(relative);
  }
  await writeFile(path.join(root, "release.request.json"), canonical(prepared.releaseRequest), { flag: "wx", mode: 0o600 });
  const summary = {
    contract: "verdify.lab-prepared-occurrence-export",
    schemaVersion: 1,
    batchId,
    feedFreshness: prepared.feedFreshness,
    executionOrder: [...mediaFiles, "release.request.json"],
    mediaRequestCount: mediaFiles.length,
    graphCount: prepared.releaseRequest.graphs.length,
    currentMediaCount: prepared.releaseRequest.currentMedia.length,
  };
  await writeFile(path.join(root, "summary.json"), canonical(summary), { flag: "wx", mode: 0o600 });
  return summary;
}

async function main() {
  const { command, values } = options(process.argv.slice(2));
  if (command === "draft-policy") {
    exactOptions(values, ["--manifest", "--policy-version", "--validated-at", "--camera-map"]);
    const manifest = await readCanonicalExportDocument(values.get("--manifest"), "static occurrence manifest");
    const cameraMap = await readCanonicalExportDocument(values.get("--camera-map"), "camera upstream map");
    if (
      Object.keys(cameraMap.document).join(",") !== "contract,schemaVersion,sources"
      || cameraMap.document.contract !== "verdify.lab-camera-upstream-map"
      || cameraMap.document.schemaVersion !== 1
      || !Array.isArray(cameraMap.document.sources)
    ) throw new Error("camera upstream map does not use the closed v1 shape");
    const policy = draftBlockedOccurrenceExportPolicy({
      manifest: manifest.document,
      manifestSha256: manifest.sha256,
      policyVersion: values.get("--policy-version"),
      activatedAt: values.get("--validated-at"),
      cameraSources: cameraMap.document.sources,
    });
    process.stdout.write(canonical(policy));
    return;
  }
  if (command === "validate") {
    exactOptions(values, ["--manifest", "--policy", "--batch", "--source"]);
    const { manifest, policy, batch } = await documents(values);
    const discovered = validatePolicyManifestBinding(policy.document, manifest.document, manifest.sha256);
    const inspected = await inspectOccurrenceExportCandidates({
      policy: policy.document,
      batch: batch.document,
      sourceRoot: values.get("--source"),
    });
    process.stdout.write(canonical({
      contract: "verdify.lab-occurrence-export-validation",
      schemaVersion: 1,
      policyVersion: policy.document.policyVersion,
      activationState: policy.document.activation.state,
      activationEligible: policy.document.activation.state === "active",
      sourceOccurrenceManifestSha256: manifest.sha256,
      feedFreshness: inspected.feedFreshness,
      graphCount: discovered.graphs.length,
      graphCandidateCount: [...inspected.graphCandidates.values()].filter(Boolean).length,
      currentMediaCount: discovered.currentMedia.length,
      currentMediaCandidateCount: [...inspected.currentMediaCandidates.values()].filter(Boolean).length,
    }));
    return;
  }
  if (command === "prepare") {
    exactOptions(values, ["--manifest", "--policy", "--batch", "--source", "--store", "--output"]);
    const { manifest, policy, batch } = await documents(values);
    const prepared = await prepareOccurrenceExportRequests({
      policy: policy.document,
      manifest: manifest.document,
      manifestSha256: manifest.sha256,
      batch: batch.document,
      sourceRoot: values.get("--source"),
      storeRoot: values.get("--store"),
    });
    const summary = await writePreparedOutput(values.get("--output"), prepared, batch.document.batchId);
    process.stdout.write(canonical(summary));
    return;
  }
  throw new Error(usage());
}

main().catch((error) => {
  process.stderr.write(`prepare-occurrence-export: ${error.message}\n`);
  process.exitCode = 1;
});
