#!/bin/bash
# export-replay-overrides.sh — Export climate + occupancy + outdoor sensors +
# equipment_state + mode_reason as-of joins for the replay harness.
#
# Originally built for Sprint 16 OBS-1e. Phase-0 stabilization plan extended
# the schema to support the replay_diff harness and 15-invariant suite
# (see docs/plans/firmware-stabilization-plan.md — the plan file at
# .claude-agents/iris-dev/plans/yo-iris-dev-you-help-humming-stonebraker.md).
#
# Usage: ./scripts/export-replay-overrides.sh [days]  (default: all history)
set -euo pipefail

DAYS=${1:-0}  # 0 = all history
# OUTDIR is env-overridable so Makefile targets can pin output directly into
# the firmware worktree's test/data/. Default preserves the original location
# for standalone invocations from the main repo.
OUTDIR=${OUTDIR:-/srv/verdify/firmware/test/data}
mkdir -p "$OUTDIR"
OUTFILE="$OUTDIR/replay_overrides.csv"

if [ "$DAYS" -eq 0 ]; then
    WHERE="c.temp_avg IS NOT NULL AND c.vpd_avg IS NOT NULL AND c.rh_avg IS NOT NULL"
    echo "Exporting all history from climate + occupancy + equipment_state + mode_reason..."
else
    WHERE="c.ts >= now() - interval '${DAYS} days' AND c.temp_avg IS NOT NULL AND c.vpd_avg IS NOT NULL AND c.rh_avg IS NOT NULL"
    echo "Exporting last ${DAYS} days..."
fi

# The replay CSV is tab-separated; header row required. NULLs export as
# empty strings (NULL '' in the COPY options).
#
# Column ordering is append-only: the replay harness validates required
# columns by header name, so adding columns is backward-compatible. Old
# columns kept for bench-test compatibility with existing replay_overrides.cpp.
#
# New Phase-0 columns:
#   outdoor_temp_f        — Tempest, for sprint-15 gate + new invariants
#   outdoor_dewpoint_f    — computed from Tempest temp+rh
#   outdoor_data_age_s    — seconds since the last persisted Tempest value change
#                           (conservative persisted observation; drives gate eligibility)
#   solar_irradiance_w_m2 — Tempest, for sunrise-ramp invariants
#   indoor_dew_point      — from climate.dew_point (Magnus inside firmware)
#   eq_<relay>            — forward-filled equipment_state at row ts (0/1)
#   mode_reason           — sprint-15.1 diagnostic enum; drives invariant #10
#   greenhouse_state      — for invariant #6 transition counting
# #24: DB access via the shared psql-verdify abstraction (docker-exec default
# preserves prior VM argv).
. "$(dirname "${BASH_SOURCE[0]}")/lib/psql-verdify.sh"
# Export each ordered source once, then perform the as-of merge locally.  The
# former query ran one correlated lookup per source field and climate row; on
# Timescale hypertables that expanded into millions of chunk probes and held a
# production DB core for tens of minutes.  These COPY queries are sequential,
# read-only scans.  The merge is deterministic and keeps production load bounded.
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/verdify-replay-export.XXXXXX")
trap 'rm -rf "$TMP_ROOT"' EXIT

