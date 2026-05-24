#!/usr/bin/env bash
# Backfill PR3 AI moisture-stress Tier 1 defaults into active routine plans.
#
# Dry-run by default. Apply with:
#   APPLY=1 scripts/backfill-ai-moisture-stress-defaults.sh
#
# Run only after deploying the PR3 registry/services from the matching branch.
# It is safe before the ESP32 OTA because the dispatcher gates these params
# until firmware exposes the matching ESPHome entities/cfg readbacks.
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-verdify-timescaledb}"
DB_USER="${DB_USER:-verdify}"
DB_NAME="${DB_NAME:-verdify}"
GREENHOUSE_ID="${GREENHOUSE_ID:-vallery}"
APPLY="${APPLY:-0}"

PSQL=(docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -A -F '|')
PSQL+=(-v greenhouse_id="$GREENHOUSE_ID")

if [ "$APPLY" = "1" ]; then
  echo "Applying PR3 AI moisture-stress default backfill for greenhouse_id=$GREENHOUSE_ID"
  "${PSQL[@]}" <<SQL
WITH defaults(parameter, value) AS (
  VALUES
    ('sw_direct_wet_stress_override_enabled', 0.0),
    ('direct_wet_stress_vpd_margin_kpa', 0.05),
    ('direct_wet_stress_min_dew_margin_f', 8.0),
    ('direct_wet_stress_latest_hour', 22.0),
    ('sw_fog_stress_window_extend_enabled', 0.0),
    ('fog_stress_window_latest_hour', 22.0),
    ('fog_stress_min_dew_margin_f', 8.0)
),
routine_plans AS (
  SELECT plan_id
    FROM setpoint_plan
   WHERE is_active = true
     AND ts > now()
     AND greenhouse_id = :'greenhouse_id'
     AND plan_id IS NOT NULL
     AND plan_id NOT LIKE 'iris-oneshot-%'
   GROUP BY plan_id
),
targets AS (
  SELECT DISTINCT ON (sp.plan_id, sp.ts)
         sp.plan_id,
         sp.ts,
         sp.greenhouse_id,
         sp.trigger_id,
         sp.planner_instance
    FROM setpoint_plan sp
    JOIN routine_plans rp ON rp.plan_id = sp.plan_id
   WHERE sp.is_active = true
     AND sp.parameter <> 'plan_metadata'
     AND sp.greenhouse_id = :'greenhouse_id'
   ORDER BY sp.plan_id, sp.ts, sp.created_at DESC NULLS LAST
),
missing AS (
  SELECT t.ts,
         d.parameter,
         d.value,
         t.plan_id,
         t.greenhouse_id,
         t.trigger_id,
         t.planner_instance
    FROM targets t
    CROSS JOIN defaults d
   WHERE NOT EXISTS (
         SELECT 1
           FROM setpoint_plan sp
          WHERE sp.plan_id = t.plan_id
            AND sp.ts = t.ts
            AND sp.parameter = d.parameter
            AND sp.is_active = true
       )
),
upserted AS (
  INSERT INTO setpoint_plan (
         ts,
         parameter,
         value,
         plan_id,
         source,
         reason,
         created_at,
         is_active,
         greenhouse_id,
         trigger_id,
         planner_instance
  )
  SELECT ts,
         parameter,
         value,
         plan_id,
         'iris',
         'PR3 default backfill for AI moisture stress contract alignment',
         now(),
         true,
         greenhouse_id,
         trigger_id,
         planner_instance
    FROM missing
  ON CONFLICT (ts, parameter, plan_id) DO UPDATE
        SET value = EXCLUDED.value,
            source = EXCLUDED.source,
            reason = EXCLUDED.reason,
            created_at = EXCLUDED.created_at,
            is_active = true,
            greenhouse_id = EXCLUDED.greenhouse_id,
            trigger_id = EXCLUDED.trigger_id,
            planner_instance = EXCLUDED.planner_instance
      WHERE setpoint_plan.is_active = false
  RETURNING plan_id, ts, parameter, value
)
SELECT 'upserted_rows' AS metric, count(*)::text AS value FROM upserted
UNION ALL
SELECT 'plans_touched', count(DISTINCT plan_id)::text FROM upserted
UNION ALL
SELECT 'transitions_touched', count(DISTINCT (plan_id, ts))::text FROM upserted;
SQL
else
  echo "Dry run only. Re-run with APPLY=1 after PR3 services are deployed."
  "${PSQL[@]}" <<SQL
WITH defaults(parameter, value) AS (
  VALUES
    ('sw_direct_wet_stress_override_enabled', 0.0),
    ('direct_wet_stress_vpd_margin_kpa', 0.05),
    ('direct_wet_stress_min_dew_margin_f', 8.0),
    ('direct_wet_stress_latest_hour', 22.0),
    ('sw_fog_stress_window_extend_enabled', 0.0),
    ('fog_stress_window_latest_hour', 22.0),
    ('fog_stress_min_dew_margin_f', 8.0)
),
routine_plans AS (
  SELECT plan_id
    FROM setpoint_plan
   WHERE is_active = true
     AND ts > now()
     AND greenhouse_id = :'greenhouse_id'
     AND plan_id IS NOT NULL
     AND plan_id NOT LIKE 'iris-oneshot-%'
   GROUP BY plan_id
),
targets AS (
  SELECT DISTINCT ON (sp.plan_id, sp.ts)
         sp.plan_id,
         sp.ts,
         sp.greenhouse_id,
         sp.trigger_id,
         sp.planner_instance
    FROM setpoint_plan sp
    JOIN routine_plans rp ON rp.plan_id = sp.plan_id
   WHERE sp.is_active = true
     AND sp.parameter <> 'plan_metadata'
     AND sp.greenhouse_id = :'greenhouse_id'
   ORDER BY sp.plan_id, sp.ts, sp.created_at DESC NULLS LAST
),
missing AS (
  SELECT t.ts,
         d.parameter,
         d.value,
         t.plan_id,
         t.greenhouse_id,
         t.trigger_id,
         t.planner_instance
    FROM targets t
    CROSS JOIN defaults d
   WHERE NOT EXISTS (
         SELECT 1
           FROM setpoint_plan sp
          WHERE sp.plan_id = t.plan_id
            AND sp.ts = t.ts
            AND sp.parameter = d.parameter
            AND sp.is_active = true
       )
)
SELECT 'candidate_rows' AS metric, count(*)::text AS value FROM missing
UNION ALL
SELECT 'candidate_plans', count(DISTINCT plan_id)::text FROM missing
UNION ALL
SELECT 'candidate_transitions', count(DISTINCT (plan_id, ts))::text FROM missing
UNION ALL
SELECT 'candidate_parameters', coalesce(string_agg(DISTINCT parameter, ',' ORDER BY parameter), '') FROM missing
UNION ALL
SELECT 'candidate_plan_ids', coalesce(string_agg(DISTINCT plan_id, ',' ORDER BY plan_id), '') FROM missing;
SQL
fi
