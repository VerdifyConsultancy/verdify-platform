# Backlog: cross-cutting

Coordinator-owned queue. Items that span 2+ agent scopes, touch shared territory, or are high-stakes enough to warrant single-driver execution.

## Project recovery train - 2026-05-22

Canonical plan: [`project-recovery-2026-05-22.md`](project-recovery-2026-05-22.md).
This is the active coordinator queue for turning the `PROJECT_STATE.md` audit
into a clean, integrated repo. PR #80 stays intact and is RM-0's baseline.

- [x] **C-RM0 Preserve PR #80.** PR #80 was merged as-is on 2026-05-22 with
  merge commit `66f0836b9cb2bec133f90586380d196cccee5d78`. Main CI run
  `26311770932` passed, including route guards, schema/drift guards, firmware
  compile, firmware replay/invariants, and restart hygiene. Firmware bake and
  last-good promotion remain tracked as `F-RM0`.
- [x] **C-RM1 Dirty-state quarantine.** Freeze broad root-worktree edits,
  create per-theme patch bundles or clean integration branches, and assign
  every dirty path from `PROJECT_STATE.md` to exactly one milestone before code
  cleanup starts. Patch bundles and the path-disposition manifest are captured
  under `/mnt/verdify/docs/recovery-2026-05-22/rm1-patch-bundles/`; clean RM-2
  and RM-5 integration worktrees exist, RM-4 and RM-6 have been integrated, and
  RM-7 disposed of stashes/temp worktrees by archive or removal.
- [x] **C-RM2 Irrigation/fertigation coordinator slice.** Own migration 134,
  schema changes, restart documentation, DB/view validation, and cross-agent
  review for the canonical irrigation/fertigation software stack. Finalizer is
  explicitly out of scope until RM-3. PR #83 merged at
  `a8f8ffaf7aa7834e393746e8d6b37d1549aecede`; main CI run `26314849773`
  passed. Deployment applied migration 134 transactionally, synced the tracked
  RM-2 files to `/srv/verdify`, restarted `verdify-ingestor` and
  `verdify-mcp`, restarted Grafana, and passed live
  `make irrigation-stack-software-check`; the post-restart ingestor journal
  window stayed clean. Physical feedback gaps remain RM-3.
- [ ] **C-RM3 Physical feedback and finalizer gate.** Coordinate operator
  repair/mapping for south probe and center moisture/runoff feedback, then run
  finalizer dry-run and finalizer only after all feedback rows are `ok`.
- [x] **C-RM4 Planner graph reconciliation.** Decide whether the root dirty
  planner-graph shadow implementation or stale `genai` worktree variant is
  canonical, then route the reviewed runtime/docs/tests through genai or a
  coordinator-owned shared PR. Root was chosen as canonical; PR #84 merged at
  `3a2eb87426a355a5e71c036bc3460686b68b5b56`; main CI run `26315767545`
  passed; deployment synced the merged files, restarted only
  `verdify-ingestor`, validated accepted shadow row `4`, confirmed no duplicate
  shadow rows per trigger, and ran deployed backfill dry-runs. Superseded by
  the ClimateIntent single-path PR, which removes the runtime shadow service,
  scripts, tests, Docker profile, MCP server, and live shadow tables.
- [x] **C-RM5 Site/Grafana integration gate.** Coordinate the site/Grafana
  pieces that cross `docker-compose.yml`, Grafana provisioning, generated site
  output, and live service reloads; include
  `verdify-grafana-render-cache-warm.service` triage. PR #81 merged at
  `f08e09490c1d1075b705eebd66b0f06a81812f43`; main CI run `26313275460`
  passed. Deployment rebuilt the site from tracked source, restarted Grafana,
  installed/enabled the cache-warmer unit/timer, and completed one warmer run
  with `133/133` HTTP 200 renders and zero failures. RM-8 validation confirmed
  public `lab.verdify.ai` and `labs.verdify.ai` both resolve to
  `gateway.verdify.ai` / `8.44.158.103` and return HTTP 200; issue #82 is
  closed.
- [x] **C-RM6 Climate overlay semantics.** Isolate Tempest/HA overlay behavior
  from broader recovery branches and drop unjustified loose planner tests. PR
  #85 merged at `45758b66c08e8c40ec1eb48cd735db48b8ced0f5`; main CI run
  `26316119564` passed. Deployment synced the merged files, restarted
  `verdify-ingestor`, verified the deployed Tempest tests, ran the standalone
  Tempest script once, confirmed current `climate` and `weather_station` rows,
  and confirmed zero orphan outdoor-only `climate` rows in the last hour.
