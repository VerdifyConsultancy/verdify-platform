-- Fixture test for migration 155 (issue #33, TWIN-6).
--
-- Self-contained, self-asserting SQL fixture for a DISPOSABLE throwaway DB only.
-- REQUIRES TimescaleDB: migration 155 calls create_hypertable() on both new
-- tables, so this fixture MUST run on a timescale/timescaledb-ha (or equivalent)
-- image, NOT vanilla postgres. It:
--   1. stands up the minimal dependency schema the migration references
--      (greenhouses + the six telemetry tables; the three live-hypertable ones
--      are made hypertables here too so privileges/shape match prod),
--   2. APPLIES migration 155,
--   3. asserts BOTH new tables exist AND are real hypertables (to_regclass +
--      timescaledb_information.hypertables + a chunk is created on insert),
--   4. asserts twin_ro has EXACTLY: SELECT on the six telemetry tables, INSERT
--      on the two observability tables, and NOTHING ELSE — no UPDATE, no DELETE
--      anywhere, no control-plane write — including a LIVE attempt (as twin_ro)
--      to INSERT into equipment_state that MUST raise insufficient_privilege,
--   5. proves IDEMPOTENCY (re-applies the migration body inline = no error, same
--      privilege set, no duplicate hypertable),
--   6. runs the documented ROLLBACK and asserts a clean teardown (both tables
--      and the role gone).
--
-- Every check RAISEs EXCEPTION on failure, so a clean run ending in the final
-- NOTICE means apply + idempotency + rollback all pass.
--
-- Run against a DISPOSABLE TimescaleDB throwaway DB, e.g.:
--   psql -v ON_ERROR_STOP=1 -f db/migrations/tests/test-155-twin-observability-tables.sql
-- NEVER run this against the live DB — it creates/drops schema objects and a role.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- 0. Preconditions: TimescaleDB present; prod owner role exists.
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS timescaledb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        RAISE EXCEPTION 'PRECONDITION FAIL: timescaledb extension required for migration 155 fixture';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verdify') THEN
        CREATE ROLE verdify;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 1. Minimal dependency schema (clean slate, in dependency order).
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS public.firmware_twin_divergence CASCADE;
DROP TABLE IF EXISTS public.twin_decisions CASCADE;
DROP TABLE IF EXISTS public.climate_action_log CASCADE;
DROP TABLE IF EXISTS public.setpoint_changes CASCADE;
DROP TABLE IF EXISTS public.setpoint_snapshot CASCADE;
DROP TABLE IF EXISTS public.system_state CASCADE;
DROP TABLE IF EXISTS public.equipment_state CASCADE;
DROP TABLE IF EXISTS public.climate CASCADE;
DROP TABLE IF EXISTS public.greenhouses CASCADE;
DROP ROLE IF EXISTS twin_ro;

CREATE TABLE public.greenhouses (
    id   text NOT NULL PRIMARY KEY,
    name text NOT NULL
);
INSERT INTO public.greenhouses (id, name) VALUES ('vallery', 'Vallery');

-- Telemetry tables the twin reads. The three that are hypertables in prod
-- (climate, equipment_state, system_state) are made hypertables here too.
CREATE TABLE public.climate (
    ts            timestamptz NOT NULL,
    temp_f        double precision,
    greenhouse_id text DEFAULT 'vallery'
);
SELECT create_hypertable('public.climate', 'ts');

CREATE TABLE public.equipment_state (
    ts            timestamptz NOT NULL,
    equipment     text NOT NULL,
    state         boolean NOT NULL,
    greenhouse_id text DEFAULT 'vallery'
);
SELECT create_hypertable('public.equipment_state', 'ts');

CREATE TABLE public.system_state (
    ts            timestamptz NOT NULL,
    entity        text NOT NULL,
    value         text NOT NULL,
    greenhouse_id text DEFAULT 'vallery'
);
SELECT create_hypertable('public.system_state', 'ts');

-- setpoint_snapshot / setpoint_changes / climate_action_log are plain tables
-- here; only SELECT is granted on them so shape detail is irrelevant.
CREATE TABLE public.setpoint_snapshot (
    ts            timestamptz NOT NULL,
    parameter     text NOT NULL,
    value         double precision NOT NULL,
    greenhouse_id text DEFAULT 'vallery'
);
CREATE TABLE public.setpoint_changes (
    ts            timestamptz NOT NULL,
    parameter     text NOT NULL,
    value         double precision NOT NULL,
    greenhouse_id text DEFAULT 'vallery'
);
CREATE TABLE public.climate_action_log (
    ts             timestamptz NOT NULL,
    climate_action text NOT NULL,
    greenhouse_id  text DEFAULT 'vallery'
);

ALTER TABLE public.greenhouses        OWNER TO verdify;
ALTER TABLE public.climate            OWNER TO verdify;
ALTER TABLE public.equipment_state    OWNER TO verdify;
ALTER TABLE public.system_state       OWNER TO verdify;
ALTER TABLE public.setpoint_snapshot  OWNER TO verdify;
ALTER TABLE public.setpoint_changes   OWNER TO verdify;
ALTER TABLE public.climate_action_log OWNER TO verdify;

-- ===========================================================================
-- 2. APPLY migration 155.
-- ===========================================================================
\echo '--- applying migration 155 ---'
\i :migration

-- ---------------------------------------------------------------------------
-- 3. Assert both new tables exist AND are hypertables.
-- ---------------------------------------------------------------------------
DO $$
DECLARE n int;
BEGIN
    IF to_regclass('public.twin_decisions') IS NULL THEN
        RAISE EXCEPTION 'APPLY FAIL: twin_decisions not created';
    END IF;
    IF to_regclass('public.firmware_twin_divergence') IS NULL THEN
        RAISE EXCEPTION 'APPLY FAIL: firmware_twin_divergence not created';
    END IF;

    -- TimescaleDB hypertable catalog (timescaledb_information.hypertables).
    SELECT count(*) INTO n
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public'
       AND hypertable_name IN ('twin_decisions', 'firmware_twin_divergence');
    IF n <> 2 THEN
        RAISE EXCEPTION 'APPLY FAIL: expected 2 new hypertables in catalog, got %', n;
    END IF;

    RAISE NOTICE 'APPLY OK: both tables exist and are registered hypertables.';
END
$$;

-- Insert a row into each and confirm a chunk materializes (hypertable proof).
INSERT INTO public.twin_decisions
    (ts, twin_env, twin_ref, input_ts, mode, climate_action, mist_stage,
     relay_fog, relay_vent, relay_fan1, relay_fan2, relay_heat1, relay_heat2,
     mode_reason, override_bits, twin_metadata)
VALUES
    (now(), 'prod', 'deadbeef', now(), 'IDLE', 'IDLE', 0,
     false, false, false, false, false, false,
     'band_ok', 0, '{"vpd_zone_inputs":"homogenized"}'::jsonb);

INSERT INTO public.firmware_twin_divergence
    (ts, comparison, window_start, window_end, ref_twin_ref, cmp_twin_ref,
     samples, disagree_count, relay_disagree_pct, mode_disagree_pct)
VALUES
    (now(), 'stage_vs_prod', now() - interval '48 hours', now(), 'prodsha', 'stagesha',
     17280, 0, 0.0, 0.0);

DO $$
DECLARE c int;
BEGIN
    SELECT count(*) INTO c FROM timescaledb_information.chunks
     WHERE hypertable_name IN ('twin_decisions', 'firmware_twin_divergence');
    IF c < 2 THEN
        RAISE EXCEPTION 'APPLY FAIL: expected >=2 chunks after insert (one per hypertable), got %', c;
    END IF;
    -- env CHECK constraint must reject a bogus env.
    BEGIN
        INSERT INTO public.twin_decisions
            (ts, twin_env, twin_ref, input_ts, mode, mist_stage,
             relay_fog, relay_vent, relay_fan1, relay_fan2, relay_heat1, relay_heat2,
             override_bits)
        VALUES (now(), 'bogus', 'x', now(), 'IDLE', 0,
                false, false, false, false, false, false, 0);
        RAISE EXCEPTION 'APPLY FAIL: twin_env CHECK did not reject bogus env';
    EXCEPTION WHEN check_violation THEN
        NULL; -- expected
    END;
    RAISE NOTICE 'APPLY OK: chunks materialized; twin_env CHECK enforced.';
END
$$;

-- ---------------------------------------------------------------------------
-- 4. Assert twin_ro privilege set is EXACTLY the intended narrow set.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    rec record;
    n_select int;
    n_insert int;
    n_bad    int;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'twin_ro') THEN
        RAISE EXCEPTION 'PRIV FAIL: twin_ro role not created';
    END IF;

    -- All table privileges held by twin_ro in public, via the live grant catalog.
    -- Expect: SELECT on the six telemetry tables; INSERT on the two obs tables.
    SELECT count(*) INTO n_select
      FROM information_schema.role_table_grants
     WHERE grantee = 'twin_ro' AND table_schema = 'public'
       AND privilege_type = 'SELECT'
       AND table_name IN ('climate','equipment_state','system_state',
                          'setpoint_snapshot','setpoint_changes','climate_action_log');
    IF n_select <> 6 THEN
        RAISE EXCEPTION 'PRIV FAIL: expected 6 SELECT grants, got %', n_select;
    END IF;

    SELECT count(*) INTO n_insert
      FROM information_schema.role_table_grants
     WHERE grantee = 'twin_ro' AND table_schema = 'public'
       AND privilege_type = 'INSERT'
       AND table_name IN ('twin_decisions','firmware_twin_divergence');
    IF n_insert <> 2 THEN
        RAISE EXCEPTION 'PRIV FAIL: expected 2 INSERT grants, got %', n_insert;
    END IF;

    -- ANY UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER granted to twin_ro = fail.
    SELECT count(*) INTO n_bad
      FROM information_schema.role_table_grants
     WHERE grantee = 'twin_ro' AND table_schema = 'public'
       AND privilege_type IN ('UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER');
    IF n_bad <> 0 THEN
        RAISE EXCEPTION 'PRIV FAIL: twin_ro holds % UPDATE/DELETE-class grant(s) (must be 0)', n_bad;
    END IF;

    -- SELECT must NOT be granted on the observability tables (INSERT-only there)
    -- and INSERT must NOT be granted on any telemetry table.
    SELECT count(*) INTO n_bad
      FROM information_schema.role_table_grants
     WHERE grantee = 'twin_ro' AND table_schema = 'public'
       AND privilege_type = 'INSERT'
       AND table_name IN ('climate','equipment_state','system_state',
                          'setpoint_snapshot','setpoint_changes','climate_action_log');
    IF n_bad <> 0 THEN
        RAISE EXCEPTION 'PRIV FAIL: twin_ro has INSERT on % control/telemetry table(s) (must be 0)', n_bad;
    END IF;

    -- Total grant rows must be exactly 8 (6 SELECT + 2 INSERT) — nothing extra.
    SELECT count(*) INTO n_bad
      FROM information_schema.role_table_grants
     WHERE grantee = 'twin_ro' AND table_schema = 'public';
    IF n_bad <> 8 THEN
        RAISE EXCEPTION 'PRIV FAIL: twin_ro has % total grants, expected exactly 8', n_bad;
    END IF;

    -- Role must be NOLOGIN (group role; no credential lives in a migration).
    SELECT count(*) INTO n_bad FROM pg_roles WHERE rolname = 'twin_ro' AND rolcanlogin;
    IF n_bad <> 0 THEN
        RAISE EXCEPTION 'PRIV FAIL: twin_ro must be NOLOGIN';
    END IF;

    RAISE NOTICE 'PRIV OK: twin_ro = exactly 6 SELECT + 2 INSERT, no UPDATE/DELETE, NOLOGIN.';
