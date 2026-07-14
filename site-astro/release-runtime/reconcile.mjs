import { execFile as execFileCallback } from "node:child_process";
import { constants as fsConstants } from "node:fs";
import {
  lstat,
  mkdir,
  open,
  readFile,
  readlink,
  realpath,
  rename,
  rm,
} from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { pathToFileURL } from "node:url";

import { siteReleaseCliEnvironment } from "../scripts/lib/runtime-s3-binding.mjs";

const execFile = promisify(execFileCallback);
const SHA256_RE = /^[0-9a-f]{64}$/;
const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const MAX_CLI_BYTES = 1024 * 1024;
const IDENTITY_KEYS = [
  "contract",
  "schemaVersion",
  "observedAt",
  "lastSuccessfulAt",
  "lastVerifiedAt",
  "ready",
  "health",
  "source",
  "releaseSha256",
  "previousReleaseSha256",
  "selectionSha256",
  "selectionGeneration",
  "triggerKind",
  "fileCount",
  "totalBytes",
  "freshness",
  "consecutiveFailures",
];

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.keys(value).join(",") === keys.join(",");
}

function integerSetting(value, fallback, minimum, maximum, label) {
  const selected = value === undefined ? fallback : Number(value);
  if (!Number.isSafeInteger(selected) || selected < minimum || selected > maximum) {
    throw new Error(`${label} is invalid`);
  }
  return selected;
}

function validateStore(value) {
  if (typeof value !== "string" || value.length < 1 || value.length > 2048 || /[\u0000-\u001f\u007f]/u.test(value)) {
    throw new Error("LAB_RELEASE_STORE is invalid");
  }
  if (path.isAbsolute(value)) return value;
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("LAB_RELEASE_STORE must be an absolute path or s3 URI");
  }
  if (
    parsed.protocol !== "s3:"
    || parsed.username
    || parsed.password
    || parsed.search
    || parsed.hash
    || !/^[a-z0-9][a-z0-9.-]{1,62}[a-z0-9]$/.test(parsed.hostname)
  ) {
    throw new Error("LAB_RELEASE_STORE does not use the credential-free s3 URI shape");
  }
  return value;
}

export function runtimeConfig(environment = process.env) {
  const store = validateStore(environment.LAB_RELEASE_STORE);
  return {
    store,
    cliEnvironment: siteReleaseCliEnvironment(store, { environment }),
    cacheRoot: path.resolve(environment.LAB_RELEASE_CACHE ?? "/srv/lab-cache"),
    bakedBundleRoot: path.resolve(environment.LAB_RELEASE_BAKED_BUNDLE ?? "/opt/verdify/lab-known-good"),
    stateRoot: path.resolve(environment.LAB_RELEASE_RUNTIME_STATE ?? "/run/verdify-lab-release"),
    cli: path.resolve(environment.LAB_RELEASE_CLI ?? "/app/scripts/manage-site-release.mjs"),
    reconcileSeconds: integerSetting(environment.LAB_RELEASE_RECONCILE_SECONDS, 60, 15, 900, "reconcile interval"),
    verifySeconds: integerSetting(environment.LAB_RELEASE_VERIFY_SECONDS, 900, 60, 86400, "verification interval"),
    cliTimeoutSeconds: integerSetting(environment.LAB_RELEASE_CLI_TIMEOUT_SECONDS, 120, 5, 600, "CLI timeout"),
  };
}

async function canonicalDirectory(directory, label, { create = false } = {}) {
  if (create) await mkdir(directory, { recursive: true, mode: 0o775 });
  const metadata = await lstat(directory);
  if (!metadata.isDirectory() || metadata.isSymbolicLink() || await realpath(directory) !== directory) {
    throw new Error(`${label} is not a canonical real directory`);
  }
  return directory;
}

