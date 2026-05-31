#!/bin/bash
# gather-plan-context.sh — Collect ALL data for Iris setpoint planning
# Uses v_iris_planning_context (single DB-side view) + supplementary queries
# Covers: conditions, zones, setpoints, plan, forecast, stress, compliance,
#         equipment, DIF, irrigation, hydro, DLI, energy, disease, crops, occupancy
set -euo pipefail

# Greenhouse ID: from arg or default
GREENHOUSE_ID="${1:-vallery}"
if [ "${1:-}" = "--greenhouse-id" ] && [ -n "${2:-}" ]; then
    GREENHOUSE_ID="$2"
fi

DB_STATEMENT_TIMEOUT_MS="${VERDIFY_PLANNER_CONTEXT_DB_STATEMENT_TIMEOUT_MS:-15000}"
# #24: DB access via the shared psql-verdify abstraction (docker-exec default
# preserves prior VM argv). The PGOPTIONS statement-timeout is injected as a
# docker-exec extra flag in docker-exec mode. Call sites add -c "...".
. "$(dirname "${BASH_SOURCE[0]}")/lib/psql-verdify.sh"
mapfile -t DB < <(verdify_psql_cmd -e "PGOPTIONS=-c statement_timeout=${DB_STATEMENT_TIMEOUT_MS}")
DB+=(-t -A)
HA_TOKEN=$(cat /mnt/agents/shared/credentials/ha_token.txt 2>/dev/null || echo "")
HA_URL="http://192.168.30.107:8123"
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON:-/srv/greenhouse/.venv/bin/python}"
STATIC_CONTEXT_FILE="${VERDIFY_PLANNER_STATIC_CONTEXT:-/srv/verdify/state/planner-static-context.md}"

mapfile -t REGISTRY_SQL < <("$PYTHON_BIN" - "$SCRIPT_ROOT/.." <<'PY'
import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo))

from verdify_schemas.tunable_registry import BAND_OWNED_REG, PLANNER_PUSHABLE_REG  # noqa: E402


def sql_list(values: set[str] | frozenset[str]) -> str:
    return ",".join("'" + value.replace("'", "''") + "'" for value in sorted(values))


active_context = PLANNER_PUSHABLE_REG | BAND_OWNED_REG
plan_compare_excluded = BAND_OWNED_REG | frozenset(
    {"mister_engage_delay_s", "mister_all_delay_s", "mister_center_penalty"}
)
print(sql_list(active_context))
print(sql_list(plan_compare_excluded))
PY
)
ACTIVE_CONTEXT_PARAMS_SQL="${REGISTRY_SQL[0]}"
PLAN_COMPARE_EXCLUDED_SQL="${REGISTRY_SQL[1]}"

echo "=== GREENHOUSE PLANNING CONTEXT ==="
echo "Greenhouse: $GREENHOUSE_ID"
echo "Generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ') ($(date '+%Y-%m-%d %H:%M %Z'))"
echo "Before trusting any individual section, check CONTEXT COMPLETENESS at the"
echo "end of this document — it reports which external dependencies were"
echo "reachable. Empty or stale sections may mean a dependency was down, not"
echo "that the greenhouse state is quiet."
echo ""

# ── 1. CORE PLANNING VIEW (7 JSON columns in one query) ───────────
echo "--- SYSTEM HEALTH ---"
"${DB[@]}" -c "
SELECT row_to_json(v)->'system_health' FROM v_iris_planning_context v;
" 2>/dev/null || echo "{}"
echo ""

# Active plan: compact transition summary (grouped by timestamp, Tier 1 only)
echo "--- ACTIVE PLAN (future transitions only — your new plan will replace this entirely) ---"
echo "Key variables shown per transition. Vent/fog timing params at defaults unless noted."
echo "ts_mdt|raw_params|cool_s2|cool_exit|all_fans|dw_stress|dw_until|fog_stress|fog_until|engage|all|gap|wt|hyst|vent_max|fog_esc"
"${DB[@]}" -c "
WITH deduped AS (
  SELECT DISTINCT ON (ts, parameter) ts, parameter, value
  FROM setpoint_plan WHERE ts > now() AND parameter != 'plan_metadata' AND is_active = true
  ORDER BY ts, parameter, created_at DESC
)
SELECT to_char(ts AT TIME ZONE 'America/Denver', 'Dy MM-DD HH24:MI'),
  count(*),
  COALESCE(max(CASE WHEN parameter='cool_stage2_over_high_f' THEN round(value::numeric,1) END), 1.0),
  COALESCE(max(CASE WHEN parameter='cool_exit_hysteresis_f' THEN round(value::numeric,1) END), 1.5),
  COALESCE(max(CASE WHEN parameter='sw_cool_all_fans_at_high_enabled' THEN value::int END), 0),
  COALESCE(max(CASE WHEN parameter='sw_direct_wet_stress_override_enabled' THEN value::int END), 0),
  COALESCE(max(CASE WHEN parameter='direct_wet_stress_latest_hour' THEN value::int END), 22),
  COALESCE(max(CASE WHEN parameter='sw_fog_stress_window_extend_enabled' THEN value::int END), 0),
  COALESCE(max(CASE WHEN parameter='fog_stress_window_latest_hour' THEN value::int END), 19),
  COALESCE(max(CASE WHEN parameter='mister_engage_kpa' THEN round(value::numeric,1) END), 1.6),
  COALESCE(max(CASE WHEN parameter='mister_all_kpa' THEN round(value::numeric,1) END), 1.9),
  COALESCE(max(CASE WHEN parameter='mister_pulse_gap_s' THEN value::int END), 45),
  COALESCE(max(CASE WHEN parameter='mister_vpd_weight' THEN round(value::numeric,1) END), 1.5),
  COALESCE(max(CASE WHEN parameter='vpd_hysteresis' THEN round(value::numeric,1) END), 0.3),
  COALESCE(max(CASE WHEN parameter='mist_max_closed_vent_s' THEN value::int END), 600),
  COALESCE(max(CASE WHEN parameter='fog_escalation_kpa' THEN round(value::numeric,1) END), 0.4)
FROM deduped
GROUP BY ts
ORDER BY ts;
" 2>/dev/null || echo "(no active plan)"
echo ""

# ── 2. CURRENT ZONE-LEVEL CONDITIONS ──────────────────────────────
echo "--- ZONE CONDITIONS ---"
"${DB[@]}" -c "
SELECT json_build_object(
  'ts', ts,
  'temp_south', round(temp_south::numeric,1), 'temp_north', round(temp_north::numeric,1),
  'temp_east', round(temp_east::numeric,1), 'temp_west', round(temp_west::numeric,1),
  'vpd_south', round(vpd_south::numeric,2), 'vpd_north', round(vpd_north::numeric,2),
  'vpd_east', round(vpd_east::numeric,2), 'vpd_west', round(vpd_west::numeric,2),
  'rh_south', round(rh_south::numeric,1), 'rh_north', round(rh_north::numeric,1),
  'rh_east', round(rh_east::numeric,1), 'rh_west', round(rh_west::numeric,1),
  'outdoor_temp_f', round(outdoor_temp_f::numeric,1), 'outdoor_rh_pct', round(outdoor_rh_pct::numeric,1),
  'outdoor_lux', outdoor_lux, 'solar_irradiance_w_m2', round(solar_irradiance_w_m2::numeric,0),
  'enthalpy_delta', round(enthalpy_delta::numeric,2),
  'dew_point', round(dew_point::numeric,1), 'abs_humidity', round(abs_humidity::numeric,1),
  'flow_gpm', round(flow_gpm::numeric,2), 'water_total_gal', round(water_total_gal::numeric,0)
) FROM climate ORDER BY ts DESC LIMIT 1;
"

# Outdoor freshness — Tempest UPDATEs outdoor_* onto recent climate rows, so
# MAX(ts) WHERE outdoor_temp_f IS NOT NULL ≈ last successful Tempest sync.
# Lets the planner see whether outdoor inputs are stale before trusting them.
"${DB[@]}" -c "
SELECT 'OUTDOOR FRESHNESS: outdoor_data_age_s=' ||
       COALESCE(extract(epoch FROM now() - MAX(ts))::int::text, 'NULL') ||
       ' (>300s = stale, planner should de-weight outdoor signals)'
FROM climate
WHERE outdoor_temp_f IS NOT NULL
  AND ts > now() - interval '30 minutes';
" 2>/dev/null

# Dynamic zone ranking with context
"${DB[@]}" -c "SELECT 'ZONE VPD (current): ' || string_agg(z || '=' || v, ', ' ORDER BY v DESC)
FROM (SELECT unnest(ARRAY['north','south','east','west']) AS z,
      unnest(ARRAY[round(vpd_north::numeric,2), round(vpd_south::numeric,2),
                    round(vpd_east::numeric,2), round(vpd_west::numeric,2)]) AS v
      FROM climate ORDER BY ts DESC LIMIT 1) ranked;" 2>/dev/null
echo "NOTE: North reads driest overnight (equipment zone). Daytime misting priority: south first (6 heads, 0.23 kPa/pulse), west second (3 heads, 0.15 kPa/pulse)."
echo ""
echo "--- ZONE SPREAD / LOCALIZED STRESS ---"
"${DB[@]}" -c "
WITH recent AS (
  SELECT ts,
    (SELECT max(v) - min(v)
       FROM unnest(ARRAY[temp_north, temp_south, temp_east, temp_west]) AS vals(v)
      WHERE v IS NOT NULL) AS temp_spread_f,
    (SELECT max(v) - min(v)
       FROM unnest(ARRAY[vpd_north, vpd_south, vpd_east, vpd_west]) AS vals(v)
      WHERE v IS NOT NULL) AS vpd_spread_kpa
  FROM climate
  WHERE ts > now() - interval '3 hours'
),
latest AS (
  SELECT * FROM recent ORDER BY ts DESC LIMIT 1
),
agg AS (
  SELECT avg(temp_spread_f) AS avg_temp_spread_f,
         max(temp_spread_f) AS max_temp_spread_f,
         avg(vpd_spread_kpa) AS avg_vpd_spread_kpa,
         max(vpd_spread_kpa) AS max_vpd_spread_kpa
  FROM recent
)
SELECT json_build_object(
  'latest_temp_spread_f', round(latest.temp_spread_f::numeric, 1),
  'latest_vpd_spread_kpa', round(latest.vpd_spread_kpa::numeric, 2),
  'avg_3h_temp_spread_f', round(agg.avg_temp_spread_f::numeric, 1),
  'max_3h_temp_spread_f', round(agg.max_temp_spread_f::numeric, 1),
  'avg_3h_vpd_spread_kpa', round(agg.avg_vpd_spread_kpa::numeric, 2),
  'max_3h_vpd_spread_kpa', round(agg.max_vpd_spread_kpa::numeric, 2),
  'planner_rule',
  'If temp spread > 4F or VPD spread > 0.5 kPa, average compliance is insufficient; use zone outliers in conditions_summary and preserve wider VPD deadband.'
) FROM latest CROSS JOIN agg;
" 2>/dev/null || echo "{}"
echo ""

# ── 3. GREENHOUSE STATE + SWITCHES (from HA or DB) ──────────────
ESP_STATE=$("${DB[@]}" -c "SELECT value FROM system_state WHERE entity = 'greenhouse_state' ORDER BY ts DESC LIMIT 1;" 2>/dev/null | tr -d ' ')
if [ -n "$ESP_STATE" ] && [ "$ESP_STATE" != "" ]; then
  echo "--- ESP32 STATE ---"
  echo "state: $ESP_STATE"
  echo ""
