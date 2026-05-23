# Backlog: `ingestor`

Owned by the [`ingestor`](../agents/ingestor.md) agent.

Full delivery plan with reasoning, cross-agent branch survey, and risk register lives at `~/.claude-agents/verdify-ingestor/plans/yes-fill-this-out-unified-stearns.md`. This file is the operational queue.

## In flight

**Sprint 25.1 — Operational recovery** (`ingestor/sprint-25.1-operational-recovery`) is closing the 2026-04-27 full-review findings:

- [x] Branch rebased onto current `origin/main` so topology/tunable registry/heap-pressure alignment is present.
- [x] `data_gaps` now measures actual disconnect→reconnect windows, not previous-connect→current-connect uptime.
- [x] Direct ESP32 push moved out of `ingestor.py` to avoid split module state under `python ingestor.py`; echo suppression now uses `shared.recently_pushed`.
- [x] Ingestor modules use repo-relative schema imports instead of hardcoding `/mnt/iris/verdify`.
- [x] Regression coverage added for push helper wiring, gap timestamp source, import path, and live active-plan pushability.
- [x] Gates passed before deploy: `make lint`; `pytest --import-mode=importlib verdify_schemas/tests/test_drift_guards.py`; `make test` (`248 passed, 1 xfailed`).

Deploy path remains `main → /mnt/iris/verdify → systemd restart`, followed by a 5+ minute journal tail and DB freshness checks.

**Launch evidence support** (coordinated through [`docs/backlog/launch.md`](launch.md)).

- [x] **I-L0.2 Live proof metrics source.** Coordinator API now exposes stable launch proof metrics from DB-backed telemetry and scorecard sources.
- [x] **I-L0.11 Public metrics/freshness contract.** `/api/v1/public/home-metrics` and `/api/v1/public/data-health` include counters, latest plan/score, climate freshness, active alerts, and `ok|warn|fail` data-health status.
- [x] **I-L0.4 Plan delta support.** Daily-plan generator now computes changed secondary parameters from waypoint/default deltas while preserving the full raw set behind details.
- [x] **I-L1.4 Outage evidence query.** Evidence page now publishes the April 22-25 zero-plan/VPD-stress narrative with plan gaps, stress hours, and compliance values.
- [x] **I-L1.5 Public sample dataset export.** `scripts/export-public-sample-dataset.sh` publishes scrubbed 7-day climate and 30-day plan-outcome CSVs under `/static/data/`, excluding local IPs, device IDs, trigger UUIDs, alert channels, hostnames, and raw sensor entity names.
- [x] **I-L0.7 Proof freshness gates.** Public metrics include latest climate/plan ages so web can label stale proof instead of silently showing old values.
- [x] **I-L1.8 Lesson duplicate support.** Implemented in the web generator as normalized lesson signatures; no ingestor DB change required for launch.
- [x] **I-L1.11 Baseline vs Iris metrics.** Added `scripts/generate-baseline-vs-iris-page.py`, which queries `daily_summary`, `plan_journal`, and `v_planner_performance` for temp compliance, VPD compliance, stress hours/day, water/day, energy/day, cost/day, and planner score. Default baseline is the 2026-04-22..25 planner-offline window.
- [ ] **I-L2.7 Daily lifecycle artifact data.** Export one representative lifecycle bundle: forecast rows, plan JSON/tunables, telemetry window, scorecard row, and lessons generated from the outcome.
- [ ] **I-L2.8 Crop-steering roadmap data gaps.** Track missing substrate, pH/EC/DO, DLI correction, and shade-cloth automation signals as explicit data-readiness gaps rather than implied capabilities.

**Planner trigger coverage + Hermes delivery** (2026-05-03 trace, superseded by 2026-05-11 Hermes cutover; coordinator-requested).

Target state: every material planning reason has an auditable trigger row, every
trigger routes to Hermes `hermes-iris` with a recorded `hermes_run_id`, and every
trigger resolves to exactly one of `plan_written`, `acked`,
`delivery_failed`, or `timed_out`.

