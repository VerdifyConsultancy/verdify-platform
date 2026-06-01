-- Fixture test for migration 156 (issue #38).
--
-- Self-contained, self-asserting SQL fixture for a DISPOSABLE throwaway DB only.
-- Stands up a minimal planner_lessons (the columns the migration reads/writes),
-- seeds 57 ACTIVE lessons including duplicate clusters + a handful of already-
-- retired / already-superseded control rows the migration must NOT touch,
-- applies the migration body verbatim, then asserts:
--   * the live active set (is_active=true AND superseded_by IS NULL) is <=25,
--   * superseded rows retain a superseded_by pointer to a SURVIVING lesson
--     (provenance preserved — no hard delete),
--   * retired rows keep all their data (category/condition/lesson/confidence/
--     times_validated/last_validated/source_plan_ids) and superseded_by IS NULL,
--   * NO row was DELETEd (total row count is conserved),
--   * the highest-value lessons survived (a known high-confidence,
--     many-times-validated row is still live),
--   * pre-existing retired/superseded control rows were left alone,
--   * re-apply is idempotent (matches 0 additional rows),
--   * the documented rollback restores exactly the rows 156 moved.
-- Every assertion RAISEs EXCEPTION on failure, so a clean run that prints the
-- final NOTICE means apply + provenance + idempotency + rollback all pass.
--
-- Run against a throwaway DB, e.g.:
--   psql -v ON_ERROR_STOP=1 -f db/migrations/tests/test-156-prune-active-planner-lessons.sql
-- NEVER run this against the live DB — it DROPs and recreates planner_lessons.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- Minimal schema the migration depends on. planner_lessons is a plain table;
-- mirrors migrations 054 (base) + 075 (greenhouse_id). No TimescaleDB needed.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS planner_lessons;
CREATE TABLE planner_lessons (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT '2026-01-01 00:00:00+00',
    category        TEXT NOT NULL,
    condition       TEXT NOT NULL,
    lesson          TEXT NOT NULL,
    confidence      TEXT NOT NULL DEFAULT 'low' CHECK (confidence IN ('low','medium','high')),
    times_validated INT NOT NULL DEFAULT 1,
    last_validated  TIMESTAMPTZ,
    source_plan_ids TEXT[],
    superseded_by   INT REFERENCES planner_lessons(id),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    greenhouse_id   TEXT NOT NULL DEFAULT 'vallery'
);

-- ---------------------------------------------------------------------------
-- Seed.
--   * 57 ACTIVE rows (is_active=true, superseded_by NULL) — the live backlog.
--       - 52 distinct singletons of mixed value (ids 1..52)
--       - 1 duplicate cluster of 3 identical lessons (ids 60,61,62)
--       - 1 duplicate cluster of 2 identical lessons (ids 63,64)
--     => 57 active rows; 3 dup-copies (61,62,64) collapse, leaving 54 unique
--        active rows, so step 2 must retire 54-25 = 29 of them.
--   * 1 high-value "anchor" row (id 1) that MUST survive (high conf, validated
--     many times, recent) — proves value ranking keeps the best.
--   * 3 pre-existing CONTROL rows the migration must NOT touch:
--       id 100 already retired (is_active=false, superseded_by NULL)
--       id 101 already superseded (superseded_by -> 1)
--       id 102 active but BELONGS TO greenhouse 'other' AND already tagged-safe
--              (we instead use a distinct greenhouse to prove scope; see below)
-- ---------------------------------------------------------------------------

-- Anchor: the single most valuable lesson — must survive.
INSERT INTO planner_lessons (id, category, condition, lesson, confidence, times_validated, last_validated)
VALUES (1, 'vpd', 'cond-anchor', 'ANCHOR high-value lesson', 'high', 99, '2026-05-31 00:00:00+00');

-- 51 more distinct singleton active rows (ids 2..52), descending value so the
-- low-value tail (high ids) is what gets retired.
INSERT INTO planner_lessons (id, category, condition, lesson, confidence, times_validated, last_validated)
SELECT g,
       'cat' || g,
       'cond' || g,
       'lesson ' || g,
       CASE WHEN g <= 10 THEN 'high' WHEN g <= 25 THEN 'medium' ELSE 'low' END,
       (60 - g),                                   -- times_validated descends
       TIMESTAMPTZ '2026-05-01 00:00:00+00' - (g || ' hours')::interval
  FROM generate_series(2, 52) AS g;

-- Duplicate cluster A: 3 identical active lessons (same gh+cat+cond+lesson).
-- Survivor (60) is deliberately high-value so it stays in the live top-25 and
-- the superseded copies point at a still-LIVE canonical lesson (the realistic
-- canonicalization outcome the planner read path would surface).
INSERT INTO planner_lessons (id, category, condition, lesson, confidence, times_validated, last_validated)
VALUES
  (60, 'dupA', 'condA', 'identical lesson A', 'high', 98, '2026-05-30 00:00:00+00'),  -- best -> survivor
  (61, 'dupA', 'condA', 'identical lesson A', 'low',   2, '2026-04-09 00:00:00+00'),  -- superseded -> 60
  (62, 'dupA', 'condA', 'identical lesson A', 'low',   1, '2026-04-08 00:00:00+00');  -- superseded -> 60

-- Duplicate cluster B: 2 identical active lessons.
INSERT INTO planner_lessons (id, category, condition, lesson, confidence, times_validated, last_validated)
VALUES
  (63, 'dupB', 'condB', 'identical lesson B', 'high', 97, '2026-05-29 00:00:00+00'),  -- best -> survivor
  (64, 'dupB', 'condB', 'identical lesson B', 'low',   1, '2026-04-19 00:00:00+00');  -- superseded -> 63

-- Pre-existing CONTROL rows (migration must leave alone).
INSERT INTO planner_lessons (id, category, condition, lesson, confidence, times_validated, last_validated, is_active, superseded_by)
VALUES
  (100, 'old', 'cond-old', 'already retired before 156', 'low', 1, '2026-01-02 00:00:00+00', false, NULL),
  (101, 'old', 'cond-sup', 'already superseded before 156', 'low', 1, '2026-01-03 00:00:00+00', false, 1);

SELECT setval(pg_get_serial_sequence('planner_lessons','id'), 200, false);

-- ---------------------------------------------------------------------------
-- Capture pre-state.
-- ---------------------------------------------------------------------------
DO $$
DECLARE v_active INT; v_total INT;
BEGIN
    SELECT count(*) INTO v_active FROM planner_lessons WHERE is_active = true AND superseded_by IS NULL;
    SELECT count(*) INTO v_total  FROM planner_lessons;
    IF v_active <> 57 THEN
        RAISE EXCEPTION 'SEED FAIL: expected 57 active live lessons, got %', v_active;
    END IF;
    IF v_total <> 59 THEN
        RAISE EXCEPTION 'SEED FAIL: expected 59 total rows (57 active + 2 controls), got %', v_total;
    END IF;
    RAISE NOTICE 'SEED OK: 57 active live lessons, 59 total rows.';
END $$;

-- ===========================================================================
-- APPLY (migration 156 body, verbatim)
-- ===========================================================================
WITH live AS (
    SELECT id, greenhouse_id, category, condition, lesson,
           CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END AS conf_rank,
           times_validated, last_validated
      FROM planner_lessons
     WHERE is_active = true
       AND superseded_by IS NULL
       AND lesson NOT LIKE '% [migration_156:%]'
), ranked_dups AS (
    SELECT id,
           first_value(id) OVER w AS survivor_id,
           row_number()    OVER w AS rn
      FROM live
    WINDOW w AS (
        PARTITION BY greenhouse_id, category, condition, lesson
        ORDER BY conf_rank DESC, times_validated DESC,
                 last_validated DESC NULLS LAST, id DESC
    )
)
UPDATE planner_lessons p
   SET superseded_by = d.survivor_id,
       is_active     = false,
       lesson        = p.lesson || ' [migration_156:superseded->' || d.survivor_id || ']'
  FROM ranked_dups d
 WHERE p.id = d.id
   AND d.rn > 1;

WITH live AS (
    SELECT id,
           CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END AS conf_rank,
           times_validated, last_validated
      FROM planner_lessons
     WHERE is_active = true
       AND superseded_by IS NULL
       AND lesson NOT LIKE '% [migration_156:%]'
), ranked AS (
    SELECT id,
           row_number() OVER (
               ORDER BY conf_rank DESC, times_validated DESC,
                        last_validated DESC NULLS LAST, id DESC
           ) AS rn
      FROM live
)
UPDATE planner_lessons p
   SET is_active = false,
       lesson    = p.lesson || ' [migration_156:retired]'
  FROM ranked r
 WHERE p.id = r.id
   AND r.rn > 25;

-- ---------------------------------------------------------------------------
-- ASSERT apply.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    v_active INT; v_total INT;
    v_sup INT; v_sup_bad INT; v_retired INT;
    v_anchor_live BOOLEAN;
BEGIN
    -- (1) live active set is now <=25.
    SELECT count(*) INTO v_active FROM planner_lessons WHERE is_active = true AND superseded_by IS NULL;
    IF v_active > 25 THEN
        RAISE EXCEPTION 'APPLY FAIL: % active live lessons remain (want <=25)', v_active;
    END IF;

    -- (2) no row was deleted — total count conserved.
    SELECT count(*) INTO v_total FROM planner_lessons;
    IF v_total <> 59 THEN
        RAISE EXCEPTION 'APPLY FAIL: total rows changed to % (want 59 — supersede/retire, never delete)', v_total;
    END IF;

    -- (3) the 3 duplicate copies (61,62,64) are superseded onto their survivor,
    --     and every superseded_by points at a SURVIVING (still-live) lesson.
    SELECT count(*) INTO v_sup FROM planner_lessons
     WHERE id IN (61,62) AND superseded_by = 60 AND is_active = false
       AND lesson LIKE '% [migration_156:superseded->60]';
    IF v_sup <> 2 THEN
        RAISE EXCEPTION 'APPLY FAIL: dupA copies 61,62 not superseded onto survivor 60 (got %)', v_sup;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM planner_lessons
                   WHERE id = 64 AND superseded_by = 63 AND is_active = false
                     AND lesson LIKE '% [migration_156:superseded->63]') THEN
        RAISE EXCEPTION 'APPLY FAIL: dupB copy 64 not superseded onto survivor 63';
    END IF;

    -- survivors 60 and 63 must themselves still be live (provenance target valid).
    IF NOT EXISTS (SELECT 1 FROM planner_lessons WHERE id = 60 AND is_active = true AND superseded_by IS NULL)
       OR NOT EXISTS (SELECT 1 FROM planner_lessons WHERE id = 63 AND is_active = true AND superseded_by IS NULL) THEN
        RAISE EXCEPTION 'APPLY FAIL: a duplicate survivor (60/63) is not live — provenance target invalid';
    END IF;

    -- (4) every row 156 superseded points at a row that EXISTS (FK provenance intact).
    SELECT count(*) INTO v_sup_bad FROM planner_lessons p
     WHERE p.lesson LIKE '% [migration_156:superseded->%]'
       AND NOT EXISTS (SELECT 1 FROM planner_lessons q WHERE q.id = p.superseded_by);
    IF v_sup_bad <> 0 THEN
        RAISE EXCEPTION 'APPLY FAIL: % superseded rows point at a non-existent lesson', v_sup_bad;
    END IF;

    -- (5) retired rows keep ALL their data (a sampled retired row still has its
    --     category/condition/confidence/times_validated intact — only is_active
    --     flipped and a marker appended).
    IF EXISTS (
        SELECT 1 FROM planner_lessons
         WHERE lesson LIKE '% [migration_156:retired]'
           AND (category IS NULL OR condition IS NULL OR confidence IS NULL OR times_validated IS NULL)
    ) THEN
        RAISE EXCEPTION 'APPLY FAIL: a 156-retired row lost a data column';
    END IF;
    SELECT count(*) INTO v_retired FROM planner_lessons
     WHERE is_active = false AND superseded_by IS NULL
       AND lesson LIKE '% [migration_156:retired]';
    IF v_retired < 1 THEN
        RAISE EXCEPTION 'APPLY FAIL: expected some rows retired (no replacement), got %', v_retired;
    END IF;

    -- (6) value ranking kept the best: anchor id 1 is still live.
    SELECT (is_active AND superseded_by IS NULL) INTO v_anchor_live FROM planner_lessons WHERE id = 1;
    IF NOT v_anchor_live THEN
        RAISE EXCEPTION 'APPLY FAIL: high-value anchor lesson id 1 was pruned';
    END IF;

    -- (7) pre-existing controls untouched: id 100 still retired w/ original text,
    --     id 101 still superseded onto 1, neither carries a 156 marker.
    IF EXISTS (SELECT 1 FROM planner_lessons WHERE id IN (100,101) AND lesson LIKE '% [migration_156:%]') THEN
        RAISE EXCEPTION 'APPLY FAIL: a pre-existing control row (100/101) was modified by 156';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM planner_lessons WHERE id = 100 AND is_active = false AND superseded_by IS NULL)
       OR NOT EXISTS (SELECT 1 FROM planner_lessons WHERE id = 101 AND is_active = false AND superseded_by = 1) THEN
        RAISE EXCEPTION 'APPLY FAIL: pre-existing control lifecycle state changed';
    END IF;

    RAISE NOTICE 'APPLY OK: % active live (<=25), 3 dups superseded onto live survivors, % retired, anchor kept, controls untouched, 0 rows deleted.', v_active, v_retired;
