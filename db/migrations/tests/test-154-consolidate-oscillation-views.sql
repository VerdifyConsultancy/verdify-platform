-- Fixture test for migration 154 (issue #47).
--
-- Self-contained, self-asserting SQL fixture for a DISPOSABLE throwaway DB only.
-- Stands up a minimal equipment_state (the only relation the oscillation views
-- read), seeds two days x multiple equipment with controlled hourly transition
-- counts, applies the migration (CREATE OR REPLACE VIEW for the canonical
-- base + the derived summary), asserts the base returns the expected
-- oscillation columns AND that the summary is provably derived from the base
-- (its rollup equals the aggregate recomputed independently from the base),
-- proves idempotency (re-apply CREATE OR REPLACE = no error and identical
-- results), then proves the documented rollback restores the prior migration-083
-- definitions (same view bodies; the #47 change is purely the COMMENT role
-- annotation, so the rollback asserts the COMMENTs revert while the data is
-- unchanged). Every assertion RAISEs EXCEPTION on failure, so a clean run that
-- prints the final NOTICE means apply + idempotency + rollback all pass.
--
-- The oscillation views read a PLAIN equipment_state table; no hypertable /
-- TimescaleDB extension is required, so a vanilla postgres:16-alpine throwaway
-- DB is sufficient (matches db/Dockerfile.migrate's base image).
--
-- Run against a throwaway DB, e.g.:
--   psql -v ON_ERROR_STOP=1 -f db/migrations/tests/test-154-consolidate-oscillation-views.sql
-- NEVER run this against the live DB — it DROPs and recreates equipment_state.

\set ON_ERROR_STOP on

-- The production schema owns these objects as role `verdify`. On a bare
-- throwaway DB we create the role so the migration body / table owners apply
-- verbatim (migration 154 itself issues no ALTER ... OWNER, but we mirror prod).
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='verdify') THEN
        CREATE ROLE verdify;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Minimal schema the oscillation views depend on. equipment_state is a plain
-- table here (in prod it is a TimescaleDB hypertable, but the view logic is
-- identical against the parent relation).
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS public.v_daily_oscillation_summary;
DROP VIEW IF EXISTS public.v_daily_oscillation;
DROP TABLE IF EXISTS public.equipment_state;
CREATE TABLE public.equipment_state (
    ts            timestamp with time zone NOT NULL,
    equipment     text NOT NULL,
    state         boolean NOT NULL,
    greenhouse_id text DEFAULT 'vallery'::text
);
ALTER TABLE public.equipment_state OWNER TO verdify;

-- ---------------------------------------------------------------------------
-- Seed: two days, controlled per-hour transition counts so peaks are exact.
--
-- Day 2026-05-30:
--   heater : hour 10 -> 5 rows, hour 11 -> 2 rows   => peak 5 (hour 10), active_hours 2, avg 3.5
--   fan    : hour 10 -> 3 rows                       => peak 3 (hour 10), active_hours 1, avg 3.0
-- Day 2026-05-29:
--   heater : hour 09 -> 2 rows                       => peak 2 (hour 09), active_hours 1, avg 2.0
--
-- Each row counts as one "transition" (the view counts rows per hour).
-- ---------------------------------------------------------------------------
INSERT INTO public.equipment_state (ts, equipment, state) VALUES
    -- 2026-05-30 heater hour 10: 5 rows
    ('2026-05-30 10:01:00+00','heater', true),
    ('2026-05-30 10:11:00+00','heater', false),
    ('2026-05-30 10:21:00+00','heater', true),
    ('2026-05-30 10:31:00+00','heater', false),
    ('2026-05-30 10:41:00+00','heater', true),
    -- 2026-05-30 heater hour 11: 2 rows
    ('2026-05-30 11:05:00+00','heater', false),
    ('2026-05-30 11:35:00+00','heater', true),
    -- 2026-05-30 fan hour 10: 3 rows
    ('2026-05-30 10:02:00+00','fan', true),
    ('2026-05-30 10:22:00+00','fan', false),
    ('2026-05-30 10:42:00+00','fan', true),
    -- 2026-05-29 heater hour 09: 2 rows
    ('2026-05-29 09:10:00+00','heater', true),
    ('2026-05-29 09:40:00+00','heater', false);

-- ===========================================================================
-- APPLY (migration 154 body)
-- ===========================================================================
\i db/migrations/154-consolidate-oscillation-views.sql

-- ---------------------------------------------------------------------------
-- ASSERT apply (1): canonical BASE view returns the expected oscillation
-- columns with the expected per-equipment values.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    r record;
    n int;
BEGIN
    -- Column contract: the base must expose exactly these named columns.
    SELECT count(*) INTO n
    FROM information_schema.columns
    WHERE table_schema='public' AND table_name='v_daily_oscillation'
      AND column_name IN ('date','equipment','peak_transitions_per_hour',
                          'peak_hour','avg_transitions_per_hour','active_hours');
    IF n <> 6 THEN
        RAISE EXCEPTION 'APPLY FAIL: v_daily_oscillation missing expected columns (found % of 6)', n;
    END IF;

    -- 2026-05-30 heater: peak 5 @ hour 10, active_hours 2, avg (5+2)/2 = 3.5
    SELECT * INTO r FROM public.v_daily_oscillation
      WHERE date='2026-05-30' AND equipment='heater';
    IF r.peak_transitions_per_hour <> 5 THEN
        RAISE EXCEPTION 'APPLY FAIL: heater 05-30 peak should be 5, got %', r.peak_transitions_per_hour;
    END IF;
    IF r.active_hours <> 2 THEN
        RAISE EXCEPTION 'APPLY FAIL: heater 05-30 active_hours should be 2, got %', r.active_hours;
    END IF;
    IF r.avg_transitions_per_hour <> 3.5 THEN
        RAISE EXCEPTION 'APPLY FAIL: heater 05-30 avg should be 3.5, got %', r.avg_transitions_per_hour;
    END IF;
    IF r.peak_hour <> '2026-05-30 10:00:00+00'::timestamptz THEN
        RAISE EXCEPTION 'APPLY FAIL: heater 05-30 peak_hour should be 10:00, got %', r.peak_hour;
    END IF;

    -- 2026-05-30 fan: peak 3 @ hour 10, active_hours 1
    SELECT * INTO r FROM public.v_daily_oscillation
      WHERE date='2026-05-30' AND equipment='fan';
    IF r.peak_transitions_per_hour <> 3 OR r.active_hours <> 1 THEN
        RAISE EXCEPTION 'APPLY FAIL: fan 05-30 expected peak 3 / active 1, got % / %',
            r.peak_transitions_per_hour, r.active_hours;
    END IF;

    -- 2026-05-29 heater: peak 2 @ hour 09, active_hours 1
    SELECT * INTO r FROM public.v_daily_oscillation
      WHERE date='2026-05-29' AND equipment='heater';
    IF r.peak_transitions_per_hour <> 2 THEN
        RAISE EXCEPTION 'APPLY FAIL: heater 05-29 peak should be 2, got %', r.peak_transitions_per_hour;
    END IF;

    RAISE NOTICE 'APPLY OK (base): v_daily_oscillation returns expected per-equipment oscillation columns.';
END $$;

-- ---------------------------------------------------------------------------
-- ASSERT apply (2): the summary is PROVABLY DERIVED from the base. Recompute
-- the rollup independently straight from v_daily_oscillation and require the
-- summary view to match it row-for-row.
-- ---------------------------------------------------------------------------
DO $$
DECLARE s record; e record;
BEGIN
    -- Day 2026-05-30 derived expectations from the base:
    --   rows: heater peak 5, fan peak 3.
    --   total_peak_per_hour = 5 + 3 = 8
    --   worst_equipment_peak = 5, worst_equipment = heater, worst_hour = 10:00
    --   avg_across_equipment = round(avg(3.5, 3.0),1) = 3.3
    SELECT * INTO s FROM public.v_daily_oscillation_summary WHERE date='2026-05-30';

    -- Recompute independently from the base view.
    SELECT
        sum(peak_transitions_per_hour) AS total_peak,
        max(peak_transitions_per_hour) AS worst_peak,
        round(avg(avg_transitions_per_hour),1) AS avg_across
    INTO e
    FROM public.v_daily_oscillation WHERE date='2026-05-30';

    IF s.total_peak_per_hour <> e.total_peak THEN
        RAISE EXCEPTION 'DERIVE FAIL: summary total_peak_per_hour % <> base-recompute %',
            s.total_peak_per_hour, e.total_peak;
    END IF;
    IF s.worst_equipment_peak <> e.worst_peak THEN
        RAISE EXCEPTION 'DERIVE FAIL: summary worst_equipment_peak % <> base-recompute %',
            s.worst_equipment_peak, e.worst_peak;
    END IF;
    IF s.avg_across_equipment <> e.avg_across THEN
        RAISE EXCEPTION 'DERIVE FAIL: summary avg_across_equipment % <> base-recompute %',
            s.avg_across_equipment, e.avg_across;
    END IF;
    IF s.total_peak_per_hour <> 8 THEN
        RAISE EXCEPTION 'DERIVE FAIL: summary total_peak_per_hour should be 8, got %', s.total_peak_per_hour;
    END IF;
    IF s.worst_equipment <> 'heater' THEN
        RAISE EXCEPTION 'DERIVE FAIL: worst_equipment should be heater, got %', s.worst_equipment;
    END IF;
    IF s.worst_hour <> '2026-05-30 10:00:00+00'::timestamptz THEN
        RAISE EXCEPTION 'DERIVE FAIL: worst_hour should be 10:00, got %', s.worst_hour;
    END IF;

    RAISE NOTICE 'APPLY OK (derive): v_daily_oscillation_summary equals an independent rollup of v_daily_oscillation.';
END $$;

-- ---------------------------------------------------------------------------
-- ASSERT apply (3): the canonical role is documented on each view (the #47
-- contract fix). The base COMMENT must mark it CANONICAL BASE; the summary
-- COMMENT must mark it DERIVED.
-- ---------------------------------------------------------------------------
DO $$
DECLARE base_c text; summ_c text;
BEGIN
    base_c := obj_description('public.v_daily_oscillation'::regclass, 'pg_class');
    summ_c := obj_description('public.v_daily_oscillation_summary'::regclass, 'pg_class');
    IF base_c IS NULL OR position('CANONICAL BASE' in base_c) = 0 THEN
        RAISE EXCEPTION 'APPLY FAIL: v_daily_oscillation comment should declare CANONICAL BASE role, got: %', base_c;
    END IF;
    IF summ_c IS NULL OR position('DERIVED' in summ_c) = 0 THEN
        RAISE EXCEPTION 'APPLY FAIL: v_daily_oscillation_summary comment should declare DERIVED role, got: %', summ_c;
    END IF;
    RAISE NOTICE 'APPLY OK (roles): canonical base + derived summary roles are documented in view COMMENTs.';
END $$;

-- ===========================================================================
-- RE-APPLY (idempotency): CREATE OR REPLACE must succeed and yield identical
-- results.
-- ===========================================================================
\i db/migrations/154-consolidate-oscillation-views.sql

DO $$
DECLARE v int; s int;
BEGIN
    SELECT peak_transitions_per_hour INTO v FROM public.v_daily_oscillation
      WHERE date='2026-05-30' AND equipment='heater';
    IF v <> 5 THEN RAISE EXCEPTION 'IDEMPOTENCY FAIL: base peak changed on re-apply, got %', v; END IF;
    SELECT total_peak_per_hour INTO s FROM public.v_daily_oscillation_summary WHERE date='2026-05-30';
    IF s <> 8 THEN RAISE EXCEPTION 'IDEMPOTENCY FAIL: summary total changed on re-apply, got %', s; END IF;
    RAISE NOTICE 'IDEMPOTENCY OK: re-apply (CREATE OR REPLACE) succeeded, results unchanged.';
END $$;

-- ===========================================================================
-- ROLLBACK (documented rollback, verbatim from migration 083) + assert the
-- prior definitions are restored. The #47 change is the COMMENT role
-- annotation, so the rollback restores the prior 083 COMMENTs while leaving
-- the data identical (the view bodies are unchanged between 083 and 154).
-- ===========================================================================
CREATE OR REPLACE VIEW v_daily_oscillation AS
WITH hourly AS (
    SELECT
        date_trunc('day', ts) AS date,
        date_trunc('hour', ts) AS hour,
        equipment,
        count(*) AS transitions
    FROM equipment_state
    GROUP BY 1, 2, 3
)
SELECT
    date::date AS date,
    equipment,
    max(transitions) AS peak_transitions_per_hour,
    (array_agg(hour ORDER BY transitions DESC))[1] AS peak_hour,
    round(avg(transitions), 1) AS avg_transitions_per_hour,
    count(*) AS active_hours
FROM hourly
GROUP BY 1, 2
ORDER BY 1 DESC, 2;

COMMENT ON VIEW v_daily_oscillation IS
    'FW-2: per-day, per-equipment peak hourly transition count. Use to detect oscillation regressions after dispatcher/firmware changes.';

CREATE OR REPLACE VIEW v_daily_oscillation_summary AS
SELECT
    date,
    sum(peak_transitions_per_hour) AS total_peak_per_hour,
    max(peak_transitions_per_hour) AS worst_equipment_peak,
    (array_agg(equipment ORDER BY peak_transitions_per_hour DESC))[1] AS worst_equipment,
    (array_agg(peak_hour ORDER BY peak_transitions_per_hour DESC))[1] AS worst_hour,
    round(avg(avg_transitions_per_hour), 1) AS avg_across_equipment
FROM v_daily_oscillation
GROUP BY 1
ORDER BY 1 DESC;

COMMENT ON VIEW v_daily_oscillation_summary IS
    'FW-2: single-row-per-day oscillation scorecard. worst_equipment + worst_hour identify the peak oscillation event of the day.';

DO $$
DECLARE base_c text; summ_c text; v int; s int;
BEGIN
    -- Data identical under both definitions (view bodies unchanged).
    SELECT peak_transitions_per_hour INTO v FROM public.v_daily_oscillation
      WHERE date='2026-05-30' AND equipment='heater';
    IF v <> 5 THEN RAISE EXCEPTION 'ROLLBACK FAIL: base peak should still be 5, got %', v; END IF;
    SELECT total_peak_per_hour INTO s FROM public.v_daily_oscillation_summary WHERE date='2026-05-30';
    IF s <> 8 THEN RAISE EXCEPTION 'ROLLBACK FAIL: summary total should still be 8, got %', s; END IF;

    -- The #47 role annotation is GONE again (prior 083 comments restored).
    base_c := obj_description('public.v_daily_oscillation'::regclass, 'pg_class');
    summ_c := obj_description('public.v_daily_oscillation_summary'::regclass, 'pg_class');
    IF base_c IS NULL OR position('CANONICAL BASE' in base_c) <> 0 THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: base comment should revert to prior 083 text (no CANONICAL BASE), got: %', base_c;
    END IF;
    IF summ_c IS NULL OR position('DERIVED' in summ_c) <> 0 THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: summary comment should revert to prior 083 text (no DERIVED), got: %', summ_c;
    END IF;

    RAISE NOTICE 'ROLLBACK OK: prior migration-083 view definitions + comments restored; data unchanged.';
END $$;

DO $$ BEGIN RAISE NOTICE 'ALL FIXTURE ASSERTIONS PASSED (apply + idempotency + rollback).'; END $$;
