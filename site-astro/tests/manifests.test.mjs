import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdir, mkdtemp, readdir, readFile, readlink, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import YAML from "yaml";

import { reconcileOnce, recordReconcileFailure, runReleaseCli, runtimeConfig } from "../release-runtime/reconcile.mjs";
import { buildBakedSiteBundle } from "../scripts/build-baked-site-bundle.mjs";
import {
  inventoryBuiltSite,
  publishSiteRelease,
  siteContentIdentitySha256,
  siteReleasePayloadSha256,
} from "../scripts/lib/site-release-store.mjs";

const SITE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(SITE_ROOT, "..");

const documents = YAML.parseAllDocuments(
  await readFile(path.join(REPO_ROOT, "deploy/k8s/components/lab-astro-stage/workload.yaml"), "utf8"),
).map((document) => document.toJSON());
const byKind = (kind) => documents.find((document) => document.kind === kind);

test("stage workload is two replicas on distinct nodes with a digest-only image sentinel", () => {
  const deployment = byKind("Deployment");
  assert.equal(deployment.spec.replicas, 2);
  assert.equal(deployment.spec.template.spec.topologySpreadConstraints[0].topologyKey, "kubernetes.io/hostname");
  assert.equal(deployment.spec.template.spec.topologySpreadConstraints[0].whenUnsatisfiable, "DoNotSchedule");
  assert.equal(deployment.spec.template.spec.automountServiceAccountToken, false);
  assert.equal(deployment.spec.template.spec.containers[0].readinessProbe.httpGet.path, "/static-build.json");
  assert.equal(deployment.spec.template.spec.containers[0].livenessProbe.httpGet.path, "/healthz");
  assert.match(
    deployment.spec.template.spec.containers[0].image,
    /@sha256:0{64}$/,
  );
});

test("stage Service exposes 80 to nginx 8080 and ingress is Traefik-only", () => {
  const service = byKind("Service");
  assert.equal(service.spec.ports[0].port, 80);
  assert.equal(service.spec.ports[0].targetPort, "http");

  const policy = byKind("NetworkPolicy");
  assert.deepEqual(policy.spec.policyTypes, ["Ingress", "Egress"]);
  assert.deepEqual(policy.spec.egress, []);
  assert.equal(policy.spec.ingress[0].ports[0].port, 8080);
  assert.deepEqual(
    policy.spec.ingress[0].from.map((peer) => ({
      namespace: peer.namespaceSelector.matchLabels["kubernetes.io/metadata.name"],
      pod: peer.podSelector.matchLabels["app.kubernetes.io/name"],
    })),
    [
      { namespace: "traefik-apps", pod: "traefik" },
      { namespace: "traefik", pod: "traefik" },
    ],
  );
});