- [x] **I-P0.1 Canonical trigger matrix.** `ingestor/tasks.py` now
  carries `PLANNER_TRIGGER_MATRIX`, the single contract for emitted planner
  triggers, labels, due-time source, catch-up window, expected ledger action,
  required-plan status, and Hermes route. The active Phase 4 matrix covers the
  required `SUNRISE`, `SUNSET`, and short-window `MIDNIGHT` full-plan cycles,
  the solar-derived `SOLAR_MAX` / `TRANSITION:peak_stress` /
  `TRANSITION:decline` checkpoints, `FORECAST_DEVIATION` events emitted by
  `forecast_deviation_check`, and MCP `MANUAL` plan runs. Regression coverage
  verifies `_compute_milestones()`, `_milestone_event()`, and
  `_expected_action_for_event()` derive from the matrix. Raw forecast refresh
  triggers and the noisy `PRE_DAWN` / `MORNING_BOUNDARY` /
  `MIDDAY_BOUNDARY` / `AFTERNOON_BOUNDARY` / `EVENING_BOUNDARY` fixed
  boundaries remain intentionally retired under the Phase 4 context-budget
  contract rather than being reintroduced.
- [x] **I-P0.2 No missed-sunrise blind spot.**
  `planning_heartbeat` now materializes expected SUNRISE/SUNSET/MIDNIGHT
  triggers in `planner_trigger_ledger` before planner delivery, marks normal
  5-minute-to-2-hour restarts with explicit `(catch-up)` labels, and lets
  `_expire_planner_trigger_slas()` mark late non-delivered required triggers
  `missed` from their own `due_at` instead of waiting for flat
  `planner_stale`. `alert_monitor` raises the deployed alert type
  `planner_required_plan_missed` for the latest required trigger whose ledger
  row did not close with `plan_written`; the 2026-05-22 live check showed the
  latest MIDNIGHT and SUNRISE rows closed with plans, the next SUNSET row was
  still future/expected, and zero open required-plan-missed alerts.
- [x] **I-P0.3 Hermes routing.** Production routing now defaults to Hermes `hermes-iris` with OpenAI GPT-5.5 high-reasoning. Legacy `local`/`opus` values are audit labels only and no longer select a different gateway.
- [x] **I-P0.4 Per-trigger SLA lifecycle.** Pending delivery rows are expired
  through `_expire_planner_trigger_slas()` using the configured
  event-type/instance timeout, `planner_trigger_ledger` is synchronized to
  terminal states, and `alert_monitor` now emits
  `planner_trigger_sla_timeout` with `trigger_id`, event type, instance,
  delivered time, elapsed seconds, gateway status, gateway body, and
  `hermes_run_id` context. This replaces reliance on flat `planner_stale` for
  per-trigger lifecycle failures.
- [x] **I-P0.5 Correct delivery correlation.** Removed the unsafe 2h fallback for rows that have UUIDs. Exact `trigger_id` match is authoritative; fallback now runs only when both sides are legacy/null. MCP `set_plan` and `set_tunable` now reject planner-owned writes that omit `trigger_id`, verify the referenced delivery row, and mark the row `plan_written` immediately on success.
- [x] **I-P0.6 Deviation trigger completeness.**
  `forecast_deviation_check` now covers temp, RH, computed outdoor VPD, solar
  irradiance, wind speed/gust, precipitation intensity, inferred cloud-cover
  regime shifts, and prolonged missing forecast data. It merges DB threshold
  overrides with built-in coverage for all planner-critical axes, logs every
  threshold-exceeding deviation with `triggered=true|false`, and applies
  cooldown per parameter so repeated same-axis noise is suppressed without
  silencing a distinct weather-axis miss.
- [x] **I-P0.7 Fixed-boundary planning.** The fixed-boundary experiment
  landed earlier, then Phase 4 narrowed the active scheduler to the canonical
  matrix above after the extra 06:00/12:00/16:00/20:00 transitions proved
  low-signal. Only the short-window `MIDNIGHT` review remains active as a
  fixed local-time required cycle; other fixed boundaries stay documented here
  as superseded deployment history.
