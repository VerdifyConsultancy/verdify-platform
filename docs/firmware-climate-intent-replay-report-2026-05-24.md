# ClimateIntent Replay Report - 2026-05-24

## Status

First `F-CI-1` replay-only artifact for the climate-intent controller design.
This does not command relays, change firmware behavior, deploy services, or
authorize an OTA.

Command:

```bash
make climate-intent-replay-report
```

Corpus:

- `firmware/test/data/replay_overrides.csv.gz`
- `193,525` historical rows
- Inputs include indoor temp/VPD/RH/dewpoint, outdoor temp/dewpoint, solar,
  active setpoint snapshots, occupancy, current firmware state/reason, and
  relay truth.

## Result

```text
rows=193525
temp_out_of_band_rows=64694
vpd_out_of_band_rows=68991
hot_dry_rows=19615
wet_relay_rows_when_replay_blocked=2423
replay_action_transitions=15669
firmware_state_transitions=18879
resource_estimate={"electric_kwh": 207.99, "gas_therm": 84.532, "water_gal": 1307.96}
replay_action_counts={"DEHUM_VENT": 6694, "HEAT": 42266, "IDLE": 97669, "SAFETY_COOL": 695, "SEALED_FOG": 3403, "SEALED_HUMIDIFY": 20942, "VENT_COOL": 5372, "VENT_COOL_FOG_ASSIST": 12857, "VENT_COOL_MIST_ASSIST": 3627}
priority_axis_counts={"resource": 91540, "safety": 695, "temp": 64416, "vpd": 36874}
firmware_state_counts={"COOL_S1_HUMID_S1": 1572, "COOL_S1_HUM_IDLE": 6185, "COOL_S2_HUMID_S1": 2842, "COOL_S3_HUMID_S1": 15456, "HEAT_S1_DEHUM_V1": 1862, "HEAT_S1_HUM_IDLE": 52293, "HEAT_S2_HUM_IDLE": 22162, "IDLE": 12585, "SEALED_MIST_S1": 2419, "SEALED_MIST_S2": 4488, "TEMP_IDLE_HUM_IDLE": 61145, "VENTILATE": 3363}
```

## Qualitative Read

The first replay selector is intentionally conservative and deterministic:

- Safety rails are resolved before normal band optimization.
- Temperature-band error wins over VPD-band error.
- VPD recovery only wins when temperature is already safe or effectively tied.
- Resource and relay-churn costs are tie-breakers, not weighted overrides.

The initial result supports the architecture direction:

- The corpus contains substantial compliance pressure: `64,694` temp-out rows,
  `68,991` VPD-out rows, and `19,615` simultaneous hot/dry rows.
- The replay action stream has fewer transitions than the historical firmware
  state stream on the same rows: `15,669` vs `18,879`.
- The selected actions are semantically explainable: `VENT_COOL*` for
  temperature pressure, `SEALED_*` for VPD recovery when temperature is not the
  active priority, `HEAT` for low temperature, `DEHUM_VENT` for low VPD, and
  `SAFETY_COOL` for hard high-temperature rails.
- `2,423` rows had wet relays on while the replay model would report moisture
  as blocked. This is a review target, not proof of a firmware bug: early
  replay history has sparse block-reason context, and the first estimator uses
  coarse wet-window/dew-margin rules.

## Limitations

- This is a replay-only model; it does not command relays.
- Resource estimates are coarse per-row scale estimates until water/electric/gas
  metering is calibrated by action and interval.
- `mode_reason` is unavailable for most older rows, so the first comparison uses
  `greenhouse_state` as the current-state baseline.
- Forecast context is represented by historical outdoor temp/dewpoint/solar
  columns in the replay corpus; the live forecast path still needs dispatcher
  and planner integration.
- Candidate projections are simple calibrated heuristics, not learned response
  slopes yet.

## Next Work

1. Keep the firmware and replay candidate projection contracts in sync.
2. Publish `climate_action`, `priority_axis`, block reasons, and timers from
   the live controller path.
3. Feed live forecast segments into `ClimateIntent` generation.
4. Compare replay decisions against live outcomes as ongoing audit evidence.
