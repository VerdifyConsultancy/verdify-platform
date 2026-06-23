# ADR-0004 Solar/KPI/Moisture Deploy Gate — 2026-06-23

Purpose: organize the June 22-23 local work into one reviewable deploy gate PR.
This PR is the source-code/config/docs gate only. It does not apply the prod DB
migration, push live tunables, sync ArgoCD, or perform firmware OTA.

## Milestone

Use GitHub milestone `Greenhouse Control Optimization`.

Why this milestone: the bundle is the ADR-0004 follow-through for greenhouse
control optimization across DB solar phase, source/default float semantics,
outcome scoring, moisture telemetry, and evidence-gated VPD/dehum policy. It
crosses containers and firmware, so one PR should be the review gate before
container promotion and Jason-gated OTA/live behavior changes.

## Issue Map

| Issue | Role In This PR | Status After PR Merge |
|---|---|---|
| #384 Deploy gate | Umbrella issue for the cross-surface source PR, validation checklist, and post-merge deploy sequencing. | Close when the PR merges; live/deploy evidence remains on child issues. |
| #293 DB solar phase parity | Migration 186 + schema/test parity for NOAA-style Longmont solar altitude, sunrise, sunset, and phase. | Source ready; prod migration apply and dependent refresh remain gated. |
| #377 ADR-0004 float flip | Source defaults, replay defaults, planner registry, and planner copy constrain `band_track_fraction` to `0`. | Source ready; live tunable push remains Jason/operator-gated. |
| #371 outcome KPIs | Adds read-only MCP `outcome_kpi(target_date)` and typed schema/tests for served/pinched compliance, VPD misses, cycles/runtime, dew, water, DLI/DIF, solar buckets, energy/cost, and action effectiveness. | Container deploy needed before live use; dashboard/lab presentation remains follow-up. |
| #327 moisture-estimator telemetry | Firmware emits `climate_moisture_exchange`; ingestor maps/parses it into existing `climate_action_log.source_system_state`; KPI summarizes live rows when present. | Requires service deploy plus Jason-gated OTA before live rows exist. |
| #383 VPD/dehum policy | Adds bounded night low-wet `heat_dehum` firmware path and VPD policy episode counters for fog/dehum review. | Offline source ready; live before/after review, fog/dehum ping-pong, and high-light dry-side VPD tuning remain open. |

## Completed In The Local Worktree

- DB solar math:
  - `db/migrations/186-noaa-solar-phase-parity.sql`
  - `db/schema.sql`
  - `db/migrations/tests/test-186-noaa-solar-phase-parity.sql`
  - `tests/test_db_solar_sql_contract.py`
- ADR-0004 source/default float semantics:
  - firmware source/defaults, ESPHome globals, replay defaults, planner registry,
    prompt copy, and MCP scorecard copy now treat target-reference deviation as
    diagnostic, not the control objective.
- Outcome/KPI reporting:
  - `mcp/server.py::outcome_kpi()`
  - `verdify_schemas.mcp_responses` typed response models and schema tests.
- Moisture-estimator telemetry:
  - firmware telemetry JSON, ingestor mapping/parsing, and KPI aggregation using
    the existing `source_system_state` JSONB path.
- VPD/dehum policy:
  - bounded low-wet night `heat_dehum` path, firmware twin mirror, and unit tests.
- Lighting audit cleanup:
  - `site-home` panel 36 now shows observed/forecast solar lux, direct
    setpoint readback/change threshold bands, and actual per-circuit switch lanes.
  - generated Grafana ConfigMap shard refreshed.
- Tracker/docs:
  - root planning/history docs updated with ADR-0004 sequence and evidence.

## Pending After PR Merge

1. Apply migration 186 through the normal migration path; do not stack another
   migration until it is applied or explicitly deferred.
2. Refresh/recompute any DB surfaces that depend on `fn_solar_phase()` if the
   migration path does not already rebuild them.
3. Promote/deploy containers for MCP and ingestor through the standard prod
   promote PR and manual ArgoCD sync gate.
4. With Jason/operator approval, push live `band_track_fraction=0` and verify
   `cfg_band_track_fraction=0`, or defer the live float trial.
5. With Jason/operator approval, perform firmware OTA for moisture telemetry and
   `heat_dehum` behavior.
6. After live service deploy plus OTA, collect a 48-72h outcome window using
   `outcome_kpi()` and update #371/#327/#383 with served-vs-pinched compliance,
   VPD misses, cycles/runtime, dew margin, water, DLI/DIF, solar buckets, and
   moisture-estimator/heat-dehum rows.
7. Do not tune fog/dehum ping-pong or high-light dry-side VPD until live KPI and
   moisture-estimator evidence exists.

## Required Review Gates

- Schema/migration:
  - `make migration-rollback-safety`
  - migration 186 rollback-wrapped proof before prod apply
- Python/runtime:
  - `make lint`
  - targeted MCP/schema/KPI tests
  - full `make test` only in an environment with the expected DB/service baseline
- Firmware:
  - `make test-firmware`
  - `make firmware-invariants`
  - `make firmware-replay-worktree OLD=origin/main` with documented intentional
    divergence threshold for heat1-only `heat_dehum`
  - `make firmware-replay-band OLD=origin/main`
  - `SECRETS_SRC=$HOME/.verdify/esphome-secrets.yaml make firmware-check`
- Grafana/dashboard:
  - `make lighting-audit-static`
  - generated dashboard ConfigMap diff reviewed

## Non-Goals For This PR

- No prod DB write.
- No live tunable push.
- No ArgoCD sync.
- No firmware OTA.
- No public DNS/edge/org setting changes.
- No credential movement or rotation.
