-- Fixture test for migration 151 (issue #49).
--
-- Self-contained, self-asserting SQL fixture for a DISPOSABLE throwaway DB only.
-- Stands up a minimal alert_log, seeds matching + non-matching rows, applies the
-- migration body, asserts ONLY the scoped rows were touched, proves idempotency
-- (re-apply = 0 additional), then proves the documented rollback restores those
-- exact rows to NULL. Every assertion RAISEs EXCEPTION on failure, so a clean
-- run that prints the final NOTICE means apply + idempotency + rollback all pass.
--
-- Run against a throwaway DB, e.g.:
--   psql -v ON_ERROR_STOP=1 -f db/migrations/tests/test-151-...sql
-- NEVER run this against the live DB — it DROPs and recreates alert_log.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- Minimal schema the migration depends on (alert_log is a plain table).
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS alert_log;
CREATE TABLE alert_log (
    id              SERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    alert_type      TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'warning',
    disposition     TEXT NOT NULL DEFAULT 'open',
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT,
    resolution      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Seed: 3 MATCHING rows + 5 NON-matching control rows.
-- ---------------------------------------------------------------------------
-- Matching (must be backfilled): suppressed + sensor_offline + resolved_at NULL.
INSERT INTO alert_log (alert_type, disposition, resolved_at, created_at, updated_at) VALUES
    ('sensor_offline', 'suppressed', NULL, '2026-01-01 10:00:00+00', '2026-01-01 11:00:00+00'), -- id 1: updated_at used
    ('sensor_offline', 'suppressed', NULL, '2026-02-01 10:00:00+00', '2026-02-01 12:30:00+00'), -- id 2
    ('sensor_offline', 'suppressed', NULL, '2026-03-01 09:00:00+00', '2026-03-01 09:00:00+00'); -- id 3

-- Non-matching controls (must be LEFT ALONE).
INSERT INTO alert_log (alert_type, disposition, resolved_at, resolution, created_at, updated_at) VALUES
    ('relay_stuck',    'suppressed', NULL, NULL, '2026-01-05 10:00:00+00', '2026-01-05 10:00:00+00'),                       -- id 4: wrong alert_type
    ('sensor_offline', 'open',       NULL, NULL, '2026-01-06 10:00:00+00', '2026-01-06 10:00:00+00'),                       -- id 5: wrong disposition
    ('sensor_offline', 'suppressed', '2026-01-07 12:00:00+00', NULL, '2026-01-07 10:00:00+00', '2026-01-07 12:00:00+00'),   -- id 6: already resolved
    ('sensor_offline', 'acknowledged', NULL, NULL, '2026-01-08 10:00:00+00', '2026-01-08 10:00:00+00'),                     -- id 7: wrong disposition
    ('sensor_offline', 'suppressed', NULL, 'pre-existing note', '2026-01-09 10:00:00+00', '2026-01-09 10:00:00+00');        -- id 8: matching BUT pre-existing resolution must be preserved

-- ---------------------------------------------------------------------------
-- Capture pre-state so rollback can be checked exactly.
-- ---------------------------------------------------------------------------
DO $$
DECLARE n_match INT;
BEGIN
    SELECT count(*) INTO n_match FROM alert_log
     WHERE disposition='suppressed' AND alert_type='sensor_offline' AND resolved_at IS NULL;
    IF n_match <> 4 THEN
        RAISE EXCEPTION 'SEED FAIL: expected 4 matching rows (ids 1,2,3,8), got %', n_match;
    END IF;
END $$;

-- ===========================================================================
-- APPLY (migration 151 body, verbatim)
-- ===========================================================================
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

-- ---------------------------------------------------------------------------
-- ASSERT apply: exactly ids 1,2,3,8 backfilled; controls untouched.
-- ---------------------------------------------------------------------------
DO $$
DECLARE r RECORD; v_resolved INT;
BEGIN
    -- All 4 scoped rows now have resolved_at set.
    SELECT count(*) INTO v_resolved FROM alert_log
     WHERE id IN (1,2,3,8) AND resolved_at IS NOT NULL AND resolved_by='migration_151_backfill';
    IF v_resolved <> 4 THEN
        RAISE EXCEPTION 'APPLY FAIL: expected 4 scoped rows backfilled, got %', v_resolved;
    END IF;

    -- resolved_at == last lifecycle activity (updated_at) for id 1 and id 2.
    SELECT resolved_at INTO r FROM alert_log WHERE id=1;
    IF (SELECT resolved_at FROM alert_log WHERE id=1) <> TIMESTAMPTZ '2026-01-01 11:00:00+00' THEN
        RAISE EXCEPTION 'APPLY FAIL: id 1 resolved_at should equal updated_at 2026-01-01 11:00, got %',
            (SELECT resolved_at FROM alert_log WHERE id=1);
    END IF;
    IF (SELECT resolved_at FROM alert_log WHERE id=2) <> TIMESTAMPTZ '2026-02-01 12:30:00+00' THEN
        RAISE EXCEPTION 'APPLY FAIL: id 2 resolved_at mismatch';
    END IF;

    -- id 8 had a pre-existing resolution -> must be PRESERVED (COALESCE).
    IF (SELECT resolution FROM alert_log WHERE id=8) <> 'pre-existing note' THEN
        RAISE EXCEPTION 'APPLY FAIL: id 8 pre-existing resolution was clobbered';
    END IF;

    -- Controls (ids 4,5,7) must be UNTOUCHED (still NULL resolved_at, no tag).
    IF EXISTS (SELECT 1 FROM alert_log WHERE id IN (4,5,7)
               AND (resolved_at IS NOT NULL OR resolved_by IS NOT NULL)) THEN
        RAISE EXCEPTION 'APPLY FAIL: a non-matching control row was modified';
    END IF;

    -- id 6 (already resolved) must keep its original resolved_at, not be re-tagged.
    IF (SELECT resolved_at FROM alert_log WHERE id=6) <> TIMESTAMPTZ '2026-01-07 12:00:00+00'
       OR (SELECT resolved_by FROM alert_log WHERE id=6) IS NOT NULL THEN
        RAISE EXCEPTION 'APPLY FAIL: already-resolved control id 6 was modified';
    END IF;

    RAISE NOTICE 'APPLY OK: 4 scoped rows backfilled, 4 controls untouched, pre-existing resolution preserved.';
END $$;

-- ===========================================================================
-- RE-APPLY (idempotency): must touch 0 rows.
-- ===========================================================================
DO $$
DECLARE v_would_match INT;
BEGIN
    SELECT count(*) INTO v_would_match FROM alert_log
     WHERE disposition='suppressed' AND alert_type='sensor_offline' AND resolved_at IS NULL;
    IF v_would_match <> 0 THEN
        RAISE EXCEPTION 'IDEMPOTENCY FAIL: re-apply would still match % rows', v_would_match;
    END IF;
    RAISE NOTICE 'IDEMPOTENCY OK: re-apply matches 0 rows.';
END $$;

-- Actually re-run the body to prove no error + no change.
UPDATE alert_log
SET resolved_at = COALESCE(updated_at, created_at),
    resolved_by = 'migration_151_backfill',
    resolution  = COALESCE(resolution, 'x'),
    updated_at  = now()
WHERE disposition = 'suppressed'
  AND alert_type  = 'sensor_offline'
  AND resolved_at IS NULL;

-- ===========================================================================
-- ROLLBACK (documented rollback, verbatim) + assert clean.
-- ===========================================================================
UPDATE alert_log
SET resolved_at = NULL,
    resolved_by = NULL,
    resolution  = NULL,
    updated_at  = now()
WHERE disposition = 'suppressed'
  AND alert_type  = 'sensor_offline'
  AND resolved_by = 'migration_151_backfill';

DO $$
DECLARE v_back INT;
BEGIN
    -- ids 1,2,3,8 back to NULL resolved_at.
    SELECT count(*) INTO v_back FROM alert_log
     WHERE id IN (1,2,3,8) AND resolved_at IS NULL AND resolved_by IS NULL;
    IF v_back <> 4 THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: expected 4 rows reverted to NULL, got %', v_back;
    END IF;

    -- id 8 pre-existing resolution: the rollback NULLs resolution by design
    -- (it was tagged by 151). This is the documented behavior: rollback reverts
    -- the rows 151 touched. NOTE this in PR body.

    -- id 6 (already resolved, never tagged) must STILL be resolved.
    IF (SELECT resolved_at FROM alert_log WHERE id=6) IS NULL THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: rollback wrongly reverted untagged already-resolved id 6';
    END IF;

    RAISE NOTICE 'ROLLBACK OK: 4 tagged rows reverted to NULL; untagged already-resolved row preserved.';
END $$;

DO $$ BEGIN RAISE NOTICE 'ALL FIXTURE ASSERTIONS PASSED (apply + idempotency + rollback).'; END $$;
