# Planner Contract

**Status:** shared Verdify ↔ planner contract, 2026-05-24.

This document defines the mutual contract between Verdify and `planner_graph`.

It is the source of truth for:

- what Verdify sends
- what `planner_graph` returns
- what each side owns
- what shapes must remain stable during cutover

## 1. Contract Summary

The contract is:

- trigger-led
- asynchronous
- correlated by exact `trigger_id`
- driven by Verdify-pushed, fully pre-shaped action-ready context
- returns one primary structured proposed action
- executed locally by Verdify
- delivered over a private authenticated HTTP service boundary

`planner_graph` does not execute greenhouse writes.

## 2. Ownership

Verdify owns:

- trigger creation and scheduling
- context gathering and normalization
- local action validation
- local dispatcher/MCP execution
- greenhouse write authority
- outcome observation

`planner_graph` owns:

- graph execution
- diagnosis
- draft proposal generation
- deterministic validation
- guardrail preview
- final structured proposed action
- run status and audit metadata

## 3. Correlation And Idempotency

The primary correlation key is:

- `trigger_id: UUID`

Rules:

- Verdify must generate or preserve the canonical `trigger_id`
- the planner must treat repeated submissions of the same `trigger_id` as the same logical run
- `thread_id` inside the planner is always equal to `trigger_id`
- all inspection APIs are keyed by `trigger_id`

## 4. Interaction Model

Recommended interaction:

1. Verdify assembles a planning request.
2. Verdify `POST`s that request to the planner.
3. Planner returns `202 Accepted` with run metadata.
4. Verdify polls the planner for the resulting proposal.
5. Verdify validates and executes the returned proposal locally if desired.

Required behavior:

- request handling must be non-blocking
- planner execution is owned by the planner runtime, not the request handler
- the planner endpoint should be exposed privately; Verdify should invoke it with service-to-service authentication

## 5. Request Contract

The planner request is fully pre-shaped and action-ready.

It must contain:

- trigger envelope
- planner metadata
- context pack
- contract version metadata

### 5.1 Trigger Envelope

Required fields:

- `trigger_id: string (uuid)`
- `greenhouse_id: string`
- `event_type: string`
- `event_label: string | null`
- `expected_action: "set_plan" | "set_tunable" | "acknowledge_trigger" | "any"`
- `triggered_at: string (ISO-8601 with timezone)`

Optional fields:

- `due_by: string (ISO-8601 with timezone)`
- `planner_instance: string`
- `source: string`

### 5.2 Planner Metadata

Required fields:

- `run_mode: "production"`
- `contract_version: string`
- `context_version: string`

Optional fields:

- `request_id: string`
- `trace_id: string`
- `compare_against: string`

### 5.3 Context Pack

Verdify sends normalized planning inputs, not raw database tables.

Required top-level context sections:

- `climate_snapshot`
- `scorecard_summary`
- `forecast_summary`
- `active_plan_summary`
- `alerts_summary`
- `clamp_summary`
- `guardrail_audit_summary`

Optional sections:

- `retrieval_refs`
- `recent_delivery_summary`
- `operator_notes`
- `site_refs`

Rules:

- context must be bounded
- large raw transcripts or unbounded telemetry blobs must not be sent
- context should prefer summaries, stable IDs, and references over raw history dumps
- for `set_plan` triggers, `active_plan_summary` may include the complete
  Verdify Tier 1 plan parameter map as context, but the planner returns bounded
  `transitions[].climate_intent` rather than raw low-level params
- descriptive extra keys in `active_plan_summary`, such as `future_waypoints`,
  are allowed as context, but the planner filters them out of the bounded
  intent payload

Current Tier 1 plan parameter names for contract version `2026-05-24`:

```text
cold_vent_guard_delta_f
cool_exit_hysteresis_f
cool_stage2_over_high_f
direct_wet_stress_latest_hour
direct_wet_stress_min_dew_margin_f
direct_wet_stress_vpd_margin_kpa
dwell_gate_ms
enthalpy_close
enthalpy_open
fog_escalation_kpa
fog_stress_min_dew_margin_f
fog_stress_window_latest_hour
heat_hysteresis
min_fog_off_s
min_fog_on_s
mist_backoff_s
mist_max_closed_vent_s
mist_thermal_relief_s
mister_all_delay_s
mister_all_kpa
mister_engage_delay_s
mister_engage_kpa
mister_pulse_gap_s
mister_pulse_on_s
mister_vpd_weight
mister_water_budget_gal
outdoor_staleness_max_s
sw_cool_all_fans_at_high_enabled
sw_direct_wet_stress_override_enabled
sw_dwell_gate_enabled
sw_fog_closes_vent
sw_fog_stress_window_extend_enabled
sw_mister_closes_vent
sw_summer_vent_enabled
temp_hysteresis
vent_prefer_dp_delta_f
vent_prefer_temp_delta_f
vpd_hysteresis
vpd_watch_dwell_s
```

### 5.4 Contract Philosophy

Verdify should send data that is already shaped for planning.

The planner should not be expected to:

- infer Verdify's schema layout
- normalize arbitrary raw operational payloads
- reconstruct missing context sections from partial inputs

## 6. Response Contract

