#!/usr/bin/env bash
# Read-only extractor for the current-firmware second pass.
# Raw outputs may contain operational telemetry and must stay outside Git.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

OUTDIR=$1
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EPOCH_START='2026-07-10 21:03:12.991915+00'
OUTCOME_START='2026-07-11 06:00:00+00'
OUTCOME_END='2026-08-14 06:00:00+00'

PLANNER_AUDIT_START_TS=$OUTCOME_START \
PLANNER_AUDIT_END_TS=$OUTCOME_END \
PLANNER_AUDIT_START_DATE=2026-07-11 \
PLANNER_AUDIT_END_DATE=2026-08-14 \
PLANNER_AUDIT_PLAN_START_TS=$EPOCH_START \
    "$SCRIPT_DIR/extract.sh" "$OUTDIR"

# Resolved relative to this script at runtime.
# shellcheck disable=SC1091
. "$SCRIPT_DIR/../../scripts/lib/psql-verdify.sh"

verdify_psql_stdin -X -v ON_ERROR_STOP=1 -q > "$OUTDIR/forecast_response.csv" <<SQL
BEGIN READ ONLY;
SET LOCAL statement_timeout='120s';
COPY (
WITH plans AS (
  SELECT plan_id, created_at, climate_intents
    FROM plan_journal
   WHERE greenhouse_id = 'vallery'
     AND created_at >= timestamptz '$EPOCH_START'
     AND created_at < timestamptz '$OUTCOME_END'
     AND climate_intents IS NOT NULL
), intent AS (
  SELECT p.plan_id, p.created_at,
         avg((i.elem->'materialized_params'->>'cool_stage2_over_high_f')::float)
           AS cool_stage2_over_high_f,
         avg((i.elem->'materialized_params'->>'sw_cool_all_fans_at_high_enabled')::float)
           AS all_fans_enabled,
         avg((i.elem->'materialized_params'->>'fog_escalation_kpa')::float)
           AS fog_escalation_kpa,
         avg((i.elem->'materialized_params'->>'mister_engage_kpa')::float)
           AS mister_engage_kpa,
         avg((i.elem->'materialized_params'->>'mister_all_kpa')::float)
           AS mister_all_kpa,
         avg((i.elem->'materialized_params'->>'mister_all_delay_s')::float)
           AS mister_all_delay_s,
         avg((i.elem->'materialized_params'->>'mister_pulse_gap_s')::float)
           AS mister_pulse_gap_s,
         avg((i.elem->'materialized_params'->>'mister_pulse_on_s')::float)
           AS mister_pulse_on_s,
         avg((i.elem->'materialized_params'->>'mister_water_budget_gal')::float)
           AS mister_water_budget_gal,
         avg((i.elem->'materialized_params'->>'min_fog_on_s')::float)
           AS min_fog_on_s,
         avg((i.elem->'climate_intent'->>'resource_sensitivity')::float)
           AS resource_sensitivity,
         avg((i.elem->'climate_intent'->>'relay_churn_penalty')::float)
           AS relay_churn_penalty
    FROM plans p
   CROSS JOIN LATERAL jsonb_array_elements(p.climate_intents) AS i(elem)
   WHERE (i.elem->>'ts')::timestamptz >= p.created_at
     AND (i.elem->>'ts')::timestamptz < p.created_at + interval '24 hours'
   GROUP BY p.plan_id, p.created_at
), forecast AS (
  SELECT p.plan_id, count(*) AS forecast_hours,
         avg(f.temp_f) AS forecast_temp_mean_f,
         max(f.temp_f) AS forecast_temp_max_f,
         avg(f.vpd_kpa) AS forecast_vpd_mean_kpa,
         max(f.vpd_kpa) AS forecast_vpd_max_kpa,
         avg(f.solar_w_m2) AS forecast_solar_mean_w_m2,
         max(f.solar_w_m2) AS forecast_solar_max_w_m2
    FROM plans p
   CROSS JOIN LATERAL (
     SELECT DISTINCT ON (wf.ts)
            wf.ts, wf.temp_f, wf.vpd_kpa, wf.solar_w_m2
       FROM weather_forecast wf
      WHERE wf.greenhouse_id = 'vallery'
        AND wf.fetched_at <= p.created_at
        AND wf.ts >= p.created_at
        AND wf.ts < p.created_at + interval '24 hours'
      ORDER BY wf.ts, wf.fetched_at DESC
   ) f
   GROUP BY p.plan_id
), current_state AS (
  SELECT p.plan_id, c.ts AS current_ts,
         c.temp_avg AS current_temp_f,
         c.vpd_avg AS current_vpd_kpa,
         c.solar_irradiance_w_m2 AS current_solar_w_m2,
         c.outdoor_temp_f AS current_outdoor_temp_f,
         extract(hour FROM p.created_at AT TIME ZONE 'America/Denver')
           + extract(minute FROM p.created_at) / 60.0 AS local_hour
    FROM plans p
   CROSS JOIN LATERAL (
     SELECT ts, temp_avg, vpd_avg, solar_irradiance_w_m2, outdoor_temp_f
       FROM climate
      WHERE greenhouse_id = 'vallery' AND ts <= p.created_at
      ORDER BY ts DESC
      LIMIT 1
   ) c
)
SELECT i.*, f.*, c.current_ts, c.current_temp_f, c.current_vpd_kpa,
       c.current_solar_w_m2, c.current_outdoor_temp_f, c.local_hour
  FROM intent i
  JOIN forecast f USING (plan_id)
  JOIN current_state c USING (plan_id)
 WHERE f.forecast_hours >= 20
 ORDER BY i.created_at
) TO STDOUT WITH CSV HEADER;
COMMIT;
SQL

