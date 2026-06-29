# Environment GitOps Reconciliation

Use this reference when platform readiness, wave planning, release
verification, or an OpenClaw release workflow depends on GitOps or environment
state.

`environment-gitops` is a mode-first capability owned initially by
`platform-readiness`. It records desired state, observed controller state,
namespace controls, deployment evidence, runtime health, drift, remediation,
review, and handoff. It does not grant permission to mutate environments.

## Inputs

- GitOps desired-state refs: Git commit, manifest path, Helm chart,
  Kustomization, image digest, workflow run, or deployment record.
- Controller observations: Argo CD Application/ApplicationSet, Flux
  Kustomization/HelmRelease, GitHub deployment environment, or explicit manual
  controller.
- Namespace controls: ResourceQuota, LimitRange, NetworkPolicy, service
  account, RBAC summary, secrets model, PVC/storage, and cleanup policy.
- Runtime evidence: endpoint health, probes, smoke tests, logs, metrics,
  traces, deployment markers, migration evidence, and rollback refs.

## Procedure

1. Identify the environment name, type, cluster, namespace, URL, protection
   level, and TTL or cleanup policy.
2. Record desired state separately from observed state. Name the source of truth
   and exact revision or artifact refs.
3. Record GitOps controller status: controller kind, app refs, sync status,
   health status, observed timestamp, and evidence refs.
4. Record namespace controls and missing controls explicitly.
5. Record deployment evidence: GitHub environment, deployment IDs, workflow
   runs, checks, observed revision, image refs, endpoints, and migrations.
6. Record runtime health and missing signals. Use an observability diagnostic
   packet when telemetry materially affects the decision.
7. Record drift findings with severity, evidence, and required action.
8. Record remediation, rollback, cleanup policy, and whether approved mutation
   is required.
9. Validate the artifact against
   `../../schemas/environment-gitops-reconciliation.schema.yaml`.

## Completeness Rules

The artifact is incomplete when:

- desired state and observed state are mixed together;
- environment identity, namespace, protection, or TTL is ambiguous;
- controller sync or health status is missing when a controller is present;
- required namespace controls are missing or not explicitly marked absent;
- deployment proof depends only on a merged PR or successful build;
- endpoint, probe, smoke-test, log, metric, or trace evidence is missing when
  runtime health matters;
- rollback, remediation, or cleanup behavior is not named.

## Stop Conditions

Stop and route to `platform-readiness`, `release-verification`,
`sprint-planning`, `human-review`, or an authorized platform operator when:

- desired and observed state disagree on the intended revision;
- controller state is out of sync, degraded, suspended, or missing without an
  approved exception;
- namespace quota, network, RBAC, secret, or cleanup policy is unsafe;
- production or protected staging mutation is required without approval;
- runtime health evidence is missing for a review or release decision.
