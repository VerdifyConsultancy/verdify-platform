# Firmware Climate-Intent Controller Final Design - 2026-05-24

## Status

Final architecture direction for the next controller simplification. This
supersedes any plan whose main effect is to add more planner-facing relay
knobs. The earlier AI-tunable audit/design work remains useful evidence, but
the implementation target is now a smaller climate-intent surface plus a
deterministic firmware action selector.

This document is design-only. It does not authorize an OTA. Any implementation
must follow firmware freeze rules, replay/invariant gates, service restart
documentation, and explicit operator approval before behavior changes.

2026-05-25 operator decision: do not create a second shadow controller path.
The implementation target is one production controller path. Replay,
invariants, unit tests, compile output, and live post-deploy health checks are
the rollout proof; diagnostic-only observability is allowed, but a parallel
shadow decision engine is not.

## Objective

Optimize greenhouse operation in this strict priority order:

1. Safety rails.
2. Maximum temperature-band compliance.
3. Maximum VPD-band compliance.
4. Minimum resource use.

The controller should be understandable from first principles. If temperature
and VPD are both out of band, the system should explain which priority is
active, which safe actions were eligible, and why an actuator was served,
paused, or blocked.

## Core Decision

Use a lexicographic controller:

1. Reject candidates that violate safety rails or hard interlocks.
2. Among safe candidates, choose the best projected temperature-band outcome.
3. If temperature outcomes are effectively tied, choose the best projected
   VPD-band outcome.
4. If temperature and VPD outcomes are effectively tied, choose the lowest
   resource and relay-churn cost.

This is intentionally not a weighted sum. A cheap action that sacrifices
temperature compliance cannot beat a safe action that protects temperature.
Likewise, a VPD improvement cannot close the vent while temperature is actively
above band unless the projection says temperature compliance remains at least
equivalent.

## Control Boundary

Firmware owns:

- Safety rails and sensor-fault behavior.
- Relay truth, relay min-on/min-off, interlocks, and sequencing.
- Candidate action generation and lexicographic selection every 5 seconds.
- Water/fog/vent/heat/fan invariants.
- Observability for chosen action, rejected candidates, timers, and blocks.

AI/planner owns:

- Bounded climate intent for upcoming forecast periods.
- Forecast-aware preconditioning and resource posture.
- Historical-response priors used by dispatcher-side planning and post-hoc
  scorecard learning.
- Plan-level explanations and post-hoc scorecard learning.

AI must not own:

- Raw relay commands.
- Safety rail bypasses.
- Leak, occupancy, fertigation, or irrigation interlock bypasses.
- Per-relay min-on/min-off mechanics.
- Fert/drip relay control from climate logic.

## Evidence Behind The Change

The current 2026-05-24 live example exposed the ambiguity:

- Temp was above band: about `75.7-76.2F` vs `temp_high=72.9F`.
- VPD hovered near the upper edge: about `1.07-1.10 kPa` vs
  `vpd_high=1.07 kPa`.
- Firmware chose `VENTILATE` with `mode_reason=temp_high`.
- `vent_mist_assist` pulsed center mister, then paused on pulse gap or when VPD
  fell back to the edge.
- Fog stayed off because current VPD was below the fog escalation trigger and
  normal fog time window had ended.

That behavior was mostly consistent with current code, but the telemetry made
it look irrational: generic `VENTILATE`, stale `mister_any`, and
`fog_block_reason=none` did not expose the actual state:
`VENT_COOL with moisture assist recently served or waiting`.

Historical evidence points the same way:

- Hot/dry May 11-16 windows drove repeated temp and VPD misses.
- Cool/cloudy May 18-20 performed well with much less actuation pressure.
- Last-7-day evidence showed the dominant climate paths were
  `vent_mist_assist`, `summer_vent`, heat, vent/fans, fog, and center mister.
- Drip and fert relays are not climate actuators and should remain outside the
  climate controller except as locks.

## Physical Action Set

Firmware should generate these candidate actions every loop:

| Candidate | Purpose | Typical relays |
|---|---|---|
| `SENSOR_FAULT` | Sensor/plausibility failure | all climate relays off |
| `SAFETY_HEAT` | Hard low-temp rail | heat1, heat2, optional circulation |
| `SAFETY_COOL` | Hard high-temp rail | vent, fans, optional fog if safe |
| `HEAT` | Temperature below band | heat1/heat2 by stage |
| `IDLE` | Band satisfied | all climate relays off except holds |
| `VENT_COOL` | Temperature above band | vent, fan1/fan2 |
| `VENT_COOL_MIST_ASSIST` | Temp priority plus dry recovery | vent, fans, pulsed misters |
| `VENT_COOL_FOG_ASSIST` | Temp priority plus severe dry recovery | vent, fans, fog if safe |
| `SEALED_HUMIDIFY` | VPD recovery when temp is safe | vent closed, pulsed misters |
| `SEALED_FOG` | Severe VPD recovery when temp is safe | vent closed, fog |
| `DEHUM_VENT` | VPD too low / humidity too high | vent and fan staging |

