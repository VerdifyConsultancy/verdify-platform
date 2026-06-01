-- Fixture test for migration 153 (issue #42).
--
-- Self-contained, self-asserting SQL fixture for a DISPOSABLE throwaway DB only.
-- Stands up the minimal base tables v_data_pipeline_health reads (only the
-- columns the view references; all plain tables -- no hypertable / TimescaleDB
-- needed), seeds rows so each NEW source row exercises a distinct branch, applies
-- the migration (CREATE OR REPLACE VIEW), asserts:
--   * weather_station appears with an age_s + health column + cadence_threshold_s,
--     fresh -> 'ok', past-threshold -> 'stale', empty -> 'stale' (age_s NULL),
--   * esp32_logs and irrigation_log carry health = 'intentional_dark',
--   * the eight legacy sources are still present with health = 'ok',
--   * the legacy five columns (rows_1h, rows_24h, age_s, null_pct_1h) survive,
-- then proves idempotency (re-apply CREATE OR REPLACE = no-op/no error, identical
-- results), then proves the documented rollback restores the PRIOR definition
-- (8 sources, no health / cadence_threshold_s columns). Every assertion RAISEs
-- EXCEPTION on failure, so a clean run that prints the final NOTICE means apply +
-- idempotency + rollback all pass.
--
-- Run against a throwaway DB, e.g.:
--   psql -v ON_ERROR_STOP=1 -f db/migrations/tests/test-153-pipeline-health-weather-station-dark-sources.sql
-- NEVER run this against the live DB -- it DROPs and recreates the base tables.

\set ON_ERROR_STOP on

-- The migration's ALTER VIEW ... OWNER TO verdify requires the verdify role.
-- In production schema.sql already created it; on a bare throwaway DB we create
-- it here so the migration body applies verbatim.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='verdify') THEN
        CREATE ROLE verdify;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Minimal schema v_data_pipeline_health depends on (only the columns the view
-- reads). All plain tables; no hypertable / TimescaleDB needed here.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS public.v_data_pipeline_health;
DROP TABLE IF EXISTS public.climate;
DROP TABLE IF EXISTS public.equipment_state;
DROP TABLE IF EXISTS public.diagnostics;
DROP TABLE IF EXISTS public.energy;
DROP TABLE IF EXISTS public.setpoint_changes;
DROP TABLE IF EXISTS public.weather_forecast;
DROP TABLE IF EXISTS public.daily_summary;
DROP TABLE IF EXISTS public.weather_station;
DROP TABLE IF EXISTS public.esp32_logs;
DROP TABLE IF EXISTS public.irrigation_log;

CREATE TABLE public.climate (
    ts             timestamptz NOT NULL,
    temp_avg       double precision,
    rh_avg         double precision,
    vpd_avg        double precision,
    dew_point      double precision,
    hydro_ph       double precision,
    hydro_ec_us_cm double precision
);
CREATE TABLE public.equipment_state  (ts timestamptz NOT NULL);
CREATE TABLE public.diagnostics (
    ts                 timestamptz NOT NULL,
    wifi_rssi          double precision,
    heap_bytes         bigint,
    uptime_s           bigint,
    active_probe_count integer
);
CREATE TABLE public.energy           (ts timestamptz NOT NULL);
CREATE TABLE public.setpoint_changes (ts timestamptz NOT NULL);
CREATE TABLE public.weather_forecast (
    fetched_at  timestamptz NOT NULL,
    temp_f      double precision,
    rh_pct      double precision,
    solar_w_m2  double precision
);
CREATE TABLE public.daily_summary (
    date           date NOT NULL,
    captured_at    timestamptz,
    temp_avg       double precision,
    rh_avg         double precision,
    vpd_avg        double precision,
    compliance_pct double precision
);
CREATE TABLE public.weather_station  (ts timestamptz NOT NULL);
CREATE TABLE public.esp32_logs       (ts timestamptz NOT NULL);
CREATE TABLE public.irrigation_log   (ts timestamptz NOT NULL);

