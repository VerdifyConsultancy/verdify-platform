-- Migration 151: Backfill resolved_at on the historical suppressed-orphan
--                sensor_offline alert_log rows (issue #49, audit §7-#10).
--
-- CONTEXT
-- alert_log carries ~61 pre-existing rows with disposition='suppressed' AND
-- alert_type='sensor_offline' AND resolved_at IS NULL. The forward alert
-- lifecycle fix already shipped (migration 149/M5 made v_open_alerts canonical:
-- `resolved_at IS NULL AND disposition <> 'suppressed'`), so these rows are
-- already EXCLUDED from the canonical open-alert view. But a bare
-- `resolved_at IS NULL` query still surfaces them, polluting parity counts
-- before the DB is migrated. This is a one-time data-hygiene UPDATE to close
-- the lifecycle on those rows (suppressed-closed) so that `resolved_at IS NULL`
-- and v_open_alerts agree on count.
--
-- Prior backfills (migrations 102, 106) handled the INVERSE mismatch
-- (resolved_at IS NOT NULL AND disposition <> 'resolved'); they deliberately
-- did NOT touch rows lacking resolved_at, which is exactly this remaining gap.
--
-- SCOPE (EXACT — never widen)
--   disposition  = 'suppressed'
--   AND alert_type = 'sensor_offline'
--   AND resolved_at IS NULL
-- No row outside that predicate is read or written.
--
-- VALUE
-- resolved_at is set to COALESCE(updated_at, created_at): the last recorded
-- lifecycle activity on the row, i.e. when it was suppressed-closed. This is
-- the "last related activity" defensible value. It is deterministic and
-- replay-stable (NOT now()-based), so re-running on a row that somehow lacks
-- resolved_at would reproduce the same timestamp. resolved_by and resolution
-- are stamped via COALESCE so an existing value is never clobbered.
--
-- IDEMPOTENCY
-- The `resolved_at IS NULL` predicate makes a re-apply match zero rows
-- (re-apply = no-op, no error). Additive / non-destructive: no DROP, no live
-- data removed, scope is a fixed predicate.
--
-- ROLLBACK (documented; see PR body)
--   UPDATE alert_log
--      SET resolved_at = NULL,
--          resolved_by = NULL,
--          resolution  = NULL,
--          updated_at  = now()
--    WHERE disposition = 'suppressed'
--      AND alert_type  = 'sensor_offline'
--      AND resolved_by = 'migration_151_backfill';
-- The resolved_by tag makes the rollback target exactly the rows this
-- migration set (it never NULLs a row that already had its own resolution).
--
-- ROLLBACK-REPLAY SAFETY (issue #23)
-- This migration contains NO top-level COMMIT and no commit-forcing statement
-- (e.g. CREATE INDEX CONCURRENTLY). It is plain DML, like migration 134, so the
-- rollback-validation harness can wrap it in an outer BEGIN..ROLLBACK without
-- the migration self-committing and defeating the dry-run.
--
-- RESTARTS (CLAUDE.md rule 7): this migration does NOT touch verdify_schemas/**,
-- ingestor/entity_map.py, or mcp/server.py, so no service-restart obligation is
-- triggered. Data-only UPDATE on a plain table (alert_log is not a hypertable).

UPDATE alert_log
SET resolved_at = COALESCE(updated_at, created_at),
    resolved_by = 'migration_151_backfill',
    resolution  = COALESCE(
        resolution,
        'migration_151: suppressed-closed sensor_offline orphan; resolved_at backfilled '
        || 'to last lifecycle activity (issue #49 / audit §7-#10)'
    ),
    updated_at  = now()
WHERE disposition = 'suppressed'
  AND alert_type  = 'sensor_offline'
  AND resolved_at IS NULL;
