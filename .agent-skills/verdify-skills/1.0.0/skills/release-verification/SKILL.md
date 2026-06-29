---
name: release-verification
description: Assembles review-ready evidence packets, records observability diagnostics for planning/review/release health, integrates critic-approved lanes, validates the whole system, verifies the intended revision in an authorized deployment environment, and completes human outcome review. Use after critic review, when a lane or wave claims review-ready status, when planning or state-of-union needs live deployment/log health evidence, after all required lanes are ready for integration, for deployment incidents, or when a merged release still needs runtime proof and acceptance.
compatibility: Requires fresh integration context, GitHub checks/reviews, repository release tooling, and separately authorized deployment access. Production credentials must not come from worker lanes.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "9"
---

# Release Verification

Combine accepted lane outputs, prove runtime reality, and close the human outcome loop. Keep integration, deployment, and outcome as explicit modes even though they share one skill.

## Mode 0: review inbox packet

1. Verify PR or merge request identity, exact reviewed head SHA, linked issues,
   lane/sprint IDs, North Star IDs, critic report, checks, preview or review
   deployment, telemetry, rollback, risks, and open human questions.
2. Block review-ready status when the exact SHA, required checks, required
   preview/review deployment, critical security disposition, rollback evidence,
   or reviewer test steps are missing.
3. Write `.agent-workflow/sprints/<sprint-id>/review/review-inbox-packet.yaml`
   and validate against `../../schemas/review-inbox-packet.schema.yaml`.
4. Route the recommendation to `approve`, `request_changes`, `reject`, or
   `escalate`; route follow-up to fix lane, replan, architecture review,
   release verification, human signoff, issue creation, or hold.

Read `references/review-inbox.md`.

## Cross-mode: observability diagnostics

1. Use diagnostics when strategy refresh, review-ready status, release health,
   platform readiness, or user feedback depends on runtime evidence.
2. Record scope, correlation IDs, hypotheses, telemetry links, signal
   assessments, runtime checks, deployment markers, findings, missing
   instrumentation, recommendation, and feedback route.
3. Write
   `.agent-workflow/sprints/<sprint-id>/release/observability-diagnostic-packet.yaml`
   and validate against `../../schemas/observability-diagnostic-packet.schema.yaml`.
4. Route to state-of-union, review inbox, release verification, platform
   readiness, lane delivery, sprint planning, incident handling, or no action.

Read `references/observability-diagnostics.md`.

## Mode 1: integration

1. Start a fresh integration session.
2. Verify every required lane has current critic approval, required checks, clean issue/contract reconciliation, and no unresolved blocker.
3. Determine dependency-aware merge order. Use the repository merge queue when configured.
4. Resolve conflicts without violating lane/module contracts; material conflict-driven redesign returns to planning or architecture.
5. Run full-system, cross-module, migration, packaging, security, and release validation required by the sprint.
6. Produce release notes, known issues, integrated commit SHA, artifact/image identity, and rollback prerequisites.

Read `references/integration.md`.

## Mode 2: deployment verification

1. Obtain the required deployment approval through the authorized environment/policy.
2. Deploy the exact integrated revision/artifact using separately scoped credentials.
3. Verify commit SHA, image digest, configuration, migrations, health, logs, routes/UI behavior, data state, observability, and rollback readiness.
4. Use an environment GitOps reconciliation record when desired state,
   controller state, namespace controls, deployment evidence, runtime health,
   drift, rollback, or cleanup behavior materially affect verification.
5. Compare runtime behavior with sprint acceptance criteria.
6. Roll back or open an incident gate when policy requires.
7. Write `.agent-workflow/sprints/<sprint-id>/release/release-verification.yaml` and validate against `../../schemas/release-verification.schema.yaml`.

Read `references/deployment-verification.md`,
`skills/platform-readiness/references/environment-gitops.md`, and
`references/rollback.md`.

## Mode 3: outcome review

Explain to the human what changed, what evidence proves it, what remains incomplete, what risks remain, and which follow-up issues were created. Record accepted, accepted-with-risks, rejected, or incomplete. Only then reconcile sprint/issue status according to policy.

Write `.agent-workflow/sprints/<sprint-id>/outcome/outcome-review.yaml` and validate against `../../schemas/outcome-review.schema.yaml`. Read `references/outcome-review.md`.

Preserve review, diagnostic, integration, deployment, rollback, and outcome
artifact refs plus GitHub check/deployment/release refs for controller-loop
session-ledger events.

## Boundaries

- A merged PR is not deployment proof.
- A healthy process is not acceptance proof.
- Do not let the worker self-deploy with production credentials.
- Do not close unresolved follow-up work by hiding it in release notes.
- Do not claim success when the running revision cannot be identified.

## Handoff

After accepted outcome and reconciled records, return to `project-router` for the next lifecycle decision.