-- Mirror production: base tables are OWNER TO verdify, and the migration's
-- ALTER VIEW ... OWNER TO verdify makes the view owner verdify -- align ownership
-- so the view owner can read its base tables.
ALTER TABLE public.climate          OWNER TO verdify;
ALTER TABLE public.equipment_state  OWNER TO verdify;
ALTER TABLE public.diagnostics      OWNER TO verdify;
ALTER TABLE public.energy           OWNER TO verdify;
ALTER TABLE public.setpoint_changes OWNER TO verdify;
ALTER TABLE public.weather_forecast OWNER TO verdify;
ALTER TABLE public.daily_summary    OWNER TO verdify;
ALTER TABLE public.weather_station  OWNER TO verdify;
ALTER TABLE public.esp32_logs       OWNER TO verdify;
ALTER TABLE public.irrigation_log   OWNER TO verdify;

-- ---------------------------------------------------------------------------
-- Seed: keep the eight legacy sources present (one fresh row each) and exercise
-- the weather_station fresh branch. The weather_station stale / empty branches
-- are re-tested below by re-seeding, because the view aggregates max(ts).
-- ---------------------------------------------------------------------------
INSERT INTO public.climate (ts, temp_avg, rh_avg, vpd_avg, dew_point, hydro_ph, hydro_ec_us_cm)
    VALUES (now(), 75, 60, 1.0, 50, 6.0, 1800);
INSERT INTO public.equipment_state  (ts) VALUES (now());
INSERT INTO public.diagnostics (ts, wifi_rssi, heap_bytes, uptime_s, active_probe_count)
    VALUES (now(), -60, 100000, 3600, 4);
INSERT INTO public.energy           (ts) VALUES (now());
INSERT INTO public.setpoint_changes (ts) VALUES (now());
INSERT INTO public.weather_forecast (fetched_at, temp_f, rh_pct, solar_w_m2)
    VALUES (now(), 70, 55, 600);
INSERT INTO public.daily_summary (date, captured_at, temp_avg, rh_avg, vpd_avg, compliance_pct)
    VALUES (current_date, now(), 74, 58, 0.9, 92);
-- weather_station fresh: 1h old -> well under the 24h threshold -> 'ok'.
INSERT INTO public.weather_station (ts) VALUES (now() - interval '1 hour');
-- esp32_logs / irrigation_log: seed an OLD row -- they must be 'intentional_dark'
-- regardless of age (proving age never flags them as broken).
INSERT INTO public.esp32_logs     (ts) VALUES (now() - interval '30 days');
INSERT INTO public.irrigation_log (ts) VALUES (now() - interval '60 days');

-- ===========================================================================
-- APPLY (migration 153 body)
-- ===========================================================================
\i db/migrations/153-pipeline-health-weather-station-dark-sources.sql

-- ---------------------------------------------------------------------------
-- ASSERT apply.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    n        int;
    h        text;
    a        int;
    thr      int;
