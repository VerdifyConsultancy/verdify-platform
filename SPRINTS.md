# Verdify Platform Sprints

Last updated: 2026-06-17

Agent name: `verdify-platform`

Sprint names are planning labels for Project Tracking blocks, not branch names.
Sprint-number-driven agent routing is retired. Current work lands through GitHub
issues and PR-scoped verification.

## Historical Sprints

### S0: Lane Board Normalization

Status: `Done`

Goal: create the exact `verdify-platform` Project Board, epic-level cards,
fallback issue blocks, root lane docs, and dependency requests.

Evidence: #334, #331, #333, `PROJECT_BOARD.md`, `docs/PROJECT_BOARD_WORKFLOW.md`.

### S1: Platform Inventory And Service Map

Status: `Done`

Goal: produce a current service map for entrypoints, ports, data stores,
external dependencies, and verification commands.

Evidence: #331 and `docs/SERVICE_MAP.md`.

### S2: GitOps And Access Hardening

Status: `Legacy/In Progress`

Goal: close least-privilege gaps for ArgoCD ownership, secret metadata,
namespace-local access, prod promotion, and direct-cluster-change exceptions.

Existing anchors: #301-#307, #317, #318, #320, #321, #335, #336.

### S3: Data/Storage And Observability Requests

Status: `Legacy/Backlog`

Goal: align persistence, backup, CNPG/PITR, health checks, dashboards, and alert
handoffs with dependency agents.

Existing anchors: #13, #14, #75, #89, #218, #233, #241, #243-#245.

## Current Controller-Replan Sprints

### S4: Controller Architecture Audit

Status: `Done` (2026-06-17)

Goal: determine what is real, stale, broken, duplicated, dead, authoritative,
fallback-only, or delete/rewrite candidate across firmware, Kubernetes,
ingestor, database, dashboards, planner, lab notebook, Home Assistant, weather,
S3, Cloudflare, and CI/CD.

Primary lane:

- #343 L1 Architecture Audit, Drift Check, and CI/CD.

Evidence: `docs/reviews/lane1-architecture-audit-2026-06-16.md`,
`docs/RELEASE-CHECKLIST.md`, `docs/handoff/monitoring-writer-absent-alert.md`;
PRs #353-#358 (dead-weight purge, schema regen + mig 180, dead dashboards, CI
gates, ingestor reliability, prod promote); prod deploy executed (mig 180 live,
images promoted/synced). Note: the original audit-only non-goal was superseded
when Jason authorized the prod sync/deploy in-session.

Verification:

- Docs-only changes: `git diff --check`.
- Live diagnostics are read-only unless a later issue explicitly gates them.
- No prod sync, OTA, device VLAN, destructive DB, credential, or edge actions.

### S5: Firmware-First Climate Core

Status: `In Progress` (L2 #344 + L3 #345 **Done 2026-06-17**; L7 #349 remains)

Goal: simplify and prove firmware-first deterministic control for climate,
lighting/occupancy, safety rails, relay transitions, disconnected behavior, and
diurnal target curves.

Primary lanes:

- #344 L2 Firmware Core — **Done 2026-06-17** (`docs/firmware-fsm-spec.md` +
  safety-rail/72h/crop-agnostic test rails; proven offline, 222/0 + 193,525-row
  invariants).
- #345 L3 Climate Control — **Done 2026-06-17** (curve math + bands/hysteresis +
  energy-waste/outdoor-air guards + graded feasibility-aware compliance; offline
  + live-prod confirmed).
- #349 L7 Lighting and Occupancy — Ready (next in S5).

Verification:

- Firmware: `make test-firmware`, `make firmware-invariants`, firmware replay,
  `make firmware-replay-band` for band-curve changes, and `make firmware-check`.
- Lighting: `make lighting-audit-static`; live/current audit only when the
  issue calls for it and access/gates are satisfied.

### S6: Data, Observability, And Planner Contracts

Status: `In Progress`

Goal: define source-of-truth contracts, read/write authority, drift detection,
dashboard/KPI truth, and the bounded planner tunable contract.

Primary lanes:

- #347 L5 Data, Schema, and Source of Truth.
- #348 L6 Observability, Dashboards, and KPIs.
- #346 L4 AI Planner and Tunables.

Verification:

- Schema/migration changes: `make migration-rollback-safety` plus targeted proof.
- Python/runtime changes: `make lint`, then `make test`.
- Dashboard/site changes: relevant Makefile/site command and render check.

### S7: Irrigation, Lab, And Testing Hardening

Status: `Ready`

Goal: make irrigation/fertilization decisions explicit, repair lab notebook
truth, and build all-year/extreme-weather firmware confidence beyond the replay
corpus.

Primary lanes:

- #350 L8 Irrigation, Fertilization, and Orchids.
- #351 L9 Lab Notebook, Website, and Publishing. (2026-07-13: executing as
  the Quartz->Astro migration program, In Progress/P1/XL — see
  `docs/plans/lab-astro-migration.md`; site-astro changes are gated by the
  site-astro test/verify suite in `site-astro/package.json` in addition to
  `make site-lint`/`make site-doctor` for the legacy path.)
- #352 L10 Testing and Research.

Verification:

- Irrigation: targeted Makefile irrigation checks from the issue.
- Lab/site: relevant `site-astro/package.json` gates plus `make ci`; use
  `make site-lint`/`make site-doctor` only for the legacy Quartz path.
- Firmware/test harness: firmware gates plus all-year/extreme scenario outputs.

### S8: Vanda Night Dehum (vent+reheat + telemetry + gated activation)

Status: `Executing — 48h flag-OFF soak (OTA ab18fe8 accepted 2026-07-04T01:33Z; flag-ON eligible ~07-06T01:33Z, execution safeguard)`

Goal: dry the overnight center-zone air per #410 (02-06h median VPD 0.61 ->
>=0.78) via the design-validated vent+reheat held-temp path, shipped flag-OFF and
activated behind the #411/#377 execution safeguards with a 48h canary bake
(night_min >= 64F rollback trigger).

The implementation history is retained in the durable issue, migration, ADR,
and release records linked below.

Primary lanes:

- #410 firmware vent+reheat hold (flag OFF, replay-identical).
- #327 moisture-estimator telemetry (migration 187) — bake prerequisite.
- #413 doc drift: pinch re-pin step, OTA-reset mechanics, envelope notes.
- #411 night-anchor migration 188 — blocked on the safeguard:runtime-preflight decision.

Verification:

- Firmware: replay (flag-OFF zero-divergence), invariants, cold-night fixture,
  `make firmware-check`; migrations: `make migration-rollback-safety` + proofs;
  docs: `git diff --check`; activation evidence: `outcome_kpi()` + the bake
  report with recorded envelope + band_track_fraction + flag state.