- [x] **C-RM7 Worktree/stash cleanup.** Remove temp lighting worktrees after
  proving no unique work remains, reconcile or recreate the stale `genai`
  worktree, and review/drop/archive both stashes without applying them to the
  production-linked root. Unique leftovers were archived under
  `/mnt/verdify/docs/recovery-2026-05-22/rm7-archives/`; temp/recovery
  worktrees were removed; the stale `genai` worktree was reset to current
  `origin/main`; the Apr 27 and May 10 stashes were dropped after archive;
  `.git/.DS_Store` was removed by exact path; persistent agent worktrees were
  fast-forwarded to `45758b6`.
- [x] **C-RM8 Final health gate.** Run the full repo/runtime validation gate,
  prove no critical/high alerts, update backlog disposition, and close or
  schedule GitHub issues #18/#19 based on exact readback-parity evidence. Local
  closure gates passed: `make lint`, `make test`, `make site-doctor`,
  firmware/tunable drift tests, live service checks, Grafana render sample,
  public lab route checks, and zero open critical/high alerts. Issues #18, #19,
  and #82 are closed with verification comments.
- [ ] **C-RM9 Grafana render CPU guard.** On 2026-05-24 post-ClimateIntent OTA,
  `verdify-grafana-renderer` was observed spawning continuous Chromium renders
  for `d-solo/*` panels and sampling at `288%` CPU. The renderer container was
  stopped as a protective mitigation; greenhouse services and Grafana stayed up.
  Follow-up: identify the requester/cache-warmer loop, add render concurrency
  and rate limits, then re-enable the renderer intentionally.

## Launch coordination

Launch work is tracked in [`docs/backlog/launch.md`](launch.md) with the command center in [`docs/launch/README.md`](../launch/README.md). Coordinator owns:

- [x] **Launch privacy/security gate.** Public Markdown and generated HTML scrubbed for family names, local IPs, camera/security details, ambiguous solar/cloud claims, and raw dollar-sign rendering.
- [ ] **Launch identity/code transparency decisions.** Jason decides attribution and repo/prompt visibility before HN/Reddit.
- [x] **Public API and indexing stance.** Public API is read-only for proof routes; writes require a key; API/Grafana are noindex while launch pages remain indexable.
- [x] **Public proof contract.** Added public-safe home metrics and data-health response models/endpoints for launch proof cards.
- [x] **Launch sequencing.** P0 hardening is deployed; remaining launch timing is Jason-owned attribution/copy/video availability.
- [x] **Cross-agent release train.** Web/genai/ingestor/saas/coordinator launch work was closed as one coordinator-driven release.
- [x] **Prior-art verification and positioning.** Added `/intelligence/related-work` using primary-source/public links for AgroNova, IOGRUCloud, Hydro0x01, HAGR, Mycodo, iGrow, GreenLight-Gym, FarmBot/OpenAg, WUR, and commercial CEA comparators. Verdify is framed as public falsifiability, not first/largest/best.
- [x] **Baseline period decision.** Selected 2026-04-22..25 planner-offline outage as baseline and 2026-04-26..2026-05-02 as the Iris-online comparison window; `/evidence/baseline-vs-iris` labels it as operational, not controlled A/B.

## Schemas / contracts

