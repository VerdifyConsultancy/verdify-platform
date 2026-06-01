-- Migration 156: Canonicalize the ACTIVE planner_lessons set down to <=25
--                (issue #38, Track-A data hygiene).
--
-- CONTEXT
-- Live carries 57 active planner_lessons (is_active=true AND superseded_by IS
-- NULL) out of ~141 total. The planner read path already caps lesson injection
-- at 10 (ingestor/iris_planner.py / mcp/server.py select the live set
-- `is_active = true AND superseded_by IS NULL`), so 57 active rows is pure
-- backlog noise: it never reaches the model but it fails
-- tests/test_07_cron_replan.py::test_planner_lessons_not_excessive (target
-- <=25). The MCP lessons_manage supersede/retire path (issue #44) exists but
-- was never used to canonicalize the accumulated active set. This is that
-- one-time canonicalization, expressed as plain DML using the SAME terminal
-- lifecycle semantics lessons_manage uses, so no provenance is lost.
--
-- PROVENANCE — SUPERSEDE/RETIRE, NEVER HARD-DELETE
-- Two terminal states, both keep the row (and all its columns) in the table:
--   * superseded: superseded_by = <surviving canonical lesson id>, is_active=false.
--                 Used for DUPLICATES — the weaker copies point at the single
--                 surviving copy, so the provenance chain to the canonical
--                 lesson is preserved (this is exactly what lessons_manage
--                 'supersede' does: `superseded_by = $new, is_active = false`).
--   * retired:    is_active=false, superseded_by stays NULL. Used for the
--                 remaining low-value/stale over-budget rows that have no
--                 replacement (lessons_manage 'deactivate'/retire). The row,
--                 its category/condition/lesson, confidence, times_validated,
--                 last_validated and source_plan_ids are all preserved.
-- No row is DELETEd. The live set after apply is the highest-value <=25.
--
-- VALUE RANKING (which active rows survive)
-- The kept set is the top KEEP_BUDGET (=25) active lessons ranked by the same
-- value signals the planner read path orders on:
--     confidence  high > medium > low   (confidence_rank 3/2/1)
--     then times_validated   DESC
--     then last_validated    DESC NULLS LAST
--     then id                DESC        (deterministic tie-break)
-- Ranking is computed only from persisted columns (no now(), no random), so it
-- is deterministic and replay-stable: re-running on the same data keeps the
-- same 25 survivors.
--
-- DUPLICATE DETECTION
-- A duplicate cluster = same (greenhouse_id, category, condition, lesson). The
-- highest-ranked row in each cluster is the canonical survivor; the rest are
-- superseded onto it (provenance pointer to the survivor). Duplicates are
-- collapsed FIRST; whatever active rows remain above the 25 budget after that
-- are retired (no replacement) lowest-value first.
--
-- SCOPE (EXACT — never widen)
-- Only rows that are part of the LIVE active set at apply time
-- (is_active=true AND superseded_by IS NULL) for greenhouse 'vallery' are
-- considered. Already-retired (is_active=false) and already-superseded
-- (superseded_by IS NOT NULL) rows are never read or written. If the live set
-- is already <=25 the migration is a no-op (e.g. fresh DB).
--
-- TAGGING (makes idempotency + rollback exact)
-- Every row this migration moves out of the active set gets a machine-readable
-- marker appended to `lesson`:
--     ' [migration_156:superseded->NNN]'   (duplicate, points at survivor NNN)
--     ' [migration_156:retired]'           (low-value/stale, no replacement)
-- The marker is what the idempotency predicate and the documented rollback
-- target, so a re-apply matches zero rows and the rollback restores exactly the
-- rows 156 touched and nothing else.
--
-- IDEMPOTENCY
-- The marker is appended only to rows that lack it, and a row that already
-- carries a 156 marker is excluded from the candidate set, so a second apply
-- selects zero rows to prune (no-op, no error). Replay-stable: the survivor
-- set is a pure function of the persisted columns.
--
-- ROLLBACK (documented; see PR body)
--   -- Re-activate everything 156 retired (no superseded_by was set):
--   UPDATE planner_lessons
--      SET is_active = true,
--          lesson = regexp_replace(lesson, ' \[migration_156:retired\]$', '')
--    WHERE is_active = false
--      AND superseded_by IS NULL
--      AND lesson LIKE '% [migration_156:retired]';
--   -- Re-activate everything 156 superseded (clear the survivor pointer):
--   UPDATE planner_lessons
--      SET is_active = true,
--          superseded_by = NULL,
--          lesson = regexp_replace(lesson, ' \[migration_156:superseded->[0-9]+\]$', '')
--    WHERE is_active = false
--      AND superseded_by IS NOT NULL
--      AND lesson LIKE '% [migration_156:superseded->%]';
-- The markers make the rollback target exactly the rows this migration moved;
-- it never re-activates a lesson that was retired/superseded before 156 ran.
--
-- ROLLBACK-REPLAY SAFETY (issue #23)
-- No top-level COMMIT and no commit-forcing statement (no CREATE INDEX
-- CONCURRENTLY / VACUUM / ALTER SYSTEM). Plain DML inside DO/CTE blocks only —
-- like migration 151 — so the rollback-validation harness can wrap it in an
-- outer BEGIN..ROLLBACK without it self-committing.
--
-- RESTARTS (CLAUDE.md rule 7): this migration does NOT touch verdify_schemas/**,
-- ingestor/entity_map.py, or mcp/server.py. It is a data-only UPDATE on the
-- plain planner_lessons table, so no service-restart obligation is triggered.
-- (The planner re-reads the live lessons set on its next run; nothing to bounce.)

-- ── Step 1: supersede DUPLICATES onto their canonical survivor ──────────────
-- Within each (greenhouse_id, category, condition, lesson) cluster of >1 active
-- rows, the top-ranked row survives; the rest are superseded onto it.
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
   AND d.rn > 1;                       -- every non-survivor copy in the cluster

-- ── Step 2: retire the lowest-value remaining active rows over the budget ───
-- After duplicate collapse, if the live set still exceeds KEEP_BUDGET, retire
-- the lowest-value rows (terminal retired state; no replacement) until <=25.
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
   AND r.rn > 25;                       -- KEEP_BUDGET = 25 highest-value survive