The action name should be the primary published controller state. Pulse stage,
zone, timers, and relay truth are separate diagnostics, not hidden sub-modes.

## Candidate Evaluation

Each candidate gets a projection record:

```text
CandidateProjection:
  action
  safety_ok
  blocked_reasons[]
  projected_temp_error_f
  projected_vpd_error_kpa
  resource_cost
  relay_churn_cost
  confidence
```

Selection rule:

```text
eligible = candidates where safety_ok
sort eligible by:
  projected_temp_error_f ascending
  projected_vpd_error_kpa ascending
  resource_cost ascending
  relay_churn_cost ascending
  prior_action_hold_preference descending
choose first
```

Projection does not need to be a complex ML model on day one. The first version
can use calibrated slopes and conservative defaults:

- Vent cooling rate by outdoor temp, wind, solar, and current vent/fan state.
- Mister VPD response by zone, dew margin, current RH, and recent pulse history.
- Fog VPD/temp response and overshoot risk.
- Heat recovery rate by stage and outdoor temp.
- Dehum/vent effect by outdoor dewpoint advantage.

Historical context should continuously refine these estimates offline and feed
back into bounded intent values. Firmware must still operate safely if the
historical model is missing or stale.

## ClimateIntent Surface

The AI tuning surface should be compact and semantic. It should tune
forecast-aware intent and tradeoff thresholds, not relay mechanics.

Proposed Tier 1 intent fields:

| Field | Meaning | First range |
|---|---|---|
| `temp_target_f` | Center of desired temperature band | crop-bounded |
| `temp_band_f` | Width of desired temperature band | `3-12F` |
| `vpd_target_kpa` | Center of desired VPD band | crop-bounded |
| `vpd_band_kpa` | Width of desired VPD band | `0.35-1.2` |
| `forecast_temp_bias_f` | Anticipatory temp offset from forecast pressure | `-4..4F` |
| `forecast_vpd_bias_kpa` | Anticipatory VPD offset from forecast pressure | `-0.4..0.4` |
| `solar_precool_gain_f` | Cooling lead under strong solar ramp | `0..4F` |
| `thermal_lead_time_min` | How early preconditioning may begin | `0..90` |
| `economizer_temp_advantage_f` | Outdoor temp advantage needed for vent cooling | `1..15F` |
| `economizer_dewpoint_advantage_f` | Outdoor dewpoint advantage for dry-air decisions | `1..15F` |
| `moisture_engage_vpd_excess_kpa` | VPD excess before mister assist | `0..0.5` |
| `mist_duty_limit_pct` | Max mister duty during the period | `0..100` |
| `fog_escalate_vpd_excess_kpa` | VPD excess before fog candidate is eligible | `0.1..0.8` |
| `dew_margin_floor_f` | Minimum air temp minus dewpoint for wet actions | `3..15F` |
| `wet_cutoff_hour` | Latest local hour for climate wetting | `17..24` |
| `daily_mist_budget_gal` | Daily climate-water budget | site-bounded |
| `resource_sensitivity` | Preference for conserving water/electricity | `0..1` |
| `relay_churn_penalty` | Preference for holding stable action | `0..1` |

This replaces the planner needing to reason about dozens of low-level fields
such as individual fog windows, mister pulse gaps, stage delays, and scattered
direct-wet bypasses. Those can exist internally or as operator/reserved fields,
but they should not be the AI's primary control surface.

## Context Inputs For AI

Live context:

- Current temp/VPD/dew margin by zone.
- Current band, intent, and selected action.
- Relay truth and pulse/gap timers.
- Water and electricity used today.
- Open alerts and sensor health.
- Occupancy, leak, irrigation, and fertigation locks.

Forecast context:

- Solar ramp and cloud cover.
- Outdoor temp/RH/dewpoint.
- Wind and gusts.
- Precipitation risk.
- Evening humidity rebound and overnight low.

Historical context:

- Vent cooling slope under similar solar/wind/outdoor conditions.
- Mister response per zone and per dew margin.
- Fog response, overshoot, and recovery time.
- Resource cost per compliance-hour gained.
- Common failure modes: stuck dry zone, stale sensor, command churn, humidity
  rebound, and over-wetting.

Greenhouse context:

- Relay inventory: `heat1`, `heat2`, `vent`, `fan1`, `fan2`, `fog`,
  `mister_south`, `mister_west`, `mister_center`, DLI lights.
- Excluded relays: drip/fert relays and fert master valve.
- Zone layout and wettable zones.
- Crop bands and crop-local priorities.
- Water budget and known feedback sensor gaps.

## Preconditioning Strategy

AI should use forecast and historical response to set intent before stress
arrives.

Examples:

- If strong solar and dry outdoor air are forecast, lower the effective cooling
  target before the temp high edge is crossed and permit low-duty mister assist
  earlier.
