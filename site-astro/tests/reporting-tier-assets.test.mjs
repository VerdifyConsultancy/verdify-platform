import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  CONFIG_MAP_BYTE_BUDGET,
  EXPECTED_DASHBOARD_COUNT,
  EXPECTED_OCCURRENCE_COUNT,
  EXPECTED_UNIQUE_PANEL_COUNT,
  runReportingAssetGenerator,
  validateReportingTargets,
} from "../scripts/generate-reporting-tier-assets.mjs";

const SITE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(SITE_ROOT, "..");
const TARGETS_FILE = path.join(SITE_ROOT, "config/lab-stage-reporting-targets.json");
const POLICY_FILE = path.join(SITE_ROOT, "config/lab-stage-occurrence-export-policy.json");
const GENERATED_ROOT = path.join(REPO_ROOT, "deploy/k8s/overlays/lab-stage/reporting-tier/generated");

async function targets() {
  return JSON.parse(await readFile(TARGETS_FILE, "utf8"));
}

test("reporting target inventory is the exact immutable 18/139/143 source projection", async () => {
  const value = validateReportingTargets(await targets());
  const policy = JSON.parse(await readFile(POLICY_FILE, "utf8"));
  assert.equal(value.dashboardCount, EXPECTED_DASHBOARD_COUNT);
  assert.equal(value.uniquePanelCount, EXPECTED_UNIQUE_PANEL_COUNT);
  assert.equal(value.occurrenceCount, EXPECTED_OCCURRENCE_COUNT);
  assert.equal(value.sourceOccurrenceManifestSha256, policy.sourceOccurrenceManifestSha256);
  assert.equal(value.snapshotId, `sanitized-content-sha256:${policy.sourceSnapshotManifestSha256}`);
  assert.equal(new Set(value.occurrences.map(({ occurrenceId }) => occurrenceId)).size, 143);
  assert.equal(new Set(value.occurrences.map(({ uid }) => uid)).size, 18);
  assert.equal(new Set(value.occurrences.map(({ uid, panelId }) => `${uid}/${panelId}`)).size, 139);
  assert.equal(value.occurrences.every(({ renderPath, uid }) => (
    renderPath === `/render/d-solo/${uid}/`
  )), true);
});

test("reporting dashboard ConfigMaps are deterministic and each stays below 900 KiB", async () => {
  const status = await runReportingAssetGenerator([
    "--check",
    "--targets",
    TARGETS_FILE,
    "--generated-root",
    GENERATED_ROOT,
  ]);
  assert.equal(status.status, "verified");
  assert.deepEqual(
    {
      dashboards: status.dashboardCount,
      panels: status.uniquePanelCount,
      occurrences: status.occurrenceCount,
    },
    { dashboards: 18, panels: 139, occurrences: 143 },
  );
  assert.deepEqual(
    status.configMaps.map(({ name }) => name),
    ["targets-cm.yaml", "dashboards-cm-0.yaml", "dashboards-cm-1.yaml"],
  );
  assert.equal(status.configMaps.every(({ bytes }) => bytes <= CONFIG_MAP_BYTE_BUDGET), true);
  assert.equal(status.configMaps.every(({ bytes }) => bytes < 1024 * 1024), true);
});

test("reporting target validation fails closed on count, route, and panel drift", async () => {
  const original = await targets();
  for (const mutate of [
    (value) => { value.occurrenceCount -= 1; },
    (value) => { value.occurrences[0].renderPath = "/render/d-solo/not-approved/"; },
    (value) => { value.occurrences[0].panelId = "999999"; },
    (value) => { value.occurrences[0].query.orgId = ["2"]; },
  ]) {
    const changed = structuredClone(original);
    mutate(changed);
    assert.throws(() => validateReportingTargets(changed), /reporting/u);
  }
});