BEGIN
    -- All 8 legacy sources still present, all health = 'ok'.
    SELECT count(*) INTO n FROM public.v_data_pipeline_health
     WHERE source IN ('climate','hydro','equipment','diagnostics','energy','setpoints','forecast','daily_summary');
    IF n <> 8 THEN RAISE EXCEPTION 'APPLY FAIL: expected 8 legacy sources, got %', n; END IF;

    SELECT count(*) INTO n FROM public.v_data_pipeline_health
     WHERE source IN ('climate','hydro','equipment','diagnostics','energy','setpoints','forecast','daily_summary')
       AND health = 'ok';
    IF n <> 8 THEN RAISE EXCEPTION 'APPLY FAIL: expected 8 legacy sources health=ok, got %', n; END IF;

    -- Total 11 source rows (8 legacy + weather_station + esp32_logs + irrigation_log).
    SELECT count(*) INTO n FROM public.v_data_pipeline_health;
    IF n <> 11 THEN RAISE EXCEPTION 'APPLY FAIL: expected 11 total source rows, got %', n; END IF;

    -- weather_station present with age_s, health, cadence_threshold_s.
    SELECT health, age_s, cadence_threshold_s INTO h, a, thr
      FROM public.v_data_pipeline_health WHERE source = 'weather_station';
    IF h IS NULL THEN RAISE EXCEPTION 'APPLY FAIL: weather_station missing from view'; END IF;
    IF thr <> 86400 THEN RAISE EXCEPTION 'APPLY FAIL: weather_station cadence_threshold_s should be 86400, got %', thr; END IF;
    IF a IS NULL OR a < 3000 OR a > 4200 THEN
        RAISE EXCEPTION 'APPLY FAIL: weather_station age_s should be ~3600 (1h old), got %', a;
    END IF;
    IF h <> 'ok' THEN RAISE EXCEPTION 'APPLY FAIL: fresh weather_station (1h) should be health=ok, got %', h; END IF;

    -- esp32_logs / irrigation_log annotated intentional_dark (NOT flagged broken
    -- despite being weeks old).
    SELECT health INTO h FROM public.v_data_pipeline_health WHERE source = 'esp32_logs';
    IF h <> 'intentional_dark' THEN RAISE EXCEPTION 'APPLY FAIL: esp32_logs should be intentional_dark, got %', h; END IF;
    SELECT health INTO h FROM public.v_data_pipeline_health WHERE source = 'irrigation_log';
    IF h <> 'intentional_dark' THEN RAISE EXCEPTION 'APPLY FAIL: irrigation_log should be intentional_dark, got %', h; END IF;

    -- Legacy 5 columns survive (selecting them must not error; climate row exists).
    SELECT rows_1h INTO n FROM public.v_data_pipeline_health WHERE source = 'climate';
    IF n < 1 THEN RAISE EXCEPTION 'APPLY FAIL: climate rows_1h should be >= 1, got %', n; END IF;

    RAISE NOTICE 'APPLY OK: 11 sources; weather_station fresh=ok w/ age_s+threshold; esp32_logs & irrigation_log intentional_dark; legacy 8 ok + 5 columns intact.';
END $$;

-- ---------------------------------------------------------------------------
-- weather_station STALE branch: push the only row past the 24h threshold.
-- ---------------------------------------------------------------------------
UPDATE public.weather_station SET ts = now() - interval '36 hours';
DO $$
DECLARE h text;
BEGIN
    SELECT health INTO h FROM public.v_data_pipeline_health WHERE source = 'weather_station';
    IF h <> 'stale' THEN RAISE EXCEPTION 'APPLY FAIL: weather_station 36h old should be health=stale, got %', h; END IF;
    RAISE NOTICE 'APPLY OK: weather_station past 24h threshold -> stale.';
END $$;

-- ---------------------------------------------------------------------------
-- weather_station EMPTY branch: no rows -> age_s NULL, health stale.
-- ---------------------------------------------------------------------------
DELETE FROM public.weather_station;
DO $$
DECLARE h text; a int;
BEGIN
    SELECT health, age_s INTO h, a FROM public.v_data_pipeline_health WHERE source = 'weather_station';
    IF a IS NOT NULL THEN RAISE EXCEPTION 'APPLY FAIL: empty weather_station age_s should be NULL, got %', a; END IF;
    IF h <> 'stale' THEN RAISE EXCEPTION 'APPLY FAIL: empty weather_station should be health=stale, got %', h; END IF;
    RAISE NOTICE 'APPLY OK: empty weather_station -> age_s NULL, health stale.';
END $$;
-- Restore a fresh weather_station row for the idempotency re-check.
INSERT INTO public.weather_station (ts) VALUES (now() - interval '1 hour');

-- ===========================================================================
-- RE-APPLY (idempotency): CREATE OR REPLACE must succeed and yield same shape.
-- ===========================================================================
\i db/migrations/153-pipeline-health-weather-station-dark-sources.sql

DO $$
DECLARE n int; h text;
BEGIN
    SELECT count(*) INTO n FROM public.v_data_pipeline_health;
    IF n <> 11 THEN RAISE EXCEPTION 'IDEMPOTENCY FAIL: re-apply changed source count, got %', n; END IF;
    SELECT health INTO h FROM public.v_data_pipeline_health WHERE source = 'esp32_logs';
    IF h <> 'intentional_dark' THEN RAISE EXCEPTION 'IDEMPOTENCY FAIL: esp32_logs annotation lost on re-apply, got %', h; END IF;
    SELECT health INTO h FROM public.v_data_pipeline_health WHERE source = 'weather_station';
    IF h <> 'ok' THEN RAISE EXCEPTION 'IDEMPOTENCY FAIL: fresh weather_station should be ok on re-apply, got %', h; END IF;
    RAISE NOTICE 'IDEMPOTENCY OK: re-apply (CREATE OR REPLACE) succeeded, shape + annotations unchanged.';