verdify_psql_stdin -X -v ON_ERROR_STOP=1 -q > "$OUTDIR/waypoints.csv" <<SQL
BEGIN READ ONLY;
SET LOCAL statement_timeout='120s';
COPY (
WITH plans AS (
  SELECT p.plan_id, p.created_at, p.climate_intents,
         e.interval_start, e.interval_end
    FROM plan_journal p
    JOIN v_plan_execution_intervals e USING (plan_id)
   WHERE p.greenhouse_id = 'vallery'
     AND p.created_at >= timestamptz '$EPOCH_START'
     AND p.created_at < timestamptz '$OUTCOME_END'
     AND p.climate_intents IS NOT NULL
)
SELECT p.plan_id, p.created_at AS plan_created_at,
       p.interval_start, p.interval_end,
       i.ordinality AS waypoint_ordinal,
       (i.elem->>'ts')::timestamptz AS waypoint_ts,
       (i.elem->>'ts')::timestamptz >= p.interval_start
         AND (i.elem->>'ts')::timestamptz < p.interval_end
         AS scheduled_while_governing,
       p.interval_end <= (i.elem->>'ts')::timestamptz
         AS superseded_before_scheduled,
       (i.elem->>'ts')::timestamptz < p.interval_start
         AS already_due_at_creation,
       fn_crop_band_value('house', 'vpd_high', (i.elem->>'ts')::timestamptz)
         AS band_vpd_high_at_waypoint,
       i.elem->'climate_intent' AS climate_intent,
       i.elem->'materialized_params' AS materialized_params
  FROM plans p
 CROSS JOIN LATERAL jsonb_array_elements(p.climate_intents)
      WITH ORDINALITY AS i(elem, ordinality)
 ORDER BY p.created_at, i.ordinality
) TO STDOUT WITH CSV HEADER;
COMMIT;
SQL

verdify_psql_stdin -X -v ON_ERROR_STOP=1 -q > "$OUTDIR/forecast_vpd_accuracy.csv" <<SQL
BEGIN READ ONLY;
SET LOCAL statement_timeout='120s';
COPY (
WITH observed AS (
  SELECT time_bucket('1 hour', ts) AS ts,
         avg(vpd_avg) AS indoor_vpd,
         avg(outdoor_temp_f) AS outdoor_temp_f,
         avg(outdoor_rh_pct) AS outdoor_rh_pct
    FROM climate
   WHERE greenhouse_id = 'vallery'
     AND ts >= timestamptz '2026-07-15 06:00:00+00'
     AND ts < timestamptz '$OUTCOME_END'
   GROUP BY 1
), paired AS (
  SELECT CASE
           WHEN extract(epoch FROM (f.ts - f.fetched_at)) / 3600.0 < 6 THEN '00-06h'
           WHEN extract(epoch FROM (f.ts - f.fetched_at)) / 3600.0 < 24 THEN '06-24h'
           WHEN extract(epoch FROM (f.ts - f.fetched_at)) / 3600.0 < 48 THEN '24-48h'
           ELSE '48h+'
         END AS lead_bucket,
         f.vpd_kpa - o.indoor_vpd AS wrong_indoor_error,
         f.vpd_kpa - (
           0.6108 * exp(
             17.27 * ((o.outdoor_temp_f - 32) * 5 / 9)
             / (((o.outdoor_temp_f - 32) * 5 / 9) + 237.3)
           ) * (1 - o.outdoor_rh_pct / 100.0)
         ) AS correct_outdoor_error
    FROM weather_forecast f
    JOIN observed o USING (ts)
   WHERE f.greenhouse_id = 'vallery'
     AND f.fetched_at <= f.ts
     AND f.vpd_kpa IS NOT NULL
)
SELECT lead_bucket, count(*) AS samples,
       avg(wrong_indoor_error) AS wrong_indoor_bias_kpa,
       avg(abs(wrong_indoor_error)) AS wrong_indoor_mae_kpa,
       avg(correct_outdoor_error) AS correct_outdoor_bias_kpa,
       avg(abs(correct_outdoor_error)) AS correct_outdoor_mae_kpa
  FROM paired
 GROUP BY lead_bucket
 ORDER BY CASE lead_bucket
            WHEN '00-06h' THEN 1 WHEN '06-24h' THEN 2
            WHEN '24-48h' THEN 3 ELSE 4
          END
) TO STDOUT WITH CSV HEADER;
COMMIT;
SQL