END
$$;

-- LIVE denial proof: as twin_ro, a control-plane write MUST raise
-- insufficient_privilege (this is the L2 entrypoint assertion the design
-- requires). SET ROLE drops to twin_ro; the INSERT must fail; RESET ROLE
-- restores. We wrap in a savepoint so the failed write doesn't poison the
-- session.
DO $$
DECLARE got_denied boolean := false;
BEGIN
    SET LOCAL ROLE twin_ro;
    BEGIN
        INSERT INTO public.equipment_state (ts, equipment, state)
        VALUES (now(), 'fan1', true);
    EXCEPTION WHEN insufficient_privilege THEN
        got_denied := true;
    END;
    RESET ROLE;
    IF NOT got_denied THEN
        RAISE EXCEPTION 'PRIV FAIL: twin_ro was ALLOWED to INSERT into control table equipment_state';
    END IF;
    RAISE NOTICE 'PRIV OK: twin_ro INSERT into equipment_state denied (insufficient_privilege).';
END
$$;

-- LIVE allow proof: as twin_ro, INSERT into twin_decisions MUST succeed,
-- but UPDATE/DELETE on it MUST be denied (append-only).
DO $$
DECLARE denied_update boolean := false; denied_delete boolean := false;
BEGIN
    SET LOCAL ROLE twin_ro;
    INSERT INTO public.twin_decisions
        (ts, twin_env, twin_ref, input_ts, mode, mist_stage,
         relay_fog, relay_vent, relay_fan1, relay_fan2, relay_heat1, relay_heat2,
         override_bits)
    VALUES (now(), 'stage', 'roprobe', now(), 'IDLE', 0,
            false, false, false, false, false, false, 0);
    BEGIN
        UPDATE public.twin_decisions SET mode = 'HEAT' WHERE twin_ref = 'roprobe';
    EXCEPTION WHEN insufficient_privilege THEN
        denied_update := true;
    END;
    BEGIN
        DELETE FROM public.twin_decisions WHERE twin_ref = 'roprobe';
    EXCEPTION WHEN insufficient_privilege THEN
        denied_delete := true;
    END;
    RESET ROLE;
    IF NOT denied_update THEN
        RAISE EXCEPTION 'PRIV FAIL: twin_ro was ALLOWED to UPDATE twin_decisions (must be INSERT-only)';
    END IF;
    IF NOT denied_delete THEN
        RAISE EXCEPTION 'PRIV FAIL: twin_ro was ALLOWED to DELETE twin_decisions (must be INSERT-only)';
    END IF;
    RAISE NOTICE 'PRIV OK: twin_ro can INSERT twin_decisions but UPDATE/DELETE denied.';
