#!/usr/bin/env bash
# Export scrubbed public sample datasets for launch readers.
#
# The files intentionally exclude device identifiers, local IPs, trigger UUIDs,
# alert routing, hostnames, and raw sensor entity names.

set -euo pipefail

OUT_DIR=${1:-/mnt/iris/verdify-vault/website/static/data}
# #24: DB access via the shared psql-verdify abstraction (docker-exec default
# preserves prior VM argv). Heredoc SQL needs stdin -> VERDIFY_DOCKER_STDIN=1.
. "$(dirname "${BASH_SOURCE[0]}")/lib/psql-verdify.sh"
mapfile -t DB < <(VERDIFY_DOCKER_STDIN=1 verdify_psql_cmd)
DB+=(-q -v ON_ERROR_STOP=1)

mkdir -p "$OUT_DIR"

# Graded, controller-attributable compliance (band-compliance design §6-§7) is
# dual-written into daily_summary.compliance_v2_attributable_pct once migration
# 146/147 land. Probe for the column so the public dataset gains a graded column
# automatically post-migration, and stays valid (column absent -> omitted) today.
GRADED_COL_PRESENT=$(verdify_psql_stdin -tAc \
  "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='daily_summary' AND column_name='compliance_v2_attributable_pct');")
if [ "$GRADED_COL_PRESENT" = "t" ]; then
  GRADED_SELECT="    round(ds.compliance_v2_attributable_pct::numeric, 1) AS graded_compliance_attributable_pct,"
else
  GRADED_SELECT="    NULL::numeric AS graded_compliance_attributable_pct,"
fi

