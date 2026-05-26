# Climate Authority Sprint Plan - 2026-05-24

## Status

Implemented and deployed on 2026-05-24 MDT for the controller, planner, and
schema/data layers. This plan follows the ClimateIntent contract foundation and
the live diagnosis from 2026-05-24 21:48 MDT, where the greenhouse was above
both dispatcher-owned bands but the physical wet actuators were off.

Deployment evidence:

- Implementation commit: `9dd2b94`.
- OTA firmware version: `2026.5.24.2255.9dd2b94`.
- Operator-approved weekly OTA override reason: climate authority deployment
  for active temp+VPD compliance miss.
- Gates passed: `make lint`, `make test`, `make test-firmware`,
  `make firmware-invariants`, `make firmware-check`,
  `make firmware-audit-traceability-proof`, and explicit replay divergence
  thresholds for intentional behavior change.
- Post-OTA sensor-health: `PASS: 27 FAIL: 0 WARN: 0`.
- Active plan coverage after late SUNSET remediation: plan `iris-20260524-2246`,
  `4/4` transitions complete with 39 tactical Tier 1 params and no
  dispatcher-owned band params.
- New `climate_action_log` rows showed `VENT_COOL_MIST_ASSIST` with physical
  wet assist served before VPD returned into band.

This is a production greenhouse plan. Safety rails remain first, the ESP32
remains relay authority, and there is still one live controller path. Offline
replay and reports may compare behavior, but production actuation must not fork
into a shadow controller.

## Problem Statement

At 2026-05-24 21:48 MDT, live telemetry showed:

- Temp: `68.72F`, active high band `65.2F`, so `+3.52F` above band.
- VPD: `1.345 kPa`, active high band `0.8 kPa`, so `+0.545 kPa` above band.
- Controller decision: `VENT_COOL_MIST_ASSIST`.
- Fans and vent: on.
- Fogger and misters: off.
- Physical status: `vent_mist_assist_status=blocked:direct_wet_window`.
- Moisture block: `direct_wet_window`.
- Fog block: `time_window`.

The controller selected the right class of action, but the wet-assist path was
still coupled to crop direct-wet irrigation windows and fog clock windows. That
left the system with only fan/vent cooling, even though dry-air ventilation had
limited authority against VPD compliance.

The fix is not more raw planner parameters. The fix is a cleaner authority
model:

1. Dispatcher owns temp/VPD low-target-high bands.
2. Planner emits bounded tactical ClimateIntent around those targets.
3. Firmware selects one climate action and owns relay truth.
4. Climate evaporative assist has its own safety gate.
5. Crop direct-wet irrigation windows continue to protect crop wetting,
   fertigation, and drip behavior.

## Sprint Objective

Make the controller capable of using safe evaporative assist for temperature
and VPD-band recovery without depending on crop direct-wet irrigation windows.
Then make planner prompts and data surfaces prove why that action was selected,
blocked, or effective.

Strict priority order:

1. Safety rails.
2. Temperature-band compliance.
3. VPD-band compliance.
4. Resource use and relay churn.

## Non-Goals

- Do not let AI write temp/VPD low-target-high values.
- Do not expose raw relay commands to AI.
- Do not add a second production controller, shadow actuation path, or runtime
  proposal sidecar.
- Do not let climate logic drive fertigation or drip relays.
- Do not disable dew-margin, occupancy, irrigation, leak, water-budget,
  sensor-freshness, or relay min-on/min-off safety rails.
- Do not ship an OTA outside firmware freeze rules.

## Architecture Change

### Current Coupling To Remove

Current mist logic computes zone availability from `direct_wet_allowed(zone)`.
That helper is appropriate for crop wetting and direct irrigation windows, but
it is too restrictive for climate evaporative assist.

The sprint splits this into two gates:

```text
crop_direct_wet_allowed(zone)
  Used by crop wetting, drips, fertigation, and scheduled direct-wet behavior.
  Keeps existing activity-window and drydown semantics.

climate_wet_assist_allowed(zone)
  Used only when the selected climate action is VENT_COOL_MIST_ASSIST,
  VENT_COOL_FOG_ASSIST, SEALED_HUMIDIFY, or SEALED_FOG.
  Ignores crop direct-wet activity windows, but enforces climate safety rails.
```