END
$$;

-- ---------------------------------------------------------------------------
-- 5. IDEMPOTENCY: re-apply the migration body; must not error and must not
--    change the privilege set or duplicate the hypertables.
-- ---------------------------------------------------------------------------
\echo '--- re-applying migration 155 (idempotency) ---'
\i :migration

DO $$
DECLARE n_grants int; n_hyper int;
BEGIN
    SELECT count(*) INTO n_grants
      FROM information_schema.role_table_grants
     WHERE grantee = 'twin_ro' AND table_schema = 'public';
    IF n_grants <> 8 THEN
        RAISE EXCEPTION 'IDEMPOTENCY FAIL: re-apply changed grant count to % (expected 8)', n_grants;
    END IF;
    SELECT count(*) INTO n_hyper
      FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public'
       AND hypertable_name IN ('twin_decisions','firmware_twin_divergence');
    IF n_hyper <> 2 THEN
        RAISE EXCEPTION 'IDEMPOTENCY FAIL: hypertable count is % (expected 2)', n_hyper;
    END IF;
    RAISE NOTICE 'IDEMPOTENCY OK: re-apply is a no-op (8 grants, 2 hypertables, no error).';
END
$$;

-- ---------------------------------------------------------------------------
-- 6. ROLLBACK (the documented rollback from the migration header / PR body).
-- ---------------------------------------------------------------------------
\echo '--- applying documented rollback ---'
REVOKE ALL ON public.twin_decisions, public.firmware_twin_divergence FROM twin_ro;
REVOKE ALL ON public.climate, public.equipment_state, public.system_state,
       public.setpoint_snapshot, public.setpoint_changes,
       public.climate_action_log FROM twin_ro;
