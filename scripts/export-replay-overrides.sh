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
        -- TWIN-3 (#31): close the setpoint-coverage gap. Every remaining
        -- dispatcher-pushed Setpoints field (active greenhouse_logic.h path)
        -- forward-filled from setpoint_snapshot so the twin sees the same
        -- setpoint surface the firmware does. Column alias is sp_<struct_field>;
        -- the snapshot `parameter` key (right of <-) is the canonical
        -- tunable-registry name. NULL/absent → replay_emit keeps the firmware
        -- default (twin agrees by construction). Default-only / firmware-derived
        -- fields (econ_block, night/dusk window, feed_hold_active,
        -- dawn_rehydrate_start_hour) are intentionally NOT joined — they have no
        -- setpoint_snapshot row and the twin matches them via default_setpoints().
        sp_heat_hysteresis.value AS sp_heat_hysteresis,         -- <- heat_hysteresis
        sp_sealed_max.value AS sp_sealed_max_s,                 -- <- mist_max_closed_vent_s
        sp_relief_duration.value AS sp_relief_duration_s,       -- <- mist_thermal_relief_s
        sp_max_relief_cycles.value AS sp_max_relief_cycles,     -- <- max_relief_cycles
        sp_fog_rh_ceiling.value AS sp_fog_rh_ceiling,           -- <- fog_rh_ceiling_pct
        sp_fog_min_temp.value AS sp_fog_min_temp,               -- <- fog_min_temp_f
        sp_fog_window_start.value AS sp_fog_window_start,       -- <- fog_time_window_start
        sp_fog_window_end.value AS sp_fog_window_end,           -- <- fog_time_window_end
        sp_dehum_aggressive.value AS sp_dehum_aggressive_kpa,   -- <- dehum_aggressive_kpa
        sp_occupancy_inhibit.value AS sp_occupancy_inhibit,     -- <- sw_occupancy_inhibit
        sp_vent_latch.value AS sp_vent_latch_timeout_ms,        -- <- vent_latch_timeout_ms
        sp_seal_margin.value AS sp_safety_max_seal_margin_f,    -- <- safety_max_seal_margin_f
        sp_econ_heat_margin.value AS sp_econ_heat_margin_f,     -- <- econ_heat_margin_f
        sp_summer_vent.value AS sp_sw_summer_vent_enabled,      -- <- sw_summer_vent_enabled
        sp_vent_prefer_temp.value AS sp_vent_prefer_temp_delta_f, -- <- vent_prefer_temp_delta_f
        sp_vent_prefer_dp.value AS sp_vent_prefer_dp_delta_f,   -- <- vent_prefer_dp_delta_f
        sp_outdoor_staleness.value AS sp_outdoor_staleness_max_s, -- <- outdoor_staleness_max_s
        sp_summer_vent_runtime.value AS sp_summer_vent_min_runtime_s, -- <- summer_vent_min_runtime_s
        sp_dwell_gate_sw.value AS sp_sw_dwell_gate_enabled,     -- <- sw_dwell_gate_enabled
        sp_dwell_gate_ms.value AS sp_dwell_gate_ms,             -- <- dwell_gate_ms
        sp_cool_s2.value AS sp_cool_stage2_over_high_f,         -- <- cool_stage2_over_high_f
        sp_cool_exit.value AS sp_cool_exit_hysteresis_f,        -- <- cool_exit_hysteresis_f
        sp_cold_vent_guard.value AS sp_cold_vent_guard_delta_f, -- <- cold_vent_guard_delta_f
        sp_cool_all_fans.value AS sp_cool_all_fans_at_high_enabled, -- <- sw_cool_all_fans_at_high_enabled
        sp_dw_override.value AS sp_direct_wet_stress_override_enabled, -- <- sw_direct_wet_stress_override_enabled
        sp_dw_vpd_margin.value AS sp_direct_wet_stress_vpd_margin_kpa, -- <- direct_wet_stress_vpd_margin_kpa
        sp_dw_dew_margin.value AS sp_direct_wet_stress_min_dew_margin_f, -- <- direct_wet_stress_min_dew_margin_f
        sp_dw_latest_hour.value AS sp_direct_wet_stress_latest_hour, -- <- direct_wet_stress_latest_hour
        sp_fog_stress_sw.value AS sp_fog_stress_window_extend_enabled, -- <- sw_fog_stress_window_extend_enabled
        sp_fog_stress_hour.value AS sp_fog_stress_window_latest_hour, -- <- fog_stress_window_latest_hour
        sp_fog_stress_dew.value AS sp_fog_stress_min_dew_margin_f, -- <- fog_stress_min_dew_margin_f
        sp_dawn_sw.value AS sp_sw_dawn_rehydrate_enabled,       -- <- sw_dawn_rehydrate_enabled
        sp_dawn_minute.value AS sp_dawn_rehydrate_start_minute, -- <- dawn_rehydrate_start_minute
        sp_dawn_window.value AS sp_dawn_rehydrate_window_min,   -- <- dawn_rehydrate_window_min
        sp_dawn_on.value AS sp_dawn_rehydrate_on_s,             -- <- dawn_rehydrate_on_s
        sp_dawn_gap.value AS sp_dawn_rehydrate_gap_s,           -- <- dawn_rehydrate_gap_s
        sp_midday_sw.value AS sp_sw_midday_drench_enabled,      -- <- sw_midday_drench_enabled
        sp_midday_hour.value AS sp_midday_drench_hour,          -- <- midday_drench_hour
        sp_midday_minute.value AS sp_midday_drench_start_minute, -- <- midday_drench_start_minute
        sp_midday_window.value AS sp_midday_drench_window_min,  -- <- midday_drench_window_min
        sp_midday_on.value AS sp_midday_drench_on_s,            -- <- midday_drench_on_s
        sp_midday_gap.value AS sp_midday_drench_gap_s,          -- <- midday_drench_gap_s
        sp_micropulse_sw.value AS sp_sw_overnight_micropulse_enabled, -- <- sw_overnight_micropulse_enabled
        sp_night_humidity.value AS sp_sw_night_humidity_source_present, -- <- sw_night_humidity_source_present
        sp_micropulse_ceiling.value AS sp_micropulse_vpd_ceiling, -- <- micropulse_vpd_ceiling
        sp_micropulse_on.value AS sp_micropulse_max_on_s,       -- <- micropulse_max_on_s
        sp_micropulse_gap.value AS sp_micropulse_min_gap_s,     -- <- micropulse_min_gap_s
        sp_micropulse_dew.value AS sp_micropulse_min_dew_margin_f, -- <- micropulse_min_dew_margin_f
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
    -- TWIN-3 (#31): forward-fill joins for the remaining dispatcher-pushed
    -- Setpoints fields. One LATERAL per setpoint_snapshot parameter, mirroring
    -- the as-of-join pattern above. Helper to keep this readable: each block is
    -- (parameter key on the right of the AS comment above).
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'heat_hysteresis' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_heat_hysteresis ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'mist_max_closed_vent_s' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_sealed_max ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'mist_thermal_relief_s' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_relief_duration ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'max_relief_cycles' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_max_relief_cycles ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'fog_rh_ceiling_pct' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_fog_rh_ceiling ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'fog_min_temp_f' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_fog_min_temp ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'fog_time_window_start' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_fog_window_start ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'fog_time_window_end' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_fog_window_end ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'dehum_aggressive_kpa' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dehum_aggressive ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'sw_occupancy_inhibit' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_occupancy_inhibit ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'vent_latch_timeout_ms' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_vent_latch ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'safety_max_seal_margin_f' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_seal_margin ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'econ_heat_margin_f' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_econ_heat_margin ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'sw_summer_vent_enabled' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_summer_vent ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'vent_prefer_temp_delta_f' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_vent_prefer_temp ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'vent_prefer_dp_delta_f' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_vent_prefer_dp ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'outdoor_staleness_max_s' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_outdoor_staleness ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'summer_vent_min_runtime_s' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_summer_vent_runtime ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'sw_dwell_gate_enabled' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dwell_gate_sw ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'dwell_gate_ms' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dwell_gate_ms ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'cool_stage2_over_high_f' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_cool_s2 ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'cool_exit_hysteresis_f' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_cool_exit ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'cold_vent_guard_delta_f' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_cold_vent_guard ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'sw_cool_all_fans_at_high_enabled' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_cool_all_fans ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'sw_direct_wet_stress_override_enabled' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dw_override ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'direct_wet_stress_vpd_margin_kpa' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dw_vpd_margin ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'direct_wet_stress_min_dew_margin_f' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dw_dew_margin ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'direct_wet_stress_latest_hour' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dw_latest_hour ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'sw_fog_stress_window_extend_enabled' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_fog_stress_sw ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'fog_stress_window_latest_hour' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_fog_stress_hour ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'fog_stress_min_dew_margin_f' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_fog_stress_dew ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'sw_dawn_rehydrate_enabled' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dawn_sw ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'dawn_rehydrate_start_minute' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dawn_minute ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'dawn_rehydrate_window_min' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dawn_window ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'dawn_rehydrate_on_s' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dawn_on ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'dawn_rehydrate_gap_s' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_dawn_gap ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'sw_midday_drench_enabled' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_midday_sw ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'midday_drench_hour' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_midday_hour ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'midday_drench_start_minute' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_midday_minute ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'midday_drench_window_min' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_midday_window ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'midday_drench_on_s' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_midday_on ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'midday_drench_gap_s' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_midday_gap ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'sw_overnight_micropulse_enabled' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_micropulse_sw ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'sw_night_humidity_source_present' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_night_humidity ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'micropulse_vpd_ceiling' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_micropulse_ceiling ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'micropulse_max_on_s' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_micropulse_on ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'micropulse_min_gap_s' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_micropulse_gap ON true
    LEFT JOIN LATERAL (
        SELECT value FROM setpoint_snapshot
        WHERE parameter = 'micropulse_min_dew_margin_f' AND ts <= c.ts ORDER BY ts DESC LIMIT 1
    ) sp_micropulse_dew ON true
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
