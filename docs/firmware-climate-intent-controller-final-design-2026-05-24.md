# Firmware Climate-Intent Controller Final Design - 2026-05-24

## Status

Final architecture direction for the next controller simplification. This
supersedes any plan whose main effect is to add more planner-facing relay
knobs. The earlier AI-tunable audit/design work remains useful evidence, but
the implementation target is now a smaller climate-intent surface plus a
deterministic firmware action selector.

This document is the implementation contract. Any OTA must still follow
firmware freeze rules, replay/invariant gates, service restart documentation,
and operator approval.

Next implementation sprint:
[`docs/climate-authority-sprint-plan-2026-05-24.md`](climate-authority-sprint-plan-2026-05-24.md).
It closes the live 2026-05-24 gap where `VENT_COOL_MIST_ASSIST` was selected
while wet relays stayed off because climate evaporative assist was still
coupled to crop direct-wet and fog clock windows.

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

Resource/churn dwell is below both compliance axes. It may hold transitions when
the greenhouse is already inside the active temp/VPD bands, but it must not hold
`IDLE`, `DEHUM_VENT`, or `SEALED_MIST` against a safe action needed for active
temperature or VPD-band recovery.

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
- Historical-response priors used by firmware or replay evaluators.
- Plan-level explanations and post-hoc scorecard learning.

AI must not own:

- Raw relay commands.
- Safety rail bypasses.
- Leak, occupancy, fertigation, or irrigation interlock bypasses.
- Per-relay min-on/min-off mechanics.
- Fert/drip relay control from climate logic.

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

There is no alternate controller in the production loop. The planner emits one
bounded `ClimateIntent` per forecast segment, MCP validates and materializes it
once into the live dispatcher/firmware contract, and the firmware chooses one
executed climate action from that path. Replay and counterfactual tools may
compare alternatives offline, but they must not fork production actuation.

Dispatcher/crop policy owns the compliance targets. Every prompt includes
read-only `temp_low`, `temp_target`, `temp_high`, `vpd_low`, `vpd_target`, and
`vpd_high` values plus current actual-minus-target deltas; the AI planner tunes
the tactical posture around those values but does not write them.

Proposed Tier 1 intent fields. The generated planner prompt and public site
render the same source-of-truth docs from `verdify_schemas.climate_intent`:

| Field | Meaning | Firmware impact | Primary materialized knobs |
|---|---|---|---|
| `forecast_temp_bias_f` | Forecast-backed hot-air pressure | More anticipatory cooling without changing dispatcher-owned bands | `cool_stage2_over_high_f`, `sw_cool_all_fans_at_high_enabled` |
| `forecast_vpd_bias_kpa` | Forecast-backed dry/wet pressure | More wet-action availability without changing dispatcher-owned bands | `sw_direct_wet_stress_override_enabled`, `mister_vpd_weight`, `min_fog_on_s` |
| `solar_precool_gain_f` | Solar ramp pressure before peak heat | Tightens stage-2 cooling and fan readiness | `cool_stage2_over_high_f`, `sw_cool_all_fans_at_high_enabled` |
| `thermal_lead_time_min` | Intended forecast preconditioning lead | Audit/hypothesis context; firmware safety still gates actuation | audit context only |
| `economizer_temp_advantage_f` | Outdoor temp advantage needed for vent cooling | Shapes vent preference and cold-vent guard | `vent_prefer_temp_delta_f`, `cold_vent_guard_delta_f` |
| `economizer_dewpoint_advantage_f` | Outdoor dewpoint advantage for dry-air decisions | Shapes dewpoint preference for vent/dehumidification | `vent_prefer_dp_delta_f` |
| `moisture_engage_vpd_excess_kpa` | VPD excess above dispatcher `vpd_high` before mister assist | Couples mister/direct-wet thresholds to active band | `direct_wet_stress_vpd_margin_kpa`, `mister_engage_kpa`, `mister_all_kpa` |
| `mist_duty_limit_pct` | Max climate-misting duty | Shapes pulse duration, wet aggression, and resource gates | `mister_pulse_on_s`, `mister_pulse_gap_s`, `mister_vpd_weight` |
| `fog_escalate_vpd_excess_kpa` | VPD excess above dispatcher `vpd_high` before fog assist | Shapes fog escalation and all-zone mister threshold | `fog_escalation_kpa`, `mister_all_kpa` |
| `dew_margin_floor_f` | Minimum air temp minus dewpoint for wet actions | Blocks condensation-risk wetting | `direct_wet_stress_min_dew_margin_f`, `fog_stress_min_dew_margin_f` |
| `wet_cutoff_hour` | Latest local hour for climate wetting | Limits evening fog/direct-wet windows | `direct_wet_stress_latest_hour`, `fog_stress_window_latest_hour` |
| `daily_mist_budget_gal` | Daily climate-water budget | Caps firmware mister water budget | `mister_water_budget_gal` |
| `resource_sensitivity` | Conserve water/electricity after compliance | Lengthens off dwell and reduces wet/cooling aggression | `mister_pulse_gap_s`, `min_fog_off_s`, `min_fog_on_s` |
| `relay_churn_penalty` | Hold stable actions instead of changing modes | Shapes hysteresis, dwell, and mist delays | `cool_exit_hysteresis_f`, `temp_hysteresis`, `vpd_hysteresis`, `dwell_gate_ms` |

