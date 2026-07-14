import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import YAML from "yaml";

import { validateReportingTargets } from "../scripts/generate-reporting-tier-assets.mjs";

const SITE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(SITE_ROOT, "..");
const OVERLAY_ROOT = path.join(REPO_ROOT, "deploy/k8s/overlays/lab-stage/reporting-tier");
const PARENT_KUSTOMIZATION = path.join(REPO_ROOT, "deploy/k8s/overlays/lab-stage/kustomization.yaml");
const ZERO_DIGEST = "0".repeat(64);
const DASHBOARD_UIDS = [
  "greenhouse-equipment",
  "greenhouse-hydroponics",
  "greenhouse-lighting",
  "greenhouse-soil",
  "greenhouse-weather",
  "site-climate",
  "site-climate-lighting",
  "site-evidence-economics",
  "site-evidence-operations",
  "site-evidence-planning-quality",
  "site-greenhouse-crops",
  "site-greenhouse-equipment",
  "site-greenhouse-zones",
  "site-home",
  "site-inference-infra",
  "site-intelligence",
  "site-intelligence-planning",
  "site-irrigation",
];

function renderOverlay() {
  const rendered = spawnSync("kubectl", ["kustomize", OVERLAY_ROOT], {
    cwd: REPO_ROOT,
    encoding: "utf8",
    timeout: 30_000,
    maxBuffer: 4 * 1024 * 1024,
  });
  assert.equal(rendered.status, 0, rendered.stderr);
  return {
    text: rendered.stdout,
    resources: YAML.parseAllDocuments(rendered.stdout)
      .map((document) => document.toJSON())
      .filter(Boolean),
  };
}

function one(resources, kind, name) {
  const matches = resources.filter((resource) => (
    resource.kind === kind && resource.metadata?.name === name
  ));
  assert.equal(matches.length, 1, `expected one ${kind}/${name}`);
  return matches[0];
}

function environment(container) {
  return new Map((container.env ?? []).map((entry) => [entry.name, entry]));
}

test("reporting tier is a standalone source-only overlay with no route or Secret", async () => {
  const { resources } = renderOverlay();
  assert.deepEqual(
    resources.map(({ kind, metadata }) => `${kind}/${metadata.name}`),
    [
      "ServiceAccount/verdify-lab-occurrence-producer",
      "ConfigMap/verdify-lab-reporting-dashboards-0",
      "ConfigMap/verdify-lab-reporting-dashboards-1",
      "ConfigMap/verdify-lab-reporting-gateway",
      "ConfigMap/verdify-lab-reporting-projection-readiness",
      "ConfigMap/verdify-lab-reporting-provisioning",
      "ConfigMap/verdify-lab-reporting-runtime-contract",
      "ConfigMap/verdify-lab-reporting-targets",
      "Service/verdify-lab-reporting-projection",
      "Service/verdify-lab-reporting-tier",
      "Deployment/verdify-lab-occurrence-producer",
      "Deployment/verdify-lab-reporting-tier",
      "NetworkPolicy/verdify-lab-occurrence-producer-isolated",
      "NetworkPolicy/verdify-lab-reporting-projection-ingress",
      "NetworkPolicy/verdify-lab-reporting-tier-isolated",
    ],
  );
  assert.equal(resources.every(({ metadata }) => metadata.namespace === "verdify-platform"), true);
  assert.equal(resources.every(({ metadata }) => (
    metadata.labels["verdify.ai/activation-state"] === "source-only"
  )), true);
  assert.equal(resources.some(({ kind }) => ["Secret", "Ingress", "IngressRoute", "CronJob", "Job"].includes(kind)), false);
  assert.equal(resources.filter(({ kind }) => kind === "Deployment").every(({ spec }) => spec.replicas === 0), true);

  const parent = YAML.parse(await readFile(PARENT_KUSTOMIZATION, "utf8"));
  assert.equal(parent.resources.some((resource) => /reporting-tier/u.test(resource)), false);
  assert.equal(parent.components?.some((resource) => /reporting-tier/u.test(resource)) ?? false, false);
});

test("projection Service and verifier are fixed, read-only, and have no backing workload", () => {
  const { resources } = renderOverlay();
  const service = one(resources, "Service", "verdify-lab-reporting-projection");
  assert.equal(service.spec.type, "ClusterIP");
  assert.deepEqual(service.spec.selector, {
    "app.kubernetes.io/name": "verdify-lab-reporting-projection",
    "app.kubernetes.io/component": "lab-reporting-projection",
  });
  assert.deepEqual(service.spec.ports, [
    { name: "postgres", port: 5432, protocol: "TCP", targetPort: "postgres" },
    { name: "watermark", port: 8080, protocol: "TCP", targetPort: "watermark" },
  ]);
  assert.equal(resources.some(({ kind, spec }) => (
    ["Deployment", "StatefulSet", "DaemonSet"].includes(kind)
      && spec.template?.metadata?.labels?.["app.kubernetes.io/name"] === "verdify-lab-reporting-projection"
  )), false);
  const sql = one(resources, "ConfigMap", "verdify-lab-reporting-projection-readiness")
    .data["projection-readiness.sql"];
  assert.match(sql, /BEGIN TRANSACTION READ ONLY;/u);
  assert.match(sql, /current_database\(\) <> 'verdify'/u);
  assert.match(sql, /current_schema\(\) = 'lab_reporting' AS reporting_search_path/u);
  assert.match(sql, /no_relation_writes/u);
  assert.match(sql, /count\(\*\) = 1 AS exactly_one/u);
  assert.match(sql, /LIMIT 2;/u);
  assert.doesNotMatch(sql, /^\s*(?:CREATE|ALTER|DROP|GRANT|REVOKE|INSERT|UPDATE|DELETE|TRUNCATE)\b/imu);
});

