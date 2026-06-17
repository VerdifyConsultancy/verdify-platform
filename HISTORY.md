# Verdify Platform History

Last updated: 2026-06-17

Agent name: `verdify-platform`

This file is a compact index of historically completed work with evidence
links. Detailed historical handoffs and evidence artifacts live in
`/Users/jason/Orbit/context_dump/verdify-platform/`.

## Completed Milestones

| Date | Work | Evidence |
|---|---|---|
| 2026-05-31 to 2026-06-02 | CI/CD green path established: manifests, images, digest write-back, and gating repairs. | GitHub issues #69, #78, #81, #82, #92, #99, #126. |
| 2026-05-31 to 2026-06-07 | k3s app desired state authored and cutover path executed. | GitHub issues #70, #73, #86, #216. |
| 2026-06-07 | Single-writer cutover executed; Verdify prod became the live writer. | GitHub issue #216. |
| 2026-06-07 | Iris VM powered off / host units retired. | GitHub issue #217. |
| 2026-06-07 to 2026-06-08 | HA incident response: resource governance, spread/PDBs, backups, edge HA, descheduler, CNPG groundwork. | GitHub milestone #14; issues #226-#234, #236, #241, #243, #244, #264. |
| 2026-06-08 to 2026-06-11 | Firmware/control optimization wave and dashboard work. | GitHub issues #249-#256, #281-#285; PRs #270, #325, #328, #329. |
| 2026-06-12 | Repo cleanup archived historical context to Orbit and codified Codex operating docs. | Commits `ecad3dd` and `7a48a0b`; GitHub issue #330. |
| 2026-06-13 | Lane board normalization created current repo docs and GitHub fallback tracking blocks. | GitHub issues #331, #332, #333, #334. |
| 2026-06-16 | Single-env model codified: `verdify-dev` proving environment and staging overlay are decommissioned/deleted; prod is the only environment and remains manual-sync behind the device-write gate. | `AGENTS.md`, `docs/runbooks/laptop-operator.md`, prod-promote workflow, overlay removal history. |
| 2026-06-16 | Controller replan created four new GitHub milestones and ten canonical lane epics for architecture, firmware, climate, planner, data, dashboards, lighting, irrigation, lab, and testing. | GitHub milestones G0-G3; issues #343-#352; Project #5 now 27 cards; `AGENT_LANE.md`, `PROJECT_BOARD.md`, `EPICS.md`, `MILESTONES.md`, `SPRINTS.md`. |
| 2026-06-17 | **L1 / G0 Architecture Audit COMPLETE**: actual-vs-intended map + drift/dead-code inventory + CI/CD model + failure-mode/HA-fallback docs; Phase 0 dead-weight purge (VM-era docker stack, 13 scripts, 52 dead dashboards) + schema regen + DROP-orphan-fns mig 180; Phase 1 CI gates (band-curve replay blocking, corpus-freshness, logic-tests job); Phase 2 ingestor liveness probe + anti-affinity; PVC storage-tiering (hermes → node-local). | `docs/reviews/lane1-architecture-audit-2026-06-16.md`, `docs/RELEASE-CHECKLIST.md`, `docs/handoff/monitoring-writer-absent-alert.md`; PRs #353-#358; issue #343. |
| 2026-06-17 | **Prod deploy executed** (Jason-authorized, in-session): promoted api/mcp/ingestor/migrate/planner digests (#358) → gated `argocd sync verdify-prod-dark` → migration 180 applied (orphan band fns dropped). During the deploy's single-writer Recreate, a Synology storage incident (DSM `/volume1` SSD tier died; new-LUN provisioning failed on both tiers) stranded the sole ESP32 writer ~1h; recovered via an emptyDir stopgap (greenhouse stayed safe — ESP32 autonomous on-chip). `/volume2` later recovered; durable PVC re-Bound, awaiting the gated revert sync. | issue #343; `db/migrations/180`; ingestor-state PVC/manifest history; storage-incident notes in the audit doc. |
| 2026-06-17 | **Storage incident root cause CORRECTED**: `synology-csi-controller-0` logs show `Number of target reach limit` — the DSM iSCSI **target-count cap is exhausted**, failing new iSCSI provisioning on BOTH `/volume1` and `/volume2` (NOT an SSD-tier hardware death as the row above believed). Aggravated by ephemeral CI iSCSI churn (`agent-fleet-ci *-workdir` PVCs). Reverted two concurrent unsafe storage edits (lab RWX→RWO node-local break; DB→synology-iscsi-ssd that would fail the immutable-VCT sync); corrected the manifest comment + SERVICE_MAP; filed storage-infra P0 + gated DB-retier requests. | commit `6ed1c24`; `deploy/k8s/overlays/prod/ingestor-state-pvc.yaml`, `docs/SERVICE_MAP.md`, `COORDINATION_REQUESTS.md`. |
| 2026-06-17 | **L2 #344 + L3 #345 (G1) DONE**: the firmware control core was already correct; this closed the documentation + test-rail acceptance gaps and proved them OFFLINE + confirmed LIVE in prod. Delivered `docs/firmware-fsm-spec.md` (authoritative FSM / per-mode relay map / safety rails / bands+hysteresis / 72h-offline / graded feasibility-aware compliance + AC traceability), invariants #25 (SAFETY_HEAT cold rail) / #26 (SENSOR_FAULT all-off), the 72h-disconnected determinism native tests, the crop-agnostic guard, the compliance-feasibility classifier test; corrected the stale "5-second loop" docs to ~1s dt_ms-based; wired the new tests into CI. Verified: 222/0 native firmware tests, 193,525-row invariant suite, `fn_zone_band_grade`/`fn_crop_band_value` live in prod. Firmware OTA arming stays Jason-gated (not an acceptance gate). | commits `d8ed531`, `417531e`, `38c6e08`, `ffc89b9`; `docs/firmware-fsm-spec.md`, `docs/reviews/l2-l3-overnight-sprint-2026-06-17.md`; issues #344, #345. |
| 2026-06-17 | **CI back to GREEN + prod-state reconcile.** Fixed two red CI checks on main (ruff-format the new contract tests; de-flake `test_11_planner_milestones` — a midnight-window-dependent exact-set assertion → MIDNIGHT-optional). The concurrent storage operator completed their LIVE tiering (verified healthy: DB→synology-iscsi-ssd reusing the existing PV, lab→node-local-rwo co-located, lab-publisher now succeeding); reconciled git==live for DB+lab (git-only, no DB roll). ArgoCD `verdify-prod-dark` OutOfSync is audit-D2 benign metadata divergence from the operator's manual recreations — deliberately NOT force-synced (cosmetic vs a live-writer/DB roll; hermes reconcile + sync is the operator's tail). No firmware binary changed → no OTA. Remaining gated residuals (DB PITR WAL-archiving, iSCSI target-cap, monitoring-stack writer-absent alert) have ready plans; held for attended/maintenance/external execution per Track A. | commits `28a3050`, `2052fc2`; CI green on HEAD; `COORDINATION_REQUESTS.md`, `docs/SERVICE_MAP.md`, `docs/reviews/l2-l3-overnight-sprint-2026-06-17.md`. |

## Important Incidents And Decisions

- Migration rollback safety was codified after the 2026-05-30 live-commit
  incident; see `AGENTS.md` and CI job `migration-rollback-safety`.
- `main` is the single canonical branch as of 2026-06-10; `live/platform-main`
  is retired.
- `verdify-dev` and staging are decommissioned/deleted as of 2026-06-16. Prod is
  the only environment.
- `verdify-prod-dark` is a legacy live App name; it currently points at the real
  prod writer overlay. Rename is cosmetic but gated because App deletion can
  prune live resources if done wrong.
- Firmware is the local deterministic safety layer. AI may tune bounded
  parameters but does not own hard safety rails, emergency behavior, target
  temperature calculation, or firmware state-machine logic.
- GitHub issues are the live tracker; local docs are the durable orientation and
  fallback index.