END $$;

-- ===========================================================================
-- ROLLBACK (documented rollback, verbatim prior definition) + assert prior shape.
-- ---------------------------------------------------------------------------
-- NOTE: CREATE OR REPLACE VIEW cannot DROP columns (Postgres: "cannot drop
-- columns from view"), so the rollback to the narrower 5-column prior view must
-- DROP the view first. In PRODUCTION v_data_trust_ledger depends on this view
-- (referencing only source + age_s, both surviving), so the production rollback
-- is `DROP VIEW public.v_data_pipeline_health CASCADE` followed by recreating
-- BOTH the prior v_data_pipeline_health AND v_data_trust_ledger (see PR body).
-- This throwaway DB has no dependent view, so a plain DROP VIEW suffices here.
-- ===========================================================================
DROP VIEW public.v_data_pipeline_health;
CREATE OR REPLACE VIEW public.v_data_pipeline_health AS
 SELECT 'climate'::text AS source,
    count(*) FILTER (WHERE (climate.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (climate.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(climate.ts))))::integer, 0) AS age_s,
    ((100.0)::double precision * ((count(*) FILTER (WHERE ((climate.ts > (now() - '01:00:00'::interval)) AND ((climate.temp_avg IS NULL) OR (climate.rh_avg IS NULL) OR (climate.vpd_avg IS NULL) OR (climate.dew_point IS NULL)))))::double precision / (NULLIF(count(*) FILTER (WHERE (climate.ts > (now() - '01:00:00'::interval))), 0))::double precision)) AS null_pct_1h
   FROM public.climate
UNION ALL
 SELECT 'hydro'::text AS source,
    count(*) FILTER (WHERE ((climate.ts > (now() - '01:00:00'::interval)) AND ((climate.hydro_ph IS NOT NULL) OR (climate.hydro_ec_us_cm IS NOT NULL)))) AS rows_1h,
    count(*) FILTER (WHERE ((climate.ts > (now() - '24:00:00'::interval)) AND ((climate.hydro_ph IS NOT NULL) OR (climate.hydro_ec_us_cm IS NOT NULL)))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(climate.ts) FILTER (WHERE ((climate.hydro_ph IS NOT NULL) OR (climate.hydro_ec_us_cm IS NOT NULL))))))::integer, 0) AS age_s,
    ((100.0)::double precision * ((count(*) FILTER (WHERE ((climate.ts > (now() - '01:00:00'::interval)) AND ((climate.hydro_ph IS NULL) OR (climate.hydro_ec_us_cm IS NULL)) AND ((climate.hydro_ph IS NOT NULL) OR (climate.hydro_ec_us_cm IS NOT NULL)))))::double precision / (NULLIF(count(*) FILTER (WHERE ((climate.ts > (now() - '01:00:00'::interval)) AND ((climate.hydro_ph IS NOT NULL) OR (climate.hydro_ec_us_cm IS NOT NULL)))), 0))::double precision)) AS null_pct_1h
   FROM public.climate
UNION ALL
 SELECT 'equipment'::text AS source,
    count(*) FILTER (WHERE (equipment_state.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (equipment_state.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(equipment_state.ts))))::integer, 0) AS age_s,
    NULL::double precision AS null_pct_1h
   FROM public.equipment_state
UNION ALL
 SELECT 'diagnostics'::text AS source,
    count(*) FILTER (WHERE (diagnostics.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (diagnostics.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(diagnostics.ts))))::integer, 0) AS age_s,
    ((100.0)::double precision * ((count(*) FILTER (WHERE ((diagnostics.ts > (now() - '01:00:00'::interval)) AND ((diagnostics.wifi_rssi IS NULL) OR (diagnostics.heap_bytes IS NULL) OR (diagnostics.uptime_s IS NULL) OR (diagnostics.active_probe_count IS NULL)))))::double precision / (NULLIF(count(*) FILTER (WHERE (diagnostics.ts > (now() - '01:00:00'::interval))), 0))::double precision)) AS null_pct_1h
   FROM public.diagnostics