- If outdoor air is cooler and drier by enough margin, favor `VENT_COOL` early.
- If outdoor air is hot and dry, avoid excessive venting unless temperature
  priority requires it; use bounded moisture assist when VPD pressure persists.
- If evening VPD remains high and dew margin is safe, extend wet eligibility
  within `wet_cutoff_hour` rather than hard-closing all moisture paths.
- If recent fog/mist caused VPD overshoot, increase resource sensitivity and
  fog escalation excess until the scorecard stabilizes.

The planner should emit intent for forecast segments, not one giant daily
posture. A useful cadence is sunrise, solar ramp, peak stress, decline, sunset,
and forecast-deviation triggers.

## Firmware Invariants

Required invariants:

- No non-safety heat while vent/fan air exchange is active.
- Heat2 cannot run without heat1.
- No fog/mister during occupancy inhibit.
- No wet action below dew-margin floor.
- No climate logic drives fert/drip relays.
- No relay runs without plausible sensors except hardwired safety fallback.
- Relay min-on/min-off and dwell guards are respected.
- Vent/fan sequencing prevents fan-with-closed-vent except explicit circulation
  or safety heat.
- Water budget is enforced unless a named safety/emergency policy allows a
  bounded exception.
- A stale forecast or stale historical model degrades to deterministic defaults,
  not unsafe behavior.

## Observability Contract

The current system is too hard to reason about from the outside. The new
controller must publish:

- `climate_action`: selected candidate action.
- `priority_axis`: `safety`, `temp`, `vpd`, or `resource`.
- `temp_error_f` and `vpd_error_kpa`.
- `candidate_summary`: compact top candidate and first rejected reason.
- `moisture_assist_state`: `inactive`, `engage_delay`, `pulse_on`,
  `pulse_gap`, `blocked`, or `served`.
- `moisture_zone`: active zone or `none`.
- `next_mist_eligible_s`.
- `fog_margin_kpa`: VPD margin to fog threshold.
- `fog_block_reason`: `none`, `below_threshold`, `time_window`,
  `dew_margin`, `rh_ceiling`, `temp_low`, `occupancy`, `relay_min_off`,
  or `resource_budget`.
- `resource_cost_estimate`: water and electric cost estimate for selected
  action.
- Actual relay truth for each climate relay.

This would have made the 2026-05-24 case obvious:

```text
climate_action=VENT_COOL_MIST_ASSIST
priority_axis=temp
moisture_assist_state=pulse_gap
next_mist_eligible_s=...
fog_block_reason=below_threshold,time_window
```

## Rollout Plan

1. Design and registry planning.
   - Land this doc and backlog entry.
   - Draft `ClimateIntent` schema and ownership map.
   - Decide which current tunables become internal/operator-only.

2. Firmware production-path implementation.
   - Implement the single controller path that owns relay decisions.
   - Add strict-priority tests for safety, temperature compliance, VPD
     compliance, and resource minimization.
   - Add diagnostic fields for chosen action, priority axis, block reasons,
     timers, target error, and band error.

3. Cross-agent contract handoff.
   - Coordinator owns schema changes.
   - Genai/planner owns `ClimateIntent` emission and forecast/historical
     context use.
   - Ingestor owns dispatcher routing, confirmation, and state logging.
   - Firmware owns action selection and relay application.

4. Behavior-changing release.
   - Firmware PR must include replay diff, invariant output, unit-test delta,
     coordinator reproduction, planner concurrence, rollback plan, and service
     restart documentation.
   - Operator approval is required for freeze-rule overrides and OTA timing.
   - Post-OTA health validation must confirm sensor registry, dispatcher,
     planner, database, site/API, and greenhouse relay state agree.

## Acceptance Gates

The implementation is not complete until all of these are true:

- A final `ClimateIntent` schema exists with bounded fields and ownership.
- Firmware candidate action selection is the single production path.
- Replay coverage includes at least the hot/dry May 2026 windows and the
  current replay corpus.
- Observability exposes action, priority axis, block reasons, and timers.
- Tests prove candidate selection follows the strict priority order.
- Tests prove climate logic cannot drive fert/drip relays.
- `make test-firmware`, `make firmware-invariants`, and firmware replay pass.
- No critical/high alerts block rollout.
- A behavior-changing OTA is separately approved under freeze rules.

## Near-Term Backlog Breakdown

Recommended implementation tasks:

1. `F-CI-1`: Finalize the single production controller path and action model.
2. `F-CI-2`: Define `ClimateIntent` schema and registry ownership.
3. `F-CI-3`: Add firmware candidate action projection structs and strict-priority tests.
4. `F-CI-4`: Add observability fields for selected action, priority axis, block reasons, target error, and band error.
5. `F-CI-5`: Wire planner/dispatcher to emit bounded intent.
6. `F-CI-6`: Run replay, invariant, compile, service-health, and post-OTA audit gates.
7. `F-CI-7`: Deploy only with explicit operator approval and rollback path.