This replaces the planner needing to reason about dozens of low-level fields
such as individual fog windows, mister pulse gaps, stage delays, and scattered
direct-wet bypasses. Those can exist internally or as operator/reserved fields,
but they should not be the AI's primary control surface.

## Context Inputs For AI

Live context:

- Current temp/VPD/dew margin by zone.
- Dispatcher-owned temp/VPD low, target, high, and signed actual-minus-target
  deltas.
- Current band, tactical intent, and selected action.
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
- Relay min-on/min-off and dwell guards are respected, except resource/churn
  dwell cannot outrank active temp/VPD-band compliance.
- Vent/fan sequencing prevents fan-with-closed-vent except explicit circulation
  or safety heat.
- Water budget is enforced unless a named safety/emergency policy allows a
  bounded exception.
- A stale forecast or stale historical model degrades to deterministic defaults,
  not unsafe behavior.

## Observability Contract

The current system is too hard to reason about from the outside. The new
controller must publish:

- `climate_action`: executed controller action after safety, dwell, and
  interlock resolution. It is not produced by a second selector pass.
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

## Rollout Plan

1. Design and registry planning.
   - Land this doc and backlog entry.
   - Draft `ClimateIntent` schema and ownership map.
   - Decide which current tunables become internal/operator-only.

2. Replay evaluator.
   - Build a replay-only evaluator that consumes historical telemetry and
     emits candidate action decisions without commanding relays.
   - Compare current firmware decisions vs proposed decisions.
   - Report temp/VPD projected compliance, relay churn, water, and electric
     cost.

3. Firmware controller path.
   - Add candidate action structs and observability to the live controller.
   - Use one selector path for mode choice, relay resolution, and published
     `climate_action`.
   - Do not run a parallel proposal controller in production.
   - Add unit tests for candidate sorting and invariants.

4. Cross-agent contract handoff.
   - Coordinator owns schema changes.
   - Genai/planner owns `ClimateIntent` emission and forecast/historical
     context use.
   - Ingestor owns dispatcher routing, confirmation, and state logging.
   - Firmware owns action selection and relay application.

5. Deploy and audit.
   - Firmware PR must include replay diff, invariant output, unit-test delta,
     coordinator reproduction, planner concurrence, rollback plan, and service
     restart documentation.
   - After OTA, validate no critical/high alerts, confirm published
     `climate_action`, and compare live outcomes against replay expectations.

## Acceptance Gates

The implementation is not complete until all of these are true:

- A final `ClimateIntent` schema exists with bounded fields and ownership.
- Firmware candidate action selection is the live controller path.
- Replay evaluator covers at least the hot/dry May 2026 windows and the
  current replay corpus.
- Observability exposes action, priority axis, block reasons, and timers.
- Tests prove candidate selection follows the strict priority order.
- Tests prove climate logic cannot drive fert/drip relays.
- `make test-firmware`, `make firmware-invariants`, and firmware replay pass.
- No critical/high alerts block rollout.
- A behavior-changing OTA is separately approved under freeze rules.

## Near-Term Backlog Breakdown

Recommended implementation tasks:

1. `F-CI-1`: Add replay evaluator and historical action report.
2. `F-CI-2`: Define `ClimateIntent` schema and registry ownership.
3. `F-CI-3`: Add firmware candidate action projection structs and tests.
4. `F-CI-4`: Publish live controller observability fields.
5. `F-CI-5`: Wire planner/dispatcher to emit bounded intent.
6. `F-CI-6`: Deploy, validate live health, and publish scorecard.
7. `F-CI-7`: Implement the Climate Authority sprint: split crop direct-wet
   windows from climate wet-assist authority, update planner intent guidance,
   and add structured action/effectiveness data. Canonical plan:
   [`docs/climate-authority-sprint-plan-2026-05-24.md`](climate-authority-sprint-plan-2026-05-24.md).
