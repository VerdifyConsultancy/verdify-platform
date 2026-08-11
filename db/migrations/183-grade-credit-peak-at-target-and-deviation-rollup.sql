-- 183-grade-credit-peak-at-target-and-deviation-rollup.sql
-- ADR0003 §6.2 (BC-12): kill the saturate-at-1.0 grade + surface |actual-target|.
--
-- The mig-146 fn_grade_credit returns a FLAT 1.0 across the WHOLE ideal band, so a
-- reading on the band edge and one on target score identically — the metric is blind
-- to in-band off-target drift, exactly the tracking the band-compliance regime exists
-- to optimize. This replaces it with a TENT that PEAKS (1.0) at the ideal-band midpoint
-- (the implicit target) and declines linearly to IDEAL_EDGE_CREDIT (0.5) at each ideal
-- edge, then to 0 at the stress edge. In-band drift is now visible AND penalized in the
-- graded metric (and the planner reward that consumes it). Same 5-arg signature, so
-- every caller (fn_zone_band_grade, mv_zone_band_grade, fn_compliance_v2, ...) is untouched.
--
-- IDEAL_EDGE_CREDIT = 0.5 (Jason-default per ADR0003 §6.2; recorded there). The Python
-- mirror ingestor/tasks/daily.py::_grade_credit uses the IDENTICAL shape + constant —
-- the DB↔Python parity test pins them together (they MUST never drift).
--
-- Also adds: the headline deviation metric storage (daily_summary dev_*_norm_* columns)
-- and the time-bounded rollup fn_band_deviation (median day/night + p95 of normalized
-- |actual − DEVICE target|), sourced from the device-published climate.house_*_target.
--
-- DEFERRED to a follow-up live step (DB-gated, cannot run offline): re-quantile-matching
-- mig-147 plan_anchor_ladder.comp_cut_graded to the new (lower) compliance distribution +
-- the BC-6 ordinal-stability ≥90% gate. mig-147's binary_fallback keeps the planner reward
-- safe in the interim. Tracked: run the mig-147.2 $reanchor$ block + the §6 validator query
-- against live data, post-deploy, before relying on the re-shaped reward.
--
-- Non-self-transactional (CREATE OR REPLACE FUNCTION + ALTER TABLE ADD COLUMN; no top-level
-- COMMIT, no CONCURRENTLY) -> SAFE to rollback-validate under BEGIN; ... ROLLBACK;.
-- Rollback: CREATE OR REPLACE fn_grade_credit back to the mig-146 flat body; the additive
-- columns + fn_band_deviation are harmless to leave (or DROP in a revert). Revert daily.py
-- _grade_credit + the deviation accumulator in LOCKSTEP (the two grade mirrors must match).
-- RESTARTS: bounce verdify-mcp (scorecard) + verdify-ingestor (daily.py grade + mv refresh).

-- ── 183.1  fn_grade_credit: tent peaking at the ideal midpoint (the target) ──
CREATE OR REPLACE FUNCTION public.fn_grade_credit(
  x numeric, stress_lo numeric, ideal_lo numeric, ideal_hi numeric, stress_hi numeric
) RETURNS numeric LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  -- 0.5 = IDEAL_EDGE_CREDIT (mirror: ingestor/tasks/daily.py::_grade_credit — keep identical).
  SELECT CASE
    WHEN x IS NULL THEN NULL
    WHEN x < stress_lo OR x > stress_hi THEN 0.0
    -- inside ideal: tent, 1.0 at the midpoint (target), 0.5 at each ideal edge
    WHEN x >= ideal_lo AND x <= (ideal_lo + ideal_hi) / 2.0
      THEN 1.0 - 0.5 * ( ((ideal_lo + ideal_hi) / 2.0 - x)
                         / NULLIF((ideal_hi - ideal_lo) / 2.0, 0) )
    WHEN x > (ideal_lo + ideal_hi) / 2.0 AND x <= ideal_hi
      THEN 1.0 - 0.5 * ( (x - (ideal_lo + ideal_hi) / 2.0)
                         / NULLIF((ideal_hi - ideal_lo) / 2.0, 0) )
    -- stress shoulders: 0.5 at the ideal edge down to 0 at the stress edge
    WHEN x < ideal_lo THEN GREATEST(0, 0.5 * (x - stress_lo) / NULLIF(ideal_lo - stress_lo, 0))
    ELSE                   GREATEST(0, 0.5 * (stress_hi - x) / NULLIF(stress_hi - ideal_hi, 0))
  END;