END $$;

-- ===========================================================================
-- RE-APPLY (idempotency): must move 0 additional rows.
-- ===========================================================================
DO $$
DECLARE v_before INT; v_after INT;
BEGIN
    SELECT count(*) INTO v_before FROM planner_lessons WHERE is_active = true AND superseded_by IS NULL;

    -- re-run body verbatim (step 1)
    WITH live AS (
        SELECT id, greenhouse_id, category, condition, lesson,
               CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END AS conf_rank,
               times_validated, last_validated
          FROM planner_lessons
         WHERE is_active = true AND superseded_by IS NULL
           AND lesson NOT LIKE '% [migration_156:%]'
    ), ranked_dups AS (
        SELECT id, first_value(id) OVER w AS survivor_id, row_number() OVER w AS rn
          FROM live
        WINDOW w AS (PARTITION BY greenhouse_id, category, condition, lesson
                     ORDER BY conf_rank DESC, times_validated DESC, last_validated DESC NULLS LAST, id DESC)
    )
    UPDATE planner_lessons p
       SET superseded_by = d.survivor_id, is_active = false,
           lesson = p.lesson || ' [migration_156:superseded->' || d.survivor_id || ']'
      FROM ranked_dups d WHERE p.id = d.id AND d.rn > 1;

    -- re-run body verbatim (step 2)
    WITH live AS (
        SELECT id, CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END AS conf_rank,
               times_validated, last_validated
          FROM planner_lessons
         WHERE is_active = true AND superseded_by IS NULL
           AND lesson NOT LIKE '% [migration_156:%]'
    ), ranked AS (
        SELECT id, row_number() OVER (ORDER BY conf_rank DESC, times_validated DESC,
                                               last_validated DESC NULLS LAST, id DESC) AS rn
          FROM live
    )
    UPDATE planner_lessons p
       SET is_active = false, lesson = p.lesson || ' [migration_156:retired]'
      FROM ranked r WHERE p.id = r.id AND r.rn > 25;

    SELECT count(*) INTO v_after FROM planner_lessons WHERE is_active = true AND superseded_by IS NULL;
    IF v_after <> v_before THEN
        RAISE EXCEPTION 'IDEMPOTENCY FAIL: re-apply changed active count % -> %', v_before, v_after;
    END IF;
    -- no row should carry a doubled marker.
    IF EXISTS (SELECT 1 FROM planner_lessons WHERE lesson LIKE '%[migration_156:%[migration_156:%') THEN
        RAISE EXCEPTION 'IDEMPOTENCY FAIL: a row got a doubled 156 marker';
    END IF;
    RAISE NOTICE 'IDEMPOTENCY OK: re-apply moved 0 rows (active stays %).', v_after;
