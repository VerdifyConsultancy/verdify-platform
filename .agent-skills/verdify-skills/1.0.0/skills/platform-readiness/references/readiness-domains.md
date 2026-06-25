# Platform Readiness Domains

Assess these domains before a Gravity pilot:

1. Agent Platform project/session API or MCP contract.
2. Controller and worktree session lifecycle.
3. Namespace model, quotas, placement, service accounts, and PVCs.
4. Secrets and credential injection without prompt, log, or source exposure.
5. Dev, stage, and production access boundaries.
6. CI/CD, image build, registry, cache, GitOps, and Argo CD flow.
7. DNS, ingress, Traefik or equivalent edge ownership, and Cloudflare health.
8. Observability: metrics, logs, traces, dashboards, SLOs, alerts, correlation
   IDs, storage, network, endpoint, and deployment markers.
9. Browser terminal access with authentication, authorization, auditing, and
   session ownership.
10. Combined review inbox with deployed URLs, CI evidence, test steps, known
    risks, recommendations, and rollback information.
11. Session ledger and semantic history handoff.
12. Non-Gravity pilot scope, rollback plan, and human sign-off.

`pass` requires inspected evidence. `warn` means a pilot can proceed only with
recorded limitation. `fail` blocks the pilot. `blocked` requires human or
external action before assessment can finish.

Use `environment-gitops-reconciliation.yaml` when the GitOps or environment
domain depends on desired-state refs, controller sync/health, namespace policy,
deployment evidence, runtime health, drift, rollback, or cleanup behavior.