test("Grafana is private, anonymous-disabled, proxy-authenticated, and projection-only", () => {
  const { resources } = renderOverlay();
  const deployment = one(resources, "Deployment", "verdify-lab-reporting-tier");
  assert.equal(deployment.spec.replicas, 0);
  assert.equal(deployment.spec.template.spec.automountServiceAccountToken, false);
  const byName = new Map(deployment.spec.template.spec.containers.map((container) => [container.name, container]));
  assert.deepEqual([...byName.keys()], ["grafana", "renderer", "gateway"]);
  assert.equal(
    byName.get("grafana").image,
    "grafana/grafana:12.4.5@sha256:26b8f35a9e4e4431995cf64c3f396505a4faf17bcfc19f9ed84943ec6bfd5ecd",
  );
  assert.equal(
    byName.get("renderer").image,
    "grafana/grafana-image-renderer:v5.10.0@sha256:c0eb7b915a181c7bbe451718f9b633843678bef93703b5ed5fda2f28fa508986",
  );
  assert.equal(
    byName.get("gateway").image,
    "nginxinc/nginx-unprivileged:1.29.3-alpine@sha256:f7d0d0f2ebc0486dc110278672b9073f7fd641e58376b112b0c8865cf36d2e36",
  );
  const grafanaEnv = environment(byName.get("grafana"));
  assert.equal(grafanaEnv.get("GF_AUTH_ANONYMOUS_ENABLED").value, "false");
  assert.equal(grafanaEnv.get("GF_AUTH_BASIC_ENABLED").value, "false");
  assert.equal(grafanaEnv.get("GF_AUTH_PROXY_ENABLED").value, "true");
  assert.equal(grafanaEnv.get("GF_AUTH_PROXY_WHITELIST").value, "127.0.0.1");
  assert.equal(grafanaEnv.get("GF_USERS_AUTO_ASSIGN_ORG_ROLE").value, "Viewer");
  assert.equal(grafanaEnv.get("GF_SECURITY_ALLOW_EMBEDDING").value, "false");
  for (const [name, secret, key] of [
    ["GF_SECURITY_ADMIN_PASSWORD", "verdify-lab-reporting-runtime", "GRAFANA_ADMIN_PASSWORD"],
    ["GF_RENDERING_RENDERER_TOKEN", "verdify-lab-reporting-runtime", "GRAFANA_RENDERER_TOKEN"],
    ["PGUSER", "verdify-lab-reporting-reader", "PGUSER"],
    ["PGPASSWORD", "verdify-lab-reporting-reader", "PGPASSWORD"],
    ["PGDATABASE", "verdify-lab-reporting-reader", "PGDATABASE"],
  ]) {
    assert.deepEqual(grafanaEnv.get(name).valueFrom.secretKeyRef, { name: secret, key });
  }
  assert.equal(byName.get("grafana").envFrom, undefined);
  assert.deepEqual(
    environment(byName.get("renderer")).get("AUTH_TOKEN").valueFrom.secretKeyRef,
    { name: "verdify-lab-reporting-runtime", key: "GRAFANA_RENDERER_TOKEN" },
  );

  const provisioning = one(resources, "ConfigMap", "verdify-lab-reporting-provisioning");
  const datasources = YAML.parse(provisioning.data["datasources.yaml"]).datasources;
  assert.deepEqual(datasources.map(({ uid }) => uid), ["verdify-tsdb", "P44368ADAD746BC27"]);
  assert.equal(datasources.every(({ url }) => (
    url === "verdify-lab-reporting-projection.verdify-platform.svc.cluster.local:5432"
  )), true);
  assert.equal(datasources.every(({ jsonData }) => jsonData.sslmode === "require"), true);
  assert.equal(datasources.every(({ editable }) => editable === false), true);
  assert.doesNotMatch(provisioning.data["datasources.yaml"], /verdify-db|verdify-prod/u);

  const gateway = one(resources, "ConfigMap", "verdify-lab-reporting-gateway").data["default.conf"];
  for (const uid of DASHBOARD_UIDS) assert.match(gateway, new RegExp(`(?:\\||\\()${uid}(?:\\||\\))`, "u"));
  assert.match(gateway, /X-WEBAUTH-USER verdify-lab-renderer;/u);
  assert.match(gateway, /proxy_set_header Authorization "";/u);
  assert.match(gateway, /proxy_set_header Cookie "";/u);
  assert.match(gateway, /location \/ \{\s*return 404;/u);

  const service = one(resources, "Service", "verdify-lab-reporting-tier");
  assert.deepEqual(service.spec.ports, [
    { name: "http", port: 8080, protocol: "TCP", targetPort: "gateway" },
  ]);
});

test("producer contract is zero-replica, zero-digest, and missing every activation authority", () => {
  const { resources } = renderOverlay();
  const producer = one(resources, "Deployment", "verdify-lab-occurrence-producer");
  assert.equal(producer.spec.replicas, 0);
  assert.equal(producer.spec.template.spec.automountServiceAccountToken, false);
  assert.equal(producer.spec.template.spec.serviceAccountName, "verdify-lab-occurrence-producer");
  const container = producer.spec.template.spec.containers[0];
  assert.equal(
    container.image,
    `registry.vallery.net/verdifyconsultancy/verdify-lab-occurrence-exporter@sha256:${ZERO_DIGEST}`,
  );
  assert.deepEqual(container.command, ["node"]);
  assert.deepEqual(container.args, ["/app/scripts/run-reporting-occurrence-producer.mjs", "once"]);
  assert.equal(container.envFrom, undefined);
  const env = environment(container);
  assert.deepEqual([...env.keys()], [
    "LAB_OCCURRENCE_MANIFEST",
    "LAB_OCCURRENCE_POLICY",
    "LAB_REPORTING_TARGETS",
    "LAB_OCCURRENCE_OUTPUT_ROOT",
    "LAB_OCCURRENCE_STORE",
    "LAB_S3_ENDPOINT_URL",
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
  ]);
  for (const key of ["LAB_OCCURRENCE_STORE", "LAB_S3_ENDPOINT_URL", "AWS_DEFAULT_REGION"]) {
    assert.deepEqual(env.get(key).valueFrom.configMapKeyRef, {
      name: "verdify-lab-occurrence-store-metadata",
      key,
    });
  }
  for (const key of ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]) {
    assert.deepEqual(env.get(key).valueFrom.secretKeyRef, {
      name: "verdify-lab-occurrence-store-writer",
      key,
    });
  }
  const volumeConfigMaps = producer.spec.template.spec.volumes
    .filter(({ configMap }) => configMap)
    .map(({ configMap }) => configMap.name);
  assert.deepEqual(volumeConfigMaps, [
    "verdify-lab-occurrence-source-manifest",
    "verdify-lab-occurrence-export-policy",
    "verdify-lab-reporting-targets",
  ]);
  const renderedConfigMaps = new Set(resources.filter(({ kind }) => kind === "ConfigMap").map(({ metadata }) => metadata.name));
  assert.equal(renderedConfigMaps.has("verdify-lab-occurrence-source-manifest"), false);
  assert.equal(renderedConfigMaps.has("verdify-lab-occurrence-export-policy"), false);
  assert.equal(renderedConfigMaps.has("verdify-lab-occurrence-store-metadata"), false);

  const targetDocument = JSON.parse(
    one(resources, "ConfigMap", "verdify-lab-reporting-targets").data["reporting-targets.json"],
  );
  const targets = validateReportingTargets(targetDocument);
  assert.deepEqual(
    [targets.dashboardCount, targets.uniquePanelCount, targets.occurrenceCount],
    [18, 139, 143],
  );
});

