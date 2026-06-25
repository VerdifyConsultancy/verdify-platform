---
name: gravity-readiness
description: Gates Gravity implementation by inventorying Gravity product, architecture, source, tests, issues, dependencies, Onyx status, environments, credentials, observability, platform integration, and pilot criteria. Use when preparing Gravity for an autonomous pilot or reconciling Gravity against Agent Platform and Skills readiness; do not use for Gravity feature implementation.
compatibility: Requires read access to the Gravity repository, relevant Agent Platform and skills artifacts, GitHub state, and environment/readiness evidence. Jason and James approval is required before pilot execution.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "6c"
---

# Gravity Readiness

Keep Gravity gated. Inventory, reconcile, and design the pilot; do not build
Gravity features from this skill.

## Canonical artifacts

- `.agent-workflow/gravity/gravity-readiness.yaml` - binary readiness checklist
- `.agent-workflow/gravity/gravity-readiness.md` - human-readable report
- `.agent-workflow/gravity/gravity-core-extraction-plan.yaml` - optional
  Sunshine-to-Gravity reusable core extraction plan
- Proposed Gravity readiness issues and gates

Validate YAML against `../../schemas/gravity-readiness.schema.yaml` and, when
core extraction is in scope,
`../../schemas/gravity-core-extraction-plan.schema.yaml`.

## Procedure

1. Read Gravity docs, source, tests, issues, architecture, installed Verdify
   artifacts, and platform integration notes.
2. Confirm whether Onyx remains a required foundation.
3. Map Gravity's intended product and service surface against Agent Platform and
   Skills contracts.
4. When Sunshine Club or another client implementation is a reuse source,
   produce a `gravity-core-extraction-plan.yaml` that inventories source
   objects, core/pack boundaries, contracts, migration risks, local filesystem
   ingestion pilot criteria, and readiness updates.
5. Assess the required readiness areas:
   - approved North Star;
   - repo hygiene;
   - lane and dependency planning;
   - worktree/session orchestration;
   - credentials and RBAC;
   - namespaces and quotas;
   - CI/CD and review environment;
   - rollback;
   - observability and diagnostics;
   - browser inspection;
   - human review and sign-off.
6. Mark every item `pass` or `fail`; use `blocked` only when evidence cannot be
   obtained.
7. Propose one small pilot wave that proves the whole lifecycle.
8. Stop until Jason and James approve the checklist and pilot plan.

## Stop conditions

Do not implement Gravity features, create lane worktrees for Gravity feature
work, mutate production/stage resources, or bypass the readiness checklist.

## Load references only when needed

- Read `references/readiness-checklist.md` for binary checklist rules and pilot
  constraints.
- Read `references/gravity-core-extraction.md` when Sunshine-to-Gravity reuse,
  generic core boundaries, pack boundaries, source-object identity, or local
  filesystem ingestion pilot planning is in scope.
