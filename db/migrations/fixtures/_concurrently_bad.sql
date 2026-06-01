-- FIXTURE — NOT A REAL MIGRATION. DO NOT APPLY TO ANY DATABASE.
--
-- Second guard fixture (issue #23): a migration with NO top-level COMMIT but a
-- commit-forcing statement that cannot run inside a transaction block. Postgres
-- runs CREATE INDEX CONCURRENTLY in its own implicit transaction; if you wrap
-- this file in an outer BEGIN..ROLLBACK it errors out ("CREATE INDEX
-- CONCURRENTLY cannot run inside a transaction block") OR, depending on the
-- replay harness, force-commits surrounding work. Either way it must NOT be
-- silently wrapped — the guard must refuse it.
--
-- Lives under db/migrations/fixtures/ with an underscore prefix so the
-- non-recursive `ls db/migrations/*.sql` apply loop never runs it.

CREATE TABLE IF NOT EXISTS _guard_fixture_concurrently (
    id   SERIAL PRIMARY KEY,
    val  INTEGER NOT NULL DEFAULT 0
);

-- Commit-forcing: cannot run inside a transaction block.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_guard_fixture_val
    ON _guard_fixture_concurrently (val);