- [x] **I-P0.8 Active/future plan range guard.**
  `alert_monitor` scans active/future `setpoint_plan` rows through
  `registry_value_error()` and emits `planner_tunable_range_drift` before
  violating waypoints reach dispatch; `setpoint_dispatcher` also coerces or
  rejects registry drift before writing/pushing `setpoint_changes`. Static
  regression coverage verifies the guard uses the shared tunable registry, and
  the 2026-05-22 live scan found `2,158` active/future planner rows with zero
  registry violations.
- [x] **I-P1.1 Planner health status surface.** `/api/v1/public/planner-health`
  now publishes explicit `last_expected_trigger`, `last_delivered_trigger`,
  `last_resolved_trigger`, `pending_by_sla_age`, current planner
  `session_key`/Hermes run/model label, and
  `active_plan_range_violation_count` alongside the existing
  required-trigger and recent-trigger summaries. The range count uses the same
  tunable registry validator as the ingestor guard so API/web consumers can
  tell whether the active planner schedule is clean without scraping alerts.

**Project recovery intake from `PROJECT_STATE.md`** (2026-05-22; coordinated
through [`project-recovery-2026-05-22.md`](project-recovery-2026-05-22.md)).

- [x] **I-RM2 Irrigation/fertigation canonicalization support.** Review and
  integrate the ingestor portions of the dirty canonical irrigation stack:
  `entity_map.py`, `ingestor.py`, `tasks.py`, feedback normalization/range
  rejection, `irrigation_feedback_gap` alert emission, and software-only
  acceptance scripts. Required gates: `make lint`, drift guards, targeted
  irrigation DB/view tests, `validate-irrigation-stack.py` dry-run, full
  `make test`, `verdify-ingestor` restart documentation, and a five-minute
  clean journal tail after deploy. PR #83 merged at `a8f8ffa`; main CI run
  `26314849773` passed. Live deploy applied migration 134, restarted
  `verdify-ingestor`/`verdify-mcp`, and passed deployed
  `make irrigation-stack-software-check`; the 16:26:31-16:32:11 MDT ingestor
  journal window had no error/validation lines. The expected four warning-level
  physical feedback gaps remain for I-RM3.
- [ ] **I-RM3 Physical irrigation feedback acceptance.** Do not run or bless
  the irrigation finalizer until `v_irrigation_sensor_feedback_status` reports
  `ok` for `south_soil_probe_1`, `center_root_zone_moisture`,
  `center_runoff_ec`, and `center_runoff_ph`. Ingestor owns validating that the
  rows and alerts transition correctly after hardware/mapping repair.
- [x] **I-RM6 Climate overlay semantics.** Isolate the dirty Tempest/HA overlay
  behavior that skips standalone climate inserts when no recent ESP32 indoor row
  exists. Add focused tests for fresh-vs-stale indoor baselines, then deploy
  with an ingestor restart and live-tail validation before calling it healthy.
  Completed via PR #85 / merge `45758b6`: `scripts/tempest-sync.py` now skips
  orphan `climate` inserts while retaining `weather_station` writes, deployed
  tests passed, ingestor restart was clean, and live validation showed zero
  orphan outdoor-only `climate` rows in the last hour.
- [x] **I-RM6 Loose guard/test adjustments.** Keep planner milestone timeout or
  optional-`MIDNIGHT` test changes only if current runtime evidence still
  requires them; otherwise drop them during recovery instead of carrying
  broad test loosening forward. Dropped: current `main` passed targeted planner
  tests and full `make test` without the loose timeout/MIDNIGHT changes.

## Findings from 2026-04-18 scope review

Seven concrete gaps from the initial scope review; one (F9) surfaced when topology commits landed mid-review; four more (F10–F13) surfaced during or after the live gates.