UNION ALL
 SELECT 'energy'::text AS source,
    count(*) FILTER (WHERE (energy.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (energy.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(energy.ts))))::integer, 0) AS age_s,
    NULL::double precision AS null_pct_1h
   FROM public.energy
UNION ALL
 SELECT 'setpoints'::text AS source,
    count(*) FILTER (WHERE (setpoint_changes.ts > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (setpoint_changes.ts > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(setpoint_changes.ts))))::integer, 0) AS age_s,
    NULL::double precision AS null_pct_1h
   FROM public.setpoint_changes
UNION ALL
 SELECT 'forecast'::text AS source,
    count(*) FILTER (WHERE (weather_forecast.fetched_at > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (weather_forecast.fetched_at > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(weather_forecast.fetched_at))))::integer, 0) AS age_s,
    ((100.0)::double precision * ((count(*) FILTER (WHERE ((weather_forecast.fetched_at = ( SELECT max(weather_forecast_1.fetched_at) AS max
           FROM public.weather_forecast weather_forecast_1)) AND ((weather_forecast.temp_f IS NULL) OR (weather_forecast.rh_pct IS NULL) OR (weather_forecast.solar_w_m2 IS NULL)))))::double precision / (NULLIF(count(*) FILTER (WHERE (weather_forecast.fetched_at = ( SELECT max(weather_forecast_1.fetched_at) AS max
           FROM public.weather_forecast weather_forecast_1))), 0))::double precision)) AS null_pct_1h
   FROM public.weather_forecast
UNION ALL
 SELECT 'daily_summary'::text AS source,
    count(*) FILTER (WHERE (daily_summary.captured_at > (now() - '01:00:00'::interval))) AS rows_1h,
    count(*) FILTER (WHERE (daily_summary.captured_at > (now() - '24:00:00'::interval))) AS rows_24h,
    GREATEST((EXTRACT(epoch FROM (now() - max(daily_summary.captured_at))))::integer, 0) AS age_s,
    ((100.0)::double precision * ((count(*) FILTER (WHERE ((daily_summary.date >= (((now() AT TIME ZONE 'America/Denver'::text))::date - 1)) AND ((daily_summary.temp_avg IS NULL) OR (daily_summary.rh_avg IS NULL) OR (daily_summary.vpd_avg IS NULL) OR (daily_summary.compliance_pct IS NULL)))))::double precision / (NULLIF(count(*) FILTER (WHERE (daily_summary.date >= (((now() AT TIME ZONE 'America/Denver'::text))::date - 1))), 0))::double precision)) AS null_pct_1h
   FROM public.daily_summary;

DO $$
DECLARE n int; cols int;
BEGIN
    -- Prior view: exactly 8 sources, none of the new ones.
    SELECT count(*) INTO n FROM public.v_data_pipeline_health;
    IF n <> 8 THEN RAISE EXCEPTION 'ROLLBACK FAIL: prior view should have 8 sources, got %', n; END IF;
    SELECT count(*) INTO n FROM public.v_data_pipeline_health
     WHERE source IN ('weather_station','esp32_logs','irrigation_log');
    IF n <> 0 THEN RAISE EXCEPTION 'ROLLBACK FAIL: prior view should NOT contain the new sources, got %', n; END IF;
    -- Prior view: exactly 5 columns (no health / cadence_threshold_s).
    SELECT count(*) INTO cols FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'v_data_pipeline_health';
    IF cols <> 5 THEN RAISE EXCEPTION 'ROLLBACK FAIL: prior view should have 5 columns, got %', cols; END IF;
    SELECT count(*) INTO cols FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'v_data_pipeline_health'
       AND column_name IN ('health','cadence_threshold_s');
    IF cols <> 0 THEN RAISE EXCEPTION 'ROLLBACK FAIL: health/cadence_threshold_s columns should be gone, got %', cols; END IF;
    RAISE NOTICE 'ROLLBACK OK: prior definition restored (8 sources, 5 columns, no health/cadence_threshold_s).';
END $$;

DO $$ BEGIN RAISE NOTICE 'ALL FIXTURE ASSERTIONS PASSED (apply + idempotency + rollback).'; END $$;
