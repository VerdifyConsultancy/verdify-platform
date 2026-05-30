-- Migration 148: Repoint the dead plan-accuracy view family onto plan_journal
--
-- PROBLEM (verified 2026-05-30, read-only):
--   v_plan_compliance / v_plan_accuracy / v_plan_accuracy_72h / v_plan_accuracy_by_day
--   all return 0 rows. Root cause: v_plan_compliance JOINs setpoint_plan
--   (parameter IN temp_low/temp_high/vpd_low/vpd_high) against the LAST 7 DAYS of
--   `climate`, but the planner stopped writing those four parameters to setpoint_plan
--   in mid-May (newest temp_high = 2026-05-12, vpd_high = 2026-04-25, temp_low =
--   2026-04-18). climate's 7-day window starts 2026-05-23, so the join window has
--   ZERO overlap with the surviving plan rows. The accuracy family has therefore been
--   a dead facade for ~2.5 weeks. The real, fresh plan-accuracy signal now lives in
--   `plan_journal` (ClimateIntent plan path, migration 139): 233 rows, newest
--   2026-05-30, with outcome_score / anchor_score on a 1..10 ordinal scale.
--
-- DECISION: REPOINT, not drop.
--   Migration 146 (§146.10) wanted to DROP this family but DEFERRED it because
--   v_plan_accuracy + v_plan_compliance are in tests/test_02_database.py
--   REQUIRED_VIEWS (lines 37-38), which is owned by coordinator/web — a bare DROP
--   would break the live required-views assertion before that test is updated. A
--   repoint keeps the view NAMES alive (REQUIRED_VIEWS stays green) AND makes them
--   return rows again (closes the dead-facade gap). It also preserves the column
--   contract that the live Grafana panels read:
--     v_plan_accuracy -> plan_id, waypoints, achieved, accuracy_pct, mean_abs_error,
--                        plan_start   (canonical-planning-learning.json,
--                        control-loop.json, greenhouse-hvac-climate.json,
--                        esp32-controller.json)
--     v_plan_compliance -> planned_ts, plan_id, parameter, plan_achieved, overshoot
--                        (site-evidence-*.json timeline panels)
--   so no Grafana / consumer change is required by this migration.
--
-- SIGNAL MAPPING (plan_journal -> accuracy contract):
--   * planned_ts     := created_at   (when the plan was emitted)
--   * outcome_score  := plan_journal.outcome_score (1=worst .. 10=ideal), the
--                       canonical self-scored plan outcome.
--   * plan_achieved  := outcome_score >= 6  ("the plan worked")
--   * accuracy_pct   := outcome_score * 10.0  (1..10 -> 10%..100%)
--   * overshoot      := outcome_score - 10    (signed distance below the ideal;
--                       0 = ideal, negative = shortfall). mean_abs_error then reads
--                       as "mean shortfall from ideal on the 0..10 scale".
--   Only scored rows (outcome_score IS NOT NULL) participate, matching the prior
--   "actual is known" semantics of the climate-join version.
--
-- CANONICAL SOURCE NOTE: plan_journal is documented (band-compliance-architecture.md
-- §3-§7) as the system of record for plan scoring (outcome_score + the re-anchored
-- anchor_score ladder). These views are a convenience projection of that signal for
-- Grafana; plan_journal itself remains the canonical accuracy source.
--
-- SCOPE: db/migrations only. DB-only, off the firmware control path. No OTA, no
--   replay-diff, no 48h bake. No verdify_schemas / entity_map / mcp change here, so
--   no service-restart drift-guard obligation from THIS file (rule 7). Coordinator
--   routing note: tests/test_02_database.py REQUIRED_VIEWS is satisfied (names kept);
--   if a future cycle still wants these dropped, that drop must land in the same
--   coordinator PR that removes them from REQUIRED_VIEWS.

BEGIN;

-- Drop the dependent accuracy views first so v_plan_compliance can be redefined with
-- a new column set (CREATE OR REPLACE cannot change the column list).
DROP VIEW IF EXISTS v_plan_accuracy_by_day;
DROP VIEW IF EXISTS v_plan_accuracy_72h;
DROP VIEW IF EXISTS v_plan_accuracy;
DROP VIEW IF EXISTS v_plan_compliance;

-- ---------------------------------------------------------------------------
-- v_plan_compliance: one row per scored plan_journal entry.
-- ---------------------------------------------------------------------------
CREATE VIEW v_plan_compliance AS
SELECT
    pj.created_at                                   AS planned_ts,
    pj.plan_id,
    'plan_outcome'::text                            AS parameter,
    'plan_outcome'::text                            AS target_type,
    pj.outcome_score,
    pj.anchor_score,
    pj.validated_at,
    (pj.outcome_score >= 6)                         AS plan_achieved,
    -- signed distance below the ideal score of 10 (0 = ideal, negative = shortfall)
    (pj.outcome_score - 10)::numeric                AS overshoot,
    round(pj.outcome_score * 10.0, 1)              AS accuracy_pct
FROM plan_journal pj
WHERE pj.outcome_score IS NOT NULL;