test("NetworkPolicies expose only projection, gateway, and DNS before activation", () => {
  const { resources } = renderOverlay();
  const producer = one(resources, "NetworkPolicy", "verdify-lab-occurrence-producer-isolated");
  assert.deepEqual(producer.spec.policyTypes, ["Ingress", "Egress"]);
  assert.deepEqual(producer.spec.ingress, []);
  assert.equal(producer.spec.egress.length, 2);
  assert.deepEqual(producer.spec.egress[1], {
    to: [{ podSelector: { matchLabels: {
      "app.kubernetes.io/name": "verdify-lab-reporting-tier",
      "app.kubernetes.io/component": "lab-reporting-tier",
    } } }],
    ports: [{ protocol: "TCP", port: 8080 }],
  });
  assert.equal(JSON.stringify(producer).includes("ipBlock"), false);

  const tier = one(resources, "NetworkPolicy", "verdify-lab-reporting-tier-isolated");
  assert.equal(tier.spec.ingress.length, 1);
  assert.equal(tier.spec.ingress[0].ports[0].port, 8080);
  assert.equal(tier.spec.egress.length, 2);
  assert.equal(tier.spec.egress[1].ports[0].port, 5432);
  assert.equal(
    tier.spec.egress[1].to[0].podSelector.matchLabels["app.kubernetes.io/name"],
    "verdify-lab-reporting-projection",
  );

  const projection = one(resources, "NetworkPolicy", "verdify-lab-reporting-projection-ingress");
  assert.deepEqual(projection.spec.policyTypes, ["Ingress"]);
  assert.deepEqual(projection.spec.ingress.map(({ ports }) => ports[0].port), [5432, 8080]);
});
