import assert from "node:assert/strict";

import YAML from "yaml";

let rendered = "";
for await (const chunk of process.stdin) rendered += chunk;
const resources = YAML.parseAllDocuments(rendered).map((document) => document.toJSON()).filter(Boolean);
assert.equal(resources.length, 4, "production candidate must render exactly four isolated resources");

const one = (kind) => {
  const matches = resources.filter((resource) => resource.kind === kind);
  assert.equal(matches.length, 1, `expected exactly one ${kind}`);
  return matches[0];
};
for (const resource of resources) {
  assert.equal(resource.metadata.namespace, "verdify-prod");
  assert.equal(resource.metadata.labels["app.kubernetes.io/instance"], "verdify-lab-astro-green");
  assert.equal(resource.metadata.labels["verdify.ai/environment"], "production");
  assert.equal(resource.metadata.labels["verdify.ai/release-track"], "green");
  assert.equal(resource.metadata.labels["verdify.ai/traffic-state"], "disconnected");
}

const deployment = one("Deployment");
assert.equal(deployment.metadata.name, "verdify-lab-astro-green");
assert.equal(deployment.spec.replicas, 2);
assert.equal(deployment.spec.template.spec.automountServiceAccountToken, false);
assert.equal(deployment.spec.template.spec.priorityClassName, "verdify-serving");
assert.equal(deployment.spec.strategy.rollingUpdate.maxUnavailable, 0);
assert.equal(deployment.spec.template.spec.topologySpreadConstraints[0].maxSkew, 1);
assert.equal(deployment.spec.template.spec.topologySpreadConstraints[0].topologyKey, "kubernetes.io/hostname");
assert.equal(deployment.spec.template.spec.topologySpreadConstraints[0].whenUnsatisfiable, "DoNotSchedule");
assert.deepEqual(deployment.spec.template.spec.topologySpreadConstraints[0].matchLabelKeys, ["pod-template-hash"]);
assert.match(
  deployment.spec.template.spec.containers[0].image,
  /^registry\.vallery\.net\/verdifyconsultancy\/verdify-lab-astro@sha256:0{64}$/u,
  "candidate image must remain the visible digest-only non-deployable sentinel",
);
assert.equal(deployment.spec.template.spec.containers[0].readinessProbe.httpGet.path, "/static-build.json");
assert.equal(deployment.spec.template.spec.containers[0].livenessProbe.httpGet.path, "/healthz");
assert.ok(
  deployment.spec.template.spec.volumes.every((volume) => volume.emptyDir && !volume.persistentVolumeClaim),
  "production candidate cannot mount the Quartz cache or any shared RWO PVC",
);

const service = one("Service");
assert.equal(service.metadata.name, "verdify-lab-astro-green");
assert.equal(service.spec.type, "ClusterIP");
assert.equal(service.spec.ports[0].port, 80);
assert.equal(service.spec.ports[0].targetPort, "http");

const budget = one("PodDisruptionBudget");
assert.equal(budget.spec.minAvailable, 1);

const policy = one("NetworkPolicy");
assert.deepEqual(policy.spec.policyTypes, ["Ingress", "Egress"]);
assert.deepEqual(policy.spec.egress, []);
assert.deepEqual(policy.spec.ingress[0].from, [{
  podSelector: { matchLabels: { "verdify.ai/lab-canary-client": "true" } },
}]);
assert.equal(policy.spec.ingress[0].ports[0].port, 8080);

for (const forbiddenKind of ["Ingress", "IngressRoute", "HTTPRoute", "Gateway", "PersistentVolumeClaim"]) {
  assert.equal(resources.some((resource) => resource.kind === forbiddenKind), false, `${forbiddenKind} is forbidden in the disconnected candidate`);
}
assert.doesNotMatch(rendered, /Host\(|lab\.verdify\.ai|name:\s+verdify-lab(?:\s|$)/u);
process.stdout.write("verified disconnected two-node Astro production candidate\n");
