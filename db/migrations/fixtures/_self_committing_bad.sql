-- FIXTURE — NOT A REAL MIGRATION. DO NOT APPLY TO ANY DATABASE.
--
-- Deliberately self-committing migration used by the rollback-safety guard
-- (scripts/check_migration_rollback_safety.py / tests/test_migration_rollback_safety.py,
-- issue #23). The underscore prefix keeps it outside the numbered applied set;
-- it lives under db/migrations/fixtures/ (a subdirectory) so the non-recursive
-- `ls db/migrations/*.sql` apply loop in CI never picks it up.
--
-- This file reproduces the 2026-05-30 live-commit incident shape: a migration
-- with its OWN top-level COMMIT chained under an outer BEGIN..ROLLBACK dry-run.
-- When wrapped, the inner COMMIT below commits to the live DB the instant psql
-- reaches it, defeating the outer ROLLBACK. The guard MUST refuse to wrap this.
--
-- The comments below intentionally MENTION commit-forcing statements
-- (CREATE INDEX CONCURRENTLY, VACUUM) to prove the guard ignores comment text
-- and only trips on the real top-level COMMIT. The DO block likewise contains a
-- PL/pgSQL BEGIN ... END that must NOT be mistaken for transaction control.

BEGIN;

CREATE TABLE IF NOT EXISTS _guard_fixture_demo (
    id   SERIAL PRIMARY KEY,
    note TEXT NOT NULL DEFAULT 'fixture'  -- not a real CREATE INDEX CONCURRENTLY
);

-- A dollar-quoted DO block: its BEGIN/END are PL/pgSQL keywords, not txn control.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM _guard_fixture_demo) THEN
        INSERT INTO _guard_fixture_demo (note) VALUES ('seed');
    END IF;
END
$$;

-- The dangerous line: a real top-level COMMIT. This is what defeats an outer
-- BEGIN..ROLLBACK and what the guard exists to catch.
COMMIT;
