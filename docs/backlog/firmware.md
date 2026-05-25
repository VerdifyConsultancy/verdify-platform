# Backlog: `firmware`

Owned by the [`firmware`](../agents/firmware.md) agent. Sprint counter is agent-local (resets under the agent-org split — see `CLAUDE.md`).

## In flight

- [x] **F-CLIMATE-INTENT first-principles controller implementation.** Canonical
  design:
  [`docs/firmware-climate-intent-controller-final-design-2026-05-24.md`](../firmware-climate-intent-controller-final-design-2026-05-24.md).
  Implement as one controller path: `F-CI-1` replay evaluator and historical
  action report; `F-CI-2` `ClimateIntent` schema and ownership map; `F-CI-3`
  firmware candidate projection structs/tests; `F-CI-4` live controller
  observability; `F-CI-5` planner/dispatcher bounded intent emission;
  `F-CI-6` deploy, validate live health, and publish scorecard. Validation
  must include replay, invariants, coordinator reproduction, and freeze-rule
  gates. Replay tooling lives behind `make climate-intent-replay-report` and
  reads the existing replay corpus. First report:
  [`docs/firmware-climate-intent-replay-report-2026-05-24.md`](../firmware-climate-intent-replay-report-2026-05-24.md).
  Live hotfix on 2026-05-24: compliance now preempts resource/churn dwell,
  and ClimateIntent telemetry describes the executed controller action
  rather than re-running a parallel selector. Production rule: no alternate
  live controller and no second proposal path; offline replay/counterfactual
  tools are diagnostic only. Accepted rollout landed through platform PR #4
  and planner PR `verdify-planner#3`, then passed live
  `make climate-authority-post-deploy-proof`, sensor-health, and site doctor.
  The separate PR #10 firmware hardening branch (`bc85f03`) represented an
  abandoned alternate controller architecture. It was archived at tag
  `archive/pr10-abandoned-controller-architecture-2026-05-25`, exported to
  `/mnt/iris/archives/verdify/`, closed unmerged, and its branch deleted. Do
  not rebase or apply PR #10 as a continuation path; start from current `main`
  if future firmware hardening is needed.