Climate wet assist may run outside crop direct-wet windows only when all safety
predicates pass:

- temp/VPD demand exists against dispatcher-owned bands;
- selected action is a wet-assist climate action;
- dew margin is at or above `dew_margin_floor_f`;
- local time is before `wet_cutoff_hour`;
- greenhouse temp is above climate wet-assist minimum temp;
- sensors are plausible and fresh enough for control;
- occupancy inhibit is false;
- irrigation/fertigation conflict is false;
- leak/water safety is clear;
- climate water budget is available, except a named safety emergency policy;
- relay min-on/min-off and pulse/gap mechanics are respected.

### Fog Gate

Fog also needs a climate-assist gate separate from the normal daytime fog
window. The normal fog window remains a default schedule. Climate fog assist can
extend beyond it only when:

- selected action is `VENT_COOL_FOG_ASSIST`, `SEALED_FOG`, or `SAFETY_COOL`;
- VPD exceeds active `vpd_high` by the configured fog escalation excess;
- dew margin, RH ceiling, min temp, occupancy, water budget, and relay min-off
  checks pass;
- local time is before the ClimateIntent wet cutoff;
- vent/fog interlock policy allows the combination for the selected action.

The planner may tune the bounded intent values that feed these gates. It may not
bypass the gates.

## Workstream A - Firmware Controller

Owner: firmware agent, with coordinator review for interface changes.

### A1. Introduce Explicit Wet-Assist Authority Helpers

Add pure helpers in firmware code:

- `crop_direct_wet_allowed(zone, context)`
- `climate_wet_assist_allowed(zone, context)`
- `climate_fog_assist_allowed(context)`
- `climate_wet_block_reason(context)`
- `climate_fog_block_reason(context)`

Keep these helpers side-effect-free where possible so unit tests can exercise
the 2026-05-24 night case without ESPHome relay side effects.

### A2. Route Climate Misters Through Climate Gate

For climate demand:

- `VENT_COOL_MIST_ASSIST` uses `climate_wet_assist_allowed()`.
- `SEALED_HUMIDIFY` uses the same climate safety gate.
- Scheduled crop direct-wet and irrigation continue to use
  `crop_direct_wet_allowed()`.
- The relay watchdog may still turn off drips/fert relays when crop windows are
  closed, but it must not kill an active climate mister pulse that is allowed by
  `climate_wet_assist_allowed()`.

This is the core behavioral fix for the observed state.

### A3. Route Fog Through Climate Gate

For climate fog demand:

- Replace the current "normal fog window or stress extension switch" ambiguity
  with one explicit climate fog gate.
- Keep `fog_stress_window_extend_enabled` as the current materialized switch if
  needed for compatibility, but the live decision should explain the climate
  reason, not only `time_window`.
- Publish whether fog was blocked by `below_threshold`, `dew_margin`,
  `rh_ceiling`, `temp_low`, `occupancy`, `relay_min_off`, `resource_budget`,
  `vent_interlock`, or `wet_cutoff`.

### A4. Make Observability Reflect Physical Truth

Telemetry must distinguish:

- selected climate action;
- allowed wet-assist authority;
- relay actually served;
- block reason if no relay served.

`climate_moisture_assist_state=served` must only appear when fog or a mister
is physically on. A selected action that cannot actuate should publish
`blocked` with the reason.

### A5. Firmware Tests

Required firmware tests:

- Reproduce the 2026-05-24 21:48 MDT state with `temp_band_error_f > 0`,
  `vpd_band_error_kpa > 0`, safe dew margin, and wet cutoff still open. Expected:
  `VENT_COOL_MIST_ASSIST` and at least one climate mister zone eligible even
  when crop direct-wet windows are closed.
- Same state with `wet_cutoff_hour` expired. Expected: wet assist blocked by
  `wet_cutoff` or `time_window`.
- Same state with dew margin below floor. Expected: wet assist blocked by
  `dew_margin`.
- Same state with occupancy true. Expected: wet assist blocked by `occupancy`.
- Same state with irrigation active. Expected: wet assist blocked by
  `irrigation`.