verdify_psql_stdin -X -v ON_ERROR_STOP=1 -q > "$OUTDIR/effective_tunables.csv" <<SQL
BEGIN READ ONLY;
SET LOCAL statement_timeout='120s';
COPY (
WITH settings(parameter, compiled_default) AS (VALUES
  ('cool_stage2_over_high_f', 1.0::float),
  ('sw_cool_all_fans_at_high_enabled', 0.0::float),
  ('fog_escalation_kpa', 0.4::float),
  ('mister_engage_kpa', 1.6::float),
  ('mister_all_kpa', 1.9::float),
  ('mister_all_delay_s', 300.0::float),
  ('mister_pulse_gap_s', 45.0::float),
  ('mister_pulse_on_s', 60.0::float),
  ('mister_water_budget_gal', 300.0::float),
  ('min_fog_on_s', 60.0::float),
  ('min_fog_off_s', 60.0::float),
  ('night_vpd_bias_kpa', 0.0::float)
)
SELECT s.parameter, s.compiled_default, count(x.value) AS samples,
       avg(x.value) AS mean_value,
       min(x.value) AS min_value, max(x.value) AS max_value,
       100.0 * avg((
         x.value < s.compiled_default - greatest(0.0001, abs(s.compiled_default) * 0.00001)
       )::int) AS pct_below_default,
       100.0 * avg((
         abs(x.value - s.compiled_default)
           <= greatest(0.0001, abs(s.compiled_default) * 0.00001)
       )::int) AS pct_at_default,
       100.0 * avg((
         x.value > s.compiled_default + greatest(0.0001, abs(s.compiled_default) * 0.00001)
       )::int) AS pct_above_default
  FROM settings s
  LEFT JOIN setpoint_snapshot x
    ON x.parameter = s.parameter
   AND x.greenhouse_id = 'vallery'
   AND x.ts >= timestamptz '$OUTCOME_START'
   AND x.ts < timestamptz '$OUTCOME_END'
 GROUP BY s.parameter, s.compiled_default
 ORDER BY s.parameter
) TO STDOUT WITH CSV HEADER;
COMMIT;
SQL

verdify_psql_stdin -X -v ON_ERROR_STOP=1 -q > "$OUTDIR/trigger_outcomes.csv" <<SQL
BEGIN READ ONLY;
SET LOCAL statement_timeout='120s';
COPY (
SELECT expected_action, status, terminal_action, count(*) AS triggers
  FROM planner_trigger_ledger
 WHERE greenhouse_id = 'vallery'
   AND expected_at >= timestamptz '$EPOCH_START'
   AND expected_at < timestamptz '$OUTCOME_END'
 GROUP BY expected_action, status, terminal_action
 ORDER BY expected_action, status, terminal_action
) TO STDOUT WITH CSV HEADER;
COMMIT;
SQL

{
    printf 'firmware_version=%s\n' '2026.7.10.1500.09ee886'
    printf 'firmware_epoch_start=%s\n' "$EPOCH_START"
    printf 'outcome_start=%s\noutcome_end=%s\n' "$OUTCOME_START" "$OUTCOME_END"
    sha256sum "$OUTDIR"/*.csv
    wc -l "$OUTDIR"/*.csv
} >> "$OUTDIR/input-manifest.txt"

echo "Current-firmware planner inputs written to $OUTDIR (raw telemetry; do not commit)."