async function syncDirectory(directory) {
  const handle = await open(directory, fsConstants.O_RDONLY);
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function atomicWrite(file, bytes, mode = 0o644) {
  const temporary = path.join(path.dirname(file), `.${path.basename(file)}-${process.pid}-${Date.now()}`);
  const handle = await open(temporary, "wx", mode);
  try {
    await handle.writeFile(bytes);
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await rename(temporary, file);
    await syncDirectory(path.dirname(file));
  } finally {
    await rm(temporary, { force: true });
  }
}

function parseCliJson(stdout, label) {
  if (typeof stdout !== "string" || stdout.length < 2 || Buffer.byteLength(stdout) > MAX_CLI_BYTES) {
    throw new Error(`${label} output is not bounded`);
  }
  let result;
  try {
    result = JSON.parse(stdout);
  } catch {
    throw new Error(`${label} output is not JSON`);
  }
  if (`${JSON.stringify(result, null, 2)}\n` !== stdout) throw new Error(`${label} output is not canonical JSON`);
  return result;
}

export async function runReleaseCli(config, args) {
  const environment = siteReleaseCliEnvironment(config.store, {
    environment: config.cliEnvironment,
  });
  const { stdout } = await execFile(process.execPath, [config.cli, ...args], {
    timeout: config.cliTimeoutSeconds * 1000,
    maxBuffer: MAX_CLI_BYTES,
    encoding: "utf8",
    windowsHide: true,
    env: environment,
  });
  return parseCliJson(stdout, `site release ${args[0]}`);
}

function validateStatus(status) {
  if (
    status?.contract !== "verdify.lab-site-release-status"
    || status.schemaVersion !== 1
    || typeof status.ready !== "boolean"
    || !Number.isSafeInteger(status.generation)
    || status.generation < 0
    || (status.selectionSha256 !== null && !SHA256_RE.test(status.selectionSha256))
  ) throw new Error("site release status violates the runtime contract");
  if (status.ready && !SHA256_RE.test(status.current?.releaseSha256)) {
    throw new Error("ready site release status lacks a release identity");
  }
  return status;
}

function validateHydration(status) {
  if (
    status?.contract !== "verdify.lab-site-cache-status"
    || status.schemaVersion !== 1
    || status.ready !== true
    || !["ready", "degraded", "alert"].includes(status.health)
    || !["store-current", "store-previous", "baked-known-good"].includes(status.source)
    || !SHA256_RE.test(status.releaseSha256)
    || (status.previousReleaseSha256 !== null && !SHA256_RE.test(status.previousReleaseSha256))
    || !Number.isSafeInteger(status.fileCount)
    || status.fileCount < 1
    || !Number.isSafeInteger(status.totalBytes)
    || status.totalBytes < 1
  ) throw new Error("site cache hydration violates the runtime contract");
  return status;
}

function validateIdentity(value) {
  if (
    !exactKeys(value, IDENTITY_KEYS)
    || value.contract !== "verdify.lab-release-runtime-identity"
    || value.schemaVersion !== 1
    || !ISO_INSTANT_RE.test(value.observedAt)
    || !ISO_INSTANT_RE.test(value.lastSuccessfulAt)
    || !ISO_INSTANT_RE.test(value.lastVerifiedAt)
    || value.ready !== true
    || !["ready", "degraded", "alert"].includes(value.health)
    || !["store-current", "store-previous", "baked-known-good"].includes(value.source)
    || !SHA256_RE.test(value.releaseSha256)
    || (value.previousReleaseSha256 !== null && !SHA256_RE.test(value.previousReleaseSha256))
    || (value.selectionSha256 !== null && !SHA256_RE.test(value.selectionSha256))
    || !Number.isSafeInteger(value.selectionGeneration)
    || value.selectionGeneration < 0
    || !["immutable-selection-digest", "baked-known-good"].includes(value.triggerKind)
    || !Number.isSafeInteger(value.consecutiveFailures)
    || value.consecutiveFailures < 0
  ) throw new Error("persisted release runtime identity is invalid");
  return value;
}

function advancedFreshness(value, now) {
  if (
    value === null
    || typeof value !== "object"
    || !ISO_INSTANT_RE.test(value.completedAt)
    || !ISO_INSTANT_RE.test(value.releasedAt)
    || !Number.isSafeInteger(value.targetSeconds)
    || !Number.isSafeInteger(value.alertAfterSeconds)
  ) return value;
  const elapsedSeconds = Math.max(0, Math.floor((Date.parse(now) - Date.parse(value.completedAt)) / 1000));
  return {
    ...value,
    evaluatedAt: now,
    elapsedSeconds,
    status: elapsedSeconds >= value.alertAfterSeconds ? "alert" : elapsedSeconds > value.targetSeconds ? "late" : "fresh",
  };
}

async function readIdentity(config) {
  try {
    const bytes = await readFile(path.join(config.stateRoot, "release.json"));
    if (bytes.length < 2 || bytes.length > 128 * 1024) throw new Error("release identity is not bounded");
    const value = JSON.parse(bytes.toString("utf8"));
    if (`${JSON.stringify(value, null, 2)}\n` !== bytes.toString("utf8")) throw new Error("release identity is not canonical");
    return validateIdentity(value);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function label(value) {
  return String(value).replaceAll("\\", "\\\\").replaceAll("\n", "\\n").replaceAll('"', '\\"');
}

function metrics(identity, { success }) {
  const health = identity.health === "ready" ? 2 : identity.health === "degraded" ? 1 : 0;
  const freshnessSeconds = Number.isSafeInteger(identity.freshness?.elapsedSeconds)
    ? identity.freshness.elapsedSeconds
    : -1;
  return [
    "# HELP verdify_lab_release_ready Whether nginx has a verified complete site generation.",
    "# TYPE verdify_lab_release_ready gauge",
    `verdify_lab_release_ready ${identity.ready ? 1 : 0}`,
    "# HELP verdify_lab_release_health Release health state: 2 ready, 1 degraded, 0 alert.",
    "# TYPE verdify_lab_release_health gauge",
    `verdify_lab_release_health ${health}`,
    "# HELP verdify_lab_release_reconcile_success Whether the most recent reconciliation completed.",
    "# TYPE verdify_lab_release_reconcile_success gauge",
    `verdify_lab_release_reconcile_success ${success ? 1 : 0}`,
    "# HELP verdify_lab_release_consecutive_failures Consecutive failed reconciliation observations.",
    "# TYPE verdify_lab_release_consecutive_failures gauge",
    `verdify_lab_release_consecutive_failures ${identity.consecutiveFailures}`,
    "# HELP verdify_lab_release_selection_generation Selected immutable release generation.",
    "# TYPE verdify_lab_release_selection_generation gauge",
    `verdify_lab_release_selection_generation ${identity.selectionGeneration}`,
    "# HELP verdify_lab_release_files Files in the selected closed release manifest.",
    "# TYPE verdify_lab_release_files gauge",
    `verdify_lab_release_files ${identity.fileCount}`,
    "# HELP verdify_lab_release_bytes Bytes in the selected closed release manifest.",
    "# TYPE verdify_lab_release_bytes gauge",
    `verdify_lab_release_bytes ${identity.totalBytes}`,
    "# HELP verdify_lab_release_event_age_seconds Age of the event represented by the served release.",
    "# TYPE verdify_lab_release_event_age_seconds gauge",
    `verdify_lab_release_event_age_seconds ${freshnessSeconds}`,
    "# HELP verdify_lab_release_info Immutable release and selection identity.",
    "# TYPE verdify_lab_release_info gauge",
    `verdify_lab_release_info{release_sha256="${label(identity.releaseSha256)}",selection_sha256="${label(identity.selectionSha256 ?? "none")}",source="${label(identity.source)}"} 1`,
    "",
  ].join("\n");
}

async function writeRuntimeState(config, identity, success) {
  validateIdentity(identity);
  await atomicWrite(path.join(config.stateRoot, "release.json"), `${JSON.stringify(identity, null, 2)}\n`);
  await atomicWrite(path.join(config.stateRoot, "metrics"), metrics(identity, { success }));
  await atomicWrite(path.join(config.stateRoot, "readyz"), "ready\n");
}

async function hasSelectedGeneration(config) {
  try {
    const metadata = await lstat(path.join(config.cacheRoot, "current"));
    if (!metadata.isSymbolicLink()) return false;
    const target = await readlink(path.join(config.cacheRoot, "current"));
    return /^generations\/[0-9a-f]{64}-[0-9a-f-]{36}$/.test(target);
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

function identityFromHydration(hydrated, status, now) {
  return {
    contract: "verdify.lab-release-runtime-identity",
    schemaVersion: 1,
    observedAt: now,
    lastSuccessfulAt: now,
    lastVerifiedAt: now,
    ready: true,
    health: hydrated.health,
    source: hydrated.source,
    releaseSha256: hydrated.releaseSha256,
    previousReleaseSha256: hydrated.previousReleaseSha256,
    selectionSha256: status?.selectionSha256 ?? null,
    selectionGeneration: status?.generation ?? 0,
    triggerKind: status?.selectionSha256 ? "immutable-selection-digest" : "baked-known-good",
    fileCount: hydrated.fileCount,
    totalBytes: hydrated.totalBytes,
    freshness: hydrated.freshness,
    consecutiveFailures: 0,
  };
}

function refreshedIdentity(previous, status, now) {
  return {
    ...previous,
    observedAt: now,
    lastSuccessfulAt: now,
    health: status.health,
    selectionSha256: status.selectionSha256,
    selectionGeneration: status.generation,
    triggerKind: "immutable-selection-digest",
    freshness: status.current.freshness,
    consecutiveFailures: 0,
  };
}

async function statusFromCli(config, cliRunner, now) {
  try {
    const status = validateStatus(await cliRunner(config, ["status", "--store", config.store, "--at", now]));
    return status.ready ? status : null;
  } catch {
    return null;
  }
}

export async function recordReconcileFailure(config, now = new Date().toISOString()) {
  await canonicalDirectory(config.cacheRoot, "site release cache", { create: true });
  await canonicalDirectory(config.stateRoot, "site release runtime state", { create: true });
  const previous = await readIdentity(config);
  if (!previous || !await hasSelectedGeneration(config)) return null;
  const freshness = advancedFreshness(previous.freshness, now);
  const identity = {
    ...previous,
    observedAt: now,
    health: freshness?.status === "alert" || previous.health === "alert" ? "alert" : "degraded",
    freshness,
    consecutiveFailures: previous.consecutiveFailures + 1,
  };
  await writeRuntimeState(config, identity, false);
  return identity;
}

export async function reconcileOnce(config, {
  cliRunner = runReleaseCli,
  now = new Date().toISOString(),
  initial = false,
} = {}) {
  if (!ISO_INSTANT_RE.test(now) || !Number.isFinite(Date.parse(now))) throw new Error("runtime observation time is invalid");
  await canonicalDirectory(config.cacheRoot, "site release cache", { create: true });
  await canonicalDirectory(config.stateRoot, "site release runtime state", { create: true });
  const previous = await readIdentity(config);
  const status = await statusFromCli(config, cliRunner, now);
  const selected = await hasSelectedGeneration(config);
  const sameTrigger = previous
    && selected
    && status?.selectionSha256
    && status.selectionSha256 === previous.selectionSha256
    && status.current.releaseSha256 === previous.releaseSha256;
  const verificationDue = !previous
    || Date.parse(now) - Date.parse(previous.lastVerifiedAt) >= config.verifySeconds * 1000;

  if (sameTrigger && !verificationDue) {
    const identity = refreshedIdentity(previous, status, now);
    await writeRuntimeState(config, identity, true);
    return identity;
  }

  if (!status && previous && selected && !initial) {
    return recordReconcileFailure(config, now);
  }

  // A baked fallback is only eligible when no mutable store selector was
  // observed. Once a selector is validated, omitting --baked prevents a
  // transient object-store failure from atomically downgrading a healthy pod.
  const offlineStore = path.join(config.cacheRoot, ".store-unavailable");
  if (!status) await rm(offlineStore, { recursive: true, force: true });
  const hydrateArgs = ["hydrate", "--store", status ? config.store : offlineStore, "--cache", config.cacheRoot];
  if (!status && config.bakedBundleRoot) hydrateArgs.push("--baked", config.bakedBundleRoot);
  hydrateArgs.push("--at", now);
  const hydrated = validateHydration(await cliRunner(config, hydrateArgs));
  if (status && (hydrated.source === "baked-known-good" || hydrated.releaseSha256 !== status.current.releaseSha256)) {
    throw new Error("hydrated cache does not match the immutable selected release trigger");
  }
  if (!status && hydrated.source !== "baked-known-good") throw new Error("untriggered store release is ineligible");
  const identity = identityFromHydration(hydrated, status, now);
  await writeRuntimeState(config, identity, true);
  return identity;
}

async function wait(milliseconds, signal) {
  await new Promise((resolve) => {
    const timer = setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

async function main() {
  const mode = process.argv[2];
  if (!new Set(["init", "reconcile"]).has(mode) || process.argv.length !== 3) {
    throw new Error("Usage: release-runtime-entrypoint init|reconcile");
  }
  const config = runtimeConfig();
  if (mode === "init") {
    await reconcileOnce(config, { initial: true });
    return;
  }
  const abort = new AbortController();
  for (const signal of ["SIGINT", "SIGTERM"]) process.once(signal, () => abort.abort());
  while (!abort.signal.aborted) {
    try {
      await reconcileOnce(config);
    } catch (error) {
      await recordReconcileFailure(config).catch(() => {});
      process.stderr.write(`release-reconciler: ${error.name ?? "Error"}; verified generation preserved\n`);
    }
    await wait(config.reconcileSeconds * 1000, abort.signal);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`release-reconciler: ${error.message}\n`);
    process.exitCode = 1;
  });
}
