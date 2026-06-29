-- Fixture test for migration 152 (issue #41).
--
-- Self-contained, self-asserting SQL fixture for a DISPOSABLE throwaway DB only.
-- Stands up a minimal daily_summary (only the columns v_daily_kpi reads), seeds
-- rows covering broken/low-meter, sane-meter, meter-only, and estimate-only
-- cases, applies the migration (CREATE OR REPLACE VIEW), asserts kwh resolves to
-- the reliable value, proves idempotency (re-apply CREATE OR REPLACE = no-op/no
-- error and identical results), then proves the documented rollback restores the
-- prior buggy behaviour (broken meter floors the KPI again). Every assertion
-- RAISEs EXCEPTION on failure, so a clean run that prints the final NOTICE means
-- apply + idempotency + rollback all pass.
--
-- Run against a throwaway DB, e.g.:
--   psql -v ON_ERROR_STOP=1 -f db/migrations/tests/test-152-kwh-coalesce-sanity-gate.sql
-- NEVER run this against the live DB — it DROPs and recreates daily_summary.

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
-- Minimal schema v_daily_kpi depends on (only the columns the view reads).
-- daily_summary is a plain table; no hypertable / TimescaleDB needed here.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS public.v_daily_kpi;
DROP TABLE IF EXISTS public.daily_summary;
CREATE TABLE public.daily_summary (
    date                  date NOT NULL,
    compliance_pct        double precision,
    temp_compliance_pct   double precision,
    vpd_compliance_pct    double precision,
    stress_hours_heat     double precision,
    stress_hours_cold     double precision,
    stress_hours_vpd_high double precision,
    stress_hours_vpd_low  double precision,
    kwh_total             double precision,
    kwh_estimated         double precision,
    therms_estimated      double precision,
    gas_used_therms       double precision,
    water_used_gal        double precision,
    mister_water_gal      double precision,
    cost_electric         double precision,
    cost_gas              double precision,
    cost_water            double precision,
    cost_total            double precision,
    temp_min              double precision,
    temp_max              double precision,
    temp_avg              double precision,
    vpd_min               double precision,
    vpd_max               double precision,
    vpd_avg               double precision,
    dli_final             double precision,
    min_dp_margin_f       double precision,
    dp_risk_hours         double precision
);
-- Mirror production: in schema.sql daily_summary is OWNER TO verdify, and the
-- migration's ALTER VIEW ... OWNER TO verdify makes the view owner match. Align
-- the throwaway table owner so the view's owner can read its base table.
ALTER TABLE public.daily_summary OWNER TO verdify;

-- ---------------------------------------------------------------------------
-- Seed: every kwh decision branch.
-- ---------------------------------------------------------------------------
INSERT INTO public.daily_summary (date, kwh_total, kwh_estimated) VALUES
    ('2026-05-30', 6.8,  41.0),   -- A: BROKEN/LOW meter (6.8 << half of 41=20.5) -> estimate must win (41.0)
    ('2026-05-29', 0,    38.0),   -- B: ZERO meter -> estimate must win (38.0)
    ('2026-05-28', NULL, 35.0),   -- C: NULL meter -> estimate wins (35.0), same as legacy
    ('2026-05-27', 40.0, 41.0),   -- D: SANE meter (40 >= 20.5) -> meter kept (40.0)
    ('2026-05-26', 22.0, 40.0),   -- E: meter exactly above half (22 >= 20.0) -> meter kept (22.0)
    ('2026-05-25', 33.0, NULL),   -- F: meter present, NO estimate -> meter kept (33.0)
    ('2026-05-24', NULL, NULL);   -- G: nothing -> 0.0 (never invent data)

-- ===========================================================================
-- APPLY (migration 152 body)
-- ===========================================================================
\i db/migrations/152-kwh-coalesce-sanity-gate.sql

-- ---------------------------------------------------------------------------
-- ASSERT apply: kwh resolves to the reliable value per branch.
-- ---------------------------------------------------------------------------
DO $$
DECLARE v numeric;
BEGIN
    -- A: broken/low meter rejected -> estimate.
    SELECT kwh INTO v FROM public.v_daily_kpi WHERE date='2026-05-30';
    IF v <> 41.00 THEN RAISE EXCEPTION 'APPLY FAIL A: broken meter 6.8 should yield estimate 41.00, got %', v; END IF;

    -- B: zero meter rejected -> estimate.
    SELECT kwh INTO v FROM public.v_daily_kpi WHERE date='2026-05-29';
    IF v <> 38.00 THEN RAISE EXCEPTION 'APPLY FAIL B: zero meter should yield estimate 38.00, got %', v; END IF;

    -- C: NULL meter -> estimate (unchanged from legacy).
    SELECT kwh INTO v FROM public.v_daily_kpi WHERE date='2026-05-28';
    IF v <> 35.00 THEN RAISE EXCEPTION 'APPLY FAIL C: NULL meter should yield estimate 35.00, got %', v; END IF;

    -- D: sane meter (>= half estimate) kept.
    SELECT kwh INTO v FROM public.v_daily_kpi WHERE date='2026-05-27';
    IF v <> 40.00 THEN RAISE EXCEPTION 'APPLY FAIL D: sane meter 40.0 should be kept, got %', v; END IF;

    -- E: meter exactly above half estimate kept.
    SELECT kwh INTO v FROM public.v_daily_kpi WHERE date='2026-05-26';
    IF v <> 22.00 THEN RAISE EXCEPTION 'APPLY FAIL E: meter 22.0 (>= half 40) should be kept, got %', v; END IF;

    -- F: meter present, no estimate -> meter kept.
    SELECT kwh INTO v FROM public.v_daily_kpi WHERE date='2026-05-25';
    IF v <> 33.00 THEN RAISE EXCEPTION 'APPLY FAIL F: meter 33.0 with no estimate should be kept, got %', v; END IF;

    -- G: nothing -> 0.
    SELECT kwh INTO v FROM public.v_daily_kpi WHERE date='2026-05-24';
    IF v <> 0.00 THEN RAISE EXCEPTION 'APPLY FAIL G: no meter/estimate should yield 0.00, got %', v; END IF;

    RAISE NOTICE 'APPLY OK: broken/zero/low meter rejected -> estimate wins; sane meter kept; no-data -> 0.';
END $$;

-- ===========================================================================
-- RE-APPLY (idempotency): CREATE OR REPLACE must succeed and yield identical kwh.
-- ===========================================================================
\i db/migrations/152-kwh-coalesce-sanity-gate.sql

DO $$
DECLARE v numeric;
BEGIN
    SELECT kwh INTO v FROM public.v_daily_kpi WHERE date='2026-05-30';
    IF v <> 41.00 THEN RAISE EXCEPTION 'IDEMPOTENCY FAIL: re-apply changed kwh for broken-meter row, got %', v; END IF;
    SELECT kwh INTO v FROM public.v_daily_kpi WHERE date='2026-05-27';
    IF v <> 40.00 THEN RAISE EXCEPTION 'IDEMPOTENCY FAIL: re-apply changed kwh for sane-meter row, got %', v; END IF;
    RAISE NOTICE 'IDEMPOTENCY OK: re-apply (CREATE OR REPLACE) succeeded, kwh unchanged.';
END $$;

-- ===========================================================================
-- ROLLBACK (documented rollback, verbatim) + assert prior buggy behaviour back.
-- ===========================================================================
CREATE OR REPLACE VIEW public.v_daily_kpi AS
 SELECT date,
    round((COALESCE(compliance_pct, (0)::double precision))::numeric, 1) AS compliance_pct,
    round((COALESCE(temp_compliance_pct, (0)::double precision))::numeric, 1) AS temp_compliance_pct,
    round((COALESCE(vpd_compliance_pct, (0)::double precision))::numeric, 1) AS vpd_compliance_pct,
    round((COALESCE(stress_hours_heat, (0)::double precision))::numeric, 2) AS heat_stress_h,
    round((COALESCE(stress_hours_cold, (0)::double precision))::numeric, 2) AS cold_stress_h,
    round((COALESCE(stress_hours_vpd_high, (0)::double precision))::numeric, 2) AS vpd_high_stress_h,
    round((COALESCE(stress_hours_vpd_low, (0)::double precision))::numeric, 2) AS vpd_low_stress_h,
    round(((((COALESCE(stress_hours_heat, (0)::double precision) + COALESCE(stress_hours_cold, (0)::double precision)) + COALESCE(stress_hours_vpd_high, (0)::double precision)) + COALESCE(stress_hours_vpd_low, (0)::double precision)))::numeric, 2) AS total_stress_h,
    round((COALESCE(kwh_total, kwh_estimated, (0)::double precision))::numeric, 2) AS kwh,
    round((COALESCE(therms_estimated, gas_used_therms, (0)::double precision))::numeric, 3) AS therms,
    round((COALESCE(water_used_gal, (0)::double precision))::numeric, 0) AS water_gal,
    round((COALESCE(mister_water_gal, (0)::double precision))::numeric, 0) AS mister_water_gal,
    round((COALESCE(cost_electric, (0)::double precision))::numeric, 2) AS cost_electric,
    round((COALESCE(cost_gas, (0)::double precision))::numeric, 2) AS cost_gas,
    round((COALESCE(cost_water, (0)::double precision))::numeric, 2) AS cost_water,
    round((COALESCE(cost_total, (0)::double precision))::numeric, 2) AS cost_total,
    round((temp_min)::numeric, 1) AS temp_min,
    round((temp_max)::numeric, 1) AS temp_max,
    round((temp_avg)::numeric, 1) AS temp_avg,
    round((vpd_min)::numeric, 2) AS vpd_min,
    round((vpd_max)::numeric, 2) AS vpd_max,
    round((vpd_avg)::numeric, 2) AS vpd_avg,
    round((dli_final)::numeric, 1) AS dli,
    round((min_dp_margin_f)::numeric, 1) AS dp_margin_min_f,
    round((COALESCE(dp_risk_hours, (0)::double precision))::numeric, 1) AS dp_risk_hours,
    round(((((COALESCE(compliance_pct, (0)::double precision) / (100.0)::double precision) * (80)::double precision) + (GREATEST((0)::double precision, ((1.0)::double precision - LEAST((COALESCE(cost_total, (0)::double precision) / (15.0)::double precision), (1.0)::double precision))) * (20)::double precision)))::numeric, 1) AS planner_score
   FROM public.daily_summary
  WHERE (date IS NOT NULL)
  ORDER BY date;

DO $$
DECLARE v numeric;
BEGIN
    -- After rollback the broken meter floors the KPI again (legacy bug back).
    SELECT kwh INTO v FROM public.v_daily_kpi WHERE date='2026-05-30';
    IF v <> 6.80 THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: prior view should re-floor broken meter to 6.80, got %', v;
    END IF;
    -- Sane-meter row is identical under both definitions (sanity check).
    SELECT kwh INTO v FROM public.v_daily_kpi WHERE date='2026-05-27';
    IF v <> 40.00 THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: sane-meter row should still be 40.00, got %', v;
    END IF;
    RAISE NOTICE 'ROLLBACK OK: prior definition restored (broken meter floors KPI again).';
END $$;

DO $$ BEGIN RAISE NOTICE 'ALL FIXTURE ASSERTIONS PASSED (apply + idempotency + rollback).'; END $$;