The planner response is not a write result.
It is a structured proposal.

Required response sections:

- run metadata
- diagnosis
- primary proposed action
- validation summary
- guardrail preview
- planner metadata

### 6.1 Run Metadata

Required fields:

- `trigger_id: string (uuid)`
- `thread_id: string (uuid)`
- `status: "queued" | "running" | "completed" | "failed"`
- `terminal_status: string | null`
- `updated_at: string (ISO-8601 with timezone)`

### 6.2 Diagnosis

Required fields:

- `situation: string`
- `likely_cause: string`
- `risks: string[]`
- `planning_intent: string`

### 6.3 Primary Proposed Action

The planner returns exactly one primary proposed action.

Allowed action types:

- `set_plan`
- `set_tunable`
- `acknowledge_trigger`
- `fail`

Required fields:

- `action_type`
- `payload`
- `rationale`
- `confidence`

Optional fields:

- `expected_effect`
- `alternatives`

## 7. Payload Mirroring Rule

The planner's returned action payload must mirror Verdify's existing downstream execution contracts as closely as possible.

This is a hard rule.

The planner should not invent a parallel write schema if Verdify already has a stable validated one.

### 7.1 `set_tunable` Proposal Payload

The payload should closely mirror Verdify's existing write contract:

- `parameter: string`
- `value: number`
- `reason: string`
- `trigger_id: string`
- `planner_instance: string | null`

### 7.2 `acknowledge_trigger` Proposal Payload

The payload should closely mirror Verdify's existing write contract:

- `trigger_id: string`
- `reason: string`
- `planner_instance: string | null`

### 7.3 `set_plan` Proposal Payload

The payload should closely mirror Verdify's existing write contract:

- `plan_id: string`
- `hypothesis: string`
- `transitions: array`
- `experiment: string | null`
- `expected_outcome: string | null`
- `trigger_id: string`
- `planner_instance: string | null`

`plan_id` must use Verdify's current public planner format:

```text
iris-YYYYMMDD-HHMM
```

Each transition uses the bounded ClimateIntent surface. MCP validates and
materializes this once into the live Verdify Tier 1/firmware contract.

```json
{
  "ts": "2026-05-18T10:00:00-06:00",
  "climate_intent": {
    "temp_target_f": 72.0,
    "temp_band_f": 6.0,
    "vpd_target_kpa": 1.0,
    "vpd_band_kpa": 0.5,
    "forecast_temp_bias_f": 0.0,
    "forecast_vpd_bias_kpa": 0.0,
    "solar_precool_gain_f": 0.0,
    "thermal_lead_time_min": 30.0,
    "economizer_temp_advantage_f": 5.0,
    "economizer_dewpoint_advantage_f": 5.0,
    "moisture_engage_vpd_excess_kpa": 0.05,
    "mist_duty_limit_pct": 25.0,
    "fog_escalate_vpd_excess_kpa": 0.4,
    "dew_margin_floor_f": 10.0,
    "wet_cutoff_hour": 19.0,
    "daily_mist_budget_gal": 300.0,
    "resource_sensitivity": 0.5,
    "relay_churn_penalty": 0.5
  },
  "reason": "Forecast dry peak stress window"
}
```

The planner may wrap this payload in planner metadata, but the payload itself
must remain ready for Verdify's single MCP validation and execution path.

## 8. Validation Summary

The planner must return deterministic validation outputs separately from the proposed action.

Required fields:

- `validation_status`
- `validation_errors`
- `registry_violations`
- `band_ownership_violations`
- `tier1_coverage_status`

This lets Verdify inspect planner quality independently from whether it chooses to execute the proposal.

## 9. Guardrail Preview

The planner must return a guardrail preview separately from the proposed action.

Required fields:

- `would_clamp: boolean | null`
- `summary: string`

Optional fields:

- `expected_clamps`
- `hold_risk`
- `transition_audit_refs`

This is advisory. Verdify still owns final local validation and execution.

## 10. Single Path Rules

In production mode:

- the planner returns exactly one valid bounded action payload
- the planner must not perform production writes
- Verdify MCP is the only write boundary
- there is no production shadow/proposal controller path
- offline replay and counterfactual comparison are diagnostic only

The planner service itself must not call:

- `set_plan`
- `set_tunable`
- `acknowledge_trigger`
- `plan_evaluate`

## 11. Failure Contract

If the planner cannot produce a valid proposal, it should return:

- `status = "failed"` for run-level failure
- or `action_type = "fail"` for a completed run that intentionally recommends no executable action

Failure responses should still include:

- `trigger_id`
- `thread_id`
- `updated_at`
- `diagnosis` when available
- `validation_summary` when available
- `last_error` or failure rationale

## 12. Versioning

The contract must be versioned.

Required version markers:

- request `contract_version`
- request `context_version`
- response `contract_version`
- response `planner_graph_version`

Breaking changes require an explicit version bump.

## 13. Non-Negotiable Rules

- Verdify pushes fully pre-shaped action-ready context.
- The planner returns one primary structured proposed action.
- The action payload mirrors Verdify's downstream execution contracts closely.
- Verdify executes locally.
- `trigger_id` is the exact correlation key.
- The planner is not the greenhouse write authority.
