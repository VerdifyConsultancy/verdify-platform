import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
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

test("runtime CSP permits only the preserved API image, fetch, and form origin", async () => {
  const nginx = await readFile(path.join(SITE_ROOT, "nginx/default.conf"), "utf8");
  const securityHeaders = await readFile(path.join(SITE_ROOT, "nginx/security-headers.inc"), "utf8");
  const csp = securityHeaders.match(/add_header Content-Security-Policy "([^"]+)" always;/)?.[1];
  assert.ok(csp);
  assert.match(csp, /img-src 'self' data: https:\/\/api\.verdify\.ai;/);
  assert.match(csp, /connect-src 'self' https:\/\/api\.verdify\.ai;/);
  assert.match(csp, /form-action 'self' https:\/\/verdify\.ai https:\/\/api\.verdify\.ai/);
  assert.doesNotMatch(csp, /\*/);
  assert.equal(
    nginx.match(/include \/etc\/nginx\/conf\.d\/security-headers\.inc;/g)?.length,
    8,
    "server and every location must explicitly include security headers because add_header does not inherit",
  );
  for (const name of ["X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "X-Robots-Tag"]) {
    assert.match(securityHeaders, new RegExp(`add_header ${name} `));
  }
  assert.match(nginx, /application\/vnd\.apple\.mpegurl m3u8/);
  assert.match(nginx, /video\/mp2t ts/);
  assert.match(nginx, /add_header Accept-Ranges "bytes" always/);
});

test("default Docker target serves only the real attested build", async () => {
  const dockerfile = await readFile(path.join(SITE_ROOT, "Dockerfile"), "utf8");
  const stages = [...dockerfile.matchAll(/^FROM\s+\S+(?:\s+AS\s+(\S+))?\s*$/gmi)].map((match) => match[1] ?? "");
  assert.equal(stages.at(-1), "runtime", "the final implicit Docker target must be the real runtime");
  const finalStage = dockerfile.slice(dockerfile.lastIndexOf("FROM runtime-base AS runtime"));
  assert.match(finalStage, /COPY --from=build \/app\/dist\/ \/usr\/share\/nginx\/html\//);
  assert.doesNotMatch(finalStage, /fixture-build|fixture-runtime|ALLOW_SYNTHETIC_FIXTURE=true/);
});
