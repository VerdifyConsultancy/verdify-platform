-- Fixture test for migration 157 (G2 — issue #72, #33-family).
--
-- Self-contained, self-asserting SQL fixture for a DISPOSABLE throwaway DB only.
-- REQUIRES TimescaleDB: migration 157 calls create_hypertable() on the 15
-- telemetry tables that the schema.sql restore leaves as PLAIN tables, so this
-- fixture MUST run on a timescale/timescaledb image (NOT vanilla postgres).
--
-- It models the exact in-cluster restore situation the migration repairs:
--   * the 4 CORE tables (climate / equipment_state / system_state /
--     weather_forecast) are ALREADY hypertables here (as migration 000 leaves
--     them), to prove migration 157 does not disturb them and that the final
--     catalog count reaches the canonical 19.
--   * the 15 OTHER canonical tables are created as PLAIN tables (exactly how
--     schema.sql lands them in-cluster), one of them (setpoint_snapshot) seeded
--     with rows so create_hypertable(..., migrate_data => TRUE) is exercised on
--     a non-empty table and the rows are asserted to survive into chunks.
--
-- Then it:
--   1. APPLIES migration 157,
--   2. asserts ALL 15 target tables are now registered hypertables AND the total
--      public hypertable count is exactly 19 (15 new + 4 core),
--   3. asserts seeded rows survived the migrate_data conversion and now live in
--      chunks (data integrity of the in-place conversion),
--   4. proves IDEMPOTENCY (re-applies the migration body inline = no error, still
--      exactly 19 hypertables, no duplicate registration),
--   5. runs the documented ROLLBACK for a representative table (drop hypertable
--      + recreate plain, preserving rows) and asserts it reverts to PLAIN
--      (present, holding its rows, ABSENT from the hypertable catalog),
--   6. (cleanup is by container teardown; nothing here touches a live DB).
--
-- Every check RAISEs EXCEPTION on failure, so a clean run ending in the final
-- NOTICE means apply + idempotency + rollback all pass.
--
-- Run against a DISPOSABLE TimescaleDB throwaway DB, e.g.:
--   psql -v ON_ERROR_STOP=1 \
--        -v migration=db/migrations/157-hypertable-parity-repair.sql \
--        -f db/migrations/tests/test-157-hypertable-parity-repair.sql
-- NEVER run this against the live DB — it creates/drops schema objects.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- 0. Preconditions: TimescaleDB present; prod owner role exists.
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS timescaledb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        RAISE EXCEPTION 'PRECONDITION FAIL: timescaledb extension required for migration 157 fixture';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verdify') THEN
        CREATE ROLE verdify;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 1. Minimal base schema modeling the post-schema.sql-restore state.
--    Clean slate, then create:
--      * 4 CORE tables AS hypertables (migration 000 already did these),
--      * 15 OTHER canonical tables as PLAIN tables.
--    Every canonical table has a `ts timestamptz NOT NULL` time column, matching
--    db/schema.sql. Extra columns are minimal shape; the migration only cares
--    about the `ts` dimension.
-- ---------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'climate','equipment_state','system_state','weather_forecast',
        'setpoint_changes','diagnostics','energy','esp32_logs','weather_station',
        'setpoint_plan','irrigation_log','setpoint_snapshot','forecast_deviation_log',
        'override_events','setpoint_clamps','gpu_power','infra_cpu',
        'climate_action_log','model_predictions'
    ]
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', t);
    END LOOP;
END
$$;

