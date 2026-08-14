#!/usr/bin/env bash
# Read-only production-data extractor for the planner efficacy audit.
# Raw outputs may contain operational telemetry and must stay outside Git.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

OUTDIR=$1
EXTRACTION_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
START_TS=${PLANNER_AUDIT_START_TS:-2025-08-15 00:00:00+00}
END_TS=${PLANNER_AUDIT_END_TS:-2026-08-14 06:00:00+00}
START_DATE=${PLANNER_AUDIT_START_DATE:-2025-08-15}
END_DATE=${PLANNER_AUDIT_END_DATE:-2026-08-14}

if [[ ! $START_TS =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}[[:space:]][0-9]{2}:[0-9]{2}:[0-9]{2}\+00$ ]] \
    || [[ ! $END_TS =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}[[:space:]][0-9]{2}:[0-9]{2}:[0-9]{2}\+00$ ]] \
    || [[ ! $START_DATE =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
    || [[ ! $END_DATE =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "audit bounds must use YYYY-MM-DD HH:MM:SS+00 / YYYY-MM-DD" >&2
    exit 2
fi

mkdir -p "$OUTDIR"
# Resolved relative to this script at runtime.
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib/psql-verdify.sh"

verdify_psql_stdin -X -v ON_ERROR_STOP=1 -q > "$OUTDIR/climate_15m.csv" <<SQL
BEGIN READ ONLY;
SET LOCAL statement_timeout='120s';
COPY (
WITH b AS (
  SELECT time_bucket(interval '15 minutes', ts) AS bucket,
         count(*) AS sample_count,
         avg(temp_avg) AS temp_f,
         avg(abs_humidity) AS abs_humidity_g_m3,
         avg(vpd_avg) AS vpd_kpa,
         avg(rh_avg) AS rh_pct,
         avg(temp_north) AS temp_north_f,
         avg(vpd_north) AS vpd_north_kpa,
         avg(rh_north) AS rh_north_pct,
         avg(temp_east) AS temp_east_f,
         avg(vpd_east) AS vpd_east_kpa,
         avg(rh_east) AS rh_east_pct,
         avg(temp_south) AS temp_south_f,
         avg(vpd_south) AS vpd_south_kpa,
         avg(rh_south) AS rh_south_pct,
         avg(temp_west) AS temp_west_f,
         avg(vpd_west) AS vpd_west_kpa,
         avg(rh_west) AS rh_west_pct,
         avg(outdoor_temp_f) AS outdoor_temp_f,
         avg(outdoor_rh_pct) AS outdoor_rh_pct,
         avg(solar_irradiance_w_m2) AS solar_w_m2,
         avg(wind_speed_avg_mph) AS wind_mph,
         avg(solar_altitude_deg) AS solar_altitude_deg,
         avg(house_temp_target_f) AS executed_temp_target_f,
         avg(house_vpd_target) AS executed_vpd_target_kpa
    FROM climate
   WHERE greenhouse_id = 'vallery'
     AND ts >= timestamptz '$START_TS'
     AND ts < timestamptz '$END_TS'
     AND temp_avg IS NOT NULL
     AND vpd_avg IS NOT NULL
   GROUP BY 1
), p AS (
  SELECT plan_id, planner_instance, trigger_id, interval_start, interval_end
    FROM v_plan_execution_intervals
)
SELECT b.*,
       fn_crop_band_value('house','temp_low',b.bucket) AS eval_temp_low_f,
       fn_crop_band_value('house','temp_target',b.bucket) AS eval_temp_target_f,
       fn_crop_band_value('house','temp_high',b.bucket) AS eval_temp_high_f,
       fn_crop_band_value('house','vpd_low',b.bucket) AS eval_vpd_low_kpa,
       fn_crop_band_value('house','vpd_target',b.bucket) AS eval_vpd_target_kpa,
       fn_crop_band_value('house','vpd_high',b.bucket) AS eval_vpd_high_kpa,
       p.plan_id, p.planner_instance, p.trigger_id
  FROM b
  LEFT JOIN p ON b.bucket >= p.interval_start AND b.bucket < p.interval_end
 ORDER BY b.bucket
) TO STDOUT WITH CSV HEADER;
COMMIT;
SQL

verdify_psql_stdin -X -v ON_ERROR_STOP=1 -q > "$OUTDIR/equipment_transitions.csv" <<SQL
BEGIN READ ONLY;
SET LOCAL statement_timeout='120s';
COPY (
WITH names(equipment) AS (VALUES
 ('heat1'),('heat2'),('vent'),('fan1'),('fan2'),('fog'),
 ('mister_south'),('mister_west'),('mister_center'),
 ('grow_light_main'),('grow_light_grow'),('drip_wall'),('drip_center'),
 ('drip_wall_fert'),('drip_center_fert'),('fert_master_valve')
), seed AS (
  SELECT DISTINCT ON (e.equipment) e.ts, e.equipment, e.state
    FROM equipment_state e JOIN names n USING (equipment)
   WHERE e.greenhouse_id='vallery' AND e.ts < timestamptz '$START_TS'
   ORDER BY e.equipment, e.ts DESC
), window_rows AS (
  SELECT e.ts, e.equipment, bool_or(e.state) AS state
    FROM equipment_state e JOIN names n USING (equipment)
   WHERE e.greenhouse_id='vallery'
     AND e.ts >= timestamptz '$START_TS'
     AND e.ts < timestamptz '$END_TS'
   GROUP BY e.ts, e.equipment
), combined AS (
  SELECT * FROM seed UNION ALL SELECT * FROM window_rows
), changed AS (
  SELECT *, lag(state) OVER (PARTITION BY equipment ORDER BY ts) AS prior_state
    FROM combined
)
SELECT ts, equipment, state
  FROM changed
 WHERE prior_state IS DISTINCT FROM state OR prior_state IS NULL
 ORDER BY ts, equipment
) TO STDOUT WITH CSV HEADER;
COMMIT;
SQL

verdify_psql_stdin -X -v ON_ERROR_STOP=1 -q > "$OUTDIR/daily_outcomes.csv" <<SQL
BEGIN READ ONLY;
SET LOCAL statement_timeout='120s';
COPY (
WITH z AS (
 SELECT date,
        avg(graded_temp_compliance_pct) FILTER (WHERE NOT proxy_flag) AS zone_graded_temp_compliance_pct,
        avg(graded_vpd_compliance_pct) FILTER (WHERE NOT proxy_flag) AS zone_graded_vpd_compliance_pct,
        avg(raw_compliance_pct) FILTER (WHERE NOT proxy_flag) AS zone_raw_compliance_pct,
        sum(graded_stress_hours_heat + graded_stress_hours_cold
            + graded_stress_hours_vpd_high + graded_stress_hours_vpd_low)
            FILTER (WHERE NOT proxy_flag) AS zone_graded_stress_hours,
        sum(controller_miss_min) FILTER (WHERE NOT proxy_flag) AS controller_miss_zone_min,
        sum(unachievable_min) FILTER (WHERE NOT proxy_flag) AS unachievable_zone_min,
        count(*) FILTER (WHERE NOT proxy_flag) AS measured_zone_count
   FROM daily_zone_compliance GROUP BY date
), wm AS (
 SELECT (day AT TIME ZONE 'America/Denver')::date AS date,
        bool_and(available_for_scoring) AS meter_available_for_scoring,
        sum(used_gal) AS meter_used_gal,
        sum(gap_events) AS meter_gap_events,
        sum(reset_events) AS meter_reset_events
   FROM v_water_meter_daily GROUP BY 1
)
SELECT d.date, d.greenhouse_id,
       d.compliance_v2_attributable_pct, d.graded_temp_compliance_pct,
       d.graded_vpd_compliance_pct,
       d.graded_stress_hours_heat + d.graded_stress_hours_cold
         + d.graded_stress_hours_vpd_high + d.graded_stress_hours_vpd_low AS graded_stress_hours,
       z.zone_graded_temp_compliance_pct, z.zone_graded_vpd_compliance_pct,
       z.zone_raw_compliance_pct, z.zone_graded_stress_hours,
       z.controller_miss_zone_min, z.unachievable_zone_min, z.measured_zone_count,
       d.temp_avg, d.vpd_avg, d.outdoor_temp_min, d.outdoor_temp_max,
       d.runtime_fan1_min, d.runtime_fan2_min, d.runtime_heat1_min, d.runtime_heat2_min,
       d.runtime_fog_min, d.runtime_vent_min,
       d.runtime_mister_south_h, d.runtime_mister_west_h, d.runtime_mister_center_h,
       d.cycles_fan1, d.cycles_fan2, d.cycles_heat1, d.cycles_heat2, d.cycles_fog, d.cycles_vent,
       wm.meter_used_gal, wm.meter_available_for_scoring, wm.meter_gap_events, wm.meter_reset_events,
       w.quality_filtered_meter_gal, w.climate_wetting_gal, w.attributed_gal,
       w.resource_quality AS water_quality, w.available_for_scoring AS water_eligible,
       e.modeled_kwh, e.modeled_kwh_low, e.modeled_kwh_high, e.runtime_coverage_pct,
       e.model_quality AS energy_quality, e.available_for_scoring AS energy_eligible
  FROM daily_summary d
  LEFT JOIN z ON z.date=d.date
  LEFT JOIN wm ON wm.date=d.date
  LEFT JOIN v_water_attribution_daily w ON w.date=d.date AND w.greenhouse_id=d.greenhouse_id
  LEFT JOIN v_runtime_energy_daily e ON e.date=d.date AND e.greenhouse_id=d.greenhouse_id
 WHERE d.greenhouse_id='vallery'
   AND d.date >= date '$START_DATE'
   AND d.date < date '$END_DATE'
 ORDER BY d.date
) TO STDOUT WITH CSV HEADER;
COMMIT;
SQL

verdify_psql_stdin -X -v ON_ERROR_STOP=1 -q > "$OUTDIR/plans.csv" <<SQL
BEGIN READ ONLY;
SET LOCAL statement_timeout='120s';
COPY (
SELECT plan_id, created_at, valid_from, expires_at, lifecycle_status, trigger_id,
       planner_instance, validated_at, outcome_score, anchor_score, guardrail_penalty,
       hypothesis_structured IS NOT NULL AS has_structured_hypothesis,
       climate_intents IS NOT NULL AS has_climate_intent
  FROM plan_journal
 WHERE created_at >= timestamptz '2026-03-24 00:00:00+00'
   AND created_at < timestamptz '$END_TS'
 ORDER BY created_at
) TO STDOUT WITH CSV HEADER;
COMMIT;
SQL

{
    printf 'extraction_started_at_utc=%s\n' "$EXTRACTION_STARTED_AT"
    printf 'extraction_completed_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'snapshot_mode=sequential_read_only_transactions\n'
    printf 'start_ts=%s\nend_ts=%s\n' "$START_TS" "$END_TS"
    sha256sum "$OUTDIR"/*.csv
    wc -l "$OUTDIR"/*.csv
} > "$OUTDIR/input-manifest.txt"

echo "Planner audit inputs written to $OUTDIR (raw telemetry; do not commit)."