"${DB[@]}" >"$OUT_DIR/verdify-sample-7d-climate.csv" <<'SQL'
COPY (
  WITH climate_5m AS (
    SELECT
      time_bucket('5 minutes', ts) AS bucket_utc,
      round(avg(temp_avg)::numeric, 2) AS temp_avg_f,
      round(avg(rh_avg)::numeric, 2) AS rh_avg_pct,
      round(avg(vpd_avg)::numeric, 3) AS vpd_avg_kpa,
      round(avg(outdoor_temp_f)::numeric, 2) AS outdoor_temp_f,
      round(avg(outdoor_rh_pct)::numeric, 2) AS outdoor_rh_pct,
      round(avg(solar_irradiance_w_m2)::numeric, 1) AS solar_irradiance_w_m2,
      NULL::numeric AS dli_today_mol_m2,
      round(max(water_total_gal)::numeric, 3) AS water_total_gal,
      round(max(mister_water_today)::numeric, 3) AS mister_water_today_gal,
      round(avg(hydro_ph)::numeric, 2) AS hydro_ph,
      round(avg(hydro_ec_us_cm)::numeric, 0) AS hydro_ec_us_cm,
      round(avg(soil_moisture_south_1)::numeric, 2) AS soil_moisture_south_1_pct,
      round(avg(soil_temp_south_1)::numeric, 2) AS soil_temp_south_1_f,
      count(*) AS source_samples
    FROM climate
    WHERE ts >= now() - interval '7 days'
    GROUP BY 1
  )
  SELECT
    to_char(bucket_utc AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI') AS bucket_local,
    temp_avg_f,
    rh_avg_pct,
    vpd_avg_kpa,
    outdoor_temp_f,
    outdoor_rh_pct,
    solar_irradiance_w_m2,
    dli_today_mol_m2,
    water_total_gal,
    mister_water_today_gal,
    hydro_ph,
    hydro_ec_us_cm,
    soil_moisture_south_1_pct,
    soil_temp_south_1_f,
    source_samples
  FROM climate_5m
  ORDER BY bucket_utc
) TO STDOUT WITH CSV HEADER;
SQL

"${DB[@]}" >"$OUT_DIR/verdify-sample-30d-plan-outcomes.csv" <<SQL
COPY (
  SELECT
    m.date,
    to_char(m.created_at AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI') AS created_local,
    m.plan_id,
    round(m.temp_mae_f, 2) AS temp_mae_f,
    round(m.vpd_mae_kpa, 3) AS vpd_mae_kpa,
    round(m.solar_mae_w, 1) AS solar_mae_w,
    round(m.compliance_pct::numeric, 1) AS compliance_pct,
${GRADED_SELECT}
    round(m.temp_compliance_pct::numeric, 1) AS temp_compliance_pct,
    round(m.vpd_compliance_pct::numeric, 1) AS vpd_compliance_pct,
    round(m.stress_hours_heat::numeric, 2) AS stress_hours_heat,
    round(m.stress_hours_vpd_high::numeric, 2) AS stress_hours_vpd_high,
    round(m.stress_hours_cold::numeric, 2) AS stress_hours_cold,
    round(m.stress_hours_vpd_low::numeric, 2) AS stress_hours_vpd_low,
    round(m.water_used_gal::numeric, 2) AS water_used_gal,
    round(m.mister_water_gal::numeric, 2) AS mister_water_gal,
    round(m.kwh::numeric, 2) AS kwh,
    round(m.therms_estimated::numeric, 3) AS therms_estimated,
    round(m.cost_total::numeric, 2) AS cost_total_usd,
    m.outcome_score,
    m.hypothesis,
    m.expected_outcome,
    m.actual_outcome
  FROM v_forecast_plan_outcome_mart m
  LEFT JOIN daily_summary ds ON ds.date = m.date
  WHERE m.date >= current_date - interval '30 days'
  ORDER BY m.date, m.created_at
) TO STDOUT WITH CSV HEADER;
SQL
perl -0pi -e 's/\bOpenClaw\/Iris\b/planner/g; s/\bIris\b(?!-)/AI planning agent/g; s/\bOpenClaw\b/planner gateway/g; s/\blocal Gemma context overflow\b/planner context overflow/gi; s/\blocal Gemma overflow\b/planner context overflow/gi; s/\blocal Gemma\b/planner/gi; s/\bGemma\b/planner/g; s/ESP32 v2 band-first controller/ESP32 band-first controller/g' "$OUT_DIR/verdify-sample-30d-plan-outcomes.csv"
"${PYTHON:-python3}" "$(dirname "${BASH_SOURCE[0]}")/redact-public-output.py" \
  "$OUT_DIR/verdify-sample-30d-plan-outcomes.csv"

cat >"$OUT_DIR/verdify-sample-readme.txt" <<EOF
Verdify public sample dataset
Generated: $(date -Is)

Files:
- verdify-sample-7d-climate.csv: 5-minute greenhouse climate/weather/hydro/soil sample for the most recent 7 days.
- verdify-sample-30d-plan-outcomes.csv: plan/outcome scorecard rows for the most recent 30 days.

Column notes:
- compliance_pct is the binary, house-level both-axis compliance: the share of samples where house-average
  temperature and VPD were both inside the single served control band. It is not a per-zone or per-plant
  guarantee, and a reading just outside the band scores the same as one far outside.
- graded_compliance_attributable_pct is the graded, per-zone, feasibility-aware controller-attributable
  compliance (band-compliance design). It is blank until the graded compliance engine is promoted, then
  populated automatically by this export.
- dli_today_mol_m2 is intentionally blank. Interior crop DLI is unavailable because the physical interior
  light sensor is broken. Reason: interior_light_sensor_broken; provenance:
  legacy_invalid_exterior_proxy_plus_fixture_estimate; validity revision:
  dli-validity-v1, 2024-01-01T00:00:00Z to open. Outdoor irradiance and fixture runtime are not relabeled as
  measured interior crop DLI; qualified-light-minute control remains independent.

Timestamps are rendered in America/Denver local time. The export intentionally omits local IPs, device IDs, trigger UUIDs, alert channels, hostnames, and raw sensor entity names.
EOF

printf 'Wrote public sample datasets to %s\n' "$OUT_DIR"