fi

# ── 4. 24H HOURLY CLIMATE PATTERN ─────────────────────────────────
echo "--- 24H HOURLY PATTERN ---"
echo "hour|temp_f|rh_pct|vpd_kpa|co2_ppm|peak_lux"
"${DB[@]}" -c "
SELECT to_char(date_trunc('hour', ts) AT TIME ZONE 'America/Denver', 'HH:MI AM') as hour,
  round(avg(temp_avg)::numeric,1) as temp_f, round(avg(rh_avg)::numeric,1) as rh,
  round(avg(vpd_avg)::numeric,2) as vpd, round(avg(co2_ppm)::numeric,0) as co2,
  round(max(lux)::numeric,0) as peak_lux
FROM climate WHERE ts > now() - interval '24 hours'
GROUP BY 1, date_trunc('hour', ts)
ORDER BY date_trunc('hour', ts);
"
echo ""

# ── 5. PLANNER SCORECARD (today + 7-day trend) ───────────────────
TODAY=$(date +%Y-%m-%d)
echo "--- PLANNER SCORECARD (${TODAY} — partial if before midnight, informational only) ---"
echo "metric|value"
"${DB[@]}" -c "SELECT * FROM fn_planner_scorecard((now() AT TIME ZONE 'America/Denver')::date);"
echo ""
echo "--- PLANNER SCORE TREND (7 complete calendar days, excludes today) ---"
echo "date|score|comp|temp%|vpd%|stress_h|heat|cold|vpd_hi|vpd_lo|kwh|therms|water_gal|cost"
"${DB[@]}" -c "
SELECT date, planner_score, compliance_pct, temp_compliance_pct, vpd_compliance_pct,
       total_stress_h, heat_stress_h, cold_stress_h, vpd_high_stress_h, vpd_low_stress_h,
       kwh, therms, water_gal, cost_total
FROM v_daily_kpi
WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - 7
  AND date < (now() AT TIME ZONE 'America/Denver')::date
ORDER BY date DESC;
"
echo "Score = 80% compliance (both temp AND VPD in band) + 20% cost efficiency (<\$5/day=full marks)"
echo "compliance_pct = % time both temp AND VPD in band. temp_comp / vpd_comp = individual axes."
echo "VPD compliance is usually the bottleneck on dry spring days (tight band, 15% outdoor RH)."
echo "Dew point margin: <5°F = condensation risk, <3°F = imminent. dp_risk_h = hours below 5°F. Target: 0h."
echo ""

# ── 6. COMPLIANCE (24h by zone) ───────────────────────────────────
echo "--- COMPLIANCE (24h by zone) ---"
echo "zone|in_band_pct|above_pct|below_pct|na_pct"
"${DB[@]}" -c "SELECT * FROM fn_compliance_pct('24 hours'::interval);"
echo ""

# ── 7. DIF (day/night temperature differential, 7 days) ──────────
echo "--- DIF (7 days) ---"
echo "day|day_avg_f|night_avg_f|dif_f"
"${DB[@]}" -c "
SELECT to_char(date, 'MM-DD') as day, round(day_avg_temp::numeric,1) as day_f,
  round(night_avg_temp::numeric,1) as night_f, round(dif::numeric,1) as dif_f
FROM v_dif WHERE date >= CURRENT_DATE - 6 ORDER BY date DESC;
"
echo ""

# ── 8. HYDROPONIC SYSTEM ──────────────────────────────────────────
echo "--- HYDROPONIC SYSTEM (East Zone) ---"
echo "ph|ec_us_cm|tds_ppm|water_temp_f|orp_mv|battery_pct"
"${DB[@]}" -c "
SELECT round(hydro_ph::numeric,2) as ph, round(hydro_ec_us_cm::numeric,0) as ec_us,
  round(hydro_tds_ppm::numeric,0) as tds, round(hydro_water_temp_f::numeric,1) as water_f,
  round(hydro_orp_mv::numeric,0) as orp, hydro_battery_pct as batt
FROM climate WHERE hydro_ph IS NOT NULL ORDER BY ts DESC LIMIT 1;
"
echo ""

# ── 9. EQUIPMENT RUNTIME 24H ──────────────────────────────────────
echo "--- EQUIPMENT RUNTIME 24H ---"
echo "equipment|on_hours|transitions"
"${DB[@]}" -c "
SELECT equipment,
  round(sum(CASE WHEN state NOT IN ('f','off','false') THEN extract(epoch from coalesce(lead_ts - ts, interval '0')) END)::numeric/3600, 2) as on_hours,
  count(*) as transitions
FROM (SELECT *, lead(ts) OVER (PARTITION BY equipment ORDER BY ts) as lead_ts
      FROM equipment_state WHERE ts > now() - interval '24 hours') sub
WHERE equipment IN ('vent','fan1','fan2','fog','mister_south','mister_west','mister_center',
  'heat1','heat2','drip_wall','grow_light_main','grow_light_grow','water_flowing')
GROUP BY equipment
HAVING count(*) > 1
ORDER BY on_hours DESC NULLS LAST;
"
echo ""

# ── 10. ENERGY CONSUMPTION 24H ────────────────────────────────────
echo "--- ENERGY 24H ---"
echo "kwh_today|avg_watts|peak_watts|avg_heat_watts"
"${DB[@]}" -c "
SELECT round(max(kwh_today)::numeric, 1) as kwh_today,
  round(avg(watts_total)::numeric, 0) as avg_watts,
  round(max(watts_total)::numeric, 0) as peak_watts,
  round(avg(watts_heat)::numeric, 0) as avg_heat_watts
FROM energy WHERE ts > now() - interval '24 hours';
"
echo ""

# ── 11. IRRIGATION ────────────────────────────────────────────────
echo "--- IRRIGATION SCHEDULE ---"
"${DB[@]}" -c "
SELECT zone_path,
       array_to_string(serves_zones, ',') AS serves_zones,
       enabled,
       start_time,
       clean_duration_min,
       fert_duration_min,
       flush_min,
       days_mask,
       fert_days_mask,
       schedule_source,
       stale_readback_count,
       readback_drift_count
  FROM v_irrigation_schedule_current
 ORDER BY schedule_id;
"
echo ""
echo "--- IRRIGATION / FERTIGATION RUNS (7 days) ---"
"${DB[@]}" -c "
SELECT to_char(run_start AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS mdt,
       zone_path,
       fert_relay,
       flush_relay,
       round(fert_duration_min::numeric, 1) AS fert_min,
       round(flush_duration_min::numeric, 1) AS flush_min,
       round(fert_master_overlap_min::numeric, 1) AS master_overlap_min,
       round(COALESCE(meter_delta_gal, 0)::numeric, 1) AS meter_delta_gal,
       quality_flag
  FROM v_irrigation_fertigation_runs
 WHERE run_start > now() - interval '7 days'
 ORDER BY run_start DESC
 LIMIT 10;
" 2>/dev/null || echo "(none)"
echo ""

# ── 12. QUALIFIED LIGHT MINUTES + GROW LIGHTS ─────────────────────
echo "--- QUALIFIED LIGHT MINUTES + GROW LIGHTS ---"
echo "Current readings:"
"${DB[@]}" -c "
SELECT round(dli_today::numeric,1) as dli_mol, round(lux::numeric,0) as lux_now
FROM climate ORDER BY ts DESC LIMIT 1;
" 2>/dev/null
echo "DLI last 7 days:"
"${DB[@]}" -c "
SELECT to_char(date_trunc('day', ts)::date, 'MM-DD') as day, round(max(dli_today)::numeric,1) as peak_dli
FROM climate WHERE ts > now() - interval '7 days' GROUP BY 1 ORDER BY 1;
"
echo ""
echo "LIGHTING POLICY (read-only; dispatcher pushes these to ESP32):"
"${DB[@]}" -c "
SELECT target_dli,
       target_light_hours,
       sunrise_hour,
       natural_sunset_hour,
       cutoff_hour,
       max_crop_name,
       source_chain
FROM fn_lighting_policy(now(), '${GREENHOUSE_ID}');
" 2>/dev/null || echo "(lighting policy unavailable)"
echo "Do not set gl_dli_target, gl_sunrise_hour, gl_sunset_hour, or sw_gl_auto_mode in your plan."
echo "Lighting automation is enforced by two ESP32 per-circuit state machines after dispatcher push."
echo ""
echo "PER-CIRCUIT LIGHTING POLICY (planner-managed tunables; crop policy seeds defaults):"
"${DB[@]}" -c "
SELECT light_key,
       equipment,
       target_light_minutes,
       start_hour,
       cutoff_hour,
       lux_on_threshold,
       lux_hysteresis,
       lux_off_threshold,
       min_on_s,
       min_off_s,
       auto_enabled
FROM fn_lighting_minutes_policy(now(), '${GREENHOUSE_ID}')
ORDER BY light_key;
" 2>/dev/null || echo "(per-circuit lighting policy unavailable)"
echo "You may set gl_main_target_light_minutes/gl_grow_target_light_minutes plus threshold/hysteresis independently when observations support diverging the two circuits."
echo "Policy rows use confirmed cfg/readback state; latest desired setpoint rows are shown separately below for traceability."
echo ""
echo "QUALIFIED LIGHT MINUTES TODAY:"
"${DB[@]}" -c "
SELECT light_key,
       target_light_minutes AS target,
       cfg_target_light_minutes AS cfg_target,
       desired_target_light_minutes AS desired_target,
       qualified_light_minutes AS qualified,
       natural_qualified_minutes AS natural,
       switch_on_minutes AS switch_on,
       overlap_minutes AS overlap,
       remaining_light_minutes AS remaining,
       CASE WHEN cfg_auto_enabled THEN 'ON' ELSE 'OFF' END AS cfg_auto,
       desired_auto_delivery_status AS desired_auto_status,
       CASE WHEN actual_on THEN 'ON' ELSE 'OFF' END AS actual_switch,
       firmware_reason
FROM v_lighting_traceability_now
ORDER BY light_key;
" 2>/dev/null || echo "(qualified light minutes status unavailable)"
echo "A qualified minute counts once when natural lux is above the circuit threshold OR the actual switch is ON; overlap is not double-counted."
echo ""
echo "TEMPEST LUX THRESHOLD RECOMMENDATION (planner guidance for per-circuit tunables):"
"${DB[@]}" -c "
SELECT sample_count,
       overcast_sample_count,
       clear_sample_count,
       round(overcast_p80_lux::numeric, 0) AS overcast_p80_lux,
       round(clear_p20_lux::numeric, 0) AS clear_p20_lux,
       recommended_gl_lux_threshold,
       recommended_gl_lux_hysteresis,
       current_gl_lux_threshold,
       current_gl_lux_hysteresis
FROM fn_lighting_lux_threshold_recommendation(now(), '${GREENHOUSE_ID}');
" 2>/dev/null || echo "(lux threshold recommendation unavailable)"
echo "current_gl_lux_threshold/current_gl_lux_hysteresis are recommendation evidence; confirmed ESP32 cfg readbacks remain the controller-state source of truth."
echo "Use Tempest outdoor illuminance as the lighting trigger. Set gl_main_target_light_minutes/gl_grow_target_light_minutes, gl_main_lux_threshold/gl_main_lux_hysteresis, and gl_grow_lux_threshold/gl_grow_lux_hysteresis from this evidence unless you have a stronger observation."
echo ""
echo "DLI CORRECTION (estimated actual plant DLI):"
SENSOR_DLI=$("${DB[@]}" -c "SELECT round(COALESCE(max(dli_today), 0)::numeric, 1) FROM climate WHERE ts >= date_trunc('day', now() AT TIME ZONE 'America/Denver');" 2>/dev/null)
GL_HOURS=$("${DB[@]}" -c "SELECT round(COALESCE(runtime_grow_light_min, 0)::numeric / 60, 1) FROM daily_summary ORDER BY date DESC LIMIT 1;" 2>/dev/null)
SENSOR_DLI=${SENSOR_DLI:-0}
GL_HOURS=${GL_HOURS:-0}
python3 -c "s=${SENSOR_DLI};g=${GL_HOURS};print(f'sensor_dli={s} | estimated_actual_dli={s*3.5:.1f} | gl_hours={g} | estimated_total_plant_dli={s*3.5+g*0.8:.1f}')" 2>/dev/null || echo "sensor_dli=${SENSOR_DLI} | gl_hours=${GL_HOURS}"
echo "SENSOR LIMITATION: The lux sensor reads 25-40% of actual plant-available light."
echo "Sensor DLI of 5-7 mol corresponds to actual plant DLI of 17-27 mol."
echo "Do NOT use DLI as the primary grow-light control signal. The lighting controller now targets qualified light minutes."
echo "The lux threshold is the overcast/shade detector; target_light_minutes sets the per-circuit photoperiod budget."
echo ""

# ── 13. DISEASE RISK ──────────────────────────────────────────────
echo "--- DISEASE RISK (last 6h) ---"
"${DB[@]}" -c "SELECT * FROM v_disease_risk ORDER BY hour DESC LIMIT 6;" 2>/dev/null || echo "(n/a)"
echo ""

# ── 14. ACTIVE CROPS ──────────────────────────────────────────────
echo "--- ACTIVE CROPS ---"
"${DB[@]}" -c "
SELECT name, variety, position, zone, stage, planted_date
FROM crops WHERE is_active ORDER BY zone, position;
" 2>/dev/null || echo "(none)"
echo ""

# ── Phase 1 (causal evaluation foundation, migration 111) ─────────
# The old logic was: GOVERNING_PLAN = latest plan with created_at::date <= yesterday.
# That let a plan written at 23:26 get graded against the entire preceding day's
# scorecard. Now: list ALL plans whose interval_end >= now() - 24h AND
# interval_start < now() (the plans that actually governed any wall-clock time
# in the last 24h), each annotated with anchor_score from v_plan_window_scorecard.
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)

