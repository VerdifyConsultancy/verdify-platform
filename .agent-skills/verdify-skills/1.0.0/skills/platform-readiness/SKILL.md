---
name: platform-readiness
description: Inventories and gates Agent Platform readiness across Kubernetes namespaces, RBAC, secrets, credentials, CI/CD, GitOps, ingress, DNS, observability, browser terminals, review inbox, Agent Platform API or MCP contracts, and non-Gravity pilot criteria. Use before autonomous platform execution, before a Gravity pilot, or when environment and control-plane readiness is uncertain.
compatibility: Requires authorized read access to platform repositories, Kubernetes/GitOps state or supplied snapshots, credential location inventory, and CI/CD state. Production changes require a separate approved operator.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "6b"
---

# Platform Readiness

Prove the delivery platform before using it on Gravity. Inventory and design;
do not apply production changes from this skill.

## Canonical artifacts

- `.agent-workflow/platform/platform-readiness.yaml` - readiness matrix
- `.agent-workflow/platform/platform-readiness.md` - human-readable report
- Optional observability diagnostic packets when runtime evidence or missing
  instrumentation affects readiness
- Optional environment GitOps reconciliation records when desired state,
  controller state, namespace controls, deployment evidence, runtime health,
  drift, rollback, or cleanup materially affect readiness
- Optional Agent Platform control requests when API/MCP operations need
  authorization, policy, target, and evidence records
- Proposed ADRs, issues, and gates for platform gaps

Validate YAML against `../../schemas/platform-readiness.schema.yaml` and
diagnostic packets against
`../../schemas/observability-diagnostic-packet.schema.yaml`. Validate control
requests against `../../schemas/agent-platform-control-request.schema.yaml`.
Validate environment GitOps reconciliation records against
`../../schemas/environment-gitops-reconciliation.schema.yaml`.

## Procedure

1. Read current project/platform docs, Agent Platform contracts, GitOps
   manifests, CI/CD config, namespace declarations, and observability docs.
2. Inventory the domains in `references/readiness-domains.md`.
3. Compare current state with target state:
   - repository as application;
   - environment-scoped namespaces;
   - dev-write and stage/prod promotion;
   - runtime secret injection without prompt/log exposure;
   - observability by default;
   - browser terminal inspection;
   - review-ready deployed pull requests;
   - controller and child-session APIs or MCP tools.
4. Mark each domain `pass`, `warn`, `fail`, or `blocked`.
5. Use an observability diagnostic packet when telemetry, runtime checks,
   deployment markers, or missing instrumentation determine readiness.
6. Use an environment GitOps reconciliation record when desired state,
   controller state, namespace controls, deployment evidence, runtime health,
   drift, remediation, rollback, or cleanup policy determine readiness.
7. Use an Agent Platform control request when a repo/session/worktree/GitHub/
   CI/CD/k3s/GitOps/review/telemetry operation needs an API/MCP contract,
   authorization, policy verdict, target identity, rollback or recovery path,
   and evidence capture.
8. Define the minimum non-Gravity pilot that proves intake, planning, worktree,
   CI/CD, preview, human review, fix/replan, sign-off, and ledger capture.
9. Write the readiness artifact and open gates for blocked domains.

## Stop conditions

Stop before production edge changes, secrets copying, broad RBAC grants,
unreviewed namespace writes outside development, or Gravity implementation.

## Load references only when needed

- Read `references/readiness-domains.md` for the required readiness matrix.
- Read `references/environment-gitops.md` before creating or evaluating an
  environment GitOps reconciliation record.
- Read `references/agent-platform-control.md` before creating or evaluating an
  Agent Platform API/MCP control request.