COMMENT ON VIEW v_plan_compliance IS
  'Per-plan scored outcomes projected from plan_journal.outcome_score (1..10). '
  'Repointed in migration 148 off the dead setpoint_plan/climate 7-day join. '
  'plan_achieved = outcome_score>=6; overshoot = outcome_score-10 (signed shortfall). '
  'Canonical source: plan_journal.';

-- ---------------------------------------------------------------------------
-- v_plan_accuracy: per-plan summary (Grafana column contract preserved).
-- ---------------------------------------------------------------------------
CREATE VIEW v_plan_accuracy AS
SELECT
    plan_id,
    count(*)                                        AS waypoints,
    count(*) FILTER (WHERE plan_achieved)           AS achieved,
    round(avg(accuracy_pct), 1)                     AS accuracy_pct,
    -- mean shortfall from the ideal score (0..10 scale)
    round(avg(abs(overshoot)), 2)                   AS mean_abs_error,
    min(planned_ts)                                 AS plan_start,
    max(planned_ts)                                 AS plan_end
FROM v_plan_compliance
GROUP BY plan_id;

COMMENT ON VIEW v_plan_accuracy IS
  'Per-plan accuracy summary over plan_journal outcome scores (repointed in '
  'migration 148). accuracy_pct = outcome_score*10; mean_abs_error = mean shortfall '
  'from the ideal 10 on the 0..10 scale.';

-- ---------------------------------------------------------------------------
-- v_plan_accuracy_72h: recency window. The legacy view bucketed waypoints by
-- 24h offset within a single 72h plan. plan_journal plans are short-lived (one
-- planning cycle each), so the meaningful 72h cut is "plans emitted in the last
-- 72 hours", bucketed by day-of-recency (1 = 0-24h ago, 2 = 24-48h, 3 = 48-72h),
-- preserving the day_offset / waypoints_count / achieved_count / accuracy_pct /
-- mean_abs_error column shape.
-- ---------------------------------------------------------------------------
CREATE VIEW v_plan_accuracy_72h AS
SELECT
    plan_id,
    (floor(EXTRACT(epoch FROM now() - planned_ts) / 86400.0))::integer + 1
                                                    AS day_offset,
    count(*)                                        AS waypoints_count,
    count(*) FILTER (WHERE plan_achieved)           AS achieved_count,
    round(avg(accuracy_pct), 1)                     AS accuracy_pct,
    round(avg(abs(overshoot)), 2)                   AS mean_abs_error
FROM v_plan_compliance
WHERE planned_ts > now() - INTERVAL '72 hours'
GROUP BY plan_id,
         (floor(EXTRACT(epoch FROM now() - planned_ts) / 86400.0))::integer + 1
ORDER BY plan_id,
         (floor(EXTRACT(epoch FROM now() - planned_ts) / 86400.0))::integer + 1;

COMMENT ON VIEW v_plan_accuracy_72h IS
  'Accuracy of plans emitted in the last 72h, bucketed by recency day_offset '
  '(1 = 0-24h ago, 2 = 24-48h, 3 = 48-72h). Repointed onto plan_journal in '
  'migration 148.';

-- ---------------------------------------------------------------------------
-- v_plan_accuracy_by_day: per Denver-local calendar day.
-- ---------------------------------------------------------------------------
CREATE VIEW v_plan_accuracy_by_day AS
SELECT
    (planned_ts AT TIME ZONE 'America/Denver')::date AS day,
    count(*)                                         AS waypoints,
    count(*) FILTER (WHERE plan_achieved)            AS achieved,
    round(avg(accuracy_pct), 1)                      AS accuracy_pct,
    round(avg(abs(overshoot)), 2)                    AS mean_abs_error,
    count(DISTINCT plan_id)                          AS plans
FROM v_plan_compliance
GROUP BY (planned_ts AT TIME ZONE 'America/Denver')::date
ORDER BY (planned_ts AT TIME ZONE 'America/Denver')::date;

COMMENT ON VIEW v_plan_accuracy_by_day IS
  'Per Denver-local-day accuracy over plan_journal outcome scores. Repointed in '
  'migration 148.';

-- ---------------------------------------------------------------------------
-- plan_journal.guardrail_penalty (task #2): nullable NUMERIC column written by the
-- planner-learning-loop group from mcp/server.py (the guardrail_penalty that is
-- currently computed in v_plan_guardrail_scorecard, fetched, then DROPPED at the
-- outcome/anchor UPDATE). Additive, nullable -> no backfill, no restart obligation
-- from this DDL alone.
-- ---------------------------------------------------------------------------
ALTER TABLE plan_journal
  ADD COLUMN IF NOT EXISTS guardrail_penalty NUMERIC;

COMMENT ON COLUMN plan_journal.guardrail_penalty IS
  'Per-plan guardrail penalty (from v_plan_guardrail_scorecard) persisted alongside '
  'outcome_score/anchor_score by the planner-learning-loop. NULL for plans scored '
  'before this column existed or not yet penalized. Added in migration 148.';

COMMIT;