# Primary governing plan for sections that need a single anchor (e.g. structured
# hypothesis display): the plan that governed the most hours in the last 24h.
GOVERNING_PLAN=$("${DB[@]}" -c "
  SELECT plan_id FROM v_plan_execution_intervals
   WHERE interval_end >= now() - interval '24 hours' AND interval_start < now()
   ORDER BY governed_hours DESC NULLS LAST LIMIT 1;
" 2>/dev/null | tr -d ' ')
GOVERNING_VALIDATED=$("${DB[@]}" -c "SELECT CASE WHEN validated_at IS NOT NULL THEN 'yes' ELSE 'no' END FROM plan_journal WHERE plan_id = '${GOVERNING_PLAN}';" 2>/dev/null | tr -d ' ')

# Evaluation backlog: every completed Iris plan that still has no validation.
# The old hour-window filter caught SUNRISE plans but missed SUNSET/manual
# updates, which made the feedback loop selective and biased.
EVAL_BACKLOG=$("${DB[@]}" -c "
  SELECT COUNT(*)
    FROM plan_journal pj
    LEFT JOIN v_plan_execution_intervals pei USING (plan_id)
   WHERE pj.plan_id LIKE 'iris-%'
     AND pj.plan_id NOT LIKE 'iris-reactive%'
     AND pj.validated_at IS NULL
     AND COALESCE(pei.interval_end, pj.created_at + interval '24 hours') < now() - interval '2 hours';
" 2>/dev/null | tr -d ' ')

# ── 15. PLANS THAT GOVERNED THE LAST 24 HOURS ─────────────────────
# Replaces the single-plan "PREVIOUS PLAN REVIEW". Each row is one plan with
# the wall-clock window it actually governed, plus its deterministic anchor
# score vs Iris's self-grade. plan_evaluate() each plan with anchor_score
# deviation in mind.
echo "--- PLANS THAT GOVERNED THE LAST 24 HOURS (causal attribution; Phase 1) ---"
echo "Historical hypotheses may mention retired knobs such as bias_heat, bias_cool,"
echo "d_heat_stage_2, d_cool_stage_2, or sw_fsm_controller_enabled. Treat those as obsolete"
echo "history only; validate outcomes, but use the AI Tunables Routine Plan"
echo "Contract and current Tier 1 surface for any new plan."
if [ -n "${EVAL_BACKLOG}" ] && [ "${EVAL_BACKLOG}" -gt 0 ]; then
  echo "EVALUATION BACKLOG: ${EVAL_BACKLOG} completed Iris plans still unevaluated — CALL plan_evaluate ON EACH BEFORE WRITING A NEW PLAN."
  "${DB[@]}" -c "
  SELECT pj.plan_id,
         to_char(pj.created_at AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS planned_at,
         to_char(COALESCE(pei.interval_end, pj.created_at + interval '24 hours') AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS ended_at,
         pj.anchor_score
    FROM plan_journal pj
    LEFT JOIN v_plan_execution_intervals pei USING (plan_id)
   WHERE pj.plan_id LIKE 'iris-%'
     AND pj.plan_id NOT LIKE 'iris-reactive%'
     AND pj.validated_at IS NULL
     AND COALESCE(pei.interval_end, pj.created_at + interval '24 hours') < now() - interval '2 hours'
   ORDER BY COALESCE(pei.interval_end, pj.created_at + interval '24 hours')
   LIMIT 10;
  " 2>/dev/null
fi
"${DB[@]}" -c "
SELECT pj.plan_id,
  to_char(pj.created_at AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS planned_at,
  round(pei.governed_hours::numeric, 1) AS gov_h,
  pj.outcome_score AS iris_score,
  pj.anchor_score AS anchor,
  CASE
    WHEN pj.anchor_score IS NOT NULL AND pj.outcome_score IS NOT NULL
         AND ABS(pj.outcome_score - pj.anchor_score) > 2
      THEN '⚠ DEVIATES'
    WHEN pj.validated_at IS NULL THEN '⚠ NEEDS VALIDATION'
    ELSE 'ok' END AS status,
  pj.hypothesis,
  pj.actual_outcome,
  pj.lesson_extracted
FROM plan_journal pj
JOIN v_plan_execution_intervals pei USING (plan_id)
WHERE pei.interval_end >= now() - interval '24 hours' AND pei.interval_start < now()
  AND pj.plan_id NOT LIKE 'iris-reactive%'
ORDER BY pei.interval_start;
" 2>/dev/null
if [ "${GOVERNING_VALIDATED}" = "no" ]; then
  echo "ACTION REQUIRED: Primary governing plan ${GOVERNING_PLAN} is unvalidated. Include its validation in your previous_plan_validation output block using its window scorecard below."
fi

# Per-plan window scorecard — what the plan actually saw during its governed
# interval, NOT the whole calendar day. Replaces the previous daily-summary
# block which mis-attributed late-day plans to the whole day.
echo ""
echo "--- WINDOW SCORECARD per plan (use these for plan_evaluate, NOT daily_summary) ---"
echo "Each row scopes stress + compliance + cost to ONLY the wall-clock time the plan governed."
"${DB[@]}" -c "
SELECT pws.plan_id,
  round(pws.governed_day_fraction::numeric, 3) AS gov_frac,
  round(pws.compliance_pct::numeric, 1)        AS comp,
  round(pws.temp_compliance_pct::numeric, 1)   AS temp_comp,
  round(pws.vpd_compliance_pct::numeric, 1)    AS vpd_comp,
  round(pws.heat_stress_h::numeric, 2)         AS heat_h,
  round(pws.cold_stress_h::numeric, 2)         AS cold_h,
  round(pws.vpd_high_stress_h::numeric, 2)     AS vpd_hi_h,
  round(pws.vpd_low_stress_h::numeric, 2)      AS vpd_lo_h,
  round(pws.cost_total::numeric, 2)            AS cost_usd,
  round(pws.planner_score::numeric, 1)         AS score
FROM v_plan_window_scorecard pws
JOIN v_plan_execution_intervals pei USING (plan_id)
WHERE pei.interval_end >= now() - interval '24 hours' AND pei.interval_start < now()
ORDER BY pei.interval_start;
" 2>/dev/null || echo "(unavailable)"
echo ""

# ── 15c. STRUCTURED HYPOTHESIS (G7: close the predict-vs-deliver loop) ─
# hypothesis_structured is a JSONB column written by set_plan when Iris
# includes a ```json``` block in her hypothesis. Until G7 it was write-only —
# stored but never surfaced back. Injecting it here lets Iris grade her own
# structured predictions (conditions, stress_windows, param rationales)
# against the MOST RECENT COMPLETE PLAN EVALUATION block above.
echo "--- STRUCTURED HYPOTHESIS (yesterday's typed predictions, compare to eval block above) ---"
HAS_STRUCTURED=$("${DB[@]}" -c "SELECT CASE WHEN hypothesis_structured IS NULL THEN 'no' ELSE 'yes' END FROM plan_journal WHERE plan_id = '${GOVERNING_PLAN}';" 2>/dev/null | tr -d ' ')
if [ "${HAS_STRUCTURED}" = "yes" ]; then
  # predicted_vs_actual: extract the scalar conditions from the structured
  # hypothesis and line them up with daily_summary + climate actuals.
  # Columns used (verified 2026-04-19 against live DB):
  #   daily_summary.outdoor_temp_max, rh_min (indoor — best proxy we have)
  #   climate.solar_irradiance_w_m2 (MAX for yesterday)
  # cloud_cover actuals don't have a first-class column; we leave n/a.
  echo "predicted_vs_actual (from hypothesis_structured.conditions):"
  "${DB[@]}" -c "
  SELECT metric, predicted, COALESCE(actual, 'n/a') AS actual FROM (
    SELECT 'outdoor_temp_peak_f' AS metric, 1 AS ord,
      (hypothesis_structured->'conditions'->>'outdoor_temp_peak_f')::text AS predicted,
      (SELECT round(outdoor_temp_max::numeric, 1)::text FROM daily_summary WHERE date = CURRENT_DATE - 1) AS actual
    FROM plan_journal WHERE plan_id = '${GOVERNING_PLAN}'
    UNION ALL SELECT 'outdoor_rh_min_pct (indoor proxy)', 2,
      (hypothesis_structured->'conditions'->>'outdoor_rh_min_pct')::text,
      (SELECT round(rh_min::numeric, 0)::text FROM daily_summary WHERE date = CURRENT_DATE - 1)
    FROM plan_journal WHERE plan_id = '${GOVERNING_PLAN}'
    UNION ALL SELECT 'solar_peak_w_m2', 3,
      (hypothesis_structured->'conditions'->>'solar_peak_w_m2')::text,
      (SELECT round(MAX(solar_irradiance_w_m2)::numeric, 0)::text FROM climate
        WHERE (ts AT TIME ZONE 'America/Denver')::date = CURRENT_DATE - 1)
    FROM plan_journal WHERE plan_id = '${GOVERNING_PLAN}'
    UNION ALL SELECT 'cloud_cover_avg_pct', 4,
      (hypothesis_structured->'conditions'->>'cloud_cover_avg_pct')::text,
      NULL
    FROM plan_journal WHERE plan_id = '${GOVERNING_PLAN}'
  ) t ORDER BY ord;
  " 2>/dev/null
  # Full structured hypothesis, pretty-printed, so Iris can grade stress_windows
  # + rationale (which aren't extractable into a flat table).
  echo ""
  echo "full structured hypothesis (stress_windows + rationale — grade each against actual stress hours + scorecard above):"
  "${DB[@]}" -c "SELECT jsonb_pretty(hypothesis_structured) FROM plan_journal WHERE plan_id = '${GOVERNING_PLAN}';" 2>/dev/null
  echo ""
  echo "Grade each stress_window: did its predicted kind/severity match the actual stress_hours column for that type?"
  echo "Grade each rationale: did new_value produce the expected_effect? If not, note it in your previous_plan_validation."
else
  echo "(no structured hypothesis for governing plan — legacy prose-only plan. Grade from the free-text hypothesis in section 15.)"
fi
echo ""

# ── 16. WATER USAGE TREND ─────────────────────────────────────────
echo "--- WATER (7 days) ---"
"${DB[@]}" -c "
SELECT to_char(day, 'MM-DD') as day, used_gal FROM v_water_daily
WHERE day >= CURRENT_DATE - 6 ORDER BY day;
"
echo "NOTE: Water meter measures total downstream usage (greenhouse + sink + hose). Misting-only water is tracked separately via mister_water_today on ESP32."
echo ""

# ── 17. OCCUPANCY ─────────────────────────────────────────────────
echo "--- OCCUPANCY ---"
"${DB[@]}" -c "
SELECT to_char(ts AT TIME ZONE 'America/Denver', 'HH:MI AM') as time, value as state
FROM system_state WHERE entity = 'occupancy' AND ts > now() - interval '12 hours'
ORDER BY ts DESC LIMIT 5;
" 2>/dev/null || echo "(n/a)"
echo ""

# ── 18. ESP32 FIRMWARE MIN/MAX CONSTRAINTS ────────────────────────
echo "--- TUNABLE CONSTRAINTS (min/max/step) ---"
if [ -n "$HA_TOKEN" ]; then
  if ! curl -fsS -H "Authorization: Bearer $HA_TOKEN" "$HA_URL/api/states" 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)
key_params = ['set_temp_low_degf','set_temp_high_degf','set_vpd_low_kpa','set_vpd_high_kpa',
  'vpd_mister_engage_kpa','vpd_mister_all_kpa','mister_water_budget_gal',
  'gl_dli_target_mol','gl_lux_threshold','irrig_wall_duration_min']
for e in data:
    eid = e['entity_id']
    if eid.startswith('number.greenhouse_'):
        name = eid.replace('number.greenhouse_','')
        if name in key_params:
            a = e.get('attributes',{})
            print(f\"{name}: val={e['state']} min={a.get('min')} max={a.get('max')} step={a.get('step')}\")
" 2>/dev/null; then
    echo "(HA states unavailable; tunable constraints omitted)"
  fi
else
  echo "(HA token unavailable; tunable constraints omitted)"
fi
echo ""

# ── 19. FORECAST BIAS (7-day rolling correction) ──────────────────
BIAS=$("${DB[@]}" -c "SELECT * FROM fn_forecast_correction('temp_f', 24);" 2>/dev/null)
if [ -n "$BIAS" ] && [ "$BIAS" != "" ]; then
  echo "--- FORECAST BIAS ---"
  echo "param|bias_f|window_hours"
  echo "$BIAS"
  echo ""
fi

# ── 20. ACTIVE LESSONS (accumulated planner knowledge) ────────────
echo "--- ACTIVE LESSONS (top 10 by confidence + validation count) ---"
"${DB[@]}" -c "
WITH deduped AS (
  SELECT DISTINCT ON (lesson) id, category, condition, lesson, confidence, times_validated
  FROM planner_lessons
  WHERE is_active = true AND superseded_by IS NULL
  ORDER BY lesson, times_validated DESC
)
SELECT category, condition, lesson, confidence, times_validated
FROM deduped
ORDER BY CASE confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
  times_validated DESC
LIMIT 10;
" 2>/dev/null
echo "When making decisions, reference applicable lessons above. If a lesson contradicts"
echo "your plan, either follow the lesson or explain why conditions differ enough to override."
echo ""

# ── 20a. RELEVANT LESSONS FOR TODAY'S FORECAST (Phase 3 — semantic) ─
# The top-10-by-confidence list above is a static ordering. For a forward-
# looking plan, call lessons_search() with the forecast headline as the
# query so you see lessons that match TODAY's conditions, not just the most-
# validated ones. knowledge_search() searches the unified embedding store
# across site docs, playbook, historical plans, lessons, and crop observations.
echo "--- RELEVANT LESSONS FOR TODAY'S FORECAST (semantic retrieval; Phase 3) ---"
echo "Beyond the top-10 above, call lessons_search(query, top_k) with a forecast-derived"
echo "query for the conditions you're actually planning around. Examples:"
echo "  lessons_search(\"hot dry day with 1100 W/m^2 solar peak, RH below 15%\", top_k=8)"
echo "  lessons_search(\"cool overcast morning then dry afternoon ramp\", top_k=8)"
echo "Before any real set_plan/set_tunable call, use knowledge_search over the full corpus:"
echo "  knowledge_search(\"hot dry high solar day misting plan outcome\", source_types=\"lesson,plan,site_doc,playbook,observation\")"
echo "  knowledge_search(\"VPD-low recovery overnight prior observations\", source_types=\"lesson,plan,site_doc,playbook,observation\")"
echo ""

# ── 20a-2. FORECAST CALIBRATION (Codex P1: forecast-bias-corrected priors) ─
# Open-Meteo can materially overshoot solar and VPD at 0-24h leads. Iris should
# use the live rolling bias rows below instead of hard-coded intuition,
# especially
# for dry-day pre-staging. Without this, the May VPD-low overshoot pattern
# repeats — Iris plans for the forecast (bright + dry) and the day arrives
# cooler/wetter, so the aggressive misting becomes over-humidification.
echo "--- FORECAST CALIBRATION (apply these biases when interpreting today's forecast) ---"
echo "Forecast freshness:"
"${DB[@]}" -c "
SELECT 'latest_fetch_mdt=' ||
       COALESCE(to_char(max(fetched_at) AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI'), 'NULL') ||
       ' age_min=' || COALESCE(round((EXTRACT(epoch FROM now() - max(fetched_at)) / 60.0)::numeric, 1)::text, 'NULL') ||
       ' distinct_future_24h=' || count(DISTINCT ts) FILTER (WHERE ts BETWEEN now() AND now() + interval '24 hours')::text
  FROM weather_forecast;
" 2>/dev/null || echo "(forecast freshness unavailable)"
echo "Rule: if forecast age is over 120 minutes, treat forecast freshness as degraded system health. Do not tune merely because forecast data is stale."
echo "Open-Meteo forecast bias (last 7 days, by lead time):"
"${DB[@]}" -c "
SELECT param,
       lead_bucket,
       round(AVG(bias)::numeric, 2)  AS bias,
       round(AVG(mae)::numeric, 2)   AS mae
  FROM v_forecast_accuracy_lead_buckets
 WHERE date >= CURRENT_DATE - 7
 GROUP BY param, lead_bucket
 ORDER BY param, lead_bucket;
" 2>/dev/null
echo "Rule: positive bias = forecast OVERSHOOTS reality. Discount accordingly."
echo "Solar bias historically +47 W/m^2 (~5-15%% of peak). Do not pre-stage aggressive"
echo "misting until live morning VPD confirms the predicted dry ramp."
echo "Bias-corrected next-24h planning priors (corrected = raw forecast - recent bias):"
"${DB[@]}" -c "
WITH bias AS (
  SELECT param, avg(bias)::float AS bias
    FROM v_forecast_accuracy_lead_buckets
   WHERE date >= CURRENT_DATE - 7
     AND lead_bucket IN ('00-06h', '0-6h', '06-24h', '6-24h')
     AND param IN ('temp_f', 'vpd_kpa', 'solar_w_m2')
   GROUP BY param
), latest_forecast AS (
  SELECT DISTINCT ON (ts) ts, temp_f, vpd_kpa, solar_w_m2
    FROM weather_forecast
   WHERE ts > now()
     AND ts <= now() + interval '24 hours'
   ORDER BY ts, fetched_at DESC
), sampled AS (
  SELECT *
    FROM latest_forecast
   WHERE extract(hour FROM ts AT TIME ZONE 'America/Denver') IN (6, 9, 12, 15, 18, 21)
)
SELECT to_char(ts AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS mdt,
       round(temp_f::numeric, 1) AS raw_temp_f,
       round((temp_f - COALESCE((SELECT bias FROM bias WHERE param = 'temp_f'), 0))::numeric, 1) AS corrected_temp_f,
       round(vpd_kpa::numeric, 2) AS raw_vpd_kpa,
       round((vpd_kpa - COALESCE((SELECT bias FROM bias WHERE param = 'vpd_kpa'), 0))::numeric, 2) AS corrected_vpd_kpa,
       round(solar_w_m2::numeric, 0) AS raw_solar_w_m2,
       round(GREATEST(0, solar_w_m2 - COALESCE((SELECT bias FROM bias WHERE param = 'solar_w_m2'), 0))::numeric, 0) AS corrected_solar_w_m2
  FROM sampled
 ORDER BY ts
 LIMIT 8;
" 2>/dev/null || echo "(forecast calibration unavailable)"
echo "Use corrected_vpd_kpa as the planning prior; keep raw_vpd_kpa as weather context."
echo ""

# ── 20b. CURRENT ACTIVE SETPOINTS (for mandatory waypoint emission) ─
echo "--- CURRENT ACTIVE SETPOINTS ---"
"${DB[@]}" -c "
SELECT parameter, value
FROM (SELECT DISTINCT ON (parameter) parameter, value FROM setpoint_changes ORDER BY parameter, ts DESC) sub
WHERE parameter IN (${ACTIVE_CONTEXT_PARAMS_SQL})
ORDER BY parameter;
" 2>/dev/null
echo "Band-driven values above reflect current diurnal crop profiles and shift throughout the day."
echo "Nighttime values are typically lower (temp_high ~62-65°F, vpd_high ~0.6-0.8 kPa). These are normal, not corruption."
echo "Do not set band-driven, retired bias, or lighting-policy params in your plan; use tactical mist, fog, hysteresis, and dwell knobs instead."
echo "Per-circuit gl_main_target_light_minutes/gl_grow_target_light_minutes and lux threshold/hysteresis params are planner-managed; legacy gl_*_dli_target values are telemetry compatibility, not the primary control goal."
echo ""

echo "--- BAND SETPOINT PROVENANCE (crop -> dispatcher/API -> firmware -> cfg readback) ---"
echo "parameter|crop_target|dispatcher_or_api|firmware_push|cfg_readback|automation_source"
"${DB[@]}" -c "
SELECT parameter,
       round(crop_target_value::numeric, 2) AS crop_target,
       round(dispatcher_value::numeric, 2) AS dispatcher_or_api,
       round(firmware_setpoint_value::numeric, 2) AS firmware_push,
       round(cfg_readback_value::numeric, 2) AS cfg_readback,
       automation_source
  FROM fn_band_setpoint_provenance(now(), '${GREENHOUSE_ID}')
 ORDER BY parameter;
" 2>/dev/null || echo "(band provenance unavailable)"
echo "Use this as a read-only source trace: crop profiles define the target, dispatcher/API derive the value, firmware pushes it, cfg_* readbacks prove acceptance."
echo ""

echo "Firmware invariants (always active, not planner-controlled):"
echo "  fog_time_window: 07:00-17:00 (fog blocked outside this window)"
echo "  fog_rh_ceiling: 90% (fog blocked when RH exceeds)"
echo "  fog_min_temp: 55°F (fog blocked when temp below)"
echo "  economiser: always enabled (planner tunes enthalpy thresholds)"
echo "  fog_closes_vent: planner-policy switch; default ON suppresses fog while vent is physically open except vent-mist assist"
echo "  mister_closes_vent: planner-policy switch; suppresses normal misters while vent is open, but explicit VENTILATE vent-mist assist bypasses it"
echo ""

# ── 20c. GENERATED TUNABLE TRACEABILITY BRIEF ─────────────────────
echo "--- TUNABLE TRACEABILITY BRIEF ---"
if [ -x "$PYTHON_BIN" ] && [ -f "$SCRIPT_ROOT/generate-ai-tunables-page.py" ]; then
  timeout 45 "$PYTHON_BIN" "$SCRIPT_ROOT/generate-ai-tunables-page.py" --planner-context 2>/dev/null \
    || echo "(tunable traceability generator failed; fall back to registry bounds and do not use reserved/no-op params)"
else
  echo "(tunable traceability generator unavailable; fall back to registry bounds and do not use reserved/no-op params)"
fi
echo ""

# ── 21. PLANNING GUIDANCE ──────────────────────────────────────────
echo "--- PLANNING GUIDANCE ---"
echo "VPD RAMP PATTERN: Historical data shows VPD increases ~60% between 9 AM and 1 PM on clear days."
echo "SEALED_MIST entry is driven by vpd_high plus vpd_watch_dwell_s; mister_engage_kpa gates physical S1 mister pulses after humidity/zone demand exists."
echo "On days with forecast outdoor RH < 25% and clear skies, consider a coordinated morning humidity posture:"
echo "lower mister_engage_kpa for earlier physical pulses, tighten mister_pulse_gap_s, and adjust vpd_watch_dwell_s only with a clear no-short-cycle hypothesis."
echo "Raise mist thresholds back toward the evening posture once the dry ramp has passed."
echo ""

# ── 22. FORECAST ALERTS (proactive warnings for next 24-48h) ─────
echo "--- FORECAST ALERTS ---"
"${DB[@]}" -c "
WITH latest AS (
  SELECT ts, temp_f, rh_pct, cloud_cover_pct, precip_prob_pct,
         ROW_NUMBER() OVER (PARTITION BY ts ORDER BY fetched_at DESC) AS rn
  FROM weather_forecast
  WHERE ts > now() AND ts < now() + interval '48 hours'
),
fc AS (SELECT * FROM latest WHERE rn = 1),
today_high AS (
  SELECT MAX(temp_f) AS hi FROM fc WHERE ts < now() + interval '24 hours'
),
tomorrow_high AS (
  SELECT MAX(temp_f) AS hi FROM fc WHERE ts >= now() + interval '24 hours'
),
alerts AS (
  -- HEAT WARNING
  SELECT 1 AS ord, 'HEAT WARNING: forecast high ' || round(max_t::numeric,0) || '°F at '
    || to_char(peak_ts AT TIME ZONE 'America/Denver', 'HH:MI AM')
    || '. Consider stronger cooling posture via fog_escalation_kpa, mist timing, and VPD dwell.' AS alert
  FROM (SELECT max(temp_f) AS max_t, (array_agg(ts ORDER BY temp_f DESC))[1] AS peak_ts FROM fc WHERE temp_f > 90) h
  WHERE h.max_t IS NOT NULL
  UNION ALL
  -- VPD WARNING (dry + hot)
  SELECT 2, 'VPD WARNING: dry conditions forecast (RH ' || round(min(rh_pct)::numeric,0) || '%, '
    || round(max(temp_f)::numeric,0) || '°F). Consider lowering mister_engage_kpa to 1.3.'
  FROM fc WHERE rh_pct < 20 AND temp_f > 75
  HAVING count(*) > 0
  UNION ALL
  -- FROST RISK
  SELECT 3, 'FROST RISK: forecast low ' || round(min_t::numeric,0) || '°F at '
    || to_char(low_ts AT TIME ZONE 'America/Denver', 'HH:MI AM')
    || '. Verify heaters operational; use hysteresis/dwell and conservative vent/moisture posture to prevent oscillation.'
  FROM (SELECT min(temp_f) AS min_t, (array_agg(ts ORDER BY temp_f ASC))[1] AS low_ts FROM fc WHERE temp_f < 35) c
  WHERE c.min_t IS NOT NULL
  UNION ALL
  -- OVERCAST / RAIN
  SELECT 4, 'OVERCAST/RAIN: avg cloud cover ' || round(avg(cloud_cover_pct)::numeric,0)
    || '%, precip prob ' || round(max(precip_prob_pct)::numeric,0)
    || '% tomorrow. DLI may be low (lighting handled by gl_auto_mode, not this planner).'
  FROM fc
  WHERE ts::date = (CURRENT_DATE + 1)
  HAVING avg(cloud_cover_pct) > 80 OR max(precip_prob_pct) > 60
  UNION ALL
  -- COLD FRONT
  SELECT 5, 'COLD FRONT: today high ' || round(th.hi::numeric,0) || '°F → tomorrow '
    || round(tmh.hi::numeric,0) || '°F (-' || round((th.hi - tmh.hi)::numeric,0)
    || '°F). Heaters will cycle heavily tonight.'
  FROM today_high th, tomorrow_high tmh
  WHERE th.hi - tmh.hi > 15
)
SELECT alert FROM alerts ORDER BY ord;
"
# If query returned nothing, show nominal
if [ $? -ne 0 ] || ! "${DB[@]}" -c "
WITH latest AS (
  SELECT ts, temp_f, rh_pct,
         ROW_NUMBER() OVER (PARTITION BY ts ORDER BY fetched_at DESC) AS rn
  FROM weather_forecast WHERE ts > now() AND ts < now() + interval '48 hours'
),
fc AS (SELECT * FROM latest WHERE rn = 1)
SELECT 1 FROM fc WHERE temp_f > 90 OR temp_f < 35 OR (rh_pct < 20 AND temp_f > 75) LIMIT 1;
" 2>/dev/null | grep -q 1; then
  echo "No forecast alerts — conditions nominal for next 48h."
fi
echo ""

# ── 23. EXPERIMENT TRACKER (recent hypotheses + outcomes) ─────────
echo "--- EXPERIMENT TRACKER ---"
echo "Latest pending:"
"${DB[@]}" -c "
SELECT plan_id, to_char(created_at AT TIME ZONE 'America/Denver', 'MM-DD HH:MI') AS date,
  COALESCE(experiment, '(none)') AS experiment
FROM plan_journal WHERE validated_at IS NULL AND plan_id NOT LIKE 'iris-reactive%'
ORDER BY created_at DESC LIMIT 1;
" 2>/dev/null || echo "(none pending)"
echo "Last completed:"
"${DB[@]}" -c "
SELECT plan_id, outcome_score, COALESCE(lesson_extracted, '(no lesson)') AS lesson
FROM plan_journal WHERE validated_at IS NOT NULL AND plan_id NOT LIKE 'iris-reactive%'
ORDER BY validated_at DESC LIMIT 1;
" 2>/dev/null || echo "(none completed)"
echo ""

# ── 22b. (moved to section 27) ───────────────────────────────────

# Replan triggers are handled by iris_planner.py event dispatch — not injected here.

# ── 24. 72-HOUR HOURLY FORECAST ──────────────────────────────────
echo "--- 72H HOURLY FORECAST ---"
echo "hour_mdt|temp_f|rh_pct|cloud_pct|wind_mph|vpd_kpa|solar_w_m2|et0_mm|precip_prob_pct|weather_code"
"${DB[@]}" -c "
SELECT to_char(ts AT TIME ZONE 'America/Denver', 'Dy MM-DD HH24:00') as hour_mdt,
  round(temp_f::numeric,0) as temp_f,
  round(rh_pct::numeric,0) as rh,
  round(cloud_cover_pct::numeric,0) as cloud,
  round(wind_speed_mph::numeric,0) as wind,
  round(vpd_kpa::numeric,2) as vpd,
  round(GREATEST(COALESCE(direct_radiation_w_m2,0),0)::numeric,0) as solar_w,
  round(COALESCE(et0_mm,0)::numeric,1) as et0,
  round(COALESCE(precip_prob_pct,0)::numeric,0) as precip_pct,
  weather_code
FROM (
  SELECT DISTINCT ON (ts) * FROM weather_forecast
  WHERE ts > now() AND ts < now() + interval '72 hours'
  ORDER BY ts, fetched_at DESC
) fc
ORDER BY ts;
"
echo ""

# ── 25. DAYS 4-7 DAILY OUTLOOK ──────────────────────────────────
echo "--- DAYS 4-7 DAILY OUTLOOK ---"
"${DB[@]}" -c "
SELECT to_char(date_trunc('day', ts) AT TIME ZONE 'America/Denver', 'Dy MM-DD') as day,
  round(min(temp_f)::numeric,0) as low_f,
  round(max(temp_f)::numeric,0) as high_f,
  round(min(rh_pct)::numeric,0) as min_rh,
  round(avg(cloud_cover_pct)::numeric,0) as avg_cloud,
  round(max(precip_prob_pct)::numeric,0) as max_precip,
  round(avg(vpd_kpa)::numeric,2) as avg_vpd,
  round(sum(COALESCE(et0_mm,0))::numeric,1) as total_et0
FROM (
  SELECT DISTINCT ON (ts) * FROM weather_forecast
  WHERE ts >= now() + interval '72 hours' AND ts < now() + interval '7 days'
  ORDER BY ts, fetched_at DESC
) fc
GROUP BY date_trunc('day', ts)
ORDER BY date_trunc('day', ts);
"
echo "Use this coarse forecast for day-level planning posture (conservative defaults)."
echo ""

# ── 27. PLAN COVERAGE VALIDATION ─────────────────────────────────
# validate-plan-coverage.sh returns exit 1 when any transition is missing
# Tier 1 params. With `set -euo pipefail` active, the non-zero exit
# aborted the whole script — sections 28-31 (including sprint-1's G10
# completeness summary and sprint-4's delivery/clamp sections) never
# ran. Trailing `|| true` keeps the coverage report visible without
# killing downstream sections.
echo "--- PLAN COVERAGE VALIDATION ---"
bash "$SCRIPT_ROOT/validate-plan-coverage.sh" 2>/dev/null || true
echo ""

# ── 28. FORECAST ACCURACY ─────────────────────────────────────────
echo "--- FORECAST ACCURACY (7 days) ---"
echo "date|metric|forecast|actual|error|abs_error|lead_hours"
"${DB[@]}" -c "SELECT * FROM v_forecast_accuracy_daily WHERE date >= CURRENT_DATE - 7 ORDER BY date DESC, param;" 2>/dev/null
echo "Use this to calibrate your trust in the forecast. If 48h accuracy is consistently worse than 24h, weight near-term forecasts more heavily."
echo ""

# ── 29. PLAN COMPARISON ───────────────────────────────────────────
echo "--- PLAN COMPARISON (Tier 1 only, current vs previous) ---"
echo "parameter|current_avg|previous_avg|delta"
"${DB[@]}" -c "
SELECT DISTINCT ON (parameter)
  parameter, round(cur_avg::numeric,2), round(prev_avg::numeric,2), round(delta_avg::numeric,2)
FROM v_plan_comparison
WHERE abs(delta_avg) > 0.01
  AND parameter NOT IN (${PLAN_COMPARE_EXCLUDED_SQL})
ORDER BY parameter, plan_created DESC;
" 2>/dev/null || echo "(not available)"
echo ""

# ── 30. CROP HEALTH (Gemini Vision observations) ────────────────
echo "--- CROP HEALTH (latest visual observations) ---"
"${DB[@]}" -c "
SELECT to_char(ts AT TIME ZONE 'America/Denver', 'MM-DD HH:MI') AS observed,
  camera, zone, confidence,
  crops_observed::text
FROM image_observations
WHERE ts > now() - interval '48 hours'
ORDER BY ts DESC LIMIT 4;
" 2>/dev/null
echo ""
"${DB[@]}" -c "
SELECT to_char(ts AT TIME ZONE 'America/Denver', 'MM-DD HH:MI') AS observed,
  array_to_string(recommended_actions, '; ') AS actions
FROM image_observations
WHERE ts > now() - interval '24 hours'
ORDER BY ts DESC LIMIT 2;
" 2>/dev/null
echo "Crop health observations are informational. Note declining scores in your conditions_summary, but do not change tuning strategy based on visual observations alone — they may indicate nutrient, light, or root-zone issues outside this planner's control surface."
echo ""

# ── 30a. PLANNER DELIVERY HISTORY (sprint-4 G-Kn-D) ─────────────
# plan_delivery_log (status column from sprint 24.9) captures every trigger
# and its lifecycle. Surfacing the 24h histogram here lets Iris see her own
# silent-drop pattern without needing the ops-side midnight_watch alert to
# page Jason first. Counts >1 'pending'/'timed_out' in one event_type row
# indicate a real silent-drop pattern; she should flag it in her Slack brief.
echo "--- YOUR RECENT DELIVERIES (last 24h from plan_delivery_log) ---"
"${DB[@]}" -c "
SELECT event_type, status, count(*) AS n,
  to_char(max(delivered_at) AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS most_recent
FROM plan_delivery_log
WHERE delivered_at > now() - interval '24 hours'
GROUP BY 1, 2
ORDER BY 1, 2;
" 2>/dev/null || echo "(plan_delivery_log unreachable)"
echo "Legend: pending = waiting for plan or acknowledge_trigger; plan_written = plan landed;"
echo "  acked = you recorded no-action-needed; timed_out = SLA breached; delivery_failed = gateway 4xx/5xx."
echo "If one event_type shows many pending/timed_out: silent-drops are happening. Flag in Slack brief."
echo ""

# ── 30b. RECENT CLAMPS (sprint-4 G-Kn-C) ────────────────────────
# setpoint_clamps records every time the dispatcher clamped a push to the
# invariant-safe range. Without this visibility, Iris repeats the same
# out-of-range push and wonders why the ESP32 never sees her value (her push
# landed in setpoint_clamps, not setpoint_changes). Grouped so a repeating
# clamp is obvious; full per-row detail would overwhelm the context.
echo "--- RECENT CLAMPS (dispatcher rejections, last 24h, top 10 params) ---"
"${DB[@]}" -c "
SELECT parameter, count(*) AS n_clamped,
  round(avg(requested)::numeric, 3) AS avg_requested,
  round(avg(applied)::numeric, 3) AS avg_applied,
  max(reason) AS reason,
  to_char(max(ts) AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS most_recent
FROM setpoint_clamps
WHERE ts > now() - interval '24 hours'
GROUP BY parameter
ORDER BY n_clamped DESC LIMIT 10;
" 2>/dev/null || echo "(setpoint_clamps unreachable or empty)"
echo "If a param clamps repeatedly at the same requested value: the push source is out of the"
echo "dispatcher's valid range. If YOU pushed that value, it reached setpoint_clamps but NOT the"
echo "ESP32 — reconsider the value or reference the Tunable Dictionary for the actual range."
echo ""

# ── 30b-2. GUARDRAIL-AWARE TRANSITION AUDIT ─────────────────────
# This is the plan -> dispatcher -> ESP32 landing check. It distinguishes
# normal matches from safety holds where the dispatcher intentionally kept the
# already-applied value and therefore did not emit a setpoint_changes row.
echo "--- GUARDRAIL-AWARE TRANSITION AUDIT (last 36h) ---"
"${DB[@]}" -c "
SELECT plan_id,
       status,
       count(*) AS n,
       round(avg(push_latency_s)::numeric, 0) AS avg_push_s,
       round(avg(confirm_latency_s)::numeric, 0) AS avg_confirm_s
  FROM fn_plan_transition_audit(NULL, '36 hours'::interval, '10 minutes'::interval)
 GROUP BY plan_id, status
 ORDER BY max(plan_created_at) DESC, plan_id, status;
" 2>/dev/null || echo "(fn_plan_transition_audit unavailable; migration 120 may not be applied)"
echo "Any status other than matched/already_at_value/guardrailed/held_by_guardrail requires investigation before trusting the plan."
echo "Recent held/missed/mismatch details:"
"${DB[@]}" -c "
SELECT to_char(transition_ts AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS transition_mdt,
       plan_id,
       parameter,
       round(planned_value::numeric, 3) AS planned,
       round(applied_value::numeric, 3) AS applied,
       status,
       COALESCE(guardrail_reason, '') AS guardrail_reason,
       CASE WHEN matching_push_ts IS NULL THEN ''
            ELSE to_char(matching_push_ts AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI')
        END AS restored_mdt
  FROM fn_plan_transition_audit(NULL, '36 hours'::interval, '10 minutes'::interval)
 WHERE status NOT IN ('matched', 'already_at_value')
 ORDER BY transition_ts DESC, parameter
 LIMIT 20;
" 2>/dev/null || true
echo ""

# ── 30b-3. DISPATCHER-OWNED TARGET CONTEXT ──────────────────────
echo "--- DISPATCHER-OWNED CLIMATE TARGETS (latest reading) ---"
"${DB[@]}" -c "
WITH latest_climate AS (
  SELECT ts,
         temp_avg,
         vpd_avg
    FROM climate
   WHERE temp_avg IS NOT NULL
     AND vpd_avg IS NOT NULL
   ORDER BY ts DESC
   LIMIT 1
), latest AS (
  SELECT c.ts,
         c.temp_avg,
         c.vpd_avg,
         fn_setpoint_at('temp_low', c.ts) AS sp_temp_low,
         fn_setpoint_at('temp_high', c.ts) AS sp_temp_high,
         fn_setpoint_at('vpd_low', c.ts) AS sp_vpd_low,
         fn_setpoint_at('vpd_high', c.ts) AS sp_vpd_high,
         (
           SELECT ss.value
             FROM system_state ss
            WHERE ss.entity = 'greenhouse_state'
              AND ss.ts <= c.ts
            ORDER BY ss.ts DESC
            LIMIT 1
         ) AS greenhouse_mode
    FROM latest_climate c
)
SELECT to_char(ts AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS reading_mdt,
       round(temp_avg::numeric, 2) AS temp_actual_f,
       round(sp_temp_low::numeric, 2) AS temp_low_f,
       round(((sp_temp_low + sp_temp_high) / 2.0)::numeric, 2) AS temp_target_f,
       round(sp_temp_high::numeric, 2) AS temp_high_f,
       round((temp_avg - ((sp_temp_low + sp_temp_high) / 2.0))::numeric, 2) AS temp_target_delta_f,
       round(vpd_avg::numeric, 3) AS vpd_actual_kpa,
       round(sp_vpd_low::numeric, 3) AS vpd_low_kpa,
       round(((sp_vpd_low + sp_vpd_high) / 2.0)::numeric, 3) AS vpd_target_kpa,
       round(sp_vpd_high::numeric, 3) AS vpd_high_kpa,
       round((vpd_avg - ((sp_vpd_low + sp_vpd_high) / 2.0))::numeric, 3) AS vpd_target_delta_kpa,
       greenhouse_mode
  FROM latest;
" 2>/dev/null || echo "(dispatcher-owned target context unavailable)"
echo "Dispatcher/crop policy owns low/target/high. The AI planner receives these as read-only prompt context and tunes only tactical ClimateIntent fields around them."
echo ""

echo "--- CLIMATE AUTHORITY MODE (current compliance priority) ---"
"${DB[@]}" -c "
WITH latest_climate AS (
  SELECT ts,
         temp_avg,
         vpd_avg,
         dew_point
    FROM climate
   WHERE temp_avg IS NOT NULL
     AND vpd_avg IS NOT NULL
   ORDER BY ts DESC
   LIMIT 1
), latest AS (
  SELECT c.ts,
         c.temp_avg,
         c.vpd_avg,
         c.dew_point,
         fn_setpoint_at('temp_low', c.ts) AS sp_temp_low,
         fn_setpoint_at('temp_high', c.ts) AS sp_temp_high,
         fn_setpoint_at('vpd_low', c.ts) AS sp_vpd_low,
         fn_setpoint_at('vpd_high', c.ts) AS sp_vpd_high,
         (c.temp_avg - c.dew_point) AS dew_margin_f
    FROM latest_climate c
)
SELECT to_char(ts AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS reading_mdt,
       round(greatest(0.0, temp_avg - sp_temp_high)::numeric, 2) AS temp_above_high_f,
       round(greatest(0.0, vpd_avg - sp_vpd_high)::numeric, 3) AS vpd_above_high_kpa,
       round(dew_margin_f::numeric, 2) AS dew_margin_f,
       CASE
         WHEN temp_avg > sp_temp_high AND vpd_avg > sp_vpd_high THEN 'COMPLIANCE_FIRST_TEMP_AND_VPD_HIGH'
         WHEN temp_avg > sp_temp_high THEN 'COMPLIANCE_FIRST_TEMP_HIGH'
         WHEN vpd_avg > sp_vpd_high THEN 'COMPLIANCE_FIRST_VPD_HIGH'
         WHEN temp_avg < sp_temp_low OR vpd_avg < sp_vpd_low THEN 'COMPLIANCE_FIRST_LOW_SIDE'
         ELSE 'RESOURCE_OPTIMIZATION_ALLOWED'
       END AS authority_mode
  FROM latest;
" 2>/dev/null || echo "(climate authority mode unavailable)"
echo "Priority order is safety rails, temp compliance, VPD compliance, then resource use. When VPD is above band and dew/occupancy rails are safe, resource minimization must not close wet/fog assist; use ClimateIntent to make moisture assist available near the dispatcher-owned VPD high edge."
echo ""

# ── 30b-4. CLIMATE ACTION PROOF ─────────────────────────────────
echo "--- CLIMATE AUTHORITY ACTION PROOF (latest controller decision) ---"
echo "age_s|action|priority|temp_target_delta_f|vpd_target_delta_kpa|temp_band_error_f|vpd_band_error_kpa|moisture_state|wet_allowed|wet_block|fog_allowed|fog_block|relay_truth|sensor_status|candidate_summary"
"${DB[@]}" -c "
WITH latest AS (
  SELECT l.*,
         EXTRACT(EPOCH FROM now() - l.ts)::int AS age_s
    FROM climate_action_log l
   WHERE COALESCE(l.greenhouse_id, 'vallery') = '${GREENHOUSE_ID}'
   ORDER BY l.ts DESC
   LIMIT 1
)
SELECT age_s,
       climate_action,
       priority_axis,
       round(temp_target_delta_f::numeric, 2) AS temp_target_delta_f,
       round(vpd_target_delta_kpa::numeric, 3) AS vpd_target_delta_kpa,
       round(temp_band_error_f::numeric, 2) AS temp_band_error_f,
       round(vpd_band_error_kpa::numeric, 3) AS vpd_band_error_kpa,
       COALESCE(moisture_assist_state, 'unknown') AS moisture_state,
       wet_assist_allowed,
       COALESCE(wet_assist_block_reason, 'none') AS wet_block,
       fog_allowed,
       COALESCE(fog_block_reason, 'none') AS fog_block,
       relay_truth,
       sensor_status,
       COALESCE(candidate_summary, '') AS candidate_summary
  FROM latest;
" 2>/dev/null || echo "(climate action proof unavailable)"
echo "This is ESP32/controller-owned truth for the current action, target deltas, wet/fog block reasons, relay truth, and input freshness. Treat stale or missing proof as degraded context; tune only bounded ClimateIntent fields, not dispatcher-owned bands."
echo ""

echo "--- CLIMATE AUTHORITY ACTION ROLLUP (last 3h) ---"
echo "action|priority|samples|max_abs_temp_band_error_f|max_abs_vpd_band_error_kpa|wet_allowed_pct|fog_allowed_pct|block_reasons"
"${DB[@]}" -c "
SELECT climate_action,
       priority_axis,
       count(*) AS samples,
       round(max(abs(temp_band_error_f))::numeric, 2) AS max_abs_temp_band_error_f,
       round(max(abs(vpd_band_error_kpa))::numeric, 3) AS max_abs_vpd_band_error_kpa,
       round((100.0 * avg(CASE WHEN wet_assist_allowed THEN 1.0 ELSE 0.0 END))::numeric, 1) AS wet_allowed_pct,
       round((100.0 * avg(CASE WHEN fog_allowed THEN 1.0 ELSE 0.0 END))::numeric, 1) AS fog_allowed_pct,
       string_agg(
         DISTINCT COALESCE(NULLIF(wet_assist_block_reason, ''), NULLIF(fog_block_reason, ''), moisture_assist_state, 'none'),
         ', '
         ORDER BY COALESCE(NULLIF(wet_assist_block_reason, ''), NULLIF(fog_block_reason, ''), moisture_assist_state, 'none')
       ) AS block_reasons
  FROM climate_action_log
 WHERE COALESCE(greenhouse_id, 'vallery') = '${GREENHOUSE_ID}'
   AND ts > now() - interval '3 hours'
 GROUP BY climate_action, priority_axis
 ORDER BY samples DESC, climate_action;
" 2>/dev/null || echo "(climate action rollup unavailable)"
echo "Use this to distinguish capacity limits from tactical limits: persistent high band errors with wet/fog disallowed point to safety/block reasons; high errors with assist allowed but low relay duty point to controller/relay proof that needs operator review."
echo ""

# ── 30b-4a. BOUNDED RESPONSE PRIORS ─────────────────────────────
echo "--- CLIMATE ACTION RESPONSE PRIORS (bounded last 24h, 5m lookahead) ---"
echo "action|priority|matched_samples|avg_temp_abs_error_delta_f|avg_vpd_abs_error_delta_kpa|wet_served_pct|avg_est_water_gal|common_blocks"
"${DB[@]}" -c "
WITH bounded AS (
  SELECT *
    FROM (
      SELECT ts,
             greenhouse_id,
             climate_action,
             priority_axis,
             temp_band_error_f,
             vpd_band_error_kpa,
             moisture_assist_state,
             wet_assist_block_reason,
             fog_block_reason,
             relay_truth,
             resource_cost_estimate
        FROM climate_action_log
       WHERE COALESCE(greenhouse_id, 'vallery') = '${GREENHOUSE_ID}'
         AND ts > now() - interval '24 hours'
         AND temp_band_error_f IS NOT NULL
         AND vpd_band_error_kpa IS NOT NULL
       ORDER BY ts DESC
       LIMIT 300
    ) recent
), scored AS (
  SELECT b.*,
         nxt.temp_band_error_f AS temp_band_error_after_f,
         nxt.vpd_band_error_kpa AS vpd_band_error_after_kpa
    FROM bounded b
    LEFT JOIN LATERAL (
      SELECT n.temp_band_error_f,
             n.vpd_band_error_kpa
        FROM climate_action_log n
       WHERE COALESCE(n.greenhouse_id, 'vallery') = COALESCE(b.greenhouse_id, 'vallery')
         AND n.ts >= b.ts + interval '5 minutes'
         AND n.ts <= b.ts + interval '7 minutes'
         AND n.temp_band_error_f IS NOT NULL
         AND n.vpd_band_error_kpa IS NOT NULL
       ORDER BY n.ts ASC
       LIMIT 1
    ) nxt ON true
)
SELECT climate_action,
       priority_axis,
       count(*) FILTER (WHERE temp_band_error_after_f IS NOT NULL) AS matched_samples,
       round(avg(abs(temp_band_error_after_f) - abs(temp_band_error_f))::numeric, 2)
         AS avg_temp_abs_error_delta_f,
       round(avg(abs(vpd_band_error_after_kpa) - abs(vpd_band_error_kpa))::numeric, 3)
         AS avg_vpd_abs_error_delta_kpa,
       round((100.0 * avg(CASE
         WHEN moisture_assist_state IN ('served', 'pulse_on')
           OR lower(COALESCE(relay_truth->>'fog', 'false')) = 'true'
           OR lower(COALESCE(relay_truth->>'mister_south', 'false')) = 'true'
           OR lower(COALESCE(relay_truth->>'mister_west', 'false')) = 'true'
           OR lower(COALESCE(relay_truth->>'mister_center', 'false')) = 'true'
         THEN 1.0 ELSE 0.0 END))::numeric, 1) AS wet_served_pct,
       round(avg(NULLIF(resource_cost_estimate->>'water_gal', '')::numeric), 3)
         AS avg_est_water_gal,
       COALESCE(string_agg(
         DISTINCT COALESCE(NULLIF(wet_assist_block_reason, ''), NULLIF(fog_block_reason, '')),
         ', '
       ) FILTER (
         WHERE COALESCE(NULLIF(wet_assist_block_reason, ''), NULLIF(fog_block_reason, '')) IS NOT NULL
           AND COALESCE(NULLIF(wet_assist_block_reason, ''), NULLIF(fog_block_reason, '')) <> 'none'
       ), 'none') AS common_blocks
  FROM scored
 GROUP BY climate_action, priority_axis
HAVING count(*) FILTER (WHERE temp_band_error_after_f IS NOT NULL) > 0
 ORDER BY matched_samples DESC, climate_action
 LIMIT 12;
" 2>/dev/null || echo "(climate action response priors unavailable)"
echo "Negative deltas mean the absolute band error improved after the action. Use this as a recent response prior alongside forecast pressure: prefer tactics that reduced temp/VPD error per unit water, and do not conserve resources by closing wet assist during safe dual-axis misses."
echo ""

# ── 30b-5. HOT/DRY VENTILATE CONTROL UTILIZATION ─────────────────
echo "--- HOT/DRY VENTILATE UTILIZATION (last 24h, firmware band basis) ---"
"${DB[@]}" -c "
WITH samples AS (
  SELECT c.ts,
         c.temp_avg,
         c.vpd_avg,
         fn_setpoint_at('temp_high', c.ts) AS sp_temp_high,
         fn_setpoint_at('vpd_high', c.ts) AS sp_vpd_high,
         fn_equip_at('fan1', c.ts) AS fan1,
         fn_equip_at('fan2', c.ts) AS fan2,
         fn_equip_at('fog', c.ts) AS fog,
         (fn_equip_at('mister_south', c.ts) OR fn_equip_at('mister_west', c.ts) OR fn_equip_at('mister_center', c.ts)) AS misting,
         (
           SELECT ss.value
             FROM system_state ss
            WHERE ss.entity = 'greenhouse_state'
              AND ss.ts <= c.ts
            ORDER BY ss.ts DESC
            LIMIT 1
         ) AS greenhouse_mode
    FROM climate c
   WHERE c.ts > now() - interval '24 hours'
     AND c.temp_avg IS NOT NULL
     AND c.vpd_avg IS NOT NULL
     AND (extract(minute FROM c.ts)::int % 5) = 0
), classified AS (
  SELECT *,
         (temp_avg > sp_temp_high AND vpd_avg > sp_vpd_high) AS both_high,
         (greenhouse_mode = 'VENTILATE') AS ventilating
    FROM samples
   WHERE sp_temp_high IS NOT NULL
     AND sp_vpd_high IS NOT NULL
)
SELECT count(*) FILTER (WHERE both_high AND ventilating) AS hot_dry_vent_samples,
       round(avg(temp_avg - sp_temp_high) FILTER (WHERE both_high AND ventilating)::numeric, 2) AS avg_temp_excess_f,
       round(avg(vpd_avg - sp_vpd_high) FILTER (WHERE both_high AND ventilating)::numeric, 3) AS avg_vpd_excess_kpa,
       round((100.0 * avg(CASE WHEN fan1 THEN 1 ELSE 0 END) FILTER (WHERE both_high AND ventilating))::numeric, 1) AS fan1_pct,
       round((100.0 * avg(CASE WHEN fan2 THEN 1 ELSE 0 END) FILTER (WHERE both_high AND ventilating))::numeric, 1) AS fan2_pct,
       round((100.0 * avg(CASE WHEN fog THEN 1 ELSE 0 END) FILTER (WHERE both_high AND ventilating))::numeric, 1) AS fog_pct,
       round((100.0 * avg(CASE WHEN misting THEN 1 ELSE 0 END) FILTER (WHERE both_high AND ventilating))::numeric, 1) AS mister_pct
  FROM classified;
" 2>/dev/null || echo "(hot/dry utilization unavailable)"
echo "If hot_dry_vent_samples is high but fan2/fog/mister pct is low, prefer plans that keep moisture thresholds band-coupled and escalate cooling assist earlier; do not solve this by widening the compliance band. Sampled at 5-minute cadence over 24h for prompt speed."
echo ""

# ── 30c. PUBLIC SITE STATIC CONTEXT ───────────────────────────────
# This file is generated from the same Markdown source tree Quartz renders for
# lab.verdify.ai. Keeping it inside the planner packet makes the public contract,
# safety docs, known limits, crop/equipment references, and operating rules part
# of the same evidence surface readers inspect on the site.
echo "--- PUBLIC SITE STATIC CONTEXT (same source as lab.verdify.ai) ---"
if [ -s "$STATIC_CONTEXT_FILE" ]; then
  echo "source_file=${STATIC_CONTEXT_FILE}"
  echo "sha256=$(sha256sum "$STATIC_CONTEXT_FILE" | awk '{print $1}')"
  echo "bytes=$(wc -c < "$STATIC_CONTEXT_FILE" | tr -d ' ')"
  echo "lines=$(wc -l < "$STATIC_CONTEXT_FILE" | tr -d ' ')"
  echo ""
  cat "$STATIC_CONTEXT_FILE"
else
  echo "STATIC_CONTEXT_MISSING: ${STATIC_CONTEXT_FILE}"
  echo "Run scripts/gather-static-context.sh or scripts/publish-site-content.sh before trusting site-context claims."
fi
echo ""

# ── 31. CONTEXT COMPLETENESS SUMMARY (G10) ─────────────────────
# Tests each external dependency this script relies on and reports pass/fail
# with a one-line explanation of what degrades when that dep is down. If any
# dep is red, mention it in your Slack brief — otherwise Jason can't tell
# the difference between "quiet greenhouse" and "missing telemetry".
echo "=== CONTEXT COMPLETENESS ==="
_ok=0
_fail=0
_check() {
  local name="$1"
  local status="$2"
  local impact="$3"
  if [ "$status" = "ok" ]; then
    printf '  ✓ %-32s  %s\n' "$name" "$impact"
    _ok=$((_ok + 1))
  else
    printf '  ✗ %-32s  %s\n' "$name" "$impact"
    _fail=$((_fail + 1))
  fi
}

# DB: container/endpoint reachable + basic query (#24: via psql-verdify abstraction)
if verdify_psql -t -A -c "SELECT 1" >/dev/null 2>&1; then
  _check "timescaledb reachable" ok "all DB-backed sections above are current"
else
  _check "timescaledb reachable" fail "every DB section above returned '(unavailable)' or an empty row; DO NOT plan from this data"
fi

# Climate freshness: latest climate row within 10 minutes
climate_age_s=$("${DB[@]}" -c "SELECT EXTRACT(epoch FROM now() - max(ts))::int FROM climate;" 2>/dev/null | tr -d ' ')
if [ -n "${climate_age_s:-}" ] && [ "${climate_age_s:-99999}" -lt 600 ]; then
  _check "climate telemetry fresh" ok "indoor sensors reporting within the last ${climate_age_s}s"
else
  _check "climate telemetry fresh" fail "last climate row is ${climate_age_s:-unknown}s old; ESP32 may be offline — flag in brief"
fi

# Dispatcher target context: the planner must see controller-owned band edges
# and signed target deltas before it can safely tune ClimateIntent around them.
target_context_complete=$("${DB[@]}" -c "
WITH latest_climate AS (
  SELECT ts, temp_avg, vpd_avg
    FROM climate
   WHERE temp_avg IS NOT NULL
     AND vpd_avg IS NOT NULL
   ORDER BY ts DESC
   LIMIT 1
), latest AS (
  SELECT c.temp_avg,
         c.vpd_avg,
         fn_setpoint_at('temp_low', c.ts) AS sp_temp_low,
         fn_setpoint_at('temp_high', c.ts) AS sp_temp_high,
         fn_setpoint_at('vpd_low', c.ts) AS sp_vpd_low,
         fn_setpoint_at('vpd_high', c.ts) AS sp_vpd_high
    FROM latest_climate c
)
SELECT CASE
         WHEN count(*) = 1
          AND bool_and(temp_avg IS NOT NULL)
          AND bool_and(vpd_avg IS NOT NULL)
          AND bool_and(sp_temp_low IS NOT NULL)
          AND bool_and(sp_temp_high IS NOT NULL)
          AND bool_and(sp_vpd_low IS NOT NULL)
          AND bool_and(sp_vpd_high IS NOT NULL)
         THEN 1
         ELSE 0
       END
  FROM latest;
" 2>/dev/null | tr -d ' ' || true)
if [ "${target_context_complete:-0}" = "1" ]; then
  _check "dispatcher climate targets" ok "low/target/high and target deltas are available for read-only planner context"
else
  _check "dispatcher climate targets" fail "band target context is incomplete; do not tune ClimateIntent from missing targets"
fi

action_age_s=$("${DB[@]}" -c "SELECT COALESCE(EXTRACT(epoch FROM now() - max(ts))::int, 2147483647) FROM climate_action_log WHERE COALESCE(greenhouse_id, 'vallery') = '${GREENHOUSE_ID}';" 2>/dev/null | tr -d ' ' || true)
action_proof_missing=$("${DB[@]}" -c "
WITH latest AS (
  SELECT *
    FROM climate_action_log
   WHERE COALESCE(greenhouse_id, 'vallery') = '${GREENHOUSE_ID}'
   ORDER BY ts DESC
   LIMIT 1
)
SELECT array_to_string(
         array_remove(ARRAY[
           CASE WHEN climate_action IS NULL OR climate_action = '' THEN 'climate_action' END,
           CASE WHEN priority_axis IS NULL OR priority_axis = '' THEN 'priority_axis' END,
           CASE WHEN climate_intent_version IS NULL OR climate_intent_version = '' THEN 'climate_intent_version' END,
           CASE WHEN temp_low_f IS NULL THEN 'temp_low_f' END,
           CASE WHEN temp_target_f IS NULL THEN 'temp_target_f' END,
           CASE WHEN temp_high_f IS NULL THEN 'temp_high_f' END,
           CASE WHEN vpd_low_kpa IS NULL THEN 'vpd_low_kpa' END,
           CASE WHEN vpd_target_kpa IS NULL THEN 'vpd_target_kpa' END,
           CASE WHEN vpd_high_kpa IS NULL THEN 'vpd_high_kpa' END,
           CASE WHEN temp_target_delta_f IS NULL THEN 'temp_target_delta_f' END,
           CASE WHEN vpd_target_delta_kpa IS NULL THEN 'vpd_target_delta_kpa' END,
           CASE WHEN temp_band_error_f IS NULL THEN 'temp_band_error_f' END,
           CASE WHEN vpd_band_error_kpa IS NULL THEN 'vpd_band_error_kpa' END,
           CASE WHEN relay_truth IS NULL OR jsonb_typeof(relay_truth) <> 'object' OR relay_truth = '{}'::jsonb THEN 'relay_truth' END,
           CASE WHEN sensor_status IS NULL OR jsonb_typeof(sensor_status) <> 'object' OR sensor_status = '{}'::jsonb THEN 'sensor_status' END,
           CASE WHEN sensor_status->>'latest_climate_ts' IS NULL OR sensor_status->>'latest_climate_ts' = '' THEN 'sensor_status.latest_climate_ts' END,
           CASE
             WHEN CASE
               WHEN sensor_status->>'latest_climate_age_s' ~ '^[0-9]+$'
               THEN (sensor_status->>'latest_climate_age_s')::int < 300
               ELSE false
             END IS NOT true THEN 'sensor_status.latest_climate_age_s'
           END,
           CASE WHEN sensor_status->>'temp_avg_present' IS DISTINCT FROM 'true' THEN 'sensor_status.temp_avg_present' END,
           CASE WHEN sensor_status->>'vpd_avg_present' IS DISTINCT FROM 'true' THEN 'sensor_status.vpd_avg_present' END,
           CASE WHEN sensor_status->>'band_context_complete' IS DISTINCT FROM 'true' THEN 'sensor_status.band_context_complete' END
         ], NULL),
         ','
       )
  FROM latest;
" 2>/dev/null | tr -d '[:space:]' || true)
if [ -n "${action_age_s:-}" ] && [ "${action_age_s:-2147483647}" -lt 300 ] && [ -z "${action_proof_missing:-}" ]; then
  _check "climate action proof fresh" ok "controller action proof is ${action_age_s}s old and includes target deltas, relay truth, and sensor freshness"
else
  _check "climate action proof fresh" fail "action proof age=${action_age_s:-unknown}s missing=${action_proof_missing:-none}; planner must not infer why relays are off without fresh proof"
fi

# Scorecard function: non-zero rows for yesterday (data rolled up)
sc_rows=$("${DB[@]}" -c "SELECT count(*) FROM fn_planner_scorecard(CURRENT_DATE - 1);" 2>/dev/null | tr -d ' ')
if [ -n "${sc_rows:-}" ] && [ "${sc_rows:-0}" -ge 20 ]; then
  _check "yesterday scorecard rolled up" ok "${sc_rows} metrics available — previous_plan_validation can be computed"
else
  _check "yesterday scorecard rolled up" fail "only ${sc_rows:-0} metrics (need ~25); daily_summary snapshot may have failed overnight"
fi

# Weather forecast: distinct future hours plus fetched_at freshness.
fc_health=$("${DB[@]}" -F '|' -c "
SELECT COALESCE(EXTRACT(epoch FROM now() - max(fetched_at))::int, 2147483647) || '|' ||
       count(DISTINCT ts) FILTER (WHERE ts BETWEEN now() AND now() + interval '24 hours')
FROM weather_forecast;
" 2>/dev/null | tr -d ' ' || true)
fc_age_s="${fc_health%%|*}"
fc_rows="${fc_health##*|}"
if [ -n "${fc_rows:-}" ] && [ "${fc_rows:-0}" -gt 0 ] && [ -n "${fc_age_s:-}" ] && [ "${fc_age_s:-2147483647}" -lt 7200 ]; then
  _check "weather forecast fresh" ok "${fc_rows} distinct forecast hours in the next 24h; latest fetch ${fc_age_s}s ago"
else
  _check "weather forecast fresh" fail "${fc_rows:-0} distinct future forecast hours; latest fetch age=${fc_age_s:-unknown}s — stale forecast is system health, not a climate-regime deviation"
fi

# HA token readable (irrigation schedule + tunable readback sections)
if [ -n "${HA_TOKEN}" ]; then
  _check "HA token loaded" ok "sections 19 (tunable constraints) + HA sync queries above are current"
else
  _check "HA token loaded" fail "HA queries in sections 19+ returned empty; tunable min/max constraints unknown — stay inside ranges in your _PLANNER_KNOWLEDGE block"
fi

# Governing plan resolvable (section 15)
if [ -n "${GOVERNING_PLAN}" ]; then
  _check "governing plan identified" ok "${GOVERNING_PLAN} — previous_plan_validation has a target"
else
  _check "governing plan identified" fail "no governing plan for yesterday; previous_plan_validation must use null plan_id"
fi

# Validate-plan-coverage helper (section 28)
if [ -x "$SCRIPT_ROOT/validate-plan-coverage.sh" ]; then
  _check "validate-plan-coverage.sh present" ok "section 28 coverage report is reliable"
else
  _check "validate-plan-coverage.sh present" fail "plan-coverage section above silently skipped"
fi

# Static site context: source contract and public planning docs.
if [ -s "$STATIC_CONTEXT_FILE" ]; then
  _check "planner static site context" ok "public site Markdown is embedded above from ${STATIC_CONTEXT_FILE}"
else
  _check "planner static site context" fail "static site context missing; planner cannot see the same public contract readers see"
fi

echo ""
echo "summary: ${_ok} ok / ${_fail} failing (of $((_ok + _fail)) checks)"
if [ "${_fail}" -gt 0 ]; then
  echo "⚠ DEGRADED CONTEXT — mention the failing dependencies in your Slack brief so Jason can restore them."
fi
echo ""

echo "=== END PLANNING CONTEXT ==="
