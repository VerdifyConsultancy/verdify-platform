-- db/ledger/schema_migrations.sql
--
-- Applied-migrations ledger for Verdify. DESIGN ARTIFACT / DDL TEMPLATE — this
-- is intentionally NOT a numbered db/migrations/NNN-*.sql file. Numbered
-- migrations are serialized and validated holistically by the coordinator; this
-- ledger lands separately so its rollout sequence is decided as part of
-- IRIS-W008 (restore reconcile) / IRIS-W010 (prod migration), not slipped into
-- the migration stream out of band.
--
-- WHY A LEDGER AT ALL
-- Today there is no schema_migrations / applied_migrations table and no
-- alembic_version. The migrate runner is a schema.sql-replay + "verify-not-
-- rebuild" model and db/migrations/000-150 are bare numbered SQL files. Parity
-- between iris and a restore TARGET is asserted only narratively. scripts/
-- db-parity.sh measures the live shape (tables/extensions/hypertables/jobs/
-- compression/row-counts/timestamps); this ledger records the *provenance* of
-- that shape so a restored DB can prove it carries the same migration set.
--
-- IDENTITY KEY = FULL FILENAME, NOT AN INTEGER VERSION
-- The repo has duplicate numbers (031, 060, 070, 071, 072, 073, 074, 078 each
-- appear twice) and gaps (001, 019, 067 are absent). An integer `version`
-- column cannot represent that history, so the primary key is the migration's
-- full filename (its stable identity), plus the originating `source` so the
-- planner_graph migration (which lives in the separate verdify planner repo)
-- coexists with the db/migrations stream in one ledger.
--
-- This DDL is idempotent (IF NOT EXISTS) and READ-SAFE to inspect; it only
-- creates an empty ledger table + a stamping helper. It does NOT backfill rows
-- (see db/ledger/backfill_ledger.sql for the generated stamping statements).

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    -- Full relative path identity, e.g. 'db/migrations/031-setpoint-plan.sql'
    -- or 'planner_graph/migrations/001_planner_memory.sql'. Stable across the
    -- duplicate-number history.
    filename     TEXT        NOT NULL,
    -- Originating stream: 'db/migrations' (verdify-platform) or
    -- 'planner_graph/migrations' (verdify planner repo).
    source       TEXT        NOT NULL DEFAULT 'db/migrations',
    -- Sort key parsed from the leading number (031, 095a -> 95). Duplicate
    -- numbers are allowed; this is for ordering/reporting only, never identity.
    seq          INTEGER     NULL,
    -- sha256 of the migration file at stamp time, so a restored DB can detect a
    -- file that was edited-in-place after it was applied.
    sha256       TEXT        NULL,
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- How the row got here: 'baseline' (stamped at ledger introduction against
    -- the existing prod schema), 'runner' (stamped by the migrate runner on a
    -- fresh apply), or 'manual'.
    stamp_method TEXT        NOT NULL DEFAULT 'runner',
    -- Runner audit fields (#583 Lane B): wall-clock apply duration and the DB
    -- role that applied the file. NULL for baseline stamps.
    duration_ms  INTEGER     NULL,
    applied_by   TEXT        NULL,
    PRIMARY KEY (source, filename)
);

-- Upgrade path for ledgers created before the audit columns existed.
ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS duration_ms INTEGER;
ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS applied_by  TEXT;

COMMENT ON TABLE schema_migrations IS
    'Applied-migrations ledger (IRIS-W007). Identity = (source, filename); '
    'integer version is insufficient due to duplicate/gapped migration numbers. '
    'Populated by the migrate runner and by db/ledger/backfill_ledger.sql.';

-- Stamping helper. The migrate runner calls this once per file it applies (or
-- verifies present, under verify-not-rebuild). Idempotent: re-stamping a file
-- refreshes its sha256/applied_at rather than erroring.
--
-- The pre-#583 5-argument signature is dropped (if present) and replaced by a
-- single 7-argument version whose trailing args default to NULL, so existing
-- 5-positional callers (db/ledger/backfill_ledger.sql) resolve unambiguously.
DROP FUNCTION IF EXISTS stamp_migration(TEXT, TEXT, INTEGER, TEXT, TEXT);

CREATE OR REPLACE FUNCTION stamp_migration(
    p_filename    TEXT,
    p_source      TEXT DEFAULT 'db/migrations',
    p_seq         INTEGER DEFAULT NULL,
    p_sha256      TEXT DEFAULT NULL,
    p_method      TEXT DEFAULT 'runner',
    p_duration_ms INTEGER DEFAULT NULL,
    p_applied_by  TEXT DEFAULT NULL
) RETURNS VOID
LANGUAGE sql AS $$
    INSERT INTO schema_migrations
        (filename, source, seq, sha256, applied_at, stamp_method, duration_ms, applied_by)
    VALUES
        (p_filename, p_source, p_seq, p_sha256, now(), p_method,
         p_duration_ms, COALESCE(p_applied_by, current_user))
    ON CONFLICT (source, filename) DO UPDATE
        SET sha256 = EXCLUDED.sha256,
            seq = EXCLUDED.seq,
            applied_at = now(),
            stamp_method = EXCLUDED.stamp_method,
            duration_ms = EXCLUDED.duration_ms,
            applied_by = EXCLUDED.applied_by;
$$;

COMMENT ON FUNCTION stamp_migration(TEXT, TEXT, INTEGER, TEXT, TEXT, INTEGER, TEXT) IS
    'Idempotently record that a migration file has been applied/verified.';

COMMIT;

-- HOW THE MIGRATE RUNNER STAMPS IT (verify-not-rebuild model)
-- ----------------------------------------------------------------------------
-- The runner (db/Dockerfile.migrate, in the platform deploy path) already
-- replays db/schema.sql then iterates db/migrations/*.sql in sorted order,
-- treating each as "apply if absent, otherwise verify". To add ledgering it
-- wraps each file in a transaction with a stamp call, e.g.:
--
--     BEGIN;
--       \i db/migrations/031-setpoint-plan.sql
--       SELECT stamp_migration(
--           'db/migrations/031-setpoint-plan.sql', 'db/migrations',
--           31, '<sha256 of the file>', 'runner');
--     COMMIT;
--
-- On a fresh build every file is applied and stamped. On an existing prod DB
-- (verify-not-rebuild) the DDL is mostly no-ops (CREATE ... IF NOT EXISTS),
-- but the stamp still records provenance — that is the one-time backfill
-- captured in db/ledger/backfill_ledger.sql, run with stamp_method='baseline'.
-- The planner_graph migration is stamped by its own apply step with
-- source='planner_graph/migrations' so both streams share one ledger.
--
-- PARITY USE
-- A restored TARGET is migration-equivalent to iris iff
--   SELECT source, filename, sha256 FROM schema_migrations ORDER BY 1,2
-- is identical on both sides. scripts/db-parity.sh covers the *physical* shape;
-- once this ledger exists, a 10th provenance dimension can diff that query and
-- catch "same shape, different applied history" (e.g. a hand-patched restore).
