import assert from "node:assert/strict";

import YAML from "yaml";

let rendered = "";
for await (const chunk of process.stdin) rendered += chunk;
const resources = YAML.parseAllDocuments(rendered).map((document) => document.toJSON()).filter(Boolean);
if (resources.length === 0) throw new Error("lab-stage kustomize render is empty");
const one = (kind, name) => {
  const matches = resources.filter((resource) => resource.kind === kind && resource.metadata.name === name);
  assert.equal(matches.length, 1, `expected exactly one ${kind}/${name}`);
  return matches[0];
};

for (const resource of resources) {
  assert.equal(resource.metadata.namespace, "verdify-platform");
  assert.equal(resource.metadata.labels["app.kubernetes.io/instance"], "verdify-platform-lab-stage");
}

const deployment = one("Deployment", "verdify-lab-astro-stage");
assert.equal(deployment.metadata.name, "verdify-lab-astro-stage");
assert.equal(deployment.spec.replicas, 2);
assert.equal(deployment.spec.template.spec.topologySpreadConstraints[0].whenUnsatisfiable, "DoNotSchedule");
assert.equal(deployment.spec.template.spec.containers[0].readinessProbe.httpGet.path, "/static-build.json");
assert.equal(deployment.spec.template.spec.containers[0].livenessProbe.httpGet.path, "/healthz");
assert.match(
  deployment.spec.template.spec.containers[0].image,
  /^registry\.vallery\.net\/verdifyconsultancy\/verdify-lab-astro@sha256:[0-9a-f]{64}$/,
  "rendered Lab stage image must be a digest-only fleet-origin reference",
);
assert.doesNotMatch(deployment.spec.template.spec.containers[0].image, /@sha256:0{64}$/u);

const service = one("Service", "verdify-lab-astro-stage");
assert.equal(service.metadata.name, "verdify-lab-astro-stage");
assert.equal(service.spec.ports[0].port, 80);
assert.equal(service.spec.ports[0].targetPort, "http");

const ingress = one("IngressRoute", "verdify-lab-astro-stage");
assert.equal(ingress.spec.routes[0].match, "Host(`lab-stage.verdify.ai`)");
assert.equal(ingress.spec.routes[0].priority, 100);
assert.ok(ingress.spec.routes[0].priority > 50, "exact stage route must outrank the shared wildcard");
assert.equal(ingress.spec.routes[0].services[0].name, "verdify-lab-astro-stage");
assert.equal(ingress.spec.routes[0].services[0].port, 80);
assert.doesNotMatch(rendered, /Host\(`lab\.verdify\.ai`\)/);

const policy = one("NetworkPolicy", "verdify-lab-astro-stage-static-only");
assert.deepEqual(policy.spec.egress, []);
assert.deepEqual(
  policy.spec.ingress[0].from.map((peer) => peer.namespaceSelector.matchLabels["kubernetes.io/metadata.name"]),
  ["traefik-apps", "traefik"],
);

const runtime = one("Deployment", "verdify-lab-release-runtime");
assert.equal(runtime.spec.replicas, 0, "release runtime must remain dormant in the stage source");
assert.equal(runtime.metadata.annotations?.["verdify.ai/object-store-endpoint"], undefined);
const runtimeContainers = [
  ...(runtime.spec.template.spec.initContainers ?? []),
  ...(runtime.spec.template.spec.containers ?? []),
];
assert.deepEqual(
  new Set(runtimeContainers.map((container) => container.image.replace(/@sha256:[0-9a-f]{64}$/u, ""))),
  new Set([
    "registry.vallery.net/verdifyconsultancy/verdify-lab-release-agent",
    "registry.vallery.net/verdifyconsultancy/verdify-lab-release-nginx",
  ]),
);
for (const container of runtimeContainers) {
  assert.match(
    container.image,
    /^registry\.vallery\.net\/verdifyconsultancy\/verdify-lab-release-(?:agent|nginx)@sha256:[0-9a-f]{64}$/u,
    `${container.name} must use a digest-only fleet-origin image`,
  );
  const environment = container.env ?? [];
  assert.ok(environment.every(({ name }) => !name.startsWith("AWS_") && !name.includes("S3")));
  if (container.name === "site") {
    assert.deepEqual(environment, []);
  } else {
    assert.equal(environment.find(({ name }) => name === "LAB_RELEASE_STORE")?.value, "/unconfigured/verdify-lab-release-store");
  }
}

const runtimeService = one("Service", "verdify-lab-release-runtime");
assert.equal(runtimeService.spec.type, "ClusterIP");
assert.equal(runtimeService.spec.ports[0].targetPort, "http");
const routedServices = resources
  .filter((resource) => resource.kind === "IngressRoute")
  .flatMap((resource) => resource.spec.routes ?? [])
  .flatMap((route) => route.services ?? [])
  .map((routeService) => routeService.name);
assert.deepEqual(routedServices, ["verdify-lab-astro-stage"], "the dormant runtime must have no route");

const runtimePolicy = one("NetworkPolicy", "verdify-lab-release-runtime-isolated");
assert.deepEqual(runtimePolicy.spec.egress, [], "the dormant runtime must have no egress");

const zeroDigestImages = [];
for (const workload of resources.filter((resource) => resource.kind === "Deployment")) {
  const containers = [
    ...(workload.spec.template.spec.initContainers ?? []),
    ...(workload.spec.template.spec.containers ?? []),
  ];
  for (const container of containers) {
    if (/@sha256:0{64}$/u.test(container.image)) {
      zeroDigestImages.push(`${workload.metadata.name}/${container.name}`);
      assert.equal(workload.spec.replicas, 0, "zero-digest images are valid only on replicas: 0 workloads");
    }
  }
}
if (zeroDigestImages.length > 0) {
  assert.deepEqual(zeroDigestImages.sort(), [
    "verdify-lab-release-runtime/hydrate-known-good",
    "verdify-lab-release-runtime/release-reconciler",
    "verdify-lab-release-runtime/site",
  ], "runtime image sentinels must be replaced together by the trusted pin workflow");
}
process.stdout.write(`verified ${resources.length} rendered lab-stage resources\n`);
