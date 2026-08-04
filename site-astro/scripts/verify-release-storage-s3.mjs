#!/usr/bin/env node

import { randomUUID } from "node:crypto";

import {
  releaseStoragePassOneContract,
} from "./lib/release-storage-s3-coordinator.mjs";
import {
  ReleaseStorageS3ActivationProofError,
  proveReleaseStorageS3Activation,
  releaseStorageS3ActivationProofContract,
} from "./lib/release-storage-s3-proof.mjs";
import {
  createOccurrenceReleaseWriterStore,
  createRuntimeS3ObjectStore,
  createSiteReleaseWriterStore,
} from "./lib/runtime-s3-binding.mjs";

const CONTROL_RE = /[\u0000-\u001f\u007f]/u;
const ENVIRONMENT_NAMES = [
  "LAB_RELEASE_STORE",
  "LAB_OCCURRENCE_STORE",
  "LAB_RELEASE_COORDINATION_STORE",
  "LAB_S3_ENDPOINT_URL",
  "AWS_DEFAULT_REGION",
  "AWS_ACCESS_KEY_ID",
  "AWS_SECRET_ACCESS_KEY",
];

function exactEnvironment(environment) {
  const selected = {};
  for (const name of ENVIRONMENT_NAMES) {
    if (!Object.prototype.hasOwnProperty.call(environment, name)) {
      throw new Error(`release storage activation environment is missing ${name}`);
    }
    const value = environment[name];
    if (
      typeof value !== "string"
      || value.length < 1
      || value.trim() !== value
      || value.length > 4096
      || CONTROL_RE.test(value)
    ) throw new Error(`release storage activation environment has invalid ${name}`);
    selected[name] = value;
  }
  return selected;
}

function print(value) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

export async function main(argv = process.argv.slice(2), environment = process.env) {
  if (argv.length === 1 && argv[0] === "contract") {
    print({
      contract: "verdify.lab-release-storage-pass-one-cli",
      schemaVersion: 1,
      status: "source-contract-only",
      readiness: releaseStoragePassOneContract.readiness,
      activationProof: releaseStoragePassOneContract.activationProof,
      activationGate: releaseStoragePassOneContract.activationGate,
      configMap: releaseStoragePassOneContract.configMap,
      readerSecret: releaseStoragePassOneContract.readerSecret,
      writerSecret: releaseStoragePassOneContract.writerSecret,
      readerEnvironmentNames: releaseStoragePassOneContract.readerEnvironmentNames,
      writerEnvironmentNames: releaseStoragePassOneContract.writerEnvironmentNames,
      locationConstraints: releaseStoragePassOneContract.locationConstraints,
      makesNetworkRequest: false,
      mutatesObjectStore: false,
      provesResourceExistence: false,
    });
    return;
  }
  if (
    argv.length !== 2
    || argv[0] !== "activation-proof"
    || argv[1] !== "--acknowledge-stage-mutation"
  ) {
    throw new Error(
      "Usage: node scripts/verify-release-storage-s3.mjs contract | activation-proof --acknowledge-stage-mutation",
    );
  }
  const selected = exactEnvironment(environment);
  const runtimeEnvironment = {
    LAB_S3_ENDPOINT_URL: selected.LAB_S3_ENDPOINT_URL,
    AWS_DEFAULT_REGION: selected.AWS_DEFAULT_REGION,
    AWS_ACCESS_KEY_ID: selected.AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY: selected.AWS_SECRET_ACCESS_KEY,
  };
  const [siteStore, occurrenceStore, coordinationObjects] = await Promise.all([
    createSiteReleaseWriterStore(selected.LAB_RELEASE_STORE, { environment: runtimeEnvironment }),
    createOccurrenceReleaseWriterStore(selected.LAB_OCCURRENCE_STORE, { environment: runtimeEnvironment }),
    createRuntimeS3ObjectStore(selected.LAB_RELEASE_COORDINATION_STORE, {
      environment: runtimeEnvironment,
      accessMode: "writer",
    }),
  ]);
  try {
    const endpointProof = await proveReleaseStorageS3Activation({
      siteObjects: siteStore.objects,
      occurrenceObjects: occurrenceStore.objects,
      coordinationObjects,
      nonce: randomUUID(),
      probedAt: new Date().toISOString(),
    });
    print({
      contract: "verdify.lab-release-storage-pass-one-activation-result",
      schemaVersion: 1,
      status: "blocked",
      activationAuthorized: false,
      activationGate: releaseStoragePassOneContract.activationGate,
      endpointProof,
    });
    throw new Error(
      "release storage activation is blocked until the packed-release capacity prerequisite lands",
    );
  } catch (error) {
    if (error instanceof ReleaseStorageS3ActivationProofError) {
      print(error.result);
    }
    throw error;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    process.stderr.write(`verify-release-storage-s3: ${error.message}\n`);
    process.exitCode = 1;
  });
}

export const releaseStorageS3CliContract = Object.freeze({
  readinessCommand: "contract",
  mutatingActivationCommand: "activation-proof --acknowledge-stage-mutation",
  environmentNames: Object.freeze([...ENVIRONMENT_NAMES]),
  activationProof: releaseStorageS3ActivationProofContract,
  activationGate: releaseStoragePassOneContract.activationGate,
});
