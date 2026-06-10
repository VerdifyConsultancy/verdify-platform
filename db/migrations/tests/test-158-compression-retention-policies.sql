-- Fixture test for migration 158 (G3 — issue #72, #33-family).
--
-- Self-contained, self-asserting SQL fixture for a DISPOSABLE throwaway DB only.
-- REQUIRES TimescaleDB: migration 158 calls add_compression_policy /
-- add_retention_policy / ALTER TABLE ... SET (timescaledb.compress ...), all of
-- which only exist on a timescale/timescaledb image (NOT vanilla postgres).
--
-- It models the post-G2 in-cluster state the migration targets: the 5 canonical
-- compressed/retained hypertables (climate, energy, diagnostics, esp32_logs,
-- setpoint_snapshot) ALREADY exist as registered hypertables (as migrations
-- 000 + 157/G2 leave them) but carry NO compression settings and NO
-- compression/retention policy jobs (exactly how a fresh schema build lands them,
-- because the Community pg_dump strips that catalog state).
--
-- Then it:
--   1. asserts the SETUP precondition (5 hypertables, 0 compression/retention
--      policy jobs, 0 compressed hypertables),
--   2. APPLIES migration 158,
--   3. asserts the PARITY end-state via the TimescaleDB catalogs:
--        * exactly 4 policy_compression jobs (climate, energy, diagnostics,
--          setpoint_snapshot — NOT esp32_logs),
--        * exactly 5 policy_retention jobs (the 5 canonical, intervals correct),
--        * exactly 5 compressed hypertables (the 5 canonical incl. esp32_logs),
--   4. proves IDEMPOTENCY (re-applies the migration body inline = no error, still
--      4 + 5 jobs, still 5 compressed, no duplicate jobs),
--   5. runs the documented ROLLBACK (remove_compression_policy /
--      remove_retention_policy + disable compress) and asserts the policy job
--      counts and compressed-hypertable count return to 0,
--   6. (cleanup is by container teardown; nothing here touches a live DB).
--
-- Every check RAISEs EXCEPTION on failure, so a clean run ending in the final
-- NOTICE means SETUP + apply + idempotency + rollback all pass.
--
-- Run against a DISPOSABLE TimescaleDB throwaway DB, e.g.:
--   psql -v ON_ERROR_STOP=1 \
--        -v migration=db/migrations/158-compression-retention-policies.sql \
--        -f db/migrations/tests/test-158-compression-retention-policies.sql
-- NEVER run this against the live DB — it creates/drops schema objects + policies.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- 0. Preconditions: TimescaleDB present; prod owner role exists.
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS timescaledb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        RAISE EXCEPTION 'PRECONDITION FAIL: timescaledb extension required for migration 158 fixture';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verdify') THEN
        CREATE ROLE verdify;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 1. Minimal base schema modeling the post-G2 (157) in-cluster state: the 5
--    canonical compressed/retained tables exist AS hypertables but with NO
--    compression settings and NO policy jobs. `ts` is the time column on all
--    (matching db/schema.sql). setpoint_snapshot carries `parameter` so its
--    segmentby setting is exercised.
-- ---------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'climate','energy','diagnostics','esp32_logs','setpoint_snapshot'
    ]
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', t);
    END LOOP;
END
$$;

