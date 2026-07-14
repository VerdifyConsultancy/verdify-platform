#!/usr/bin/env node

import { createHash } from "node:crypto";
import {
  mkdir,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const REPORTING_TARGET_CONTRACT = "verdify.lab-reporting-targets";
export const REPORTING_TARGET_SCHEMA_VERSION = 1;
export const EXPECTED_DASHBOARD_COUNT = 18;
export const EXPECTED_UNIQUE_PANEL_COUNT = 139;
export const EXPECTED_OCCURRENCE_COUNT = 143;
export const CONFIG_MAP_BYTE_BUDGET = 900 * 1024;

const SHA256_RE = /^[0-9a-f]{64}$/u;
const OCCURRENCE_ID_RE = /^graph_[0-9a-f]{24}$/u;
const UID_RE = /^[A-Za-z0-9_-]{1,128}$/u;
const PANEL_ID_RE = /^(?:0|[1-9][0-9]{0,9})$/u;
const SAFE_QUERY_KEY_RE = /^[A-Za-z][A-Za-z0-9_.-]{0,127}$/u;
const SITE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(SITE_ROOT, "..");
const DEFAULT_TARGET_PATH = path.join(SITE_ROOT, "config/lab-stage-reporting-targets.json");
const DEFAULT_GENERATED_ROOT = path.join(
  REPO_ROOT,
  "deploy/k8s/overlays/lab-stage/reporting-tier/generated",
);
const DASHBOARD_ROOTS = Object.freeze([
  path.join(REPO_ROOT, "grafana/dashboards"),
  path.join(REPO_ROOT, "grafana/provisioning/dashboards/json"),
]);

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype
    && Object.keys(value).join(",") === keys.join(",");
}

function validateSourceOccurrenceManifest(manifest) {
  if (
    !exactKeys(manifest, [
      "contract",
      "schemaVersion",
      "snapshotId",
      "selectedManifestSha256",
      "graphs",
      "currentMedia",
    ])
    || manifest.contract !== "verdify.lab-static-occurrence-manifest"
    || manifest.schemaVersion !== 1
    || typeof manifest.snapshotId !== "string"
    || !manifest.snapshotId.startsWith("sanitized-content-sha256:")
    || manifest.selectedManifestSha256 !== null
    || !Array.isArray(manifest.graphs)
    || !Array.isArray(manifest.currentMedia)
    || manifest.graphs.length !== EXPECTED_OCCURRENCE_COUNT
    || manifest.currentMedia.length !== 2
  ) throw new Error("source occurrence manifest is not the closed unselected v1 inventory");
  const seen = new Set();
  for (const graph of manifest.graphs) {
    if (
      !exactKeys(graph, [
        "occurrenceId",
        "route",
        "ordinal",
        "semanticRole",
        "uid",
        "panelId",
        "query",
        "variables",
        "timeRange",
        "liveUrl",
        "renderCadenceSeconds",
        "selected",
      ])
      || !OCCURRENCE_ID_RE.test(graph.occurrenceId)
      || seen.has(graph.occurrenceId)
      || !UID_RE.test(graph.uid)
      || !PANEL_ID_RE.test(graph.panelId)
      || graph.selected !== null
      || !Number.isSafeInteger(graph.ordinal)
      || graph.ordinal < 0
      || !Number.isSafeInteger(graph.renderCadenceSeconds)
      || graph.renderCadenceSeconds < 1
      || typeof graph.liveUrl !== "string"
      || !graph.liveUrl.startsWith(`https://graphs.verdify.ai/d-solo/${graph.uid}/`)
    ) throw new Error("source occurrence manifest contains an invalid graph target");
    seen.add(graph.occurrenceId);
  }
  for (const media of manifest.currentMedia) {
    if (
      typeof media?.occurrenceId !== "string"
      || !/^media_[0-9a-f]{24}$/u.test(media.occurrenceId)
      || seen.has(media.occurrenceId)
      || media.selected !== null
    ) throw new Error("source occurrence manifest contains an invalid current-media target");
    seen.add(media.occurrenceId);
  }
  return { graphs: manifest.graphs, currentMedia: manifest.currentMedia };
}