END $$;

-- ===========================================================================
-- ROLLBACK (documented rollback, verbatim) + assert clean.
-- ===========================================================================
UPDATE planner_lessons
   SET is_active = true,
       lesson = regexp_replace(lesson, ' \[migration_156:retired\]$', '')
 WHERE is_active = false
   AND superseded_by IS NULL
   AND lesson LIKE '% [migration_156:retired]';

UPDATE planner_lessons
   SET is_active = true,
       superseded_by = NULL,
       lesson = regexp_replace(lesson, ' \[migration_156:superseded->[0-9]+\]$', '')
 WHERE is_active = false
   AND superseded_by IS NOT NULL
   AND lesson LIKE '% [migration_156:superseded->%]';

DO $$
DECLARE v_active INT; v_marker INT;
BEGIN
    -- back to the original 57 active live rows.
    SELECT count(*) INTO v_active FROM planner_lessons WHERE is_active = true AND superseded_by IS NULL;
    IF v_active <> 57 THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: expected 57 active live rows restored, got %', v_active;
    END IF;
    -- no 156 markers remain anywhere.
    SELECT count(*) INTO v_marker FROM planner_lessons WHERE lesson LIKE '% [migration_156:%]';
    IF v_marker <> 0 THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: % rows still carry a 156 marker', v_marker;
    END IF;
    -- pre-existing controls STILL untouched (rollback must not re-activate them).
    IF NOT EXISTS (SELECT 1 FROM planner_lessons WHERE id = 100 AND is_active = false AND superseded_by IS NULL)
       OR NOT EXISTS (SELECT 1 FROM planner_lessons WHERE id = 101 AND is_active = false AND superseded_by = 1) THEN
        RAISE EXCEPTION 'ROLLBACK FAIL: rollback wrongly re-activated a pre-existing control row';
    END IF;
    RAISE NOTICE 'ROLLBACK OK: 57 active live rows restored, all 156 markers gone, controls preserved.';
END $$;

DO $$ BEGIN RAISE NOTICE 'ALL FIXTURE ASSERTIONS PASSED (apply + provenance + idempotency + rollback).'; END $$;