test("runtime CSP permits Pagefind WASM without broad eval or cross-origin media", async () => {
  const nginx = await readFile(path.join(SITE_ROOT, "nginx/default.conf"), "utf8");
  const securityHeaders = await readFile(path.join(SITE_ROOT, "nginx/security-headers.inc"), "utf8");
  const csp = securityHeaders.match(/add_header Content-Security-Policy "([^"]+)" always;/)?.[1];
  assert.ok(csp);
  assert.match(csp, /img-src 'self' data:;/);
  assert.match(csp, /font-src 'self';/);
  assert.match(csp, /script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval';/);
  assert.doesNotMatch(csp, /(?:^|\s)'unsafe-eval'(?:\s|;|$)/);
  assert.match(csp, /connect-src 'self' https:\/\/api\.verdify\.ai;/);
  assert.match(csp, /form-action 'self' https:\/\/verdify\.ai https:\/\/api\.verdify\.ai/);
  assert.doesNotMatch(csp, /\*/);
  assert.doesNotMatch(csp, /cloudflareinsights/);
  assert.doesNotMatch(csp.match(/img-src ([^;]+)/)?.[1] ?? "", /api\.verdify\.ai/);
  assert.equal(
    nginx.match(/include \/etc\/nginx\/conf\.d\/security-headers\.inc;/g)?.length,
    9,
    "server and every location must explicitly include security headers because add_header does not inherit",
  );
  for (const name of ["X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "X-Robots-Tag"]) {
    assert.match(securityHeaders, new RegExp(`add_header ${name} `));
  }
  assert.match(nginx, /application\/vnd\.apple\.mpegurl m3u8/);
  assert.match(nginx, /video\/mp2t ts/);
  assert.match(nginx, /add_header Accept-Ranges "bytes" always/);
  assert.match(nginx, /evidence\/blobs\/sha256\/\[a-f0-9\]\{64\}/);
  assert.match(nginx, /max-age=31536000, immutable/);
});

test("default Docker target serves only the real attested build", async () => {
  const dockerfile = await readFile(path.join(SITE_ROOT, "Dockerfile"), "utf8");
  const stages = [...dockerfile.matchAll(/^FROM\s+\S+(?:\s+AS\s+(\S+))?\s*$/gmi)].map((match) => match[1] ?? "");
  assert.equal(stages.at(-1), "runtime", "the final implicit Docker target must be the real runtime");
  const finalStage = dockerfile.slice(dockerfile.lastIndexOf("FROM runtime-base AS runtime"));
  assert.match(finalStage, /COPY --from=build \/app\/dist\/ \/usr\/share\/nginx\/html\//);
  assert.doesNotMatch(finalStage, /fixture-build|fixture-runtime|ALLOW_SYNTHETIC_FIXTURE=true/);
});

test("Phase 4c reporting boundary is deny-all and impossible to activate from current overlays", async () => {
  const boundaryRoot = path.join(REPO_ROOT, "deploy/k8s/components/lab-occurrence-reporting-boundary");
  const boundary = YAML.parseAllDocuments(await readFile(path.join(boundaryRoot, "boundary.yaml"), "utf8"))
    .map((document) => document.toJSON());
  assert.deepEqual(boundary.map((document) => document.kind), ["ConfigMap", "ServiceAccount", "NetworkPolicy"]);
  const config = boundary[0].data;
  assert.equal(config.activation, "blocked");
  assert.equal(config.reportingFeedAuthority, "operator-owned");
  assert.equal(config.reportingFeedDirection, "one-way-read-only");
  assert.equal(config.reportingCredentialClass, "reporting-read-only");
  assert.equal(config.trackAPrimaryRoleAllowed, "false");
  assert.equal(config.existingAnonymousGraphsAllowed, "false");
  assert.equal(config.cameraMethod, "GET");
  assert.equal(config.cameraRedirectsAllowed, "false");
  assert.equal(config.cameraAuthorization, "forbidden");
  assert.match(config.cameraSanitization, /decode-reencode.*metadata-free/);
  assert.equal(config.futureEgressContract, "api.verdify.ai:443-and-occurrence-store-only");
  assert.equal(boundary[1].automountServiceAccountToken, false);
  assert.deepEqual(boundary[2].spec.policyTypes, ["Ingress", "Egress"]);
  assert.deepEqual(boundary[2].spec.ingress, []);
  assert.deepEqual(boundary[2].spec.egress, []);

  const component = YAML.parse(await readFile(path.join(boundaryRoot, "kustomization.yaml"), "utf8"));
  assert.equal(component.kind, "Component");
  assert.deepEqual(component.resources, ["boundary.yaml"]);

  async function kustomizations(directory) {
    const files = [];
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name);
      if (entry.isDirectory()) files.push(...await kustomizations(target));
      if (entry.isFile() && entry.name === "kustomization.yaml") files.push(target);
    }
    return files;
  }
  for (const file of await kustomizations(path.join(REPO_ROOT, "deploy/k8s"))) {
    if (file.startsWith(`${boundaryRoot}${path.sep}`)) continue;
    assert.doesNotMatch(await readFile(file, "utf8"), /lab-occurrence-reporting-boundary/);
  }
});