CREATE TABLE public.climate           (ts timestamptz NOT NULL, temp_f double precision,         greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.energy            (ts timestamptz NOT NULL, watts double precision,          greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.diagnostics       (ts timestamptz NOT NULL, metric text,                     greenhouse_id text DEFAULT 'vallery');
CREATE TABLE public.esp32_logs        (ts timestamptz NOT NULL, level text, message text);
CREATE TABLE public.setpoint_snapshot (ts timestamptz NOT NULL, parameter text, value double precision, greenhouse_id text DEFAULT 'vallery');

-- All 5 are registered hypertables (as migrations 000 + 157/G2 leave them).
SELECT create_hypertable('public.climate',           'ts');
SELECT create_hypertable('public.energy',            'ts');
SELECT create_hypertable('public.diagnostics',       'ts');
SELECT create_hypertable('public.esp32_logs',        'ts');
SELECT create_hypertable('public.setpoint_snapshot', 'ts');

DO $$
DECLARE n_ht int; n_comp_jobs int; n_ret_jobs int; n_compressed int;
BEGIN
    SELECT count(*) INTO n_ht
      FROM timescaledb_information.hypertables WHERE hypertable_schema = 'public';
    IF n_ht <> 5 THEN
        RAISE EXCEPTION 'SETUP FAIL: expected 5 hypertables before migration, got %', n_ht;
    END IF;

    SELECT count(*) INTO n_comp_jobs
      FROM timescaledb_information.jobs WHERE proc_name = 'policy_compression';
    SELECT count(*) INTO n_ret_jobs
      FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention';
    SELECT count(*) INTO n_compressed
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public' AND compression_enabled;

    IF n_comp_jobs <> 0 OR n_ret_jobs <> 0 OR n_compressed <> 0 THEN
        RAISE EXCEPTION 'SETUP FAIL: expected 0 compression jobs / 0 retention jobs / 0 compressed, got %/%/%',
            n_comp_jobs, n_ret_jobs, n_compressed;
    END IF;

    RAISE NOTICE 'SETUP OK: 5 hypertables, 0 compression jobs, 0 retention jobs, 0 compressed hypertables.';
END
$$;

-- ===========================================================================
-- 2. APPLY migration 158.
-- ===========================================================================
\echo '--- applying migration 158 ---'
\i :migration

-- ---------------------------------------------------------------------------
-- 3. Assert the PARITY end-state via the TimescaleDB catalogs:
--    4 policy_compression jobs (NOT esp32_logs), 5 policy_retention jobs,
--    5 compressed hypertables. Also assert the exact per-table membership and
--    the retention intervals.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    n_comp_jobs   int;
    n_ret_jobs    int;
    n_compressed  int;
    comp_set      text[];
    ret_set       text[];
    compressed_set text[];
    expected_comp  text[] := ARRAY['climate','diagnostics','energy','setpoint_snapshot'];
    expected_ret   text[] := ARRAY['climate','diagnostics','energy','esp32_logs','setpoint_snapshot'];
    expected_compd text[] := ARRAY['climate','diagnostics','energy','esp32_logs','setpoint_snapshot'];
BEGIN
    -- counts
    SELECT count(*) INTO n_comp_jobs
      FROM timescaledb_information.jobs WHERE proc_name = 'policy_compression';
    SELECT count(*) INTO n_ret_jobs
      FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention';
    SELECT count(*) INTO n_compressed
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public' AND compression_enabled;

    IF n_comp_jobs <> 4 THEN
        RAISE EXCEPTION 'APPLY FAIL: expected 4 policy_compression jobs, got %', n_comp_jobs;
    END IF;
    IF n_ret_jobs <> 5 THEN
        RAISE EXCEPTION 'APPLY FAIL: expected 5 policy_retention jobs, got %', n_ret_jobs;
    END IF;
    IF n_compressed <> 5 THEN
        RAISE EXCEPTION 'APPLY FAIL: expected 5 compressed hypertables, got %', n_compressed;
    END IF;

    -- exact membership (compression policies)
    SELECT array_agg(hypertable_name ORDER BY hypertable_name) INTO comp_set
      FROM timescaledb_information.jobs WHERE proc_name = 'policy_compression';
    IF comp_set IS DISTINCT FROM expected_comp THEN
        RAISE EXCEPTION 'APPLY FAIL: compression-policy set % <> expected % (esp32_logs must NOT have a compression policy)',
            comp_set, expected_comp;
    END IF;

    -- exact membership (retention policies)
    SELECT array_agg(hypertable_name ORDER BY hypertable_name) INTO ret_set
      FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention';
    IF ret_set IS DISTINCT FROM expected_ret THEN
        RAISE EXCEPTION 'APPLY FAIL: retention-policy set % <> expected %', ret_set, expected_ret;
    END IF;

    -- exact membership (compressed hypertables — the parity dimension 8 set)
    SELECT array_agg(hypertable_name ORDER BY hypertable_name) INTO compressed_set
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public' AND compression_enabled;
    IF compressed_set IS DISTINCT FROM expected_compd THEN
        RAISE EXCEPTION 'APPLY FAIL: compressed-hypertable set % <> expected %', compressed_set, expected_compd;
    END IF;

    RAISE NOTICE 'APPLY OK: 4 compression jobs %, 5 retention jobs %, 5 compressed hypertables %.',
        comp_set, ret_set, compressed_set;
END
$$;

-- 3b. Assert the retention intervals match the live values (drop_after schedule).
DO $$
DECLARE
    bad text;
BEGIN
    -- timescaledb stores the retention interval in jobs.config->>'drop_after'.
    SELECT string_agg(hypertable_name || '=' || (config->>'drop_after'), ', ' ORDER BY hypertable_name)
      INTO bad
      FROM timescaledb_information.jobs
     WHERE proc_name = 'policy_retention'
       AND NOT (
            (hypertable_name = 'climate'           AND (config->>'drop_after')::interval = INTERVAL '365 days') OR
            (hypertable_name = 'energy'            AND (config->>'drop_after')::interval = INTERVAL '365 days') OR
            (hypertable_name = 'diagnostics'       AND (config->>'drop_after')::interval = INTERVAL '180 days') OR
            (hypertable_name = 'esp32_logs'        AND (config->>'drop_after')::interval = INTERVAL '30 days')  OR
            (hypertable_name = 'setpoint_snapshot' AND (config->>'drop_after')::interval = INTERVAL '90 days')
       );
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'APPLY FAIL: retention interval mismatch on %', bad;
    END IF;
    RAISE NOTICE 'APPLY OK: retention intervals match live (climate/energy 365d, diagnostics 180d, esp32_logs 30d, setpoint_snapshot 90d).';
END
$$;

-- ---------------------------------------------------------------------------
-- 4. IDEMPOTENCY: re-apply the migration body; must not error and must leave
--    exactly 4 + 5 jobs and 5 compressed hypertables (no duplicate jobs).
-- ---------------------------------------------------------------------------
\echo '--- re-applying migration 158 (idempotency) ---'
\i :migration

DO $$
DECLARE n_comp_jobs int; n_ret_jobs int; n_compressed int;
BEGIN
    SELECT count(*) INTO n_comp_jobs
      FROM timescaledb_information.jobs WHERE proc_name = 'policy_compression';
    SELECT count(*) INTO n_ret_jobs
      FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention';
    SELECT count(*) INTO n_compressed
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public' AND compression_enabled;

    IF n_comp_jobs <> 4 OR n_ret_jobs <> 5 OR n_compressed <> 5 THEN
        RAISE EXCEPTION 'IDEMPOTENCY FAIL: after re-apply got %/%/% (expected 4 compression jobs / 5 retention jobs / 5 compressed)',
            n_comp_jobs, n_ret_jobs, n_compressed;
    END IF;
    RAISE NOTICE 'IDEMPOTENCY OK: re-apply is a no-op (4 compression jobs, 5 retention jobs, 5 compressed, no duplicates).';
END
$$;

-- ---------------------------------------------------------------------------
-- 5. ROLLBACK (documented): remove the 4 compression + 5 retention policies and
--    disable compression on all 5. Assert the job counts and compressed-set
--    return to 0.
-- ---------------------------------------------------------------------------
\echo '--- applying documented rollback (remove policies + disable compress) ---'
SELECT remove_compression_policy('setpoint_snapshot', if_exists => TRUE);
SELECT remove_compression_policy('diagnostics',       if_exists => TRUE);
SELECT remove_compression_policy('energy',            if_exists => TRUE);
SELECT remove_compression_policy('climate',           if_exists => TRUE);

SELECT remove_retention_policy('setpoint_snapshot', if_exists => TRUE);
SELECT remove_retention_policy('esp32_logs',        if_exists => TRUE);
SELECT remove_retention_policy('diagnostics',       if_exists => TRUE);
SELECT remove_retention_policy('energy',            if_exists => TRUE);
SELECT remove_retention_policy('climate',           if_exists => TRUE);

ALTER TABLE setpoint_snapshot SET (timescaledb.compress = false);
ALTER TABLE esp32_logs        SET (timescaledb.compress = false);
ALTER TABLE diagnostics       SET (timescaledb.compress = false);
ALTER TABLE energy            SET (timescaledb.compress = false);
ALTER TABLE climate           SET (timescaledb.compress = false);

DO $$
DECLARE n_comp_jobs int; n_ret_jobs int; n_compressed int;
BEGIN
    SELECT count(*) INTO n_comp_jobs
      FROM timescaledb_information.jobs WHERE proc_name = 'policy_compression';
    SELECT count(*) INTO n_ret_jobs
      FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention';
    SELECT count(*) INTO n_compressed
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public' AND compression_enabled;

    IF n_comp_jobs <> 0 OR n_ret_jobs <> 0 OR n_compressed <> 0 THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: after revert got %/%/% (expected 0/0/0)',
            n_comp_jobs, n_ret_jobs, n_compressed;
    END IF;
    RAISE NOTICE 'ROLLBACK OK: all compression/retention policies removed, compression disabled (0/0/0).';
END
$$;

DO $$ BEGIN RAISE NOTICE 'ALL FIXTURE ASSERTIONS PASSED (setup + apply + idempotency + rollback).'; END $$;
