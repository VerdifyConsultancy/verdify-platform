-- 158-compression-retention-policies.sql
-- =============================================================================
-- G3 (issue #72, #33-family): recreate the TimescaleDB compression + retention
-- background-job POLICIES so the migrated in-cluster DB matches the live VM.
--
-- PARITY TARGET (docs/runbooks/db-copy-not-move.md §"Canonical parity baseline"
-- + scripts/db-parity.sh): the live VM (TimescaleDB 2.25.2) carries 11 background
-- jobs and 5 compressed hypertables:
--
--   * 11 bg jobs = policy_compression×4 + policy_retention×5
--                  + refresh_climate_merged×1 + refresh_relay_stuck×1
--                  (job_ids 1002-1009,1014,1015,1021)
--   * 5 compressed hypertables: climate, diagnostics, energy, esp32_logs,
--                               setpoint_snapshot
--
-- This migration is G3 and owns ONLY the compression + retention policies — the
-- policy_compression×4 + policy_retention×5 = 9 of those 11 jobs, plus the
-- compression-ENABLED flag on all 5 compressed hypertables. The remaining 2 jobs
-- (refresh_climate_merged / refresh_relay_stuck) are matview-refresh user-defined
-- actions; on the live VM those run as host-cron (migration 047), are NOT
-- TimescaleDB compression/retention policies, and are explicitly OUT OF SCOPE for
-- G3. (If a later migration registers them as TimescaleDB UDA jobs to reach the
-- full 11, that is a separate, serialized PR.)
--
-- WHY THESE ARE NOT IN db/schema.sql: db/schema.sql is a pg_dump snapshot from the
-- TimescaleDB *Community Edition*. Community pg_dump emits neither create_hypertable
-- calls (repaired by migrations 000 + 157/G2) NOR add_compression_policy /
-- add_retention_policy / ALTER TABLE ... SET (timescaledb.compress ...) — the
-- compression settings and the policy jobs are TimescaleDB *catalog* state, not
-- table DDL, so they are silently dropped by the dump. The authoritative source
-- for the exact policies is therefore the repo's own migration history (where the
-- live policies were originally created) cross-checked against the parity baseline:
--
--   * compression policy (compress_after 7d): climate, energy, diagnostics
--         (migration 050), setpoint_snapshot (migrations 060/149)            -> 4
--   * retention policy: climate 365d, energy 365d, diagnostics 180d,
--         esp32_logs 30d (migration 050), setpoint_snapshot 90d (migration 060) -> 5
--   * compression ENABLED on all 5 (climate, energy, diagnostics, esp32_logs,
--         setpoint_snapshot). esp32_logs is compression-ENABLED but has NO
--         compression policy — exactly the 5-compressed / 4-compression-job split
--         in the parity baseline (esp32_logs shows "3 chunks (0 compressed)":
--         compression is enabled but nothing auto-compresses it; its 30-day
--         retention drops chunks before a manual compress would matter).
--
-- compress_segmentby / compress_orderby: setpoint_snapshot reuses the exact
-- settings migration 149 created on the live VM (segmentby 'parameter',
-- orderby 'ts DESC'). For climate / energy / diagnostics / esp32_logs the live
-- compress settings are not recoverable from any checked-in artifact (the dump
-- strips them and migration 050 created the policy without an explicit
-- ALTER ... SET), so we enable compression with TimescaleDB's safe default
-- ordering (compress_orderby 'ts DESC', no segmentby). This makes the hypertable
-- count as compression_enabled (the parity dimension db-parity.sh dimension 8
-- checks) and lets add_compression_policy register; segmentby is a storage-layout
-- optimization, not a parity dimension, and can be tuned later without affecting
-- the job/compressed-set parity this migration restores.
--
-- DEPENDENCY ON G2 (migration 157): G2 converts the 15 non-core telemetry tables
-- (incl. energy, diagnostics, esp32_logs, setpoint_snapshot) from PLAIN tables
-- back into hypertables. add_compression_policy / add_retention_policy /
-- ALTER ... SET (timescaledb.compress) only work on a registered hypertable. As of
-- this PR, G2 (iris/g2-hypertables, migration 157) is NOT YET MERGED into
-- live/platform-main. This migration is therefore authored as the NEXT number
-- (158, after 157) and is written to be SAFE whether or not G2 has run: every
-- table is guarded by a check that it is a *registered hypertable* before any
-- compression/retention call. If G2 has run (or on the live VM) all 5 are
-- hypertables and all policies are (re)asserted. If G2 has NOT run, only `climate`
-- (a core hypertable from migration 000) is touched and the other 4 are skipped
-- idempotently — so 158 must be applied AFTER 157 to reach full parity. The order
-- 157 -> 158 is the serialized sequence coordinator approves.
--
-- IDEMPOTENCY: ALTER TABLE ... SET (timescaledb.compress...) is a settings UPSERT
-- (safe to re-run). add_compression_policy / add_retention_policy are called with
-- if_not_exists => TRUE, so a re-apply (and the LIVE no-op, where these policies
-- already exist) is a clean no-op — no duplicate jobs. The hypertable guard also
-- means a missing table is skipped, never an error.
--
-- #23 ROLLBACK-REPLAY SAFETY: this migration is NON-self-transactional. It has NO
-- top-level BEGIN;/COMMIT; and NO commit-forcing statement (no CREATE INDEX
-- CONCURRENTLY, no VACUUM, no ALTER SYSTEM, no CREATE/DROP DATABASE|TABLESPACE).
-- All work is inside a single plpgsql DO block. ALTER TABLE ... SET, and the
-- TimescaleDB add_compression_policy / add_retention_policy functions, all run
-- inside an ordinary transaction in TimescaleDB 2.x, so this whole migration is
-- SAFE to wrap in an outer `BEGIN; ... ROLLBACK;` for rollback validation — there
-- is no inner COMMIT to defeat the rollback. (Verified by
-- scripts/check_migration_rollback_safety.py: classified safe-to-wrap.)
--
-- SCHEMA-CHANGE SERVICE-RESTART NOTE (CLAUDE.md rule 7): this PR touches NONE of
-- verdify_schemas/**, ingestor/entity_map.py, or mcp/server.py. It only changes
-- TimescaleDB physical/catalog policy state (compression flag + background policy
-- jobs); table names, columns, and the logical read/write contract are unchanged.
-- No service needs to bounce (verdify-mcp / verdify-ingestor keep working). The
-- policies are background jobs that run server-side on the StatefulSet.
--
-- ROLLBACK (documented; for the disposable rollback fixture, NEVER live — on live
-- these policies are long-standing and dropping them stops compression/retention):
-- the inverse of each operation, in reverse:
--
--   -- remove the 4 compression policies (skips ones not present):
--   SELECT remove_compression_policy('setpoint_snapshot', if_exists => TRUE);
--   SELECT remove_compression_policy('diagnostics',       if_exists => TRUE);
--   SELECT remove_compression_policy('energy',            if_exists => TRUE);
--   SELECT remove_compression_policy('climate',           if_exists => TRUE);
--   -- remove the 5 retention policies (skips ones not present):
--   SELECT remove_retention_policy('setpoint_snapshot', if_exists => TRUE);
--   SELECT remove_retention_policy('esp32_logs',        if_exists => TRUE);
--   SELECT remove_retention_policy('diagnostics',       if_exists => TRUE);
--   SELECT remove_retention_policy('energy',            if_exists => TRUE);
--   SELECT remove_retention_policy('climate',           if_exists => TRUE);
--   -- (optionally) disable compression on each — only valid once chunks are
--   -- decompressed; for the empty fixture tables it is a clean settings reset:
--   ALTER TABLE setpoint_snapshot SET (timescaledb.compress = false);
--   ALTER TABLE esp32_logs        SET (timescaledb.compress = false);
--   ALTER TABLE diagnostics       SET (timescaledb.compress = false);
--   ALTER TABLE energy            SET (timescaledb.compress = false);
--   ALTER TABLE climate           SET (timescaledb.compress = false);
--
-- The fixture (db/migrations/tests/test-158-compression-retention-policies.sql)
-- runs this exact rollback on a disposable container and asserts the
-- compression/retention job count and compressed-hypertable count return to 0.
-- =============================================================================

DO $$
DECLARE
    rec          record;
    is_ht        boolean;
BEGIN
    -- ── Step 1: enable compression on the 5 canonical compressed hypertables ──
    -- segmentby/orderby per the WHY-block above. esp32_logs is enabled here but
    -- intentionally gets NO compression policy in step 2 (5 compressed / 4 jobs).
    FOR rec IN
        SELECT * FROM (VALUES
            ('climate'::text,           NULL::text,        'ts DESC'::text),
            ('energy',                  NULL,              'ts DESC'),
            ('diagnostics',             NULL,              'ts DESC'),
            ('esp32_logs',              NULL,              'ts DESC'),
            ('setpoint_snapshot',       'parameter',       'ts DESC')
        ) AS t(table_name, segmentby, orderby)
    LOOP
        -- Skip if the table is not a registered hypertable in this DB. This is
        -- the G2-not-yet-merged guard (only climate is a hypertable then) AND the
        -- defensive guard for a partial schema. It also short-circuits before any
        -- compression call so nothing runs that could matter on a non-hypertable.
        SELECT EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables
             WHERE hypertable_schema = 'public'
               AND hypertable_name = rec.table_name
        ) INTO is_ht;

        IF NOT is_ht THEN
            RAISE NOTICE '158: % is not a hypertable here (G2 not applied?) — skipping compression enable', rec.table_name;
            CONTINUE;
        END IF;

        -- Enable compression (settings UPSERT — idempotent / safe to re-run).
        IF rec.segmentby IS NULL THEN
            EXECUTE format(
                'ALTER TABLE public.%I SET (timescaledb.compress = true, timescaledb.compress_orderby = %L)',
                rec.table_name, rec.orderby);
        ELSE
            EXECUTE format(
                'ALTER TABLE public.%I SET (timescaledb.compress = true, timescaledb.compress_segmentby = %L, timescaledb.compress_orderby = %L)',
                rec.table_name, rec.segmentby, rec.orderby);
        END IF;
        RAISE NOTICE '158: compression enabled on public.%', rec.table_name;
    END LOOP;

    -- ── Step 2: (re)create the 4 compression POLICIES (compress_after 7d) ──────
    -- climate, energy, diagnostics, setpoint_snapshot. NOT esp32_logs.
    FOR rec IN
        SELECT * FROM (VALUES
            ('climate'::text),
            ('energy'),
            ('diagnostics'),
            ('setpoint_snapshot')
        ) AS t(table_name)
    LOOP
        SELECT EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables
             WHERE hypertable_schema = 'public' AND hypertable_name = rec.table_name
        ) INTO is_ht;
        IF NOT is_ht THEN
            CONTINUE;
        END IF;

        -- if_not_exists => idempotent; no duplicate policy_compression job.
        EXECUTE format(
            'SELECT add_compression_policy(%L, INTERVAL ''7 days'', if_not_exists => TRUE)',
            rec.table_name);
        RAISE NOTICE '158: compression policy ensured on public.% (7d)', rec.table_name;
    END LOOP;

    -- ── Step 3: (re)create the 5 retention POLICIES (per-table interval) ───────
    -- climate 365d, energy 365d, diagnostics 180d, esp32_logs 30d,
    -- setpoint_snapshot 90d — exactly the live intervals (migrations 050 + 060).
    FOR rec IN
        SELECT * FROM (VALUES
            ('climate'::text,         '365 days'::text),
            ('energy',                '365 days'),
            ('diagnostics',           '180 days'),
            ('esp32_logs',            '30 days'),
            ('setpoint_snapshot',     '90 days')
        ) AS t(table_name, drop_after)
    LOOP
        SELECT EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables
             WHERE hypertable_schema = 'public' AND hypertable_name = rec.table_name
        ) INTO is_ht;
        IF NOT is_ht THEN
            CONTINUE;
        END IF;

        -- if_not_exists => idempotent; no duplicate policy_retention job.
        EXECUTE format(
            'SELECT add_retention_policy(%L, INTERVAL %L, if_not_exists => TRUE)',
            rec.table_name, rec.drop_after);
        RAISE NOTICE '158: retention policy ensured on public.% (drop_after %)', rec.table_name, rec.drop_after;
    END LOOP;
END $$;