DROP TABLE IF EXISTS public.firmware_twin_divergence;
DROP TABLE IF EXISTS public.twin_decisions;
DROP ROLE IF EXISTS twin_ro;

DO $$
DECLARE n int;
BEGIN
    IF to_regclass('public.twin_decisions') IS NOT NULL THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: twin_decisions still present';
    END IF;
    IF to_regclass('public.firmware_twin_divergence') IS NOT NULL THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: firmware_twin_divergence still present';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'twin_ro') THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: twin_ro role still present';
    END IF;
    SELECT count(*) INTO n FROM timescaledb_information.hypertables
     WHERE hypertable_schema = 'public'
       AND hypertable_name IN ('twin_decisions','firmware_twin_divergence');
    IF n <> 0 THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: % twin hypertable(s) still in catalog', n;
    END IF;
    -- Dependency tables the twin only read must be untouched by the rollback.
    IF to_regclass('public.climate') IS NULL OR to_regclass('public.equipment_state') IS NULL THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: rollback should not drop telemetry tables';
    END IF;
    RAISE NOTICE 'ROLLBACK OK: both tables + twin_ro gone; telemetry tables intact.';
END
$$;

DO $$ BEGIN RAISE NOTICE 'ALL FIXTURE ASSERTIONS PASSED (apply + idempotency + rollback).'; END $$;
