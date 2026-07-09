# Controller bootstrap — software recovery 2026-07-09

**Status:** READY_FOR_DISCOVERY

**Captured:** 2026-07-09T20:55:04Z

## Baseline

- Repository: `VerdifyConsultancy/verdify-platform`
- Controller worktree: `/Users/jason/repos/verdify-worktrees/software-recovery-20260709`
- Branch: `codex/software-recovery-2026-07-09`
- Default branch: `main`
- Baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`
- Working tree before bootstrap: clean
- Target: the sole production environment, namespace `verdify-prod`, ArgoCD app `verdify-prod-dark`

The laptop root checkout is clean but remains on the stale PR #409 branch. It is not an implementation surface for this sprint. The controller worktree was created directly from current `origin/main`.

## Sources available

- Git history, current `origin/main`, repository instructions, architecture, runbooks, Makefile, CI workflows, tests, and manifests.
- Authenticated GitHub Issues, pull requests, checks, and workflow dispatch for `VerdifyConsultancy/verdify-platform`.
- Kubernetes context `vallery`, the `verdify-prod` namespace, ArgoCD application status, workload logs, and read-only production database queries.
- Public API, lab, and graphs endpoints.
- The operator feedback at `/Users/jason/.codex/attachments/afeed265-feb3-4eab-b454-7b0e6dcdcfeb/pasted-text-1.txt`.
- Existing external analysis under `/Users/jason/Orbit/context_dump/verdify-platform/greenhouse-analysis-2026-07-09/` and the correction worktree.

## Runtime snapshot

- ArgoCD: `Healthy / OutOfSync`, revision `0a9a19a`.
- Core API, DB, Grafana, Hermes, ingestor, lab, MCP, MQTT, planner, setpoint-server, and Traefik workloads are Running.
- Public API, lab, and graphs endpoints return HTTP 200.
- Latest telemetry at bootstrap: `2026-07-09T20:54:38Z`.
- Device firmware: `2026.7.3.1931.ab18fe8`.
- One unresolved critical alert: `planner_required_plan_missed`.
- Latest `plan_journal` entry: 2026-06-25.
- Live `band_track_fraction=0.25` remains active from `operator-relax-pinch-20260618`.

## Authority and safety

Jason authorized delivery of all proposed software fixes and explicitly authorized any required OTA without another approval request. This resolves the normal deployment and OTA human-approval questions for the defined objective. It does not waive deterministic safety gates, CI, migration rollback rules, firmware replay/invariants, weekly OTA limits, the 48-hour bake rule, or secret-handling policy.

No raw secret value may enter repository content, command output, evidence, or chat. Destructive production database work, credential rotation, DNS/edge changes, and unrelated hardware work remain outside scope.

## Sources unavailable or not yet verified

- The correct shared wall fertigation recipe and volume for lime/citrus plus cannabis requires authoritative horticultural research and commissioning constraints.
- The exact deployed root causes from the July 9 review must be revalidated against current source and live logs before implementation.
- Firmware OTA preflight cannot pass while the critical planner alert remains unresolved.

## Exact next action

Run the adversarial repository/runtime audit, correlate the operator feedback, conduct the fertigation research, and reconcile the resulting executable work into GitHub issues and bounded lane contracts.
