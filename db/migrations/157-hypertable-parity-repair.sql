-- 157-hypertable-parity-repair.sql
-- =============================================================================
-- G2 (issue #72, #33-family): full TimescaleDB hypertable parity repair.
--
-- Problem: db/schema.sql is a pg_dump snapshot. When it is replayed to stand up
-- an empty in-cluster (k3s) database, the inherited _timescaledb_internal chunk
-- tables come across but TimescaleDB's hypertable *catalog* rows do not get
-- reconstructed (pg_dump from the Community edition does not emit the
-- create_hypertable calls). The result: a table that is "already partitioned"
-- in the dump's eyes but is ABSENT from timescaledb_information.hypertables, so
-- in-cluster it lands as a PLAIN table — no chunking, no compression, no
-- retention. db/migrations/000-fresh-schema-hypertable-repair.sql repairs only
-- the 4 CORE hypertables (climate, equipment_state, system_state,
-- weather_forecast). The other 15 of the canonical 19 come out flat.
--
-- The canonical 19-hypertable set (parity baseline: docs/runbooks/
-- db-copy-not-move.md + scripts/db-parity.sh) was derived by diffing
-- db/schema.sql's hypertables (every public table whose chunks appear as
-- _hyper_<htid>_<chunkid>_chunk INHERITS (public.<parent>), all on time column
-- `ts`, plus model_predictions which has no chunks yet but is a hypertable via
-- migration 072) against the 4 repaired by 000-*. Every one of the 19 uses the
-- TimescaleDB DEFAULT chunk_time_interval (7 days): the climate chunk CHECK
-- boundaries in db/schema.sql step exactly 7 days, and no create_hypertable call
-- anywhere in db/migrations/ specifies a custom chunk_time_interval. So this
-- migration matches schema.sql's create_hypertable definitions by using the
-- same time column (`ts`) and the same (default) interval — i.e. no explicit
-- chunk_time_interval argument.
--
-- This migration converts the 15 MISSING telemetry tables into hypertables:
--   setpoint_changes, diagnostics, energy, esp32_logs, weather_station,
--   setpoint_plan, irrigation_log, setpoint_snapshot, forecast_deviation_log,
--   override_events, setpoint_clamps, gpu_power, infra_cpu, climate_action_log,
--   model_predictions
-- so that, together with the 4 from 000-*, timescaledb_information.hypertables
-- reaches the canonical 19. On a REAL production database every one of these is
-- already a registered hypertable, so this migration is a no-op there.
--
-- Each conversion uses:
--     create_hypertable(<tbl>, 'ts', if_not_exists => TRUE, migrate_data => TRUE)
-- exactly as 000-* and the original create_hypertable calls do — `if_not_exists`
-- makes it safe on a table that is already a hypertable; `migrate_data` makes it
-- safe on a table that already holds rows (e.g. setpoint_snapshot's millions),
-- moving existing rows into chunks. We additionally guard with the TimescaleDB
-- catalog (skip tables that are missing or already registered) so re-applies and
-- the production no-op are clean, and so a table sitting as a partitioned-but-
-- unregistered shell from the dump gets its orphan _timescaledb_internal chunk
-- children dropped first (same approach as migration 000).
--
-- #23 rollback-replay safety: this migration is NON-self-transactional — it has
-- NO top-level BEGIN;/COMMIT; and NO commit-forcing statement (no CREATE INDEX
-- CONCURRENTLY, no VACUUM). All work is in a single plpgsql DO block. It is
-- therefore SAFE to wrap in an outer `BEGIN; ... ROLLBACK;` for rollback
-- validation: there is no inner COMMIT to defeat the rollback. create_hypertable
-- runs fine in psql's implicit autocommit and inside a transaction when the
-- table is empty (the rollback-replay fixture exercises the empty case). On the
-- live no-op path the tables are already hypertables, so the guard short-circuits
-- BEFORE any create_hypertable call — nothing executes that could force a commit.
-- It is idempotent (catalog guard + if_not_exists => TRUE), so it is safe to
-- re-run.
--
-- Schema-change service-restart note (CLAUDE.md rule 7 does not apply — this PR
-- touches neither verdify_schemas/** nor ingestor/entity_map.py nor mcp/server.py
-- — but for completeness): no service needs to bounce. This only changes physical
-- table topology (plain -> hypertable); table names, columns, and the logical
-- read/write contract are unchanged, so verdify-mcp / verdify-ingestor keep
-- working without a restart.
--
-- ROLLBACK (documented; for the disposable rollback fixture, NEVER live — on
-- live these are long-standing hypertables holding the bulk of the DB and must
-- not be flattened). De-hypertable-ing in place is not a supported TimescaleDB
-- operation, so the documented rollback reverts each table to PLAIN by copying
-- its rows into a temp table, dropping the hypertable, recreating the plain
-- table from the temp copy, and restoring rows. For the empty fixture tables
-- this collapses to: drop the hypertable wrapper and recreate the bare table.
-- The fixture (db/migrations/tests/test-157-hypertable-parity-repair.sql) runs a
-- representative rollback (drop + recreate plain) and asserts the tables revert
-- to plain (absent from timescaledb_information.hypertables):
--
--   -- per missing table T (example for setpoint_snapshot; repeat per table):
--   --   1. CREATE TEMP TABLE _rb_T AS TABLE public.T;          -- preserve rows
--   --   2. DROP TABLE public.T;                                -- drops hypertable + chunks
--   --   3. <recreate public.T with its original plain DDL from db/schema.sql>
--   --   4. INSERT INTO public.T SELECT * FROM _rb_T;           -- restore rows (now plain)
--   --   5. DROP TABLE _rb_T;
--   -- end result: public.T present, holding its rows, ABSENT from
--   --             timescaledb_information.hypertables (reverted to plain).
-- =============================================================================

DO $$
DECLARE
    target record;
    child   record;
    registered boolean;
BEGIN
    -- The 15 canonical hypertables NOT covered by migration 000 (000 does
    -- climate / equipment_state / system_state / weather_forecast). All on `ts`,
    -- all default chunk_time_interval (7 days) — matching db/schema.sql.
    FOR target IN
        SELECT * FROM (VALUES
            ('setpoint_changes'::text,       'ts'::text),
            ('diagnostics',                  'ts'),
            ('energy',                       'ts'),
            ('esp32_logs',                   'ts'),
            ('weather_station',              'ts'),
            ('setpoint_plan',                'ts'),
            ('irrigation_log',               'ts'),
            ('setpoint_snapshot',            'ts'),
            ('forecast_deviation_log',       'ts'),
            ('override_events',              'ts'),
            ('setpoint_clamps',              'ts'),
            ('gpu_power',                    'ts'),
            ('infra_cpu',                    'ts'),
            ('climate_action_log',          'ts'),
            ('model_predictions',           'ts')
        ) AS t(table_name, time_column)
    LOOP
        -- Skip if the table does not exist in this database (defensive: lets the
        -- migration apply cleanly on a partial/older schema).
        IF to_regclass(format('public.%I', target.table_name)) IS NULL THEN
            CONTINUE;
        END IF;

        -- Skip if it is already a registered hypertable. This is the LIVE no-op
        -- path AND the idempotent re-apply path; it short-circuits before any
        -- create_hypertable call, so nothing commit-forcing ever runs on live.
        SELECT EXISTS (
            SELECT 1
            FROM timescaledb_information.hypertables
            WHERE hypertable_schema = 'public'
              AND hypertable_name = target.table_name
        ) INTO registered;

        IF registered THEN
            CONTINUE;
        END IF;

        -- Not registered but the dump may have left inherited
        -- _timescaledb_internal chunk children attached (a "partitioned but
        -- unregistered" shell). Drop those orphans first so create_hypertable
        -- starts from a clean parent — same repair pattern as migration 000.
        FOR child IN
            SELECT inhrelid::regclass AS child_table
            FROM pg_inherits
            WHERE inhparent = format('public.%I', target.table_name)::regclass
        LOOP
            EXECUTE format('DROP TABLE IF EXISTS %s CASCADE', child.child_table);
        END LOOP;

        -- Register the hypertable. Same call shape as migration 000 and the
        -- original create_hypertable calls: time column `ts`, default chunk
        -- interval, if_not_exists for safety, migrate_data so a table that
        -- already holds rows (e.g. setpoint_snapshot) keeps them, routed into
        -- chunks.
        EXECUTE format(
            'SELECT create_hypertable(%L, %L, if_not_exists => TRUE, migrate_data => TRUE)',
            target.table_name,
            target.time_column
        );

        RAISE NOTICE '157: converted public.% to hypertable on %',
            target.table_name, target.time_column;
    END LOOP;
END $$;