- Crop direct-wet/drip/fert relays remain blocked outside crop windows.
- Climate action selection remains lexicographic: safety, temp, VPD, resource.
- Climate relay surface still excludes fert/drip relays.

## Workstream B - Planner And Dispatcher

Owner: genai for prompt/planner behavior, ingestor for dispatcher/materialized
plan context, coordinator for shared ClimateIntent schema or contract changes.

### B1. Prompt The Planner With Control Authority State

The planner already receives dispatcher-owned bands and target deltas. Add a
small authority section:

- current selected climate action;
- temp and VPD band errors;
- current wet-assist switches and cutoffs;
- latest wet/fog block reasons;
- whether the current state is control-authority-limited or actuator-capacity
  limited;
- recent clamps and setpoint confirmations for ClimateIntent-derived knobs.

This prevents the planner from declaring "mist assist" while leaving the wet
gate off during an active compliance miss.

### B2. Compliance-First ClimateIntent Guidance

Update planner guidance so that when both temp and VPD are above band:

- resource conservation cannot disable wet-assist availability if dew margin,
  occupancy, irrigation, and water-budget rails are safe;
- `wet_cutoff_hour` should extend far enough to cover the forecast stress
  segment when evening VPD remains high and dew margin is safe;
- `moisture_engage_vpd_excess_kpa` should stay near the lower bound during
  active VPD-band misses;
- `all_zone_vpd_excess_kpa` should express distributed mister escalation
  independently from fog so Iris can request all-zone mist while holding fog
  back for dew or disease risk;
- `fog_escalate_vpd_excess_kpa` should be lower only when fog is safe and recent
  fog response helped compliance without overshoot;
- resource sensitivity may reduce duty cycle, but it should not make the wet
  path unavailable during a dual-axis compliance miss.

### B3. Materializer Guardrail

Add a materializer or MCP validation guard that flags contradictory intent:

- high positive VPD pressure but wet-assist switches materialize off;
- wet cutoff before the forecast stress segment while VPD is already above
  band;
- resource sensitivity high while both temp and VPD are out of band and no
  safety block exists.

The guard should reject only clear contradictions. Otherwise it should annotate
the plan and let the bounded intent through.

### B4. Historical And Forecast Context

The planner context should include recent response priors:

- vent-only response under similar outdoor temp/dewpoint/wind/solar;
- mister response by zone and dew margin;
- fog response, overshoot risk, and recovery time;
- water used per VPD-compliance-hour gained;
- electric use per temp-compliance-hour gained;
- forecast pressure for the next solar ramp, peak stress, decline, and evening
  humidity rebound.

The planner uses these to set semantic intent. Firmware still owns execution.

## Workstream C - Schema, Data, And Scorecards

Owner: coordinator with ingestor/web handoffs.

### C1. Structured Climate Action Log

Add a durable structured climate action log instead of relying only on latest
key-value system state. Proposed columns:

- `ts`, `greenhouse_id`
- `climate_action`
- `priority_axis`
- `temp_low_f`, `temp_target_f`, `temp_high_f`
- `vpd_low_kpa`, `vpd_target_kpa`, `vpd_high_kpa`
- `temp_target_delta_f`, `vpd_target_delta_kpa`
- `temp_band_error_f`, `vpd_band_error_kpa`
- `moisture_assist_state`, `moisture_zone`
- `wet_assist_allowed`, `wet_assist_block_reason`
- `fog_allowed`, `fog_block_reason`
- `relay_truth` JSONB for climate relays
- `resource_cost_estimate` JSONB
- `climate_intent_version`
- `plan_id`, `trigger_id`, `planner_instance` when known
- `sensor_confidence` or `sensor_status` summary

This table lets Grafana and analysis query decisions without reconstructing
state from multiple latest-value rows.

### C2. Effectiveness Views

Add views that measure whether actions worked:

- `v_climate_action_effectiveness_5m`
- `v_climate_action_effectiveness_15m`
- `v_climate_action_daily_scorecard`

Minimum metrics:

- temp band error before and after;
- VPD band error before and after;
- time to return inside band;
- wet relay duty and gallons;
- fan/vent/fog runtime;
- outdoor temp/dewpoint/solar context;
- selected action and block reasons;
- plan/intent segment that governed the window.

