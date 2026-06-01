-- Migration 154: consolidate the oscillation view pair (issue #47)
--
-- Context: both `v_daily_oscillation` and `v_daily_oscillation_summary` exist
-- live (migration 083). The summary already wraps the base
-- (`SELECT ... FROM v_daily_oscillation GROUP BY date`), so there is no
-- duplicated logic to collapse — the reported defect is *role ambiguity*:
-- renderers were unsure which of the two to read.
--
-- The #129 / IRIS-W007 parity runbook (docs/runbooks/db-copy-not-move.md)
-- pins the canonical oscillation set to EXACTLY these two names and fails
-- parity if either is dropped or an extra ad-hoc one appears. Dropping the
-- summary would also break verdify_schemas/views.py (DailyOscillationSummary),
-- tests/test_08_observability.py, and verdify_schemas/tests/test_views.py.
--
-- So this migration does NOT delete a view. It formalizes the pair as a
-- clearly-named, documented base + summary:
--   * v_daily_oscillation          = CANONICAL BASE. Per-day, per-equipment
--                                     peak hourly transition count. Render this
--                                     for per-equipment drill-down.
--   * v_daily_oscillation_summary  = DERIVED. Single-row-per-day rollup built
--                                     strictly FROM v_daily_oscillation. Render
--                                     this for the day scorecard / embeds.
-- The view bodies are re-asserted verbatim (CREATE OR REPLACE — no column
-- rename or reorder, so it is idempotent and replay-safe), and the COMMENTs
-- are rewritten to state the canonical role so the renderer confusion is
-- resolved at the contract level.
--
-- Migration-safety (issue #23): this migration is NON-self-transactional — it
-- carries NO top-level BEGIN/COMMIT and no commit-forcing statement (no
-- CREATE INDEX CONCURRENTLY). It is pure CREATE OR REPLACE VIEW + COMMENT, so
-- it is safe to replay under an outer BEGIN .. ROLLBACK rollback-validation
-- dry-run (unlike a self-committing migration, which would defeat the
-- rollback). When the migrate Job replays it directly, each statement still
-- auto-commits in its own implicit transaction.

-- Canonical BASE view: per-day, per-equipment peak hourly transition count.
-- Body unchanged from migration 083 (re-asserted for idempotent replay).
CREATE OR REPLACE VIEW v_daily_oscillation AS
WITH hourly AS (
    SELECT
        date_trunc('day', ts) AS date,
        date_trunc('hour', ts) AS hour,
        equipment,
        count(*) AS transitions
    FROM equipment_state
    GROUP BY 1, 2, 3
)
SELECT
    date::date AS date,
    equipment,
    max(transitions) AS peak_transitions_per_hour,
    (array_agg(hour ORDER BY transitions DESC))[1] AS peak_hour,
    round(avg(transitions), 1) AS avg_transitions_per_hour,
    count(*) AS active_hours
FROM hourly
GROUP BY 1, 2
ORDER BY 1 DESC, 2;

COMMENT ON VIEW v_daily_oscillation IS
    'FW-2 CANONICAL BASE oscillation view: per-day, per-equipment peak hourly transition count. Render this for per-equipment drill-down. v_daily_oscillation_summary is the single-row-per-day rollup derived FROM this view; render the summary for the day scorecard. (#47 consolidation: documented base of the canonical base+summary pair.)';

-- DERIVED summary view: single-row-per-day rollup built strictly FROM the
-- canonical base above. Body unchanged from migration 083.
CREATE OR REPLACE VIEW v_daily_oscillation_summary AS
SELECT
    date,
    sum(peak_transitions_per_hour) AS total_peak_per_hour,
    max(peak_transitions_per_hour) AS worst_equipment_peak,
    (array_agg(equipment ORDER BY peak_transitions_per_hour DESC))[1] AS worst_equipment,
    (array_agg(peak_hour ORDER BY peak_transitions_per_hour DESC))[1] AS worst_hour,
    round(avg(avg_transitions_per_hour), 1) AS avg_across_equipment
FROM v_daily_oscillation
GROUP BY 1
ORDER BY 1 DESC;

COMMENT ON VIEW v_daily_oscillation_summary IS
    'FW-2 DERIVED oscillation scorecard: one row per day, computed strictly FROM v_daily_oscillation (the canonical base). worst_equipment + worst_hour identify the peak oscillation event of the day. Render this for the day scorecard / embeds; drill down via v_daily_oscillation. (#47 consolidation: documented summary of the canonical base+summary pair.)';