function orderedMap(value) {
  if (Array.isArray(value)) return value.map(orderedMap);
  if (value === null || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, orderedMap(value[key])]),
  );
}

function normalizeRelativeFile(file) {
  const relative = path.relative(REPO_ROOT, file).split(path.sep).join("/");
  if (
    relative.length === 0
    || relative.startsWith("../")
    || !relative.endsWith(".json")
    || /[\u0000-\u001f\u007f\\]/u.test(relative)
  ) throw new Error("reporting dashboard source path is invalid");
  return relative;
}

function collectPanelIds(value, result = new Set()) {
  if (value === null || typeof value !== "object") return result;
  if (Array.isArray(value)) {
    for (const entry of value) collectPanelIds(entry, result);
    return result;
  }
  if (Array.isArray(value.panels)) {
    for (const panel of value.panels) {
      if (Number.isSafeInteger(panel?.id) && panel.id >= 0) result.add(String(panel.id));
      collectPanelIds(panel, result);
    }
  }
  return result;
}

async function dashboardInventory() {
  const inventory = new Map();
  for (const root of DASHBOARD_ROOTS) {
    const names = (await readdir(root)).filter((name) => name.endsWith(".json")).sort();
    for (const name of names) {
      const file = path.join(root, name);
      const raw = await readFile(file, "utf8");
      let dashboard;
      try {
        dashboard = JSON.parse(raw);
      } catch {
        throw new Error(`reporting dashboard source is not JSON: ${normalizeRelativeFile(file)}`);
      }
      if (typeof dashboard.uid !== "string" || !UID_RE.test(dashboard.uid)) continue;
      if (inventory.has(dashboard.uid)) {
        throw new Error(`reporting dashboard UID is ambiguous: ${dashboard.uid}`);
      }
      inventory.set(dashboard.uid, {
        uid: dashboard.uid,
        file,
        sourceFile: normalizeRelativeFile(file),
        raw: raw.endsWith("\n") ? raw : `${raw}\n`,
        panelIds: collectPanelIds(dashboard),
      });
    }
  }
  return inventory;
}

function validateQueryMap(value, label) {
  if (!exactKeys(value, Object.keys(value).sort())) {
    throw new Error(`${label} keys are not canonical`);
  }
  for (const [key, entries] of Object.entries(value)) {
    if (!SAFE_QUERY_KEY_RE.test(key) || !Array.isArray(entries) || entries.length === 0 || entries.length > 32) {
      throw new Error(`${label} is invalid`);
    }
    for (const entry of entries) {
      if (
        typeof entry !== "string"
        || entry.length === 0
        || entry.length > 256
        || /[\u0000-\u001f\u007f]/u.test(entry)
      ) throw new Error(`${label} is invalid`);
    }
  }
}