These views should be read-only evidence surfaces, not controller inputs in the
first sprint.

### C3. Target Delta Graphing

The existing `v_greenhouse_state` target-delta columns are useful but expensive
when scanned naively. Keep the fast latest-context path for planner prompts and
add graph-friendly indexed or materialized surfaces for:

- `temp_target_delta_f`
- `vpd_target_delta_kpa`
- `temp_band_error_f`
- `vpd_band_error_kpa`
- selected `climate_action`
- wet/fog block reasons

### C4. Data Tests

Required schema/data tests:

- climate action log accepts every schema action and rejects unknown actions;
- every firmware-published climate decision field has a DB home;
- views smoke-test against seeded rows;
- no view relies on an unbounded `v_greenhouse_state ORDER BY ts DESC` scan for
  live planner context;
- every row has `greenhouse_id`.

## Implementation Sequence

### Step 1 - Contract And Backlog

- Land this sprint plan and GitHub issues.
- Update firmware, genai, ingestor, and cross-cutting backlogs.
- Keep PR #4 as the ClimateIntent foundation and this plan as the next
  implementation train.

### Step 2 - Schema/Data PR

- Add migration and schema tests for `climate_action_log` and effectiveness
  views.
- Update ingestor routing to write structured rows from ESP32 climate telemetry.
- Restart documentation: `verdify-ingestor`, `verdify-mcp` only if schemas or
  MCP context change.

### Step 3 - Firmware PR

- Implement climate wet/fog assist gates.
- Add firmware unit tests for all block reasons and the 2026-05-24 regression.
- Run `make test-firmware`, `make firmware-invariants`, `make firmware-check`,
  and replay diff.
- The replay divergence is expected and must be justified: the exact target is
  more wet assist during hot/dry above-band windows while preserving safety.

### Step 4 - Planner/Dispatcher PR

- Update planner context and prompt guidance.
- Add materializer contradiction guard.
- Add tests proving full plans include ClimateIntent, dispatcher targets are
  read-only, and dual-axis compliance misses do not materialize wet assist off
  without a named safety block.

### Step 5 - Integrated Validation

- Replay hot/dry windows:
  - 2026-05-24 evening high-VPD state;
  - 2026-04-21 and 2026-04-22 hot/dry peak windows;
  - current replay corpus.
- Validate no safety invariant violations.
- Validate no fert/drip relay activation from climate logic.
- Validate planned intent and physical relay action agree in telemetry.

### Step 6 - Deploy

- Deploy service/schema changes first.
- Confirm live context and action logging.
- OTA only with firmware freeze compliance, operator approval if needed, and
  rollback artifact.
- Post-OTA watch:
  - critical/high alerts;
  - temp/VPD target deltas;
  - climate action;
  - wet/fog block reasons;
  - wet relay duty;
  - water budget use;
  - sensor freshness;
  - setpoint confirmation.

## Acceptance Criteria

The sprint is complete only when all are true:

- In the 2026-05-24 regression fixture, the controller can serve safe
  evaporative assist outside crop direct-wet windows.
- Crop direct-wet/drip/fert behavior remains window-gated and cannot be driven
  by climate logic.
- Fog and mister assist expose exact block reasons.
- `climate_moisture_assist_state=served` requires physical relay service.
- Planner receives dispatcher-owned targets and authority/block context.
- Planner materialization does not disable wet availability during a safe
  dual-axis compliance miss.
- Structured action/effectiveness data can be graphed without reconstructing
  latest state from key-value rows.
- `make lint`, `make test`, `make test-firmware`, `make firmware-invariants`,
  `make firmware-check`, and replay gates pass in the relevant PRs.
- Firmware OTA follows freeze rules and has operator approval when required.

## Issue Map

GitHub issues created from this plan:

- [#5](https://github.com/VerdifyConsultancy/verdify-platform/issues/5):
  Controller - separate climate wet assist from crop direct-wet windows.
- [#6](https://github.com/VerdifyConsultancy/verdify-platform/issues/6):
  Planner - emit compliance-first ClimateIntent from target and authority
  context.
- [#7](https://github.com/VerdifyConsultancy/verdify-platform/issues/7):
  Schema/data - log climate actions and measure actuator effectiveness.
