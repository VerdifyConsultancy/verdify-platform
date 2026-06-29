# Wave Release Planning

Use this reference when a sprint or wave has CI/CD, preview, review,
deployment, promotion, rollback, or release-health implications.

`wave-release-planning` is a promoted capability contract owned first by
`sprint-planning`. It is not a standalone canonical skill yet. The plan must be
complete before `sprint-orchestrator` dispatches worker sessions whose output
will depend on release, preview, or deployment evidence.

## Inputs

- Sprint goal, candidate issues, lane topology, dependencies, baseline SHA,
  branch policy, and current GitHub snapshot.
- Required checks, workflow names, merge queue policy, merge-group event needs,
  concurrency expectations, and CI artifact outputs.
- Target preview, review, dev, staging, or production environments.
- Namespace, quota, limit, NetworkPolicy, TTL, endpoint, secret-scope, and
  environment protection expectations.
- GitOps controller, desired-state refs, sync order, reconciliation checks, and
  remediation policy.
- Existing or planned `environment-gitops-reconciliation.yaml` refs when
  environment state determines dispatch readiness.
- Deployment strategy, promotion gates, release-health signals, telemetry,
  rollback plan, and human review requirements.

## Procedure

1. Decide the branch and merge model for this wave: per-lane branches, wave
   branch, merge queue, manual release, or explicit exception.
2. Record required GitHub checks and whether merge queue support requires
   `merge_group` workflow events.
3. Record each required CI workflow, trigger, timeout, artifact output, and
   evidence expected.
4. Define every required environment and its namespace, quota, limit,
   NetworkPolicy, TTL, secret scope, endpoint, protection rules, and expected
   revision source.
5. Record GitOps desired-state refs, sync order, reconciliation checks, and
   remediation behavior. Keep Verdify product waves distinct from Argo CD sync
   waves.
6. Link any environment GitOps reconciliation record that proves desired state,
   observed controller state, namespace controls, runtime health, drift, and
   rollback or cleanup behavior.
7. Define deployment strategy, promotion steps, success criteria, rollback
   triggers, and release-health stabilization windows.
8. Record observability and missing telemetry before dispatch.
9. Define rollback procedure and validation steps before review-ready status.
10. Record the expected review inbox packet path and human test steps.
11. Write `.agent-workflow/sprints/<sprint-id>/release/wave-release-plan.yaml`
    and validate it against `../../schemas/wave-release-plan.schema.yaml`.

## Completeness Rules

Do not mark a wave release plan `approved` unless:

- branch or merge queue identity is explicit;
- required checks, events, workflows, and artifacts are named;
- preview/review/deployment environments are explicit or marked not applicable;
- namespace, quota, limits, NetworkPolicy, TTL, and secret scope are defined
  for generated environments;
- GitOps desired state, reconciliation, and remediation are explicit when a
  controller is used;
- deployment strategy, promotion criteria, release-health signals, and
  rollback validation are explicit;
- review inbox handoff is defined.

## Stop Conditions

Stop and route back to strategy, architecture, platform readiness, or human
approval when:

- branch identity conflicts with the one issue/lane/branch/worktree default;
- required GitHub checks cannot run on the planned events;
- required environments lack quota, network, secret, endpoint, or TTL policy;
- GitOps desired-state or reconciliation evidence cannot be named;
- rollback readiness is not credible for a release-impacting change;
- runtime telemetry is insufficient for the planned review or promotion.
