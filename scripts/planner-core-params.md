# Planner Core Parameters

## Canonical Contract

The canonical routine-plan surface is `mcp/server.py::PLAN_REQUIRED_PARAMS`,
derived from the `control_class="planner_policy"` Tier 1 rows in
`verdify_schemas/tunable_registry.py`.

Full `set_plan` transitions must include every required Tier 1 planner-policy
parameter. This prevents stale active rows from old plans carrying forward under
the same horizon. Crop-band rows are not part of this contract.

## Ownership

- Crop bands: `temp_low`, `temp_high`, `vpd_low`, and `vpd_high` are
  dispatcher-owned from crop profiles. The planner must not write them.
- Planner policy: tactical controls such as hysteresis, cooling staging, mist timing,
  fog thresholds, vent gates, and dwell gates are writable through
  MCP after registry validation.
- Controller gate: `sw_fsm_controller_enabled` is readback/context only; ESPHome
  keeps the unified band-first path ON.
- Reserved/no-op rows are context only and must stay out of plans until firmware
  consumes them.

## Key Semantics

- `mister_engage_kpa` gates physical S1 mister pulses once `SEALED_MIST` or
  explicit `VENTILATE` assist creates humidity demand. SEALED_MIST entry itself comes from `vpd_high` plus
  `vpd_watch_dwell_s`.
- `mister_all_kpa` controls physical all-zone mister rotation.
- During live VPD-high or near-edge `VENTILATE` stress with healthy dew margin,
  moisture thresholds must stay coupled to the active band: engage near
  `vpd_high + 0.05`, all-zone near `max(1.0, vpd_high + 0.25)`, fog escalation
  near `0.20` or `0.15` in hot/dry venting, short mist delays/gaps, and
  shorter `min_fog_off_s`. Dispatcher clamps conservative planner values that
  would let dry-air `VENTILATE` hold temperature while leaving VPD above band.
  Do not unwind this posture until observed VPD has stayed below the high band;
  forecasted solar decline alone is not recovery evidence.
- `d_heat_stage_2` and `d_cool_stage_2` are retired from live band-first
  planner policy. Use `cool_stage2_over_high_f`,
  `cool_exit_hysteresis_f`, `cold_vent_guard_delta_f`, and
  `sw_cool_all_fans_at_high_enabled` for cooling posture.
- `DEHUM_VENT` exits immediately if it overshoots dry-side VPD above
  `vpd_high`; do not use dwell gates to hold dehumidification through dry
  stress.
- Heat2 is invalid without heat1, and non-safety heat is suppressed during
  physical vent/fan air exchange.
- The dispatcher keeps the house VPD control band at least 0.55 kPa wide to
  prevent mixed-zone crop targets from creating chatter.
- `sw_fog_closes_vent` and `sw_mister_closes_vent` are planner-policy
  interlocks for normal moisture cycles; explicit VENTILATE vent-mist assist
  bypasses them because temperature remains the primary control objective.

## Drift Guards

Use `scripts/audit-tunable-traceability.py` before deployment. It verifies the
schema, registry, MCP required set, planner policy, and cfg readback coverage
agree for the active tunables surface.