test("release runtime candidate is a two-node, no-PVC, no-route read-only cache", () => {
  const candidate = path.join(REPO_ROOT, "deploy/k8s/candidates/lab-release-runtime");
  const rendered = spawnSync("kubectl", ["kustomize", candidate], {
    cwd: REPO_ROOT,
    encoding: "utf8",
    timeout: 30_000,
  });
  assert.equal(rendered.status, 0, rendered.stderr);
  const resources = YAML.parseAllDocuments(rendered.stdout).map((document) => document.toJSON()).filter(Boolean);
  assert.equal(resources.length, 4);
  const one = (kind) => resources.find((resource) => resource.kind === kind);
  for (const resource of resources) {
    assert.equal(resource.metadata.namespace, "verdify-platform");
    assert.equal(resource.metadata.labels["verdify.ai/traffic-state"], "disconnected");
  }

  const deployment = one("Deployment");
  assert.equal(deployment.spec.replicas, 2);
  assert.equal(deployment.spec.template.spec.automountServiceAccountToken, false);
  assert.equal(deployment.spec.template.spec.topologySpreadConstraints[0].topologyKey, "kubernetes.io/hostname");
  assert.equal(deployment.spec.template.spec.topologySpreadConstraints[0].whenUnsatisfiable, "DoNotSchedule");
  assert.deepEqual(deployment.spec.template.spec.topologySpreadConstraints[0].matchLabelKeys, ["pod-template-hash"]);
  assert.equal(deployment.spec.template.spec.initContainers.length, 1);
  assert.equal(deployment.spec.template.spec.initContainers[0].name, "hydrate-known-good");
  assert.deepEqual(
    deployment.spec.template.spec.initContainers[0].command,
    ["/app/release-runtime/entrypoint.sh"],
  );
  assert.deepEqual(deployment.spec.template.spec.initContainers[0].args, ["init"]);
  assert.deepEqual(deployment.spec.template.spec.containers.map((container) => container.name), ["site", "release-reconciler"]);
  assert.deepEqual(deployment.spec.template.spec.containers[0].command, ["nginx"]);
  assert.deepEqual(deployment.spec.template.spec.containers[0].args, ["-g", "daemon off;"]);
  assert.deepEqual(
    deployment.spec.template.spec.containers[1].command,
    ["/app/release-runtime/entrypoint.sh"],
  );
  assert.deepEqual(deployment.spec.template.spec.containers[1].args, ["reconcile"]);
  assert.equal(deployment.spec.template.spec.containers[0].readinessProbe.httpGet.path, "/readyz");
  assert.equal(deployment.spec.template.spec.containers[0].livenessProbe.httpGet.path, "/healthz");
  assert.equal(
    deployment.spec.template.spec.containers[0].volumeMounts.find((mount) => mount.name === "release-cache").readOnly,
    true,
  );
  const siteMounts = deployment.spec.template.spec.containers[0].volumeMounts;
  assert.equal(
    siteMounts.find((mount) => mount.name === "release-state")?.mountPath,
    "/run/verdify-lab-release",
  );
  assert.equal(siteMounts.find((mount) => mount.name === "release-state")?.readOnly, true);
  assert.equal(
    siteMounts.some((mount) => mount.mountPath === "/var/run"),
    false,
    "a broad /var/run mount aliases /run in nginx-unprivileged and masks the release-state submount",
  );
  assert.equal(
    deployment.spec.template.spec.volumes.some((volume) => volume.name === "nginx-run"),
    false,
  );
  for (const container of [...deployment.spec.template.spec.initContainers, ...deployment.spec.template.spec.containers]) {
    assert.equal(container.securityContext.readOnlyRootFilesystem, true);
    assert.deepEqual(container.securityContext.capabilities.drop, ["ALL"]);
    assert.equal(container.envFrom, undefined);
    assert.ok((container.env ?? []).every(({ name }) => !name.startsWith("AWS_") && !name.includes("S3")));
    if (container.name === "site") {
      assert.deepEqual(container.env ?? [], []);
    } else {
      assert.equal(
        (container.env ?? []).find(({ name }) => name === "LAB_RELEASE_STORE")?.value,
        "/unconfigured/verdify-lab-release-store",
      );
    }
    assert.match(container.image, /^registry\.vallery\.net\/verdifyconsultancy\/verdify-lab-release-(?:agent|nginx)@sha256:0{64}$/u);
  }
  assert.equal(deployment.spec.template.metadata.annotations["verdify.ai/object-store-endpoint"], undefined);
  assert.ok(deployment.spec.template.spec.volumes.every((volume) => volume.emptyDir && !volume.persistentVolumeClaim));
  assert.doesNotMatch(rendered.stdout, /secretKeyRef|secretRef|PersistentVolumeClaim|IngressRoute|kind: Ingress\b/u);

  const service = one("Service");
  assert.equal(service.spec.type, "ClusterIP");
  assert.equal(service.spec.ports[0].port, 80);
  assert.equal(service.spec.ports[0].targetPort, "http");
  assert.equal(one("PodDisruptionBudget").spec.minAvailable, 1);

  const policy = one("NetworkPolicy");
  assert.deepEqual(policy.spec.policyTypes, ["Ingress", "Egress"]);
  assert.deepEqual(policy.spec.ingress[0].from, [{
    podSelector: { matchLabels: { "verdify.ai/lab-canary-client": "true" } },
  }]);
  assert.deepEqual(policy.spec.egress, []);
});

