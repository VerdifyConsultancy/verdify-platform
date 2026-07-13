import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import YAML from "yaml";

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