- [x] **F-CI-7 Climate Authority: decouple climate wet assist from crop
  direct-wet windows.** Canonical sprint plan:
  [`docs/climate-authority-sprint-plan-2026-05-24.md`](../climate-authority-sprint-plan-2026-05-24.md).
  GitHub issue:
  [#5](https://github.com/VerdifyConsultancy/verdify-platform/issues/5).
  Implemented `crop_direct_wet_allowed`, `climate_wet_assist_allowed`, and
  `climate_fog_assist_allowed` as separate safety gates. `VENT_COOL_MIST_ASSIST`
  and sealed humidification may use safe climate misters outside crop direct-wet
  windows; crop wetting, drips, and fertigation remain window-gated. Shipped in
  commit `9dd2b94` and OTA `2026.5.24.2255.9dd2b94` with operator-approved
  weekly override. Gates: `make lint`, `make test`, `make test-firmware`,
  `make firmware-invariants`, `make firmware-check`,
  `make firmware-audit-traceability-proof`, replay diff `2.65%` vs pre-change
  baseline under explicit `3%` threshold, and post-OTA sensor-health
  `PASS: 27 FAIL: 0 WARN: 0`.
- [ ] **48-hour v2 bake + reboot forensics follow-through.** Behavior-changing OTA for `2026.4.27.2009.2b5f2a5` began at `2026-04-28 02:12:25 UTC`; a wrapper-only main redeploy later put `2026.4.27.2040.c1a6403` live at `2026-04-28 02:41:54 UTC`. Sensor-health is `PASS 27 / FAIL 0 / WARN 0`, open critical/high alerts are `0`, and there are no post-OTA `Guru/Panic` / `Task WDT` resets in the sampled window. Keep OTA freeze intact during the bake. Current evidence reframes the old "midday crash-loop" as broader JSON/API/crash-forensics work; see [`docs/firmware-v2-postdeploy-forensics-2026-04-27.md`](../firmware-v2-postdeploy-forensics-2026-04-27.md).
- [ ] **Contract + alert drift guard PR.** `firmware/contracts-alert-drift` makes `vpd_low` explicitly dispatcher/band-owned, adds expected-firmware-version mismatch alerting, and adds static drift guards for firmware override tags, `sw_mister_closes_vent` routing, and MCP Tier 1 validation.

**Project recovery intake from `PROJECT_STATE.md`** (2026-05-22; coordinated
through [`project-recovery-2026-05-22.md`](project-recovery-2026-05-22.md)).

- [ ] **F-RM0 PR #80 bake and last-good promotion gate.** PR #80 firmware
  `2026.5.22.1331.19886ea` is already deployed; recovery must not treat this as
  permission for another OTA. After PR #80 merge, keep firmware freeze checks in
  force, verify zero critical/high alerts, complete the required bake window,
  and only then decide whether to promote the artifact to
  `firmware/artifacts/last-good.ota.bin`.
- [x] **F-RM7 May 10 firmware/ingestor stash review.** The May 10 stash touches
  firmware-freeze territory. Review it only in a clean temporary worktree,
  compare against current firmware behavior and invariants, then apply through a
  PR, archive as obsolete context, or drop it. Do not apply it directly to the
  production-linked root checkout. Archived without applying at
  `/mnt/verdify/docs/recovery-2026-05-22/rm7-archives/stashes/2026-05-10-firmware-agent-changes.patch`
  and dropped from the stash stack during RM-7.

**Launch support** (coordinated through [`docs/backlog/launch.md`](launch.md)).

- [x] **F-L1.1 Architecture fact review.** Public safety copy now uses the defensible wording: 8-state firmware controller evaluated every 5 seconds; Iris writes tactics and does not flip relays.
- [x] **F-L1.2 Bill-of-materials fact pack.** `/intelligence/build-notes` links the public equipment inventory and uses launch-safe ESP32/probe/relay/heater/fan/vent/fog/mister facts.
- [ ] **F-L0 Freeze discipline.** No launch-driven behavior-changing OTA. Firmware changes remain Track A only and must obey replay/invariant/freeze rules.

## Recently landed

- **`2b5f2a5` / firmware PR #37** — v2 controller deployment, controlled fog+heat assist, replay/invariant/test coverage, ESPHome worktree deploy cleanup, rollback path cleanup, and final OTA of `2026.4.27.2009.2b5f2a5`. Validation: `make lint`, `make test`, `make test-firmware`, `make firmware-invariants`, `make firmware-check`, replay diff, OTA, and post-deploy sensor-health all passed. `/srv/greenhouse/esphome` symlink farm was retired; `make firmware-check` / `make firmware-deploy` now compile from the active git worktree.
- **`firmware/sprint-11-simplify-rip-day-night`** — architectural simplification hotfix after sprint-10 caused overnight non-compliance. Ripped out the 0.3 day/night / photoperiod infrastructure entirely: 8 day/night `Setpoints` fields removed, `is_photoperiod` removed from `SensorInputs`, `ActiveBand` struct + `resolve_active_band()` helper deleted, hybrid photoperiod computation removed from controls.yaml, 10 HA Number entities removed, 10 `/setpoints` parse rules removed, 10 globals removed, 6 s10 day/night unit tests removed. Firmware default band widened to permissive safety-rail-bounded values (temp 40-95°F, vpd 0.35-2.80 kPa). Kept from sprint-10: 0.2 THERMAL_RELIEF both fans, 0.4a `dt_ms` clamp, 0.4b magic-number extraction, 0.4c SEALED_MIST `vpd_min_safe` override. 79/79 unit tests pass, replay clean, self-test all ✓.
- **`firmware/sprint-10-phase0-completion`** — Phase 0 completion bundle per the agronomy review. **0.2** THERMAL_RELIEF both fans; **0.4a** `dt_ms` clamped to 5 s at the caller; **0.4b** three hardcoded magic numbers (`1800000` ms vent-latch, `5.0` safety-max seal margin, `ECON_HEAT_MARGIN_F`) moved into `Setpoints`; **0.4c** `vpd_min_safe` override extended to break SEALED_MIST (with state cleanup). **0.3 day/night was REVERTED in sprint-11** — firmware-default day/night values (night 62-68°F) silently outranked the dispatcher's pushed crop band for ~10 hours overnight; dispatcher has no SETPOINT_MAP entries for day/night keys, so the fallback path never activated. Root cause: firmware was modeling temporal policy (day vs night) the dispatcher already owns. Architecture now: **two bands only** — safety rails + the one active band pushed by the ingestor.

- **`firmware/sprint-9-quick-hardening`** — reviewer-recommended one-session bundle after sprint-8 validation: P1#7 Heat S2 latch via new `heat2_latched` bit in ControlState (prevents gas-valve rapid cycling); P2#8 relational asserts in `validate_setpoints()`; P1#4 R2-3 cycle-breaker bypass documented; P2#11 SAFETY_HEAT runs lead fan for canopy circulation. 10 new tests; 74/74 pass.
- **`firmware/sprint-8-r23-fog-helper`** — multi-reviewer synthesis P0 pass. P0#1 R2-3 FOG demotion, P0#2 sealed_timer reset, P0#3 midnight-wrap fog window, P1#5 safety reset symmetry, P1#6 fog_permitted helper, P3#14 "full battery" comment. **Bonus win confirmed by review:** resetting `vpd_watch_timer_ms` on safety entry also closed the stale-dwell-across-safety concern from the original P1#4.
- **`firmware/sprint-7-cycle-audit`** — per-zone cycle counters for misters + drips (5 new `cnt_*_today` globals + template sensors). Rising-edge counted in `turn_on_zone` and irrigation dispatch. Sensors live on ESP32; ingestor DAILY_ACCUM_MAP + coordinator migration queued as handoffs.
- **`firmware/sprint-6-midnight-audit`** — docs-only. 60-day telemetry found no midnight edge-case; midday crash clustering logged for a separate sprint.
- **`firmware/sprint-5-tooling`** — `make replay-corpus-refresh` + deploy-time `fw_version` bump. Corpus now covers through today; `diagnostics.firmware_version` is per-commit unique.
- **`firmware/sprint-4-leak-debounce`** — `bs_leak_detected` gained a 30 s bleed-down grace period + added `fog_rly` to the valve list. Post-deploy: 0 `leak_detected` transitions in first 5-min watch window (vs baseline 2.3/5min).
- **`firmware/sprint-3-cfg-readbacks`** — per-zone VPD target readback sensors. Ingestor routing in commit `4cc5df5`.
- **`firmware/sprint-2-zone-fairness`** — 10-min last-fire watchdog. Counter `mister_fairness_overrides_today`. Ingestor wire-up followed.
- **`firmware/sprint-1-housekeeping`** — drift fixes + dead-code cleanup + doc sync.

## Coordination queue (handoffs to other agents)

Findings from the 2026-04-18 audit that live outside firmware scope. Each is a focused handoff PR filed into the owning agent's scope, labeled `requested-by: firmware`:

- [x] **Coordinator — EquipmentId schema reconciliation.** ✅ Landed as ingestor sprint-24 hotfix (commit `4a89844`).
- [x] **Ingestor — route `mister_fairness_overrides_today`.** ✅ Landed as ingestor sprint-24-alignment; `daily_summary.mister_fairness_overrides_today` column exists.
- [x] **Ingestor — F10: `mister_state` / `mister_zone` STATE_MAP mismatch.** ✅ Landed as ingestor sprint-24-alignment; sensor_health is now 100% (0 stale).
- [x] **Coordinator — override flag enum guard.** ✅ In `firmware/contracts-alert-drift`: `OVERRIDE_EVENT_TYPES` validates `OverrideEvent.override_type`; a firmware drift guard compares `OverrideFlags` fields, controls.yaml published tags, and schema-accepted tags.
- [x] **Coordinator + genai — `sw_mister_closes_vent` routing.** ✅ Routed through `entity_map.SETPOINT_MAP`, `CFG_READBACK_MAP`, `SWITCH_TO_ENTITY`, `verdify_schemas.tunables`, firmware `controls.yaml`, and MCP `TIER1_TUNABLES`; static tests now pin the route.
- [x] **Ingestor — alert monitor coverage for OBS-3.** ✅ `alert_monitor` watches `diagnostics.relief_cycle_count` and `diagnostics.vent_latch_timer_s`; the same PR adds expected firmware-version mismatch alerting from `/srv/verdify/state/expected-firmware-version`.
- [x] **Ingestor — override events smoke test.** Added
  `test_override_events_written` to `tests/test_05_ingestor.py` to verify
  `gh_overrides`/`active_overrides` diffs enqueue one row per new flag and
  flush valid rows to `override_events`.
- [x] **Ingestor — `setpoint_unconfirmed` lifecycle fix.**
  `setpoint_confirmation_monitor` owns the full alert lifecycle now: it
  resolves confirmed rows, resolves alerts superseded by newer non-ESP32
  setpoint pushes, marks stale pending rows `delivery_status='superseded'`
  with `superseded_by_ts`, ignores superseded rows when opening new alerts,
  and confirms switch-only rows from `equipment_state`. Static tests cover the
  superseded-row path, and the 2026-05-22 live check found zero open
  `setpoint_unconfirmed` alerts and zero non-terminal recent pending rows.
- [x] **Coordinator — `daily_summary.cycles_{mister_south,mister_west,mister_center,drip_wall,drip_center}` columns (sprint-7 follow-up).** Migration 097 adds the columns.
- [x] **Ingestor — DAILY_ACCUM_MAP entries for the sprint-7 cycle sensors.** Slug keys route to the new `daily_summary` columns; daily snapshot validation/writes include them.
- [x] **Coordinator — `v_cycle_count_audit` view (sprint-7 follow-up).** Migration 097 adds the audit view and flags >5% divergence as `warn`.
- [ ] **Ingestor — F12 triage: `v_stress_hours_today` semantics.** "32 h cold_stress/day" suggests zone-summing not clock-summing. Doc fix or view fix.
- [x] **Genai — MCP `set_tunable()` validator.** ✅ MCP rejects unknown params via `ALL_TUNABLES` and rejects non-Tier-1 params via `TIER1_TUNABLES`; tests pin `vpd_low` as band-owned rather than tactical.
- [ ] **Saas — real-time setpoint push.** `controls.yaml` references an aioesphomeapi push path that doesn't exist. Either implement or drop the comment.

## Candidates (operational, co-owned with `saas`)

- [ ] **Cloud-fallback setpoints** (was Sprint 10 B10.6) — firmware OTA: add cloud URL as secondary setpoint source.
- [ ] **Direct MQTT to cloud** (was Sprint 10 B10.7) — firmware OTA: publish to `mqtt.verdify.ai` directly.
- [ ] **Cloud-only test window** (was Sprint 10 B10.8) — 24 h test: disable local ingestor, verify ESP32 runs from cloud.

## Ideas (not yet committed)

### Stability & safety

- **Reboot / illegal-instruction forensics**. Read-only forensics completed on `2026-04-27`; see [`docs/firmware-v2-postdeploy-forensics-2026-04-27.md`](../firmware-v2-postdeploy-forensics-2026-04-27.md). Updated finding: the reset problem is real but not purely midday heat. Last 14 days: `143` unexpected reboot events (`106` Task WDT, `37` Guru/Panic); only `29/143` occurred above `80 F` and `22/143` above `1.8 kPa` VPD. The clearest recent panic had `[E][json:064]: Parse error: IncompleteInput` followed by `Fault - IllegalInstruction`, slow API work, and an average-sensor NaN fallback. Next actions: retain per-version `firmware.elf`, add crash symbolization tooling, count JSON/API failure bursts into diagnostics/alerts, and keep the current v2 OTA baking before any control-loop changes.

### Phase 1 sensor wire-up _(queued for sprint-11)_

Extend `SensorInputs` to expose data already flowing through the ESP32 so the control logic can reason over it. No hardware orders — Tempest covers outdoor temp/RH/lux; `greenhouse_co2_ppm` is on the onboard ADC; `tempest_solar_w_m2` is live via UDP.

- `outdoor_temp_f` (from `tempest_temp_f` / `pulled_outdoor_temp_f`). Enables `vent_can_cool = outdoor_temp_f < temp_f - 2.0f` gate in VENTILATE/DEHUM_VENT decisions.
- `outdoor_dewpoint_f` (computed inside controls.yaml from outdoor temp + RH). Enables `vent_can_dehum = outdoor_dewpoint_f < inside_dewpoint_f - 2.0f` gate for DEHUM_VENT (prevents vent-exchanging saturated air for saturated air).
- `co2_ppm` (from `greenhouse_co2_ppm`). Enables a CO2 exchange cap on long sealed cycles during photoperiod (hard cap at `sealed_max_ms` regardless of timer is already the blunt proxy; this adds a proper `co2_ppm < 350` trigger).
- `solar_w_m2` (from `tempest_solar_w_m2`). Solar-driven enthalpy gain is a dominant term for sealed-daytime thermal runaway; this lets `sealed_max_ms` shrink under strong sun.
- `lux` (from `tempest_lux`, also used for `is_photoperiod`). Exposed so downstream decisions can read it directly.

Leaf MLX90614 (`leaf_temp_f` + computed `leaf_air_vpd_kpa`) is separately a hardware order and the only Phase 1 item that actually needs new hardware.

### P3 semantic polish _(queued for sprint-12)_

- **P3#12 — Include THERMAL_RELIEF in `was_ventilating`.** `was_ventilating = (prev in {VENTILATE, THERMAL_RELIEF})`. Prevents chatter at the post-relief boundary when temp is marginal.
- **P3#15 — Clarify `econ_block` vs `vpd_min_safe` policy.** Current behavior documented in sprint-10 `s10_vpd_min_safe_override_respects_econ_block` test: with `econ_block=true`, greenhouse stays in IDLE even at dangerous humidity. Either add an unconditional safety override (economy can't block safety) or document the intentional precedence.

### P4 code health _(queued for sprint-13)_

- **P4#17 — Remove dead occupancy branch in mist_stage progression.** `if (moisture_blocked) { state.mist_stage = MIST_WATCH; }` inside `if (mode == SEALED_MIST)` is unreachable (outer planner exits SEALED_MIST on occupancy first). Delete + add comment explaining absence.
- **P4#19 — Consolidate R2-X / FW-X history into `DESIGN.md`.** `firmware/DESIGN.md` now captures the v1.0 control boundary, state machine, setpoint contract, safety layers, observability, and deployment contract. Remaining cleanup: strip old sprint-tag inline comments in favor of stable design-doc references.

### P4 larger refactor _(discretionary, sprint-14)_

- **P4#18 — Split `determine_mode()`.** ~150 lines of dense temporal logic. Extract `evaluate_safety()`, `evaluate_planner()`, `apply_emergency_seal()`, `progress_mist()`. Preserves observable behavior; bolts test hooks onto each sub-function.
- **P4#20 — Sharpen `evaluate_overrides()` counterfactuals.** Upgrade each flag from "condition present" to true counterfactual ("would the final mode differ if this gate wasn't here?"). Reviewer calls current behavior "good diagnostics, not a mathematically exact explanation engine" — fine as-is, optional to sharpen.

## Closed / resolved

- **Phase 0 agronomy-review items** — _sprint-10, 2026-04-19_ — closed: 0.2 THERMAL_RELIEF both fans; 0.4a `dt_ms` clamp; 0.4b three magic numbers into Setpoints; 0.4c vpd_min_safe override for SEALED_MIST (P3#13 absorbed); 0.3 day/night setpoint pairs with hybrid is_photoperiod. P2#9, P2#10, P3#13 all closed. Open from the original Phase 0: 0.1 always-on HAF (hardware question — spare relay vs multi-tap).
- **Multi-reviewer synthesis P0/P1/P3 items** — _sprint-8 + sprint-9, 2026-04-19_ — closed: P0#1/P0#2/P0#3 R2-3 bug trio + midnight wrap; P1#4 R2-3 cycle-breaker bypass documented; P1#5 safety-mode reset symmetry; P1#6 fog_permitted extraction; P1#7 heat-S2 latch; P2#8 validate_setpoints relational asserts; P2#11 SAFETY_HEAT fan; P3#14 "full battery" comment. Bonus fix: vpd_watch_timer_ms reset on safety entry closed the stale-dwell half of P1#4.
- **Midnight-transition investigation** — _sprint-6, 2026-04-19_ — 60-day telemetry analysis found no evidence of edge-case behavior near 00:00 local. State-transition density, null rate, equipment activity, and crash distribution are all normal at midnight; midday (11-14 local) is where anomalies cluster. Counter-reset code paths reviewed and found correct. Full writeup in sprint-6 commit.

## Trigger-dated

- **2026-05-19 — 30-day sprint-2 fairness follow-up audit.** Re-run the 90-day fairness audit query from the sprint-2 commit message (`4471743`). Expect west's on-cycle share to climb from 15% toward 30% of total mister cycles. Prerequisite: `make replay-corpus-refresh` has been run routinely so the corpus extends to that date.

## Gates / reminders

- **Two-band rule (sprint-11 hotfix).** The firmware exposes exactly two sources of band: safety rails (`safety_min/max`, `vpd_min/max_safe`) and the one active band pushed by the ingestor (`temp_low/high`, `vpd_low/high`). Anything that looks like "make the firmware aware of day vs night" or "shrink the band when solar is high" is a dispatcher decision — have the dispatcher push the right band at the right time, not the firmware model a second mode. Firmware defaults for dispatcher-pushed fields must stay wide and safety-rail-bounded; a silent dispatcher falls through to "safety only," not "firmware-invented band." See commit message for the sprint-10 overnight incident that produced this rule.
- Replay against 8 months of telemetry (`make test-firmware`) is a **permanent gate** for structural firmware changes.
- `make firmware-check` (ESPHome compile) must pass before commit; `make check` runs the full chain.
- Any new override flag must land in `verdify_schemas/telemetry.OverrideEvent` and `firmware/lib/greenhouse_types.h` (`OverrideFlags` struct) — coordinate with coordinator first.
- Any relay / switch rename touches `entity_map.py` — coordinate with ingestor.

## Launch-support tasks from prior-art review

- [x] **F-L1.10 Safety Architecture facts.** `/intelligence/safety-architecture` documents loop cadence, local enforcement, relay ownership, safety rails, and what happens if cloud/Wi-Fi/planner fail.
- [x] **F-L1.14 Builder path facts.** `/intelligence/build-notes` reviews BOM/wiring/equipment claims and says not to copy photos as wiring instructions.
- [x] **F-L2.5 Progressive autonomy roadmap.** `/intelligence/safety-architecture` defines L1-L4 autonomy and states that no roadmap item bypasses firmware safety or implies launch-time OTA.