test("release runtime images bake a real digest-bound fallback and serve only the atomic symlink", async () => {
  const dockerfile = await readFile(path.join(SITE_ROOT, "Dockerfile.release-runtime"), "utf8");
  assert.match(dockerfile, /^FROM node:22\.22\.0-alpine3\.22@sha256:[0-9a-f]{64} AS dependencies$/mu);
  assert.match(dockerfile, /^FROM nginxinc\/nginx-unprivileged:1\.29\.3-alpine@sha256:[0-9a-f]{64} AS site$/mu);
  assert.match(dockerfile, /ARG LAB_RUNTIME_BUILDER_COMMIT/u);
  assert.match(dockerfile, /ARG LAB_RUNTIME_RELEASED_AT/u);
  assert.match(dockerfile, /build-baked-site-bundle\.mjs/u);
  assert.match(dockerfile, /COPY --from=build \/image\/known-good\/ \/opt\/verdify\/lab-known-good\//u);
  assert.doesNotMatch(dockerfile, /AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|secretKeyRef/u);

  const nginx = await readFile(path.join(SITE_ROOT, "release-runtime/nginx.conf"), "utf8");
  assert.match(nginx, /root \/srv\/lab-cache\/current\/tree;/u);
  assert.match(nginx, /location = \/metrics/u);
  assert.match(nginx, /location = \/\.well-known\/verdify-release\.json/u);
  assert.match(nginx, /location = \/readyz/u);

  assert.throws(
    () => runtimeConfig({ LAB_RELEASE_STORE: "s3://verdify-platform/lab/releases" }),
    /runtime S3 LAB_S3_ENDPOINT_URL is required/u,
  );
  const s3Environment = {
    LAB_RELEASE_STORE: "s3://verdify-platform/lab/releases",
    LAB_S3_ENDPOINT_URL: "https://s3-hdd.vallery.net",
    AWS_DEFAULT_REGION: "garage",
    AWS_ACCESS_KEY_ID: "fixture-access-key",
    AWS_SECRET_ACCESS_KEY: "fixture-secret-key",
  };
  const config = runtimeConfig(s3Environment);
  assert.equal(config.store, "s3://verdify-platform/lab/releases");
  assert.deepEqual(config.cliEnvironment, {
    LAB_S3_ENDPOINT_URL: "https://s3-hdd.vallery.net",
    AWS_DEFAULT_REGION: "garage",
    AWS_ACCESS_KEY_ID: "fixture-access-key",
    AWS_SECRET_ACCESS_KEY: "fixture-secret-key",
  });
  assert.equal(Object.isFrozen(config.cliEnvironment), true);
});

test("release reconciler CLI forwards only the store-specific environment allowlist", async (context) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-release-cli-environment-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  const cli = path.join(root, "report-environment.mjs");
  await writeFile(cli, [
    "const result = { keys: Object.keys(process.env).sort() };",
    "process.stdout.write(`${JSON.stringify(result, null, 2)}\\n`);",
    "",
  ].join("\n"));
  const base = {
    cli,
    cliTimeoutSeconds: 5,
  };

  const local = await runReleaseCli({
    ...base,
    store: path.join(root, "local-store"),
    cliEnvironment: {
      AWS_ACCESS_KEY_ID: "ambient-access-key",
      AWS_SECRET_ACCESS_KEY: "ambient-secret-key",
      AWS_SESSION_TOKEN: "ambient-session-token",
    },
  }, ["ignored"]);
  assert.deepEqual(local.keys, []);

  const s3 = await runReleaseCli({
    ...base,
    store: "s3://verdify-platform/lab/releases",
    cliEnvironment: {
      LAB_S3_ENDPOINT_URL: "https://s3-hdd.vallery.net",
      AWS_DEFAULT_REGION: "garage",
      AWS_ACCESS_KEY_ID: "fixture-access-key",
      AWS_SECRET_ACCESS_KEY: "fixture-secret-key",
      AWS_SESSION_TOKEN: "must-not-be-forwarded",
      HOME: "/must/not/be/forwarded",
    },
  }, ["ignored"]);
  assert.deepEqual(s3.keys, [
    "AWS_ACCESS_KEY_ID",
    "AWS_DEFAULT_REGION",
    "AWS_SECRET_ACCESS_KEY",
    "LAB_S3_ENDPOINT_URL",
  ]);
});

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function createReleaseBuild(root, title) {
  await writeFile(path.join(root, "index.html"), `<!doctype html><title>${title}</title>\n`);
  await writeFile(path.join(root, "static-build.json"), `${JSON.stringify({
    contract: "verdify.lab-astro-stage-build",
    schemaVersion: 1,
    siteOrigin: "https://lab-stage.verdify.ai",
    stageGlobalNoindex: true,
    approvalEligible: false,
    snapshotManifestDigest: `sha256:${"1".repeat(64)}`,
    sanitization: {
      fixtureOnly: false,
      policyVersion: "verdify-public-output-stage-v1",
    },
  }, null, 2)}\n`);
}

