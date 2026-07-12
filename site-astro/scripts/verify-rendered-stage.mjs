import assert from "node:assert/strict";

import YAML from "yaml";

let rendered = "";
for await (const chunk of process.stdin) rendered += chunk;
const resources = YAML.parseAllDocuments(rendered).map((document) => document.toJSON()).filter(Boolean);
if (resources.length === 0) throw new Error("lab-stage kustomize render is empty");
const one = (kind) => {
  const matches = resources.filter((resource) => resource.kind === kind);
  assert.equal(matches.length, 1, `expected exactly one ${kind}`);
  return matches[0];
};

for (const resource of resources) {
  assert.equal(resource.metadata.namespace, "verdify-platform");
  assert.equal(resource.metadata.labels["app.kubernetes.io/instance"], "verdify-platform-lab-stage");
}

const deployment = one("Deployment");
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

const service = one("Service");
assert.equal(service.metadata.name, "verdify-lab-astro-stage");
assert.equal(service.spec.ports[0].port, 80);
assert.equal(service.spec.ports[0].targetPort, "http");

const ingress = one("IngressRoute");
assert.equal(ingress.spec.routes[0].match, "Host(`lab-stage.verdify.ai`)");
assert.equal(ingress.spec.routes[0].priority, 100);
assert.ok(ingress.spec.routes[0].priority > 50, "exact stage route must outrank the shared wildcard");
assert.equal(ingress.spec.routes[0].services[0].name, "verdify-lab-astro-stage");
assert.equal(ingress.spec.routes[0].services[0].port, 80);
assert.doesNotMatch(rendered, /Host\(`lab\.verdify\.ai`\)/);

const policy = one("NetworkPolicy");
assert.deepEqual(policy.spec.egress, []);
assert.deepEqual(
  policy.spec.ingress[0].from.map((peer) => peer.namespaceSelector.matchLabels["kubernetes.io/metadata.name"]),
  ["traefik-apps", "traefik"],
);
process.stdout.write(`verified ${resources.length} rendered lab-stage resources\n`);