export function validateReportingTargets(targets) {
  if (
    !exactKeys(targets, [
      "contract",
      "schemaVersion",
      "sourceOccurrenceManifestSha256",
      "snapshotId",
      "dashboardCount",
      "uniquePanelCount",
      "occurrenceCount",
      "dashboards",
      "occurrences",
    ])
    || targets.contract !== REPORTING_TARGET_CONTRACT
    || targets.schemaVersion !== REPORTING_TARGET_SCHEMA_VERSION
    || !SHA256_RE.test(targets.sourceOccurrenceManifestSha256)
    || typeof targets.snapshotId !== "string"
    || !targets.snapshotId.startsWith("sanitized-content-sha256:")
    || targets.dashboardCount !== EXPECTED_DASHBOARD_COUNT
    || targets.uniquePanelCount !== EXPECTED_UNIQUE_PANEL_COUNT
    || targets.occurrenceCount !== EXPECTED_OCCURRENCE_COUNT
    || !Array.isArray(targets.dashboards)
    || !Array.isArray(targets.occurrences)
    || targets.dashboards.length !== targets.dashboardCount
    || targets.occurrences.length !== targets.occurrenceCount
  ) throw new Error("reporting targets do not use the closed v1 shape");

  const dashboardsByUid = new Map();
  let priorUid = "";
  for (const dashboard of targets.dashboards) {
    if (
      !exactKeys(dashboard, ["uid", "sourceFile", "panelIds"])
      || !UID_RE.test(dashboard.uid)
      || dashboard.uid <= priorUid
      || typeof dashboard.sourceFile !== "string"
      || !dashboard.sourceFile.endsWith(".json")
      || dashboard.sourceFile.startsWith("/")
      || dashboard.sourceFile.includes("..")
      || !Array.isArray(dashboard.panelIds)
      || dashboard.panelIds.length === 0
      || dashboard.panelIds.some((panelId) => !PANEL_ID_RE.test(panelId))
      || new Set(dashboard.panelIds).size !== dashboard.panelIds.length
      || JSON.stringify(dashboard.panelIds) !== JSON.stringify(
        [...dashboard.panelIds].sort((left, right) => Number(left) - Number(right)),
      )
    ) throw new Error("reporting dashboard target is invalid");
    priorUid = dashboard.uid;
    dashboardsByUid.set(dashboard.uid, dashboard);
  }

  const occurrenceIds = new Set();
  const uniquePanels = new Set();
  for (const occurrence of targets.occurrences) {
    if (
      !exactKeys(occurrence, [
        "occurrenceId",
        "uid",
        "panelId",
        "renderPath",
        "query",
        "variables",
        "timeRange",
      ])
      || !OCCURRENCE_ID_RE.test(occurrence.occurrenceId)
      || occurrenceIds.has(occurrence.occurrenceId)
      || !UID_RE.test(occurrence.uid)
      || !PANEL_ID_RE.test(occurrence.panelId)
      || occurrence.renderPath !== `/render/d-solo/${occurrence.uid}/`
      || !exactKeys(occurrence.timeRange, ["from", "to"])
      || occurrence.timeRange.from !== (occurrence.query?.from?.[0] ?? "")
      || occurrence.timeRange.to !== (occurrence.query?.to?.[0] ?? "")
      || occurrence.query?.panelId?.length !== 1
      || occurrence.query.panelId[0] !== occurrence.panelId
      || occurrence.query?.orgId?.length !== 1
      || occurrence.query.orgId[0] !== "1"
      || occurrence.query?.theme?.length !== 1
      || occurrence.query.theme[0] !== "light"
    ) throw new Error("reporting occurrence target is invalid");
    validateQueryMap(occurrence.query, "reporting occurrence query");
    validateQueryMap(occurrence.variables, "reporting occurrence variables");
    const dashboard = dashboardsByUid.get(occurrence.uid);
    if (!dashboard?.panelIds.includes(occurrence.panelId)) {
      throw new Error("reporting occurrence target is absent from its dashboard inventory");
    }
    occurrenceIds.add(occurrence.occurrenceId);
    uniquePanels.add(`${occurrence.uid}/${occurrence.panelId}`);
  }
  if (uniquePanels.size !== targets.uniquePanelCount) {
    throw new Error("reporting target unique panel count is invalid");
  }
  const usedDashboards = new Set(targets.occurrences.map(({ uid }) => uid));
  if (usedDashboards.size !== targets.dashboardCount || usedDashboards.size !== dashboardsByUid.size) {
    throw new Error("reporting target dashboard count is invalid");
  }
  return targets;
}