async function publishTestRelease({ storeRoot, buildRoot, builderCommit, releasedAt, expectedSelectionSha256 = null }) {
  const inventory = await inventoryBuiltSite(buildRoot);
  const files = inventory.files.map(({ sourcePath: _sourcePath, ...record }) => record);
  const sourceSnapshotManifestSha256 = "1".repeat(64);
  const policyVersion = "verdify-public-output-stage-v1";
  const contentIdentitySha256 = siteContentIdentitySha256({
    sourceSnapshotManifestSha256,
    policyVersion,
    builderCommit,
    files,
  });
  const payloadSha256 = siteReleasePayloadSha256({
    sourceSnapshotManifestSha256,
    policyVersion,
    builderCommit,
    contentIdentitySha256,
  });
  return publishSiteRelease({
    storeRoot,
    buildRoot,
    event: {
      contract: "verdify.lab-release-trigger",
      schemaVersion: 1,
      eventId: `evt_runtime_${builderCommit.slice(0, 16)}`,
      eventType: "reconciliation",
      sourceId: "release-runtime-test",
      sourceWatermark: builderCommit,
      occurredAt: releasedAt,
      payloadSha256,
    },
    sourceSnapshotManifestSha256,
    policyVersion,
    builderCommit,
    releasedAt,
    expectedSelectionSha256,
  });
}