- [x] **Per-alert-type discriminated union for `AlertEnvelope`.** Requested by `ingestor`. `AlertEnvelope` now preserves the existing model API while validating through a tagged per-alert registry covering every current alert writer, including planner, API, dispatcher, heap, firmware, setpoint-confirmation, and forecast-deviation alerts.
- [x] **Migrate `verdify_schemas.crops.ObservationAction.data` union to also accept `HarvestCreate` / `TreatmentCreate`.** Verified with regression coverage.
- [x] **Scorecard typed projection** (requested by `genai` + `web`). `ScorecardResponse` is the shared typed shape; migration 096 and `db/schema.sql` now match the live 25-metric numeric `fn_planner_scorecard()`, and `/api/v1/scorecard` returns that schema.
- [x] **C-CI.1 Climate action/effectiveness data contract.** Canonical sprint
  plan:
  [`docs/climate-authority-sprint-plan-2026-05-24.md`](../climate-authority-sprint-plan-2026-05-24.md).
  GitHub issue:
  [#7](https://github.com/VerdifyConsultancy/verdify-platform/issues/7).
  Added a structured climate action log and durable schema surface for
  selected action, priority axis, dispatcher-owned target deltas, band errors,
  wet/fog allowance, block reasons, relay truth, resource estimates,
  ClimateIntent version, and plan/trigger correlation. Added 5-minute,
  15-minute, and daily effectiveness views so controller changes can be judged
  by before/after temp/VPD error, time to recovery, wet relay duty, water,
  energy, and outdoor context. Keep live planner context on indexed/latest-row
  reads, not unbounded `v_greenhouse_state` scans. Migration
  `142-climate-action-log.sql` was applied live and `db/schema.sql` regenerated.
- [x] **C-CI.2 ClimateIntent rollout closeout and post-merge proof.** GitHub
  issue [#8](https://github.com/VerdifyConsultancy/verdify-platform/issues/8)
  was the live closeout tracker. Platform PR #4, planner PR
  `verdify-planner#3`, and the doc-only review PR are merged. The live
  `/srv/verdify` Slack WIP branch was merged forward to current platform
  `main`, and affected runtime services were restarted. Final proof passed:
  `make climate-authority-post-deploy-proof`, ClimateIntent audit, active-plan
  coverage, firmware deploy preflight, `make sensor-health SINCE='5 minutes'`,
  and `scripts/site-doctor.py`. The only open platform PRs after closeout are
  Slack WIP PRs owned by another agent. PR #10 (`bc85f03`) was not merged; it
  was an abandoned alternate controller architecture, archived at tag
  `archive/pr10-abandoned-controller-architecture-2026-05-25`, exported to
  `/mnt/iris/archives/verdify/`, closed unmerged, and its branch deleted.

### Planner contract v1.5 — historical local-first hardening

Trace date: 2026-05-03; superseded by the 2026-05-11 Hermes cutover. The
trigger-ledger, correlation, manual-plan, and registry-range requirements remain
current, but OpenClaw/local-Gemma routing is historical. Coordinator owns the
shared contract and schema pieces; genai and ingestor own their code slices.

- [x] **C-P0.1 Ratify historical local-first planner contract.** `docs/iris-planner-contract.md` v1.5 made the former OpenClaw `iris-planner` path the local Gemma4-on-cortext default and named cloud planning as explicit `cloud_escalation`; Hermes has since superseded the gateway details while keeping the ledger semantics.
- [x] **C-P0.2 Trigger ledger schema.** v1.5 defines the logical trigger ledger backed by `plan_delivery_log`: required `trigger_id`, event type/label, planner path/session/model, lifecycle status, SLA fields, result/ack fields, validation status, and context digest hooks.
- [x] **C-P0.3 Shared registry range validation.** `PlanTransition`,
  `SetpointChange`, and `SetpointPlanRow` all reject values outside
  `tunable_registry` min/max before planner or dispatcher writes can persist an
  invalid waypoint/change. Schema regression tests now fail if these Pydantic
  boundaries accept a value the dispatcher or firmware registry will reject.
- [x] **C-P0.4 Reconcile historical planner delivery rows.** Live prod `plan_delivery_log` pending rows older than 30 minutes from the pre-v1.5/local-first cutover were marked `timed_out` with a reconciliation note, so operational dashboards distinguish historical silent drops from current live work.
- [x] **C-P0.5 Planner model observability.** `/api/v1/public/planner-health`
  now publishes recent `plan_delivery_log` rows with `session_key`,
  `hermes_run_id`, `planner_gateway`, and `planner_model_label` alongside the
  trigger lifecycle summary. API/dashboard consumers can prove a recent trigger
  was accepted by Hermes/GPT-5.5 without scraping gateway logs, while the DB
  audit table remains the durable full-history source.
- [ ] **C-P1.1 Planner context digest view.** If genai's distilled site/lesson memory needs DB support, publish a coordinator-owned view or table that versions planner context digests and records which digest version was used by each trigger.

## Migrations

- [x] Audit all `greenhouse_id` defaults — migration `103-greenhouse-id-default-audit.sql` sets the missing single-site defaults and publishes `v_greenhouse_id_default_audit`; DB tests enforce zero missing defaults.
- [ ] Consolidate `v_daily_oscillation` + `v_daily_oscillation_summary` — one wraps the other; renderer confusion on which to use.

## Infra

- [ ] Secret Manager migration (Sprint 10 B10.5 from SaaS backlog) — credentials move from `.env` to Secret Manager refs. Touches every service.
- [x] Flaky `test_dew_point_risk_computes`. Increased the shared DB smoke-test wrapper timeout from 15 s to 45 s.
- [x] Grafana dashboard audit. 55 live dashboards / 904 panels were swept on 2026-04-28; JSON changes are committed with the web/runtime reconciliation.

## CI / tooling

- [x] Sprint 22 added drift guards in CI with a Postgres service container. CI now runs the DB schema smoke subset (`tests/test_02_database.py::TestSchemaIntegrity`) against that service container via the direct psql test helper mode.
- [x] `ruff format` in pre-commit reformats files Claude agents just wrote, occasionally creating a 2-round edit cycle. Pre-commit now passes `--config pyproject.toml` explicitly to both ruff hooks.

## Docs

- [ ] `docs/FOLDER-HIERARCHY.md` predates the agent split; refresh to reflect agent ownership.
- [ ] `docs/SYSTEM-ARCHITECTURE.md` — same; add agent boundaries overlaid on the component diagram.
- [ ] Move `docs/RUNBOOK.md` operational procedures into per-agent scope docs where they fit, and leave the runbook as cross-cutting incident response only.

## Observability

Currently handled as ephemeral coordinator-dispatched work (see `CLAUDE.md` open question 2). If this queue grows past ~5 items, revisit whether a persistent `observability` agent is warranted.

- [ ] **C-M20 Planning Quality and Resource Cost Data Integrity.** Canonical research backlog: [`lab-site-refactor-2026-05-20.md`](lab-site-refactor-2026-05-20.md), `RP-001` through `RP-005`. Covers no-data Planning Quality Grafana panels, the water-cost historical anomaly, cost graph definition standardization, outlined-bar rendering, and the Climate page information-design/panel-placement pass.
- [x] **C-M21 Electric meter coverage and cost truth.** Canonical research backlog: [`lab-site-refactor-2026-05-20.md`](lab-site-refactor-2026-05-20.md), `RP-008`. Public electric cost now uses published equipment watts multiplied by observed on-time; Shelly EM50 kWh remains diagnostic/reconciliation evidence through `v_energy_estimate_reconciliation`.

## Data trust / data science audit — 2026-05-01

Read-only multi-axis audit covered climate/weather, HVAC/control, water/soil/nutrients, crop outcomes, planner/forecast, and owner storytelling. Core finding: Verdify has strong telemetry for what happened in the greenhouse, but weaker proof for what it produced. Prioritize trust fixes first, then outcome closure.

### In progress / immediate software fixes

- [x] **Data trust migration.** Migration `101-data-trust-and-outcome-views.sql` repairs misleading view definitions and adds trust/outcome surfaces:
  - `v_dew_point_risk` uses America/Denver days and observed sample durations instead of hard-coded 2-minute cadence.
  - `v_water_daily` uses America/Denver days and positive meter deltas instead of UTC-day consecutive max deltas.
  - `v_forecast_accuracy` / `v_forecast_accuracy_daily` only use forecasts fetched before the observed hour.
  - `v_iris_planning_context.active_plan` filters `is_active = true`.
  - `v_setpoint_compliance` / `fn_compliance_pct()` report active temp/VPD band compliance instead of static schedule compliance.
  - New trust/story views: `v_water_accountability`, `v_forecast_accuracy_lead_buckets`, `v_required_sensor_coverage`, `v_energy_daily`, `v_energy_estimate_reconciliation`, `v_setpoint_delivery_latency`, `v_mister_zone_effectiveness`, `v_plan_tactical_outcome_daily`, `v_data_trust_ledger`.
- [x] **Live daily summary completeness.** `ingestor/tasks.py::daily_summary_live` now writes `rh_avg`, `outdoor_temp_min`, `outdoor_temp_max`, refreshes `captured_at`, and reads water from canonical `v_water_daily`.
- [x] **Backfill corrected daily summary fields.** Migration `102-data-backlog-completion.sql` recomputes historical `daily_summary` climate fields, `rh_avg`, outdoor min/max, dew-point risk, canonical water totals, measured `kwh_total`, peak kW, and measured-electric cost.
- [x] **Regenerate dashboard/site SQL catalog after migration 101.** Added the provisioned Grafana `Greenhouse: Data Trust Ledger` dashboard and moved generated daily plan pages to DB-backed archive self-check rows.
- [x] **Add CI drift tests for trust views.** `tests/test_02_database.py` now requires and smoke-tests the trust, water, irrigation, forecast-action, crop-completeness, mart, and archive self-check views.

### Near-term data-quality work

- [x] **Water accounting hardening.** Added `water_meter_events`, `v_water_meter_daily`, event reset/phantom-zero tracking, and canonical `v_water_daily` from positive event deltas.
- [x] **Irrigation log repair.** Migration 102 replays drip events from `equipment_state` into `irrigation_log` with `schedule_id`, zone, duration, estimated gallons, weather skip, fertigation, and metering method.
- [x] **Energy reconciliation.** `daily_summary.kwh_total` now uses watt-time integration from `v_energy_daily`; `v_energy_estimate_reconciliation` remains the estimate-vs-measured quality surface.
- [x] **Alert lifecycle cleanup.** Migration 102 normalizes resolved rows, keeps `suppressed` as an explicit schema disposition, and adds `v_alert_lifecycle_quality`.
- [x] **Sensor registry coverage.** Migration 102 activates or registers required live climate/soil/wind/intake/hydro fields so required coverage is represented in `v_required_sensor_coverage`.
- [x] **Forecast action outcomes.** Migration 102 backfills `forecast_action_log.outcome`, adds outcome timestamps/metrics, and publishes `v_forecast_action_outcomes`.
- [x] **Planner model observability.** `mcp/server.py::plan_status` now writes `openclaw_interaction_log` rows; the existing OpenClaw dashboard now has a real write path.
- [x] **Active-plan cleanup.** Migration 102 deactivates past active waypoints and adds `delivery_status`, `expired_at`, and `superseded_by_ts` for `setpoint_changes`, surfaced in `v_setpoint_change_delivery`.

### Outcome closure / agronomy layer

- [x] **Crop lifecycle completeness.** Migration 102 fills active crop counts/expected harvests/target defaults from `crop_catalog` and publishes `v_crop_lifecycle_completeness`.
- [x] **Harvest logging.** Harvest tables/API/MCP schemas now capture salable weight, culls, quality reason, destination, price/revenue, labor, operator, crop, zone, and position linkage; `v_harvest_story` normalizes outcomes by DLI/water/kWh.
- [x] **Structured phenology observations.** Observation schemas and writers now accept plant height, leaf count, canopy cover, flowering, fruit count, root condition, mortality, and stress tags; `v_growth_observation_quality` tracks coverage.
- [x] **Treatment/IPM logging.** Treatment schemas now include follow-up due/completed timestamps and outcome, with `v_nutrient_lab_status`/treatment rows preserving crop linkage.
- [x] **Nutrient/lab evidence.** `lab_results` now links to recipes/source sample IDs; `v_nutrient_lab_status` joins latest hydro/lab chemistry to active recipe targets.
- [x] **Succession plan data.** `v_succession_plan_readiness` now exposes every active position's crop/successor status so empty positions and missing follow-on plans are measurable.

### Hardware / physical sensing backlog

- [x] **PAR/PPFD sensor.** Codified as `instrumentation_requirements.par_ppfd` and surfaced in `v_instrumentation_readiness`; physical install remains an operator/hardware action.
- [x] **Leaf wetness + leaf temperature.** Codified as `instrumentation_requirements.leaf_wetness_temp` and surfaced in `v_instrumentation_readiness`; physical install remains an operator/hardware action.
- [x] **Independent actuator feedback.** Codified as `instrumentation_requirements.actuator_feedback` and surfaced in `v_instrumentation_readiness`; physical install remains an operator/hardware action.
- [x] **Water system instrumentation.** Codified as `instrumentation_requirements.zone_flow_meters` and surfaced in `v_instrumentation_readiness`; physical install remains an operator/hardware action.
- [x] **Energy submetering.** Codified as `instrumentation_requirements.energy_submetering` and surfaced in `v_instrumentation_readiness`; physical install remains an operator/hardware action.

### Story products

- [x] **Forecast -> plan -> outcome mart.** Added `v_forecast_plan_outcome_mart`.
- [x] **Grower economics story.** Added `v_grower_economics_story`.
- [x] **Data trust ledger dashboard.** Added provisioned Grafana dashboard `greenhouse-data-trust-ledger` on `v_data_trust_ledger`, instrumentation readiness, and daily plan archive self-checks.
- [x] **Daily plan archive self-check.** Added `daily_plan_archive_audit`, `v_daily_plan_archive_self_check`, and writer support in `scripts/generate-daily-plan.py`.

## Open design questions (flagged earlier)

1. Worktree migration path — rename `slot-*` to `worktrees/{agent}/` now vs. lazily per first sprint.
2. Replay corpus ownership — firmware owns tests, ingestor exports telemetry fixture. How frozen is the fixture?
3. Branch-prefix enforcement — convention + review vs. pre-commit hook that refuses out-of-scope edits.

Coordinator decides these before the first parallel cycle starts.