- **F1 — CRITICAL:** `safety_invalid` alerts silently dropped since Sprint 23. `ingestor/tasks.py::alert_monitor` built `"category": "safety"` but `AlertCategory` only allows `sensor|equipment|climate|water|system`; post-Sprint-23 `AlertEnvelope.model_validate` rejected and logged+skipped. Blast radius: `safety_min/max/vpd_min/vpd_max` zero-or-invalid detection was offline. ✅ Sprint 24 (`a9d2147`) — changed to `"system"`.
- **F2 — HIGH:** Two parallel forecast writers; `scripts/forecast-sync.py` validated via `OpenMeteoForecastResponse` (Sprint 23) but `ingestor/tasks.py::forecast_sync` reimplemented the fetch unvalidated and was the live path (no systemd timer on the script). ✅ Sprint 24 — consolidated via Option A (script + systemd unit deleted, validated fetch inlined).
- **F3 — HIGH:** `_PHYSICS_INVARIANTS` drifted from canonical `ALL_TUNABLES` names (`fog_window_start` vs `fog_time_window_start`, etc.) — half the keys were dead defense. Pre-flight: 30-day `setpoint_clamps` audit = 0 rows on all renamed params (no planner bugs masked). ✅ Sprint 24 — renamed canonical, deleted non-tunable entries, added CI guard `test_physics_invariants_are_canonical`.
- **F4 — MEDIUM:** `write_esp32_logs` bypassed `ESP32LogRow` validation. ✅ Sprint 24 — validates before INSERT; drops empty-after-ANSI-strip rows.
- **F5 — MEDIUM:** `EquipmentStateEvent.equipment: str` should be `EquipmentId` Literal. ✅ **Already done upstream** by topology Sprint 22 (`a172576` on main) — `verdify_schemas/telemetry.py:201` is tight. Discovered mid-sprint when topology commits merged between session start and first edit.
- **F6 — MEDIUM:** `write_setpoint_changes` INSERT omitted the `source` column for ESP32-reported changes. ✅ Sprint 24 — tags `source='esp32'`.
- **F7 — LOW:** `daily_summary` two-writer pattern was implicit. ✅ Sprint 24 — both writer functions now carry authoritative-column docstrings.
- **F9 — LOW (new, surfaced by topology):** `alert_log.zone_id` column exists (topology migration 086) and `AlertEnvelope.zone_id: int | None` is wired, but `ingestor/tasks.py::alert_monitor` never populates it — every built alert has `"zone": None` and implicitly `zone_id=None`. No data-loss risk (column is nullable), but an opportunity for richer alert routing once the topology tables are populated. Sprint 27+ candidate.
- **F10 — MEDIUM (surfaced by firmware's live-controller report):** `mister_state` + `mister_selected_zone` emitted as numeric template sensors (state_class=measurement), not text — `on_state_change` sensor branch had no STATE_MAP check so both routes went stale 27+ days. ✅ Sprint 24-alignment (`3b1d93a`) — added `_MISTER_STATE_NAMES` + `_MISTER_ZONE_NAMES` decoder tables, new branch in sensor path decodes codes (0=WATCH, 1=S1, 2=S2, 3=FOG; 0=none, 1=south, 2=west, 3=center) → routes to `system_state`. Live gate exercised WATCH/S1/S2 + none/south/west/center.
- **F11 — MEDIUM (firmware sprint-2 gap):** `mister_fairness_overrides_today` firmware sensor had no DB home → 7-day watchdog validation would require journalctl scraping. ✅ Sprint 24-alignment — migration `091-fairness-counter.sql` adds `daily_summary.mister_fairness_overrides_today`, schema + entity_map + `write_daily_summary` INSERT all wired. First midnight snapshot ~00:05 Apr 20 will land today's count.
- **F12 — LOW:** `v_stress_hours_today` fixed by coordinator migration 095. The view is now time-weighted from actual sample deltas instead of assuming a fixed 2-minute cadence.
- **F13 — LOW (surfaced by Sprint 24-alignment live gate):** `setpoint_confirmation_monitor` has no mechanism to age out "pre-readback-era" unconfirmed rows — old `setpoint_changes` pushed before firmware's cfg sensors existed never confirm and periodically re-fire alerts (observed: 1 stale critical for `vpd_target_south=1.530` from 18:04:33 UTC, before firmware sprint-3). Fix: age out unconfirmed rows >N hours old OR supersede: when newer same-param row confirms, backdate older. Sprint 27 candidate.

F8 (staleness window in `write_climate`) investigated and dismissed as correct.

## Sprint 24 — Correctness Closure (shipped `a9d2147`, live-gate PASSED 2026-04-19)

Closed F1/F2/F3/F4/F6/F7. Hotfix `4a89844` closed surface bugs in topology's `EquipmentId` literal + `fallback_window_s` + a test regression I missed; alignment `3b1d93a` closed F10 + F11 + firmware-drift allowlist.

- [x] **S24.1** `safety_invalid` alert category → `"system"`
- [x] **S24.2** ESP32-reported setpoint changes tagged `source='esp32'`
- [x] **S24.3** `ESP32LogRow` validation in `write_esp32_logs`
- [x] **S24.4** `_PHYSICS_INVARIANTS` renamed to canonical; dead entries removed
- [x] **S24.5** `scripts/forecast-sync.py` + `systemd/verdify-forecast.service` deleted; `OpenMeteoForecastResponse` validation inlined
- [x] **S24.6** Two-writer `daily_summary` authority docstrings
- [x] **S24.7** CI test `test_physics_invariants_are_canonical`
- [x] **Hotfix (F5-reopen):** `EquipmentId` widened from 26 → 42 names (topology's tightening was under-complete)
- [x] **Hotfix (readback):** `fallback_window_s` added to `NUMERIC_TUNABLES`; `test_tunable_set_matches_entity_map` relaxed to accept CFG_READBACK_MAP-only tunables
- [x] **Alignment F10:** numeric `mister_state` + `mister_selected_zone` → decoded `system_state` rows (live-verified WATCH/S1/S2 + none/south/west/center)
- [x] **Alignment F11:** migration 091 + schema + entity_map + INSERT for `mister_fairness_overrides_today`
- [x] **Alignment maintenance:** applied coordinator's unapplied migration 090; added dehum entries to DAILY_ACCUM_MAP drift allowlist

**Milestones achieved:** M1 (every write validates; zero dead invariants; safety_invalid fires). Live gate: zero errors for 5 min × 2 runs (hotfix + alignment).

**Deploy verified:** `main @ b25c09e` live on `verdify-ingestor.service` since 2026-04-19 12:48:41; 154+ log lines in watch window, 0 ERROR/WARNING.

## Sprint 25 — Discriminated `AlertEnvelope`

**Branch:** `ingestor/sprint-25-alert-union`. Target: 2 weeks. Blocked by coordinator schema PR.

**Schema PR spec:** [`docs/proposals/sprint-25-alert-envelope-union.md`](../proposals/sprint-25-alert-envelope-union.md) — full technical spec covering all 15 alert types with typed `*Details` models, drift guards, rollout sequence. Ready for coordinator execution.

Scope reduced from the original plan: `EquipmentStateEvent.equipment: EquipmentId` was delivered by topology Sprint 22 upstream. Sprint 25 now covers the alert union only.

- [x] **Schema PR (coordinator, blocking)** Split `AlertEnvelope` into discriminated union keyed by `alert_type`. Delivered as a backward-compatible schema wrapper over a tagged per-alert registry; current coverage is 25 alert types, including later planner/heap/firmware additions beyond the original 13-type spec. Each `*Details` subtype uses `extra="forbid"` and drift tests scan current write paths for unregistered alert types.
- [x] **S25.1** Migrate `alert_monitor` (`tasks.py::alert_monitor`) to per-type typed builders. The monitor's existing envelope validation now dispatches through the typed registry, so every active alert detail payload is validated before insert/update without changing runtime behavior.
- [x] **S25.2** Migrate `setpoint_confirmation_monitor` alert build. Creation and escalation now validate through `AlertEnvelope` before writing `setpoint_unconfirmed` details.
- [ ] **S25.3** Migrate `forecast_deviation_check` trigger write into unified `AlertEnvelope` path (supersedes the cross-cutting "deviation → structured alerts" item)

**Milestone M2:** alerts are type-checked at build time. Deviation monitor uses the unified alert path.

**Success criteria:** every `*Details` subtype has `extra="forbid"`; new `test_alert_envelope_dispatches_by_type` covers every branch; `alert_log` new-row rate within ±10% of 30-day baseline; post-deploy Slack chatter unchanged.

**Handshakes:**
- Coordinator: schema PR first
- Genai: MCP `alerts` tool `AlertAction` envelope (`verdify_schemas/alerts.py:83`) unchanged

## Sprint 26 — `tasks.py` Split

**Branch:** `ingestor/sprint-26-tasks-split`. Target: 1 week, pure refactor.

- [ ] **S26.1** Create `ingestor/tasks/` package: `ha.py`, `alerts.py`, `dispatcher.py`, `forecast.py`, `daily.py`, `heartbeat.py`, `confirmation.py`, `water.py`
- [ ] **S26.2** `tasks/__init__.py` re-exports existing names — import surface unchanged
- [ ] **S26.3** Delete `ingestor/tasks.py`
- [ ] **S26.4** Verify `from tasks import X` still works for every caller
- [ ] **S26.5** Update test coverage to import the new modules

**Milestone M3:** every module <500 lines; zero behavior change.

**Success criteria:** `wc -l ingestor/tasks/*.py` max <500; `make lint` + `make test` clean; 10 min live-tail shows every one of 16 tasks fires ≥1×; journalctl output identical modulo module-name prefix.

**Handshakes:** coordinator approves the import-surface change.

## Sprint 27 — Observability Tier 2 + SaaS prep

**Branch:** `ingestor/sprint-27-observability`. Target: 2 weeks.

- [ ] **S27.1** Structured JSON trace for `plan → dispatch → push → snapshot → confirmation` loop. Correlation ID from plan emission through every step. New `dispatcher_trace` hypertable (coordinator migration)
- [ ] **S27.2** Retire `_parse_float` — audit callers, migrate to `HAEntityState.as_float()`
- [ ] **S27.3** `greenhouse_id` write-path audit — every INSERT/UPDATE in ingestor scope includes it; coordinator-side backfill migration for existing NULLs
- [ ] **S27.4** Ingestor self-health endpoint — last-write-ts per hypertable + task loop last-run matrix — for `web` agent's status API (previously an "Ideas" bullet)

**Milestone M4:** every plan waypoint traceable end-to-end by `trace_id`. `greenhouse_id` consistent on every write. Web can render ingestor health without log scraping.

**Success criteria:** `SELECT * FROM dispatcher_trace WHERE trace_id=$1` returns full loop for a single tunable change; `grep _parse_float ingestor/` empty; drift guard covers `dispatcher_trace`; `/health/writes` endpoint returns 200 with per-table timestamps.

**Handshakes:**
- Coordinator: `dispatcher_trace` migration, `greenhouse_id` backfill migration
- Web: consumes `/health/writes`
- Genai: correlation ID propagates through `set_plan`/`set_tunable` MCP tools

## Decisions resolved 2026-04-18

All Sprint 24 blockers cleared and applied.

1. **F1 fix shape:** ✅ Shipped `"category": "system"` in Sprint 24. Semantic tightening deferred to Sprint 25's discriminated union where `safety_invalid` gets its own subtype.
2. ~~**Branch B1 (`iris-dev/sprint-22-pydantic-rollout`):** archive~~. **Superseded:** the two "unmerged" commits landed on `main` with slightly different SHAs (`a172576`, `6766ff9`) between session start and the first edit of this plan. Topology is shipped. The tracking branch can be pruned if it still exists locally; no archival tag needed.
3. **Branch B2 (`backend-dev`):** archive as `archive/backend-dev-experiment`. Still separate; coordinator-owned action.
4. **F2 consolidation:** ✅ Option A shipped — `scripts/forecast-sync.py` and `systemd/verdify-forecast.service` deleted, validation inlined into `tasks.py::forecast_sync`.
5. **Sprint 26 ordering:** AFTER Sprint 25. Alert migration lands on the existing monolith; split is a separate pure-refactor sprint.

## Candidate / Sprint 27+ (not yet committed)

- **F9 follow-up — alert `zone_id` population.** `alert_log.zone_id` (topology migration 086) is NULL on every ingestor-emitted alert today. Once topology tables are populated downstream, `alert_monitor` can resolve sensor/equipment → zone and tag alerts. Touches `tasks.py::alert_monitor` + lightweight `zone_of(sensor_id)` helper. Low priority until web/genai consume `zone_id`.
- **F12 — `v_stress_hours_today` semantics follow-up.** The fixed view is time-weighted by sample deltas. Remaining work, if any, is only to validate planner thresholds after several days of lower stress-hour values.
- **F13 — age out or supersede stale `setpoint_unconfirmed` alerts.** When a newer same-param `setpoint_changes` row confirms, either backdate older rows' `confirmed_at` or filter them from `setpoint_confirmation_monitor`'s candidate set so pre-readback-era rows stop re-firing alerts.
- Replace `docker exec verdify-timescaledb` wrappers in smoke tests with asyncpg (cross-cutting item; ~15s off test suite).
- Dispatcher self-health heartbeat — emit one row every 5 min proving loop is alive.
- Migrate hardcoded mister/safety dispatcher defaults (`tasks.py:1054-1083`) into a DB-backed `ingestor_defaults` table — tunable without redeploy.

## Recent history

- **Sprint 24-alignment** (merged `3b1d93a`, 2026-04-19 12:54 live-gate PASSED): firmware sprint-2 fairness counter wire-up (migration 091 + schema + entity_map), F10 decoded-state routing, DAILY_ACCUM_MAP drift allowlist. Also applied coordinator's unapplied migration 090 as part of the deploy.
- **Sprint 24-hotfix** (merged `4a89844`, 2026-04-19 12:30 live-gate PASSED): post-deploy findings — `EquipmentId` literal widened 26→42 to cover all ingestor-emitted names (topology's tightening was under-complete), `fallback_window_s` added to NUMERIC_TUNABLES + test_tunables relaxed to accept readback-only tunables, `test_fw3_invariants_table_defined` updated for Sprint 24 rename.
- **Sprint 24** (merged `a9d2147`, 2026-04-19): Correctness closure — F1/F2/F3/F4/F6/F7 fixes + CI guard.
- **Topology merge** (`a172576` Sprint 22 + `6766ff9` Sprint 23, merged 2026-04-18): not an ingestor sprint, but closed F5 (`EquipmentStateEvent.equipment: EquipmentId`) and added `alert_log.zone_id` upstream (see F9).
- **Sprint 23** (shipped `47f8154`, 2026-04-17): Pydantic rollout — every ingestor write path validates through `verdify_schemas`, HA integrations route through `HAEntityState`, Open-Meteo script validates via `OpenMeteoForecastResponse`, MCP harvest/treatment fix
- **Sprint 22** (`e96f9ba`): `verdify_schemas.telemetry` + drift guards foundation (~15 new schemas, 146 tests, 18 drift guards, vault migration, API `response_model`)
- **Sprint 21**: Full-stack Pydantic coverage (DB ↔ API ↔ MCP ↔ vault ↔ external)
- **Sprint 20**: Unified plan schema + feedback loop + manifestation
- **Sprint 19**: Signal quality + test coverage (Milestone A3) — leak hysteresis, deviation σ-gate, plan-context error routing
- **Sprint 18**: Deterministic dispatch (Milestone A2) — physics invariants, proportional dead-band, clamp audit

## Gates / reminders

- Every new DB write path goes through a `verdify_schemas` model at the boundary.
- Restart-then-tail is the live gate: `sudo systemctl restart verdify-ingestor && sudo journalctl -u verdify-ingestor -f`. Watch 5 min for `ValidationError` / schema errors.
- Drift guards must pass: `pytest verdify_schemas/tests/test_drift_guards.py`.
- Tunable invariant must pass: `pytest verdify_schemas/tests/test_tunables.py` — any new ESP32 entity goes into both `entity_map.py::SETPOINT_MAP` and `verdify_schemas/tunables.py::ALL_TUNABLES` in the same commit.
- Coordinator handshake for anything touching `verdify_schemas/`, `db/migrations/`, `systemd/` (cross-agent units), or `.github/workflows/`.
