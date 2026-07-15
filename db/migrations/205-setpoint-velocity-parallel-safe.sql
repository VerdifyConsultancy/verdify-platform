-- 205-setpoint-velocity-parallel-safe.sql
--
-- The "Oscillation Heatmap (param × hour)" panel (site-climate-controller)
-- fails with a live executor error, reproduced 2026-07-15T03:2xZ:
--   ERROR:  subplan "SubPlan 1" was not initialized
--   CONTEXT: parallel worker
-- v_setpoint_velocity (migration 059) counts oscillations with a correlated
-- EXISTS inside an aggregate FILTER; when the planner parallelizes the
-- setpoint_changes scan, the un-initialized-subplan executor bug fires and
-- the panel errors on every load.
--
-- Rewrite with an equivalent window/group formulation: rows are grouped
-- into consecutive same-value runs per parameter; the first row of the NEXT
-- run is by construction the earliest subsequent change to a different
-- value, so "EXISTS a different-value change within 10 minutes" is exactly
-- "next run starts within 10 minutes". Window functions also keep the plan
-- out of the parallel-worker path that triggers the bug.
--
-- Semantics preserved: same 30-day window, same (hour, parameter, source)
-- grouping, writes = count(*), oscillations = rows followed by a
-- different-value write of the same parameter (any source) within 10 min.
-- (setpoint_changes.value is non-null in practice; the original's
-- NULL-strict <> and IS DISTINCT FROM grouping only differ on NULLs.)
--
-- Non-self-transactional: CREATE OR REPLACE VIEW only. Safe for an outer
-- BEGIN..ROLLBACK proof. Functional rollback: restore the migration-059
-- body.

CREATE OR REPLACE VIEW public.v_setpoint_velocity AS
WITH marks AS (
    SELECT sc.ts,
           sc.parameter,
           sc.source,
           CASE WHEN sc.value IS DISTINCT FROM
                     lag(sc.value) OVER (PARTITION BY sc.parameter ORDER BY sc.ts)
                THEN 1 ELSE 0 END AS is_new_grp
    FROM setpoint_changes sc
    WHERE sc.ts > (now() - '30 days'::interval)
), base AS (
    SELECT ts,
           parameter,
           source,
           sum(is_new_grp) OVER (PARTITION BY parameter ORDER BY ts
                                 ROWS UNBOUNDED PRECEDING) AS grp
    FROM marks
), grp_start AS (
    SELECT parameter, grp, min(ts) AS grp_ts
    FROM base
    GROUP BY parameter, grp
), flagged AS (
    SELECT b.ts,
           b.parameter,
           b.source,
           (gs.grp_ts IS NOT NULL
            AND gs.grp_ts < b.ts + '00:10:00'::interval) AS is_oscillation
    FROM base b
    LEFT JOIN grp_start gs
      ON gs.parameter = b.parameter AND gs.grp = b.grp + 1
)
SELECT date_trunc('hour'::text, ts) AS hour,
       parameter,
       source,
       count(*) AS writes,
       count(*) FILTER (WHERE is_oscillation) AS oscillations
FROM flagged
GROUP BY (date_trunc('hour'::text, ts)), parameter, source;

COMMENT ON VIEW public.v_setpoint_velocity IS
'Setpoint write velocity + oscillation counts per hour/parameter/source '
'(30-day window). Migration 205 rewrote the migration-059 correlated-EXISTS '
'oscillation test as a same-value-run window formulation after the live '
'"subplan was not initialized / parallel worker" executor error broke the '
'Oscillation Heatmap panel (2026-07-15).';