$$;

COMMENT ON FUNCTION public.fn_grade_credit(numeric,numeric,numeric,numeric,numeric) IS
'Graded compliance credit g in [0,1] for reading x against (stress_lo, ideal_lo, ideal_hi, '
'stress_hi). BC-12/ADR0003 §6.2: TENT peaking 1.0 at the ideal MIDPOINT (target), declining '
'to 0.5 (IDEAL_EDGE_CREDIT) at each ideal edge, then to 0 at the stress edge — so in-band '
'off-target drift is penalized (kills the old flat-1.0-in-band saturation). Continuous at all '
'edges. Mirror: ingestor/tasks/daily.py::_grade_credit (parity-tested).';

-- ── 183.2  daily_summary: headline deviation-from-target storage (additive) ──
ALTER TABLE public.daily_summary
  ADD COLUMN IF NOT EXISTS dev_temp_norm_median_day   double precision,
  ADD COLUMN IF NOT EXISTS dev_temp_norm_median_night double precision,
  ADD COLUMN IF NOT EXISTS dev_temp_norm_p95          double precision,
  ADD COLUMN IF NOT EXISTS dev_vpd_norm_median_day    double precision,
  ADD COLUMN IF NOT EXISTS dev_vpd_norm_median_night  double precision,
  ADD COLUMN IF NOT EXISTS dev_vpd_norm_p95           double precision;

COMMENT ON COLUMN public.daily_summary.dev_temp_norm_median_day IS
'Headline tracked metric (BC-12/ADR0003 §6.2): median |actual_temp - device target| / (ideal '
'half-width), day phase (solar_phase in [0,2)). Written by tasks/daily.py alongside the graded '
'columns. 0 = on target, 1 = at the band edge. Device target = climate.house_temp_target_f.';

-- ── 183.3  fn_band_deviation: time-bounded |actual − device target| rollup ──
CREATE OR REPLACE FUNCTION public.fn_band_deviation(
  p_start timestamptz, p_end timestamptz, p_greenhouse_id text DEFAULT 'vallery'
) RETURNS TABLE(axis text, median_day numeric, median_night numeric, p95 numeric)
LANGUAGE sql STABLE AS $$
  WITH s AS (
    SELECT c.temp_avg, c.vpd_avg,
           c.house_temp_target_f AS t_tgt, c.house_vpd_target AS v_tgt,
           b.temp_high, b.temp_low, b.vpd_high, b.vpd_low,
           fn_solar_phase(c.ts) AS phase
      FROM public.climate c
      LEFT JOIN LATERAL (SELECT * FROM fn_band_setpoints(c.ts)) b ON true
     WHERE c.greenhouse_id = p_greenhouse_id
       AND c.ts >= p_start AND c.ts <= p_end
       AND c.house_temp_target_f IS NOT NULL   -- DEVICE target present (forward-only)
       AND c.temp_avg IS NOT NULL AND c.vpd_avg IS NOT NULL
  ),
  d AS (
    SELECT abs(temp_avg - t_tgt) / GREATEST(0.5,  (temp_high - temp_low) / 2.0) AS dt,
           abs(vpd_avg  - v_tgt) / GREATEST(0.05, (vpd_high  - vpd_low)  / 2.0) AS dv,
           (phase < 2.0) AS is_day
      FROM s
  )
  SELECT 'temp'::text,
         percentile_cont(0.5)  WITHIN GROUP (ORDER BY dt) FILTER (WHERE is_day),
         percentile_cont(0.5)  WITHIN GROUP (ORDER BY dt) FILTER (WHERE NOT is_day),
         percentile_cont(0.95) WITHIN GROUP (ORDER BY dt)
    FROM d
  UNION ALL
  SELECT 'vpd'::text,
         percentile_cont(0.5)  WITHIN GROUP (ORDER BY dv) FILTER (WHERE is_day),
         percentile_cont(0.5)  WITHIN GROUP (ORDER BY dv) FILTER (WHERE NOT is_day),
         percentile_cont(0.95) WITHIN GROUP (ORDER BY dv)
    FROM d;
$$;

COMMENT ON FUNCTION public.fn_band_deviation(timestamptz,timestamptz,text) IS
'BC-12/ADR0003 §6.2 headline tracking metric: per-axis median (day/night) + p95 of normalized '
'|actual − DEVICE target| (climate.house_*_target) over the window. 0 = on target, 1 = at the '
'ideal band edge. Time-bounded (never full-table). Forward-only (excludes NULL-target history).';