export async function reportingTargetsFromManifest(manifest) {
  const discovered = validateSourceOccurrenceManifest(manifest);
  const inventory = await dashboardInventory();
  const dashboardPanels = new Map();
  const occurrences = discovered.graphs.map((occurrence) => {
    const source = inventory.get(occurrence.uid);
    if (source === undefined || !source.panelIds.has(occurrence.panelId)) {
      throw new Error(`reporting occurrence dashboard/panel is not in source: ${occurrence.uid}/${occurrence.panelId}`);
    }
    if (!dashboardPanels.has(occurrence.uid)) dashboardPanels.set(occurrence.uid, new Set());
    dashboardPanels.get(occurrence.uid).add(occurrence.panelId);
    return {
      occurrenceId: occurrence.occurrenceId,
      uid: occurrence.uid,
      panelId: occurrence.panelId,
      renderPath: `/render/d-solo/${occurrence.uid}/`,
      query: orderedMap(occurrence.query),
      variables: orderedMap(occurrence.variables),
      timeRange: { ...occurrence.timeRange },
    };
  });
  const dashboards = [...dashboardPanels].sort(([left], [right]) => left.localeCompare(right)).map(([uid, panels]) => ({
    uid,
    sourceFile: inventory.get(uid).sourceFile,
    panelIds: [...panels].sort((left, right) => Number(left) - Number(right)),
  }));
  const targets = {
    contract: REPORTING_TARGET_CONTRACT,
    schemaVersion: REPORTING_TARGET_SCHEMA_VERSION,
    sourceOccurrenceManifestSha256: sha256(canonicalBytes(manifest)),
    snapshotId: manifest.snapshotId,
    dashboardCount: dashboards.length,
    uniquePanelCount: new Set(occurrences.map(({ uid, panelId }) => `${uid}/${panelId}`)).size,
    occurrenceCount: occurrences.length,
    dashboards,
    occurrences,
  };
  return validateReportingTargets(targets);
}

async function validateDashboardSources(targets) {
  const inventory = await dashboardInventory();
  return targets.dashboards.map((target) => {
    const source = inventory.get(target.uid);
    if (
      source === undefined
      || source.sourceFile !== target.sourceFile
      || target.panelIds.some((panelId) => !source.panelIds.has(panelId))
    ) throw new Error(`reporting dashboard source drifted: ${target.uid}`);
    return source;
  });
}

function blockScalar(raw) {
  return raw.replace(/\n$/u, "").split("\n").map((line) => (
    line.length === 0 ? "" : `    ${line}`
  )).join("\n");
}

function renderConfigMap(index, dashboards) {
  const header = [
    "apiVersion: v1",
    "kind: ConfigMap",
    "metadata:",
    `  name: verdify-lab-reporting-dashboards-${index}`,
    "  labels:",
    "    app.kubernetes.io/name: verdify-lab-reporting-tier",
    "    app.kubernetes.io/component: lab-reporting-tier",
    "    app.kubernetes.io/part-of: verdify",
    "    verdify.ai/generated-by: generate-reporting-tier-assets",
    "data:",
  ];
  for (const dashboard of dashboards) {
    header.push(`  ${path.basename(dashboard.sourceFile)}: |-`);
    header.push(blockScalar(dashboard.raw));
  }
  return `${header.join("\n")}\n`;
}

function shardDashboards(dashboards) {
  const shards = [];
  let current = [];
  for (const dashboard of dashboards) {
    const candidate = [...current, dashboard];
    const index = shards.length;
    if (Buffer.byteLength(renderConfigMap(index, candidate)) > CONFIG_MAP_BYTE_BUDGET) {
      if (current.length === 0) throw new Error(`reporting dashboard exceeds ConfigMap budget: ${dashboard.uid}`);
      shards.push(current);
      current = [dashboard];
      if (Buffer.byteLength(renderConfigMap(shards.length, current)) > CONFIG_MAP_BYTE_BUDGET) {
        throw new Error(`reporting dashboard exceeds ConfigMap budget: ${dashboard.uid}`);
      }
    } else {
      current = candidate;
    }
  }
  if (current.length > 0) shards.push(current);
  return shards;
}

async function expectedGeneratedFiles(targets) {
  const dashboards = await validateDashboardSources(targets);
  const shards = shardDashboards(dashboards);
  return new Map(shards.map((shard, index) => [
    `dashboards-cm-${index}.yaml`,
    renderConfigMap(index, shard),
  ]));
}

async function readCanonicalJson(file, label) {
  const bytes = await readFile(file);
  let value;
  try {
    value = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error(`${label} is not JSON`);
  }
  if (!canonicalBytes(value).equals(bytes)) throw new Error(`${label} is not canonical JSON`);
  return { value, bytes, sha256: sha256(bytes) };
}