test("release reconciler cold-starts baked, consumes an immutable selection, and preserves it on outage", async (context) => {
  const root = await mkdtemp(path.join(tmpdir(), "verdify-release-runtime-test-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  const bakedBuild = path.join(root, "baked-build");
  const selectedBuild = path.join(root, "selected-build");
  const store = path.join(root, "store");
  const cache = path.join(root, "cache");
  const state = path.join(root, "state");
  const bundle = path.join(root, "bundle");
  for (const directory of [bakedBuild, selectedBuild, store, cache, state]) {
    await mkdir(directory);
  }
  await createReleaseBuild(bakedBuild, "baked");
  await createReleaseBuild(selectedBuild, "selected");
  const bakedCommit = "a".repeat(40);
  const selectedCommit = "b".repeat(40);
  await buildBakedSiteBundle({
    buildRoot: bakedBuild,
    destination: bundle,
    builderCommit: bakedCommit,
    releasedAt: "2026-07-13T00:00:00.000Z",
  });

  const baseConfig = {
    store: path.join(root, "absent-store"),
    cacheRoot: cache,
    bakedBundleRoot: bundle,
    stateRoot: state,
    cli: path.join(SITE_ROOT, "scripts/manage-site-release.mjs"),
    reconcileSeconds: 60,
    verifySeconds: 900,
    cliTimeoutSeconds: 30,
  };
  const cold = await reconcileOnce(baseConfig, { now: "2026-07-13T00:01:00.000Z", initial: true });
  assert.equal(cold.source, "baked-known-good");
  assert.equal(cold.triggerKind, "baked-known-good");
  assert.match(await readlink(path.join(cache, "current")), /^generations\/[0-9a-f]{64}-[0-9a-f-]{36}$/u);

  const selected = await publishTestRelease({
    storeRoot: store,
    buildRoot: selectedBuild,
    builderCommit: selectedCommit,
    releasedAt: "2026-07-13T00:02:00.000Z",
  });
  const configured = { ...baseConfig, store };
  const promoted = await reconcileOnce(configured, { now: "2026-07-13T00:03:00.000Z" });
  assert.equal(promoted.source, "store-current");
  assert.equal(promoted.releaseSha256, selected.releaseSha256);
  assert.equal(promoted.selectionSha256, selected.selectionSha256);
  assert.equal(promoted.triggerKind, "immutable-selection-digest");
  assert.equal(promoted.consecutiveFailures, 0);
  assert.match(await readFile(path.join(state, "metrics"), "utf8"), /verdify_lab_release_reconcile_success 1/u);

  const preservedLink = await readlink(path.join(cache, "current"));
  const outage = await reconcileOnce(baseConfig, { now: "2026-07-13T00:04:00.000Z" });
  assert.equal(outage.releaseSha256, promoted.releaseSha256);
  assert.equal(outage.health, "degraded");
  assert.equal(outage.consecutiveFailures, 1);
  assert.equal(await readlink(path.join(cache, "current")), preservedLink);
  assert.equal(sha256(await readFile(path.join(state, "release.json"))), sha256(Buffer.from(`${JSON.stringify(outage, null, 2)}\n`)));
  assert.match(await readFile(path.join(state, "metrics"), "utf8"), /verdify_lab_release_reconcile_success 0/u);

  const changedTrigger = {
    contract: "verdify.lab-site-release-status",
    schemaVersion: 1,
    selectionSha256: "c".repeat(64),
    generation: 2,
    ready: true,
    health: "ready",
    current: {
      releaseSha256: "d".repeat(64),
      freshness: promoted.freshness,
    },
    previous: null,
  };
  await assert.rejects(
    () => reconcileOnce(configured, {
      now: "2026-07-13T00:05:00.000Z",
      cliRunner: async (_config, args) => {
        if (args[0] === "status") return changedTrigger;
        throw new Error("injected hydrate failure");
      },
    }),
    /injected hydrate failure/u,
  );
  const failed = await recordReconcileFailure(configured, "2026-07-13T00:05:00.000Z");
  assert.equal(failed.releaseSha256, promoted.releaseSha256);
  assert.equal(failed.consecutiveFailures, 2);
  assert.equal(await readlink(path.join(cache, "current")), preservedLink);
});
