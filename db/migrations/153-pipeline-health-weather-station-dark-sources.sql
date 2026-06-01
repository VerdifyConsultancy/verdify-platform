-- Migration 153: Extend v_data_pipeline_health to cover weather_station and to
--                explicitly annotate intentionally-dark sources (issue #42,
--                audit §7-#8 / P1).
--
-- CONTEXT
-- `SELECT DISTINCT source FROM v_data_pipeline_health` returns only 8 sources
-- (climate, hydro, equipment, diagnostics, energy, setpoints, forecast,
-- daily_summary). weather_station -- the raw Tempest/Panorama feed, live but
-- intermittent -- is the genuine gap: a fully dead weather_station pipeline is
-- currently invisible to the freshness view. Separately, two tables look "stale"
-- forever by design and must not be mistaken for broken pipelines:
--   * esp32_logs       -- intentionally dark (firmware log ingestion off; 90bc358)
--   * irrigation_log   -- intentionally retired (migration 134; canonical
--                         irrigation/fertigation is reconstructed from
--                         equipment_state in v_irrigation_fertigation_runs).
-- The view should monitor weather_station like the other live sources AND carry
-- an explicit annotation on the two intentional ones, so a dead live pipeline
-- can never silently slip in disguised as an "expected-dark" source.
--
-- FIX
-- CREATE OR REPLACE the view. Every existing source row (climate, hydro,
-- equipment, diagnostics, energy, setpoints, forecast, daily_summary) is
-- reproduced VERBATIM from the production definition (db/schema.sql), including
-- the existing five output columns in the same order and names:
--     source, rows_1h, rows_24h, age_s, null_pct_1h
-- so every existing consumer (api/main.py /api/v1/public/data-health and the
-- forecast freshness lookup; v_data_trust_ledger) sees an unchanged shape. Two
-- columns are APPENDED (additive — appended last, never reorders existing ones):
--     cadence_threshold_s int   -- per-source freshness threshold in seconds
--                                  (NULL for sources that already had bespoke
--                                  thresholds wired in v_data_trust_ledger, i.e.
--                                  the legacy 8; the view stays the source of
--                                  truth for the NEW rows only).
--     health text               -- 'ok' | 'stale' | 'intentional_dark'
-- and three rows are ADDED:
--   * weather_station -- age_s from max(ts); threshold 86400s (24h). The Tempest
--     feed is intermittent (the issue notes ~7d intermittency at the extreme),
--     but a 24h ceiling still catches a fully-dead pipeline while tolerating the
--     normal intermittency. health = 'stale' once age_s exceeds the threshold,
--     else 'ok'. An empty table (no rows ever) reports age_s = NULL and
--     health = 'stale' (cannot prove freshness).
--   * esp32_logs      -- health = 'intentional_dark', age_s informational
--     (max(ts) if any rows, else NULL), cadence_threshold_s = NULL. NOT flagged.
--   * irrigation_log  -- health = 'intentional_dark', same treatment.
-- The eight legacy rows get health = 'ok' (their freshness is already gated by
-- the bespoke checks in v_data_trust_ledger / alert_monitor; this migration does
-- not change their semantics, only labels them so the new `health` column is
-- non-NULL across the board).
--
-- SCOPE NOTE
-- The issue's `alert_monitor raises on weather_station staleness` acceptance
-- criterion is ingestor-owned Python (ingestor/tasks.py::alert_monitor) and ships
-- as a separate follow-on PR into ingestor scope. This migration is the
-- shared-territory DB-view layer only (the issue's explicit `Depends on`).
--
-- IDEMPOTENCY
-- CREATE OR REPLACE VIEW is inherently idempotent: re-applying replaces the view
-- with the identical definition (no-op, no error). Additive / non-destructive:
-- no DROP, no table or row touched, no live data removed. Pure read-side change.
-- NB: CREATE OR REPLACE VIEW only permits ADDING columns at the end (which this
-- does); it never reorders or drops existing columns, so the replace is accepted
-- against the live view in place.
--
-- ROLLBACK (documented; see PR body) -- restore the prior view definition
-- verbatim (8 sources, the original 5 columns, no health/cadence_threshold_s).
-- IMPORTANT: CREATE OR REPLACE VIEW can only APPEND columns; it CANNOT drop them
-- (Postgres errors "cannot drop columns from view"). So the rollback to the
-- narrower prior view must DROP first. v_data_trust_ledger depends on this view
-- (referencing only source + age_s -- both survive in the forward migration, so
-- the forward CREATE OR REPLACE is accepted in place), therefore the rollback
-- must DROP ... CASCADE and recreate the dependent too:
--   DROP VIEW public.v_data_pipeline_health CASCADE;   -- also drops v_data_trust_ledger
--   CREATE VIEW public.v_data_pipeline_health AS
--    SELECT 'climate'::text AS source, ... (8 UNION ALL branches, 5 columns) ...
--   FROM public.daily_summary;
--   ALTER VIEW public.v_data_pipeline_health OWNER TO verdify;
--   -- then recreate v_data_trust_ledger from db/schema.sql verbatim.
-- The full prior text of BOTH views is in db/schema.sql (v_data_pipeline_health
-- lines 23764-23821; v_data_trust_ledger from line 24610) and reproduced in the
-- PR body. The fixture test applies the v_data_pipeline_health rollback verbatim
-- (no dependent present in the throwaway DB, so a plain DROP VIEW) and asserts
-- the view returns to 8 rows / 5 columns with no health/cadence_threshold_s.
--
-- ROLLBACK-REPLAY SAFETY (issue #23)
-- This migration contains NO top-level COMMIT and no commit-forcing statement
-- (e.g. CREATE INDEX CONCURRENTLY). It is a single CREATE OR REPLACE VIEW (plus
-- one ALTER VIEW ... OWNER and a COMMENT), so the rollback-validation harness can
-- wrap it in an outer BEGIN..ROLLBACK without the migration self-committing and
-- defeating the dry-run.
--
-- RESTARTS (CLAUDE.md rule 7): this migration does NOT touch verdify_schemas/**,
-- ingestor/entity_map.py, or mcp/server.py, so no service-restart obligation is
-- triggered. Read-side view change. No device contact.

CREATE OR REPLACE VIEW public.v_data_pipeline_health AS
 SELECT 'climate'::text AS source,
    count(*) FILTER (WHERE (climate.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (climate.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(climate.ts))))::integer, 0) AS age_s,
    ((100.0)::double precision * ((count(*) FILTER (WHERE ((climate.ts > (now() - '01:00:00'::interval)) AND ((climate.temp_avg IS NULL) OR (climate.rh_avg IS NULL) OR (climate.vpd_avg IS NULL) OR (climate.dew_point IS NULL)))))::double precision / (NULLIF(count(*) FILTER (WHERE (climate.ts > (now() - '01:00:00'::interval))), 0))::double precision)) AS null_pct_1h,
    NULL::integer AS cadence_threshold_s,
    'ok'::text AS health
   FROM public.climate
UNION ALL
 SELECT 'hydro'::text AS source,
    count(*) FILTER (WHERE ((climate.ts > (now() - '01:00:00'::interval)) AND ((climate.hydro_ph IS NOT NULL) OR (climate.hydro_ec_us_cm IS NOT NULL)))) AS rows_1h,
    count(*) FILTER (WHERE ((climate.ts > (now() - '24:00:00'::interval)) AND ((climate.hydro_ph IS NOT NULL) OR (climate.hydro_ec_us_cm IS NOT NULL)))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(climate.ts) FILTER (WHERE ((climate.hydro_ph IS NOT NULL) OR (climate.hydro_ec_us_cm IS NOT NULL))))))::integer, 0) AS age_s,
    ((100.0)::double precision * ((count(*) FILTER (WHERE ((climate.ts > (now() - '01:00:00'::interval)) AND ((climate.hydro_ph IS NULL) OR (climate.hydro_ec_us_cm IS NULL)) AND ((climate.hydro_ph IS NOT NULL) OR (climate.hydro_ec_us_cm IS NOT NULL)))))::double precision / (NULLIF(count(*) FILTER (WHERE ((climate.ts > (now() - '01:00:00'::interval)) AND ((climate.hydro_ph IS NOT NULL) OR (climate.hydro_ec_us_cm IS NOT NULL)))), 0))::double precision)) AS null_pct_1h,
    NULL::integer AS cadence_threshold_s,
    'ok'::text AS health
   FROM public.climate
UNION ALL
 SELECT 'equipment'::text AS source,
    count(*) FILTER (WHERE (equipment_state.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (equipment_state.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(equipment_state.ts))))::integer, 0) AS age_s,
    NULL::double precision AS null_pct_1h,
    NULL::integer AS cadence_threshold_s,
    'ok'::text AS health
   FROM public.equipment_state
UNION ALL
 SELECT 'diagnostics'::text AS source,
    count(*) FILTER (WHERE (diagnostics.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (diagnostics.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(diagnostics.ts))))::integer, 0) AS age_s,
    ((100.0)::double precision * ((count(*) FILTER (WHERE ((diagnostics.ts > (now() - '01:00:00'::interval)) AND ((diagnostics.wifi_rssi IS NULL) OR (diagnostics.heap_bytes IS NULL) OR (diagnostics.uptime_s IS NULL) OR (diagnostics.active_probe_count IS NULL)))))::double precision / (NULLIF(count(*) FILTER (WHERE (diagnostics.ts > (now() - '01:00:00'::interval))), 0))::double precision)) AS null_pct_1h,
    NULL::integer AS cadence_threshold_s,
    'ok'::text AS health
   FROM public.diagnostics
UNION ALL
 SELECT 'energy'::text AS source,
    count(*) FILTER (WHERE (energy.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (energy.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(energy.ts))))::integer, 0) AS age_s,
    NULL::double precision AS null_pct_1h,
    NULL::integer AS cadence_threshold_s,
    'ok'::text AS health
   FROM public.energy
UNION ALL
 SELECT 'setpoints'::text AS source,
    count(*) FILTER (WHERE (setpoint_changes.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (setpoint_changes.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(setpoint_changes.ts))))::integer, 0) AS age_s,
    NULL::double precision AS null_pct_1h,
    NULL::integer AS cadence_threshold_s,
    'ok'::text AS health
   FROM public.setpoint_changes
UNION ALL
 SELECT 'forecast'::text AS source,
    count(*) FILTER (WHERE (weather_forecast.fetched_at > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (weather_forecast.fetched_at > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(weather_forecast.fetched_at))))::integer, 0) AS age_s,
    ((100.0)::double precision * ((count(*) FILTER (WHERE ((weather_forecast.fetched_at = ( SELECT max(weather_forecast_1.fetched_at) AS max
           FROM public.weather_forecast weather_forecast_1)) AND ((weather_forecast.temp_f IS NULL) OR (weather_forecast.rh_pct IS NULL) OR (weather_forecast.solar_w_m2 IS NULL)))))::double precision / (NULLIF(count(*) FILTER (WHERE (weather_forecast.fetched_at = ( SELECT max(weather_forecast_1.fetched_at) AS max
           FROM public.weather_forecast weather_forecast_1))), 0))::double precision)) AS null_pct_1h,
    NULL::integer AS cadence_threshold_s,
    'ok'::text AS health
   FROM public.weather_forecast
UNION ALL
 SELECT 'daily_summary'::text AS source,
    count(*) FILTER (WHERE (daily_summary.captured_at > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (daily_summary.captured_at > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(daily_summary.captured_at))))::integer, 0) AS age_s,
    ((100.0)::double precision * ((count(*) FILTER (WHERE ((daily_summary.date >= (((now() AT TIME ZONE 'America/Denver'::text))::date - 1)) AND ((daily_summary.temp_avg IS NULL) OR (daily_summary.rh_avg IS NULL) OR (daily_summary.vpd_avg IS NULL) OR (daily_summary.compliance_pct IS NULL)))))::double precision / (NULLIF(count(*) FILTER (WHERE (daily_summary.date >= (((now() AT TIME ZONE 'America/Denver'::text))::date - 1))), 0))::double precision)) AS null_pct_1h,
    NULL::integer AS cadence_threshold_s,
    'ok'::text AS health
   FROM public.daily_summary
UNION ALL
-- weather_station: live but intermittent raw Tempest/Panorama feed. Monitored
-- with a 24h ceiling (86400s) -- generous for the normal intermittency, but a
-- fully dead pipeline is caught. No rows at all => age_s NULL, health 'stale'.
 SELECT 'weather_station'::text AS source,
    count(*) FILTER (WHERE (weather_station.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (weather_station.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    (EXTRACT(epoch FROM (now() - max(weather_station.ts))))::integer AS age_s,
    NULL::double precision AS null_pct_1h,
    86400 AS cadence_threshold_s,
        CASE
            WHEN (max(weather_station.ts) IS NULL) THEN 'stale'::text
            WHEN ((EXTRACT(epoch FROM (now() - max(weather_station.ts))))::integer > 86400) THEN 'stale'::text
            ELSE 'ok'::text
        END AS health
   FROM public.weather_station
UNION ALL
-- esp32_logs: intentionally dark (firmware log ingestion off; commit 90bc358).
-- Annotated so a dead live pipeline can't masquerade as this expected-dark one.
 SELECT 'esp32_logs'::text AS source,
    count(*) FILTER (WHERE (esp32_logs.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (esp32_logs.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    (EXTRACT(epoch FROM (now() - max(esp32_logs.ts))))::integer AS age_s,
    NULL::double precision AS null_pct_1h,
    NULL::integer AS cadence_threshold_s,
    'intentional_dark'::text AS health
   FROM public.esp32_logs
UNION ALL
-- irrigation_log: intentionally retired (migration 134; canonical events come
-- from equipment_state via v_irrigation_fertigation_runs). Annotated, not flagged.
 SELECT 'irrigation_log'::text AS source,
    count(*) FILTER (WHERE (irrigation_log.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (irrigation_log.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    (EXTRACT(epoch FROM (now() - max(irrigation_log.ts))))::integer AS age_s,
    NULL::double precision AS null_pct_1h,
    NULL::integer AS cadence_threshold_s,
    'intentional_dark'::text AS health
   FROM public.irrigation_log;


ALTER VIEW public.v_data_pipeline_health OWNER TO verdify;

COMMENT ON VIEW public.v_data_pipeline_health IS
'Per-source freshness/quality. Eight live sources plus weather_station (24h cadence_threshold_s; health ok/stale). esp32_logs and irrigation_log are annotated health=''intentional_dark'' (esp32_logs ingestion off per 90bc358; irrigation_log retired per migration 134) so a dead live pipeline cannot masquerade as an expected-dark source. Columns rows_1h/rows_24h/age_s/null_pct_1h preserved verbatim for existing consumers; cadence_threshold_s/health appended.';