function parseArguments(argv) {
  const result = {
    check: true,
    write: false,
    manifest: null,
    targetPath: DEFAULT_TARGET_PATH,
    generatedRoot: DEFAULT_GENERATED_ROOT,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--check") {
      result.check = true;
      result.write = false;
    } else if (argument === "--write") {
      result.write = true;
      result.check = false;
    } else if (["--manifest", "--targets", "--generated-root"].includes(argument)) {
      const value = argv[index + 1];
      if (!value) throw new Error(`${argument} requires a path`);
      index += 1;
      if (argument === "--manifest") result.manifest = path.resolve(value);
      if (argument === "--targets") result.targetPath = path.resolve(value);
      if (argument === "--generated-root") result.generatedRoot = path.resolve(value);
    } else {
      throw new Error(`unknown argument: ${argument}`);
    }
  }
  return result;
}

async function compareGeneratedFiles(root, expected) {
  const observedNames = (await readdir(root)).filter((name) => name.endsWith(".yaml")).sort();
  const expectedNames = [...expected.keys()].sort();
  if (JSON.stringify(observedNames) !== JSON.stringify(expectedNames)) {
    throw new Error("generated reporting dashboard ConfigMap file set drifted");
  }
  for (const [name, content] of expected) {
    if (await readFile(path.join(root, name), "utf8") !== content) {
      throw new Error(`generated reporting dashboard ConfigMap drifted: ${name}`);
    }
    if (Buffer.byteLength(content) > CONFIG_MAP_BYTE_BUDGET) {
      throw new Error(`generated reporting dashboard ConfigMap exceeds budget: ${name}`);
    }
  }
}

async function writeGeneratedFiles(root, expected) {
  await mkdir(root, { recursive: true });
  for (const name of await readdir(root)) {
    if (name.endsWith(".yaml") && !expected.has(name)) {
      await rm(path.join(root, name));
    }
  }
  for (const [name, content] of expected) {
    await writeFile(path.join(root, name), content);
  }
}

export async function runReportingAssetGenerator(argv = process.argv.slice(2)) {
  const options = parseArguments(argv);
  let targets;
  if (options.manifest !== null) {
    const manifest = await readCanonicalJson(options.manifest, "source occurrence manifest");
    targets = await reportingTargetsFromManifest(manifest.value);
    if (targets.sourceOccurrenceManifestSha256 !== manifest.sha256) {
      throw new Error("source occurrence manifest is not its discovery projection");
    }
    if (options.write) {
      await mkdir(path.dirname(options.targetPath), { recursive: true });
      await writeFile(options.targetPath, canonicalBytes(targets));
    } else {
      const committed = await readCanonicalJson(options.targetPath, "reporting targets");
      validateReportingTargets(committed.value);
      if (!canonicalBytes(targets).equals(committed.bytes)) {
        throw new Error("reporting targets drifted from the source occurrence manifest");
      }
    }
  } else {
    targets = validateReportingTargets(
      (await readCanonicalJson(options.targetPath, "reporting targets")).value,
    );
  }
  const generated = await expectedGeneratedFiles(targets);
  if (options.write) await writeGeneratedFiles(options.generatedRoot, generated);
  else await compareGeneratedFiles(options.generatedRoot, generated);
  const sizes = [...generated].map(([name, content]) => ({ name, bytes: Buffer.byteLength(content) }));
  return {
    contract: "verdify.lab-reporting-assets-status",
    schemaVersion: 1,
    status: options.write ? "written" : "verified",
    sourceOccurrenceManifestSha256: targets.sourceOccurrenceManifestSha256,
    dashboardCount: targets.dashboardCount,
    uniquePanelCount: targets.uniquePanelCount,
    occurrenceCount: targets.occurrenceCount,
    configMaps: sizes,
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const status = await runReportingAssetGenerator();
    process.stdout.write(`${JSON.stringify(status, null, 2)}\n`);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