echo "  reading climate + conservative outdoor provenance..."
verdify_psql -c "
COPY (
    WITH ranked_climate AS (
        -- climate contains historical duplicate timestamps from earlier
        -- ingestion paths. Select one deterministic whole row for both replay
        -- output and provenance; never merge fields from different rows or
        -- emit more than one replay row for a timestamp.
        SELECT
            c0.*,
            row_number() OVER (
                PARTITION BY c0.greenhouse_id, c0.ts
                ORDER BY
                    (
                        c0.temp_avg IS NOT NULL
                        AND c0.vpd_avg IS NOT NULL
                        AND c0.rh_avg IS NOT NULL
                    ) DESC,
                    (
                        c0.outdoor_temp_f IS NOT NULL
                        AND c0.outdoor_rh_pct IS NOT NULL
                    ) DESC,
                    c0.temp_avg DESC NULLS LAST,
                    c0.vpd_avg DESC NULLS LAST,
                    c0.rh_avg DESC NULLS LAST,
                    c0.outdoor_temp_f DESC NULLS LAST,
                    c0.outdoor_rh_pct DESC NULLS LAST,
                    c0.solar_irradiance_w_m2 DESC NULLS LAST,
                    c0.dew_point DESC NULLS LAST,
                    c0.enthalpy_delta DESC NULLS LAST
            ) AS duplicate_rank
        FROM climate c0
        WHERE c0.greenhouse_id = 'vallery'
    ),
    climate_rows AS (
        SELECT *
        FROM ranked_climate
        WHERE duplicate_rank = 1
    ),
    outdoor_samples AS (
        SELECT greenhouse_id, ts, outdoor_temp_f, outdoor_rh_pct
        FROM climate_rows
    ),
    ordered_outdoor AS (
        SELECT
            c0.greenhouse_id,
            c0.ts,
            c0.outdoor_temp_f,
            c0.outdoor_rh_pct,
            lag(c0.outdoor_temp_f) OVER (
                PARTITION BY c0.greenhouse_id ORDER BY c0.ts
            ) AS previous_outdoor_temp_f,
            lag(c0.outdoor_rh_pct) OVER (
                PARTITION BY c0.greenhouse_id ORDER BY c0.ts
            ) AS previous_outdoor_rh_pct
        FROM outdoor_samples c0
    ),
    marked_outdoor AS (
        SELECT
            o.*,
            CASE
                WHEN o.outdoor_temp_f IS NOT NULL
                 AND o.outdoor_rh_pct IS NOT NULL
                 AND (
                     o.outdoor_temp_f IS DISTINCT FROM o.previous_outdoor_temp_f
                     OR o.outdoor_rh_pct IS DISTINCT FROM o.previous_outdoor_rh_pct
                 )
                THEN o.ts
            END AS outdoor_observed_at
        FROM ordered_outdoor o
    ),
    outdoor_provenance AS (
        SELECT
            m.greenhouse_id,
            m.ts,
            max(m.outdoor_observed_at) OVER (
                PARTITION BY m.greenhouse_id
                ORDER BY m.ts
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS outdoor_observation_ts
        FROM marked_outdoor m
    )
    SELECT
        c.greenhouse_id,
        c.ts,
        c.temp_avg,
        c.vpd_avg,
        c.rh_avg,
        COALESCE(c.outdoor_rh_pct, 30) AS outdoor_rh_pct,
        COALESCE(c.enthalpy_delta, -5) AS enthalpy_delta,
        c.outdoor_temp_f,
        CASE
            WHEN c.outdoor_temp_f IS NULL
              OR c.outdoor_rh_pct IS NULL
              OR c.outdoor_rh_pct <= 0 THEN NULL
            ELSE (
                (
                    243.04 * (
                        ln(c.outdoor_rh_pct / 100.0)
                        + (
                            17.625 * ((c.outdoor_temp_f - 32.0) * 5.0 / 9.0)
                        ) / (
                            243.04 + ((c.outdoor_temp_f - 32.0) * 5.0 / 9.0)
                        )
                    )
                ) / (
                    17.625 - (
                        ln(c.outdoor_rh_pct / 100.0)
                        + (
                            17.625 * ((c.outdoor_temp_f - 32.0) * 5.0 / 9.0)
                        ) / (
                            243.04 + ((c.outdoor_temp_f - 32.0) * 5.0 / 9.0)
                        )
                    )
                )
            ) * 9.0 / 5.0 + 32.0
        END AS outdoor_dewpoint_f,
        c.dew_point AS indoor_dew_point,
        c.solar_irradiance_w_m2,
        CASE
            WHEN c.outdoor_temp_f IS NULL
              OR c.outdoor_rh_pct IS NULL
              OR p.outdoor_observation_ts IS NULL THEN NULL
            ELSE GREATEST(
                0,
                extract(epoch FROM (c.ts - p.outdoor_observation_ts))::int
            )
        END AS outdoor_data_age_s,
        p.outdoor_observation_ts
    FROM climate_rows c
    LEFT JOIN outdoor_provenance p
      ON p.greenhouse_id = c.greenhouse_id
     AND p.ts = c.ts
    WHERE c.greenhouse_id = 'vallery' AND ${WHERE}
    ORDER BY c.ts
) TO STDOUT WITH (FORMAT csv, DELIMITER E'\t', HEADER, NULL '')
" > "$TMP_ROOT/climate.tsv"

echo "  reading complete setpoint batches..."
verdify_psql -c "
COPY (
    SELECT
        greenhouse_id,
        ts,
        jsonb_object_agg(parameter, to_jsonb(value) ORDER BY parameter)
            AS config_payload
    FROM setpoint_snapshot
    WHERE greenhouse_id = 'vallery'
    GROUP BY greenhouse_id, ts
    HAVING bool_or(parameter = 'temp_high')
    ORDER BY ts
) TO STDOUT WITH (FORMAT csv, DELIMITER E'\t', HEADER, NULL '')
" > "$TMP_ROOT/setpoints.tsv"

echo "  reading changed system states..."
verdify_psql -c "
COPY (
    WITH ordered AS (
        SELECT
            greenhouse_id,
            ts,
            entity,
            value,
            lag(value) OVER (
                PARTITION BY greenhouse_id, entity ORDER BY ts
            ) AS previous_value
        FROM system_state
        WHERE greenhouse_id = 'vallery'
          AND entity IN ('occupancy', 'greenhouse_state', 'mode_reason')
    )
    SELECT greenhouse_id, ts, entity, value
    FROM ordered
    WHERE previous_value IS DISTINCT FROM value
    ORDER BY ts, entity
) TO STDOUT WITH (FORMAT csv, DELIMITER E'\t', HEADER, NULL '')
" > "$TMP_ROOT/system.tsv"

echo "  reading normalized equipment transitions..."
verdify_psql -c "
COPY (
    WITH normalized AS (
        SELECT
            greenhouse_id,
            ts,
            equipment,
            bool_or(state) AS state
        FROM equipment_state
        WHERE greenhouse_id = 'vallery'
          AND equipment IN (
              'fog', 'vent', 'fan1', 'fan2', 'heat1', 'heat2',
              'mister_south', 'mister_west', 'mister_center'
          )
        GROUP BY greenhouse_id, ts, equipment
    ),
    ordered AS (
        SELECT
            n.*,
            lag(state) OVER (
                PARTITION BY greenhouse_id, equipment ORDER BY ts
            ) AS previous_state
        FROM normalized n
    )
    SELECT greenhouse_id, ts, equipment, state
    FROM ordered
    WHERE previous_state IS DISTINCT FROM state
    ORDER BY ts, equipment
) TO STDOUT WITH (FORMAT csv, DELIMITER E'\t', HEADER, NULL '')
" > "$TMP_ROOT/equipment.tsv"

echo "  merging ordered sources locally..."
python3 - "$TMP_ROOT" "$OUTFILE" <<'PY'
import csv
import json
import sys
from pathlib import Path

tmp = Path(sys.argv[1])
outfile = Path(sys.argv[2])

base_fields = [
    "ts",
    "temp_avg",
    "vpd_avg",
    "rh_avg",
    "outdoor_rh_pct",
    "enthalpy_delta",
    "outdoor_temp_f",
    "outdoor_dewpoint_f",
    "indoor_dew_point",
    "solar_irradiance_w_m2",
    "outdoor_data_age_s",
]

setpoint_fields = [
    ("sp_temp_high", "temp_high"),
    ("sp_temp_low", "temp_low"),
    ("sp_vpd_high", "vpd_high"),
    ("sp_vpd_low", "vpd_low"),
    ("sp_bias_cool", "bias_cool"),
    ("sp_vpd_hysteresis", "vpd_hysteresis"),
    ("sp_watch_dwell_s", "vpd_watch_dwell_s"),
    ("sp_bias_heat", "bias_heat"),
    ("sp_temp_hysteresis", "temp_hysteresis"),
    ("sp_safety_max", "safety_max"),
    ("sp_safety_min", "safety_min"),
    ("sp_vpd_max_safe", "safety_vpd_max"),
    ("sp_vpd_min_safe", "safety_vpd_min"),
    ("sp_fog_escalation_kpa", "fog_escalation_kpa"),
    ("sp_sw_fsm_controller_enabled", "sw_fsm_controller_enabled"),
    ("sp_mist_backoff_s", "mist_backoff_s"),
    ("sp_mist_s2_delay_s", "mister_all_delay_s"),
    ("sp_heat_hysteresis", "heat_hysteresis"),
    ("sp_sealed_max_s", "mist_max_closed_vent_s"),
    ("sp_relief_duration_s", "mist_thermal_relief_s"),
    ("sp_max_relief_cycles", "max_relief_cycles"),
    ("sp_fog_rh_ceiling", "fog_rh_ceiling_pct"),
    ("sp_fog_min_temp", "fog_min_temp_f"),
    ("sp_fog_window_start", "fog_time_window_start"),
    ("sp_fog_window_end", "fog_time_window_end"),
    ("sp_dehum_aggressive_kpa", "dehum_aggressive_kpa"),
    ("sp_occupancy_inhibit", "sw_occupancy_inhibit"),
    ("sp_vent_latch_timeout_ms", "vent_latch_timeout_ms"),
    ("sp_safety_max_seal_margin_f", "safety_max_seal_margin_f"),
    ("sp_econ_heat_margin_f", "econ_heat_margin_f"),
    ("sp_sw_summer_vent_enabled", "sw_summer_vent_enabled"),
    ("sp_vent_prefer_temp_delta_f", "vent_prefer_temp_delta_f"),
    ("sp_vent_prefer_dp_delta_f", "vent_prefer_dp_delta_f"),
    ("sp_outdoor_staleness_max_s", "outdoor_staleness_max_s"),
    ("sp_summer_vent_min_runtime_s", "summer_vent_min_runtime_s"),
    ("sp_sw_dwell_gate_enabled", "sw_dwell_gate_enabled"),
    ("sp_dwell_gate_ms", "dwell_gate_ms"),
    ("sp_cool_stage2_over_high_f", "cool_stage2_over_high_f"),
    ("sp_cool_exit_hysteresis_f", "cool_exit_hysteresis_f"),
    ("sp_cold_vent_guard_delta_f", "cold_vent_guard_delta_f"),
    ("sp_cool_all_fans_at_high_enabled", "sw_cool_all_fans_at_high_enabled"),
    ("sp_direct_wet_stress_override_enabled", "sw_direct_wet_stress_override_enabled"),
    ("sp_direct_wet_stress_vpd_margin_kpa", "direct_wet_stress_vpd_margin_kpa"),
    ("sp_direct_wet_stress_min_dew_margin_f", "direct_wet_stress_min_dew_margin_f"),
    ("sp_direct_wet_stress_latest_hour", "direct_wet_stress_latest_hour"),
    ("sp_fog_stress_window_extend_enabled", "sw_fog_stress_window_extend_enabled"),
    ("sp_fog_stress_window_latest_hour", "fog_stress_window_latest_hour"),
    ("sp_fog_stress_min_dew_margin_f", "fog_stress_min_dew_margin_f"),
    ("sp_sw_dawn_rehydrate_enabled", "sw_dawn_rehydrate_enabled"),
    ("sp_dawn_rehydrate_start_minute", "dawn_rehydrate_start_minute"),
    ("sp_dawn_rehydrate_window_min", "dawn_rehydrate_window_min"),
    ("sp_dawn_rehydrate_on_s", "dawn_rehydrate_on_s"),
    ("sp_dawn_rehydrate_gap_s", "dawn_rehydrate_gap_s"),
    ("sp_sw_midday_drench_enabled", "sw_midday_drench_enabled"),
    ("sp_midday_drench_hour", "midday_drench_hour"),
    ("sp_midday_drench_start_minute", "midday_drench_start_minute"),
    ("sp_midday_drench_window_min", "midday_drench_window_min"),
    ("sp_midday_drench_on_s", "midday_drench_on_s"),
    ("sp_midday_drench_gap_s", "midday_drench_gap_s"),
    ("sp_sw_overnight_micropulse_enabled", "sw_overnight_micropulse_enabled"),
    ("sp_sw_night_humidity_source_present", "sw_night_humidity_source_present"),
    ("sp_micropulse_vpd_ceiling", "micropulse_vpd_ceiling"),
    ("sp_micropulse_max_on_s", "micropulse_max_on_s"),
    ("sp_micropulse_min_gap_s", "micropulse_min_gap_s"),
    ("sp_micropulse_min_dew_margin_f", "micropulse_min_dew_margin_f"),
]

equipment_names = [
    "fog",
    "vent",
    "fan1",
    "fan2",
    "heat1",
    "heat2",
    "mister_south",
    "mister_west",
    "mister_center",
]

fieldnames = (
    base_fields
    + [output for output, _ in setpoint_fields]
    + ["occupied", "greenhouse_state", "mode_reason"]
    + [f"eq_{name}" for name in equipment_names]
    + [
        "outdoor_observation_ts",
        "outdoor_freshness_basis",
        "sp_dehum_vent_hold_enabled",
    ]
)


def open_rows(name):
    handle = (tmp / name).open(newline="")
    return handle, csv.DictReader(handle, delimiter="\t")


def next_or_none(rows):
    try:
        return next(rows)
    except StopIteration:
        return None


sp_handle, sp_rows = open_rows("setpoints.tsv")
sys_handle, sys_rows = open_rows("system.tsv")
eq_handle, eq_rows = open_rows("equipment.tsv")
current_sp = next_or_none(sp_rows)
current_sys = next_or_none(sys_rows)
current_eq = next_or_none(eq_rows)
config = {}
system = {}
equipment = {}

try:
    with (tmp / "climate.tsv").open(newline="") as climate_handle, outfile.open(
        "w", newline=""
    ) as output_handle:
        climate_rows = csv.DictReader(climate_handle, delimiter="\t")
        writer = csv.DictWriter(
            output_handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()

        for climate in climate_rows:
            ts = climate["ts"]

            while current_sp is not None and current_sp["ts"] <= ts:
                config = json.loads(current_sp["config_payload"])
                current_sp = next_or_none(sp_rows)

            while current_sys is not None and current_sys["ts"] <= ts:
                system[current_sys["entity"]] = current_sys["value"]
                current_sys = next_or_none(sys_rows)

            while current_eq is not None and current_eq["ts"] <= ts:
                equipment[current_eq["equipment"]] = (
                    current_eq["state"].lower() in {"t", "true", "1"}
                )
                current_eq = next_or_none(eq_rows)

            output = {field: climate.get(field, "") for field in base_fields}
            for output_name, parameter in setpoint_fields:
                value = config.get(parameter, "")
                output[output_name] = "" if value is None else value

            output["occupied"] = (
                "t" if system.get("occupancy") == "occupied" else "f"
            )
            output["greenhouse_state"] = system.get("greenhouse_state", "")
            output["mode_reason"] = system.get("mode_reason", "")
            for name in equipment_names:
                output[f"eq_{name}"] = "1" if equipment.get(name, False) else "0"

            output["outdoor_observation_ts"] = climate.get(
                "outdoor_observation_ts", ""
            )
            output["outdoor_freshness_basis"] = (
                "conservative_change_observation"
            )
            hold = config.get("sw_dehum_vent_hold_enabled", "")
            output["sp_dehum_vent_hold_enabled"] = "" if hold is None else hold
            writer.writerow(output)
finally:
    sp_handle.close()
    sys_handle.close()
    eq_handle.close()
PY

ROWS=$(wc -l < "$OUTFILE")
echo "Exported $((ROWS - 1)) rows to $OUTFILE"