-- A tiny helper macro is not available in psql; create each table explicitly.
-- Shape: ts + one payload column + greenhouse_id, enough to be realistic.
CREATE TABLE public.climate                (ts timestamptz NOT NULL, temp_f double precision,  greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.equipment_state        (ts timestamptz NOT NULL, equipment text,           greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.system_state           (ts timestamptz NOT NULL, entity text,              greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.weather_forecast       (ts timestamptz NOT NULL, temp_f double precision,  greenhouse_id text DEFAULT 'vallery');

CREATE TABLE public.setpoint_changes       (ts timestamptz NOT NULL, parameter text,           greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.diagnostics            (ts timestamptz NOT NULL, metric text,              greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.energy                 (ts timestamptz NOT NULL, watts double precision,   greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.esp32_logs             (ts timestamptz NOT NULL, line text,                greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.weather_station        (ts timestamptz NOT NULL, temp_f double precision,  greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.setpoint_plan          (ts timestamptz NOT NULL, parameter text,           greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.irrigation_log         (ts timestamptz NOT NULL, zone text,                greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.setpoint_snapshot      (ts timestamptz NOT NULL, parameter text, value double precision, greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.forecast_deviation_log (ts timestamptz NOT NULL, deviation double precision, greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.override_events        (ts timestamptz NOT NULL, source text,              greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.setpoint_clamps        (ts timestamptz NOT NULL, parameter text,           greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.gpu_power              (ts timestamptz NOT NULL, watts double precision,   greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.infra_cpu              (ts timestamptz NOT NULL, pct double precision,     greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.climate_action_log     (ts timestamptz NOT NULL, climate_action text,      greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.model_predictions      (ts timestamptz NOT NULL, predicted_temp_f double precision, greenhouse_id text DEFAULT 'vallery');

-- The 4 CORE tables are ALREADY hypertables (as migration 000 leaves them).
SELECT create_hypertable('public.climate',          'ts');
SELECT create_hypertable('public.equipment_state',  'ts');
SELECT create_hypertable('public.system_state',     'ts');
SELECT create_hypertable('public.weather_forecast', 'ts');

-- Seed setpoint_snapshot (a PLAIN table here) with rows BEFORE the migration so
-- create_hypertable(..., migrate_data => TRUE) is exercised on a NON-EMPTY table
-- and we can assert the rows survive into chunks.
INSERT INTO public.setpoint_snapshot (ts, parameter, value)
SELECT now() - (g || ' hours')::interval, 'temp_c', 20 + g
FROM generate_series(1, 50) AS g;

DO $$
DECLARE n int;
BEGIN
    -- Sanity: the 15 targets must currently be PLAIN (NOT hypertables) and the 4
    -- core must already be hypertables, modeling the post-restore state.
    SELECT count(*) INTO n
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public';
    IF n <> 4 THEN
        RAISE EXCEPTION 'SETUP FAIL: expected exactly 4 core hypertables before migration, got %', n;
    END IF;
    RAISE NOTICE 'SETUP OK: 4 core hypertables, 15 plain telemetry tables, 50 seeded setpoint_snapshot rows.';
END
$$;

-- ===========================================================================
-- 2. APPLY migration 157.
-- ===========================================================================
\echo '--- applying migration 157 ---'
\i :migration

-- ---------------------------------------------------------------------------
-- 3. Assert ALL 15 targets are now hypertables AND total = canonical 19.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    expected text[] := ARRAY[
        'setpoint_changes','diagnostics','energy','esp32_logs','weather_station',
        'setpoint_plan','irrigation_log','setpoint_snapshot','forecast_deviation_log',
        'override_events','setpoint_clamps','gpu_power','infra_cpu',
        'climate_action_log','model_predictions'
    ];
    t   text;
    n   int;
    tot int;
BEGIN
    FOREACH t IN ARRAY expected
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables
             WHERE hypertable_schema = 'public' AND hypertable_name = t
        ) THEN
            RAISE EXCEPTION 'APPLY FAIL: % was not converted to a hypertable', t;
        END IF;
    END LOOP;

    SELECT count(*) INTO n
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public' AND hypertable_name = ANY(expected);
    IF n <> 15 THEN
        RAISE EXCEPTION 'APPLY FAIL: expected 15 newly-converted hypertables, got %', n;
    END IF;

    SELECT count(*) INTO tot
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public';
    IF tot <> 19 THEN
        RAISE EXCEPTION 'APPLY FAIL: expected canonical 19 hypertables total (15 + 4 core), got %', tot;
    END IF;

    RAISE NOTICE 'APPLY OK: all 15 targets are hypertables; total = 19 (parity).';
END
$$;

-- ---------------------------------------------------------------------------
-- 3b. Assert migrate_data preserved the seeded rows AND they are in chunks.
-- ---------------------------------------------------------------------------
DO $$
DECLARE rowcount int; chunkcount int;
BEGIN
    SELECT count(*) INTO rowcount FROM public.setpoint_snapshot;
    IF rowcount <> 50 THEN
        RAISE EXCEPTION 'APPLY FAIL: migrate_data lost rows — setpoint_snapshot has % (expected 50)', rowcount;
    END IF;

    SELECT count(*) INTO chunkcount
      FROM timescaledb_information.chunks
     WHERE hypertable_schema = 'public' AND hypertable_name = 'setpoint_snapshot';
    IF chunkcount < 1 THEN
        RAISE EXCEPTION 'APPLY FAIL: setpoint_snapshot rows were not routed into chunks (chunks=%)', chunkcount;
    END IF;

    RAISE NOTICE 'APPLY OK: migrate_data preserved 50 rows into % chunk(s).', chunkcount;
END
$$;

-- ---------------------------------------------------------------------------
-- 4. IDEMPOTENCY: re-apply the migration body; must not error and must leave
--    exactly 19 hypertables with no duplicate registration.
-- ---------------------------------------------------------------------------
\echo '--- re-applying migration 157 (idempotency) ---'
\i :migration

DO $$
DECLARE tot int; rowcount int;
BEGIN
    SELECT count(*) INTO tot
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public';
    IF tot <> 19 THEN
        RAISE EXCEPTION 'IDEMPOTENCY FAIL: hypertable count is % after re-apply (expected 19)', tot;
    END IF;
    SELECT count(*) INTO rowcount FROM public.setpoint_snapshot;
    IF rowcount <> 50 THEN
        RAISE EXCEPTION 'IDEMPOTENCY FAIL: re-apply changed setpoint_snapshot row count to % (expected 50)', rowcount;
    END IF;
    RAISE NOTICE 'IDEMPOTENCY OK: re-apply is a no-op (19 hypertables, 50 rows, no error).';
END
$$;

-- ---------------------------------------------------------------------------
-- 5. ROLLBACK (representative): the documented revert-to-plain for one table.
--    Copy rows -> drop hypertable -> recreate plain -> restore rows. Assert the
--    table is present, holding its rows, and ABSENT from the hypertable catalog.
-- ---------------------------------------------------------------------------
\echo '--- applying documented rollback (setpoint_snapshot -> plain) ---'
CREATE TEMP TABLE _rb_setpoint_snapshot AS TABLE public.setpoint_snapshot;
DROP TABLE public.setpoint_snapshot;                         -- drops hypertable + chunks
CREATE TABLE public.setpoint_snapshot (
    ts            timestamptz NOT NULL,
    parameter     text,
    value         double precision,
    greenhouse_id text DEFAULT 'vallery'
);
INSERT INTO public.setpoint_snapshot SELECT * FROM _rb_setpoint_snapshot;
DROP TABLE _rb_setpoint_snapshot;

DO $$
DECLARE rowcount int; tot int;
BEGIN
    IF to_regclass('public.setpoint_snapshot') IS NULL THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: setpoint_snapshot missing after revert';
    END IF;
    IF EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
         WHERE hypertable_schema = 'public' AND hypertable_name = 'setpoint_snapshot'
    ) THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: setpoint_snapshot still a hypertable after revert';
    END IF;
    SELECT count(*) INTO rowcount FROM public.setpoint_snapshot;
    IF rowcount <> 50 THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: rows lost on revert — % (expected 50)', rowcount;
    END IF;
    -- The other 14 reverted-via-the-same-recipe tables stay hypertables here
    -- (we only exercised one for brevity); total drops from 19 to 18.
    SELECT count(*) INTO tot
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public';
    IF tot <> 18 THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: expected 18 hypertables after reverting one, got %', tot;
    END IF;
    RAISE NOTICE 'ROLLBACK OK: setpoint_snapshot reverted to plain, 50 rows intact, total now 18.';
END
$$;

DO $$ BEGIN RAISE NOTICE 'ALL FIXTURE ASSERTIONS PASSED (apply + migrate_data + idempotency + rollback).'; END $$;
