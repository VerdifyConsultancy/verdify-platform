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
#   outdoor_data_age_s    — seconds since last outdoor push; drives gate eligibility
#   solar_irradiance_w_m2 — Tempest, for sunrise-ramp invariants
#   indoor_dew_point      — from climate.dew_point (Magnus inside firmware)
#   eq_<relay>            — forward-filled equipment_state at row ts (0/1)
#   mode_reason           — sprint-15.1 diagnostic enum; drives invariant #10
#   greenhouse_state      — for invariant #6 transition counting
# #24: DB access via the shared psql-verdify abstraction (docker-exec default
# preserves prior VM argv).
. "$(dirname "${BASH_SOURCE[0]}")/lib/psql-verdify.sh"
verdify_psql -c "
COPY (
    SELECT
        c.ts,
        c.temp_avg, c.vpd_avg, c.rh_avg,
        COALESCE(c.outdoor_rh_pct, 30) AS outdoor_rh_pct,
        COALESCE(c.enthalpy_delta, -5) AS enthalpy_delta,
        -- Phase-0 additions: outdoor sensor suite
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
        -- Computed indoor dewpoint (Magnus); NULL if inputs missing
        c.dew_point AS indoor_dew_point,
        -- Tempest solar (W/m²); often NULL overnight
        c.solar_irradiance_w_m2,
        CASE WHEN c.outdoor_temp_f IS NULL THEN NULL
             ELSE EXTRACT(EPOCH FROM (c.ts - outdoor_sp.ts))::int END AS outdoor_data_age_s,
        -- Setpoints forward-filled from dispatcher snapshots.
        sp_temp_high.value AS sp_temp_high,
        sp_temp_low.value AS sp_temp_low,
        sp_vpd_high.value AS sp_vpd_high,
        sp_vpd_low.value AS sp_vpd_low,
        sp_bias_cool.value AS sp_bias_cool,
        sp_vpd_hysteresis.value AS sp_vpd_hysteresis,
        sp_watch_dwell.value AS sp_watch_dwell_s,
        sp_bias_heat.value AS sp_bias_heat,
        sp_temp_hysteresis.value AS sp_temp_hysteresis,
        sp_safety_max.value AS sp_safety_max,
        sp_safety_min.value AS sp_safety_min,
        sp_vpd_max_safe.value AS sp_vpd_max_safe,
        sp_vpd_min_safe.value AS sp_vpd_min_safe,
        sp_fog_escalation.value AS sp_fog_escalation_kpa,
        sp_sw_fsm.value AS sp_sw_fsm_controller_enabled,
        sp_mist_backoff.value AS sp_mist_backoff_s,
        sp_mist_s2_delay.value AS sp_mist_s2_delay_s,
        COALESCE(occ.occupied, false) AS occupied,
        -- Phase-0: greenhouse_state + mode_reason (forward-filled)
        greenhouse_state.value AS greenhouse_state,
        mode_reason.value AS mode_reason,
        -- Phase-0: equipment state bitmask (one column per relay)
        COALESCE(eq_fog.on, 0) AS eq_fog,
        COALESCE(eq_vent.on, 0) AS eq_vent,
        COALESCE(eq_fan1.on, 0) AS eq_fan1,
        COALESCE(eq_fan2.on, 0) AS eq_fan2,
        COALESCE(eq_heat1.on, 0) AS eq_heat1,
        COALESCE(eq_heat2.on, 0) AS eq_heat2,
        COALESCE(eq_mister_south.on, 0) AS eq_mister_south,
        COALESCE(eq_mister_west.on, 0) AS eq_mister_west,
        COALESCE(eq_mister_center.on, 0) AS eq_mister_center
    FROM climate c
    LEFT JOIN LATERAL (
        SELECT (value = 'occupied') AS occupied
        FROM system_state
        WHERE entity = 'occupancy' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) occ ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM system_state
        WHERE entity = 'greenhouse_state' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) greenhouse_state ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM system_state
        WHERE entity = 'mode_reason' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) mode_reason ON true
    LEFT JOIN LATERAL (
        SELECT ts
        FROM setpoint_changes
        WHERE parameter = 'outdoor_temp' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) outdoor_sp ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'temp_high' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_temp_high ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'temp_low' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_temp_low ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'vpd_high' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_vpd_high ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'vpd_low' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_vpd_low ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'bias_cool' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_bias_cool ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'bias_heat' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_bias_heat ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'vpd_hysteresis' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_vpd_hysteresis ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'temp_hysteresis' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_temp_hysteresis ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'vpd_watch_dwell_s' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_watch_dwell ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'safety_max' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_safety_max ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'safety_min' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_safety_min ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'safety_vpd_max' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_vpd_max_safe ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'safety_vpd_min' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_vpd_min_safe ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'fog_escalation_kpa' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_fog_escalation ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'sw_fsm_controller_enabled' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_sw_fsm ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'mist_backoff_s' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_mist_backoff ON true
    LEFT JOIN LATERAL (
        SELECT value
        FROM setpoint_snapshot
        WHERE parameter = 'mister_all_delay_s' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) sp_mist_s2_delay ON true
    LEFT JOIN LATERAL (
        SELECT state::int AS on
        FROM equipment_state
        WHERE equipment = 'fog' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) eq_fog ON true
    LEFT JOIN LATERAL (
        SELECT state::int AS on
        FROM equipment_state
        WHERE equipment = 'vent' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) eq_vent ON true
    LEFT JOIN LATERAL (
        SELECT state::int AS on
        FROM equipment_state
        WHERE equipment = 'fan1' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) eq_fan1 ON true
    LEFT JOIN LATERAL (
        SELECT state::int AS on
        FROM equipment_state
        WHERE equipment = 'fan2' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) eq_fan2 ON true
    LEFT JOIN LATERAL (
        SELECT state::int AS on
        FROM equipment_state
        WHERE equipment = 'heat1' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) eq_heat1 ON true
    LEFT JOIN LATERAL (
        SELECT state::int AS on
        FROM equipment_state
        WHERE equipment = 'heat2' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) eq_heat2 ON true
    LEFT JOIN LATERAL (
        SELECT state::int AS on
        FROM equipment_state
        WHERE equipment = 'mister_south' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) eq_mister_south ON true
    LEFT JOIN LATERAL (
        SELECT state::int AS on
        FROM equipment_state
        WHERE equipment = 'mister_west' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) eq_mister_west ON true
    LEFT JOIN LATERAL (
        SELECT state::int AS on
        FROM equipment_state
        WHERE equipment = 'mister_center' AND ts <= c.ts
        ORDER BY ts DESC
        LIMIT 1
    ) eq_mister_center ON true
    WHERE ${WHERE}
    ORDER BY c.ts
) TO STDOUT WITH (FORMAT csv, DELIMITER E'\t', HEADER, NULL '')
" > "$OUTFILE"

ROWS=$(wc -l < "$OUTFILE")
echo "Exported $((ROWS - 1)) rows to $OUTFILE"
