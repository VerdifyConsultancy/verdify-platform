-- Migration 147: Reward swap to controller-attributable compliance + ladder re-anchor
--
-- Implements docs/design/band-compliance-architecture.md §7 (consumer re-point,
-- reward recommendation, ladder re-anchor) and §8.3. Backlog items G7-G9 (DB side).
--
-- STRICTLY AFTER migration 146 and its Phase-2 validation gate. 146 dual-writes the
-- graded *_v2 columns (binary columns kept byte-stable). 147 flips the reward to
-- compliance_v2_attributable_pct and re-anchors the 9/10-tier ladder so the frozen
-- anchored plans preserve rank-order + median anchor.
--
-- THREE corruption vectors, all fixed here (design §7.3):
--  1. In-place mutation would re-scale every anchor -> we NEVER mutate compliance_pct;
--     146 added *_v2 columns; the reward switches column, not formula.
--  2. Re-running the backfill would re-grade history -> we FREEZE the already-anchored
--     and already-outcome-scored plan_journal rows; we backfill ONLY the null/in-flight
--     anchors (and, optionally, the outcome-without-anchor population) on the new scale.
--  3. Ladder cut-points must move with the distribution -> we re-anchor by quantile-match
--     against the (graded) anchored-plan history and store the cut-points in a table that
--     fn_plan_anchor_score reads. Replay-safe fallback to the binary distribution when the
--     graded history is not yet populated (so the rollback-replay and the pre-dual-write
--     window stay identity-preserving rather than NULLing scores).
--
-- DB-only, off the control path. No OTA / no replay-diff block / no 48h bake.
-- RESTARTS (CLAUDE.md rule 7): bounce verdify-mcp (scorecard/anchor reads) +
-- verdify-ingestor (writes outcome/anchor). Keep the binary columns one more cycle
-- for rollback. Reverting 147 restores the exact prior reward (binary columns intact).
--
-- verdify_schemas changes (graded/attributable/per-zone fields + ScorecardResponse
-- accepting new keys) land ONE cycle ahead as a schema-only PR (G8) so the public
-- /api/v1/scorecard cannot 500 on a new metric key. This migration does not touch
-- verdify_schemas itself.

-- =====================================================================
-- 147.0  GUARD: 146 must be applied (the *_v2 columns must exist)
-- =====================================================================
DO $guard$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name='daily_summary' AND column_name='compliance_v2_attributable_pct'
    ) THEN
        RAISE EXCEPTION 'Migration 147 prerequisite failed: daily_summary.compliance_v2_attributable_pct missing (apply migration 146 first).';
    END IF;
    IF to_regprocedure('fn_house_compliance(interval)') IS NULL THEN
        RAISE EXCEPTION 'Migration 147 prerequisite failed: fn_house_compliance missing (apply migration 146 first).';
    END IF;
END
$guard$;

-- =====================================================================
-- 147.1  Re-anchored ladder cut-point table  [G7]
-- =====================================================================
-- The legacy ladder (fn_plan_anchor_score) is hard-coded against BINARY
-- compliance_pct:  >=90 & str<2 ->10 ; >=85 & str<3 ->9 ; >=75 & str<5 ->8 ;
-- >=70 & str<7 ->7 ; >=60 & str<10 ->6 ; >=50 & str<14 ->5 ; >=40 & str<20 ->4 ;
-- >=25 & str<28 ->3 ; >=10 ->2 ; else 1.
-- We store these tiers in a table keyed by anchor, with both the legacy binary
-- compliance cut and the re-anchored graded-attributable compliance cut. The
-- stress cut (graded-deficit hours, <= binary) is carried unchanged: graded stress
-- is bounded above by the binary stress it replaces, so the existing stress gates
-- remain valid upper bounds and do not need quantile re-fitting.
CREATE TABLE IF NOT EXISTS plan_anchor_ladder (
  anchor smallint PRIMARY KEY,
  comp_cut_binary numeric NOT NULL,   -- legacy compliance_pct floor for this tier
  comp_cut_graded numeric,            -- re-anchored compliance_v2_attributable_pct floor (quantile-matched)
  stress_cut numeric,                 -- total_stress_h strict upper bound (NULL = no bound)
  derived_at timestamptz NOT NULL DEFAULT now(),
  derivation text                     -- 'quantile_match' | 'binary_fallback'
);

COMMENT ON TABLE plan_anchor_ladder IS
'9/10-tier anchor ladder cut-points. comp_cut_binary = legacy compliance_pct floors; comp_cut_graded = '
're-anchored compliance_v2_attributable_pct floors fit by quantile-match against the anchored-plan history '
'so the frozen anchors preserve rank-order + median (band-compliance design §7.3). stress_cut unchanged '
'(graded-deficit stress <= binary). fn_plan_anchor_score reads comp_cut_graded.';

-- Seed the legacy tier structure (binary cuts + stress cuts).
INSERT INTO plan_anchor_ladder (anchor, comp_cut_binary, stress_cut, derivation) VALUES
  (10, 90, 2,  'binary_fallback'),
  ( 9, 85, 3,  'binary_fallback'),
  ( 8, 75, 5,  'binary_fallback'),
  ( 7, 70, 7,  'binary_fallback'),
  ( 6, 60, 10, 'binary_fallback'),
  ( 5, 50, 14, 'binary_fallback'),
  ( 4, 40, 20, 'binary_fallback'),
  ( 3, 25, 28, 'binary_fallback'),
  ( 2, 10, NULL, 'binary_fallback'),
  ( 1,  0, NULL, 'binary_fallback')
ON CONFLICT (anchor) DO NOTHING;

-- =====================================================================
-- 147.2  Quantile-match the graded cut-points against anchored-plan history  [G7]
-- =====================================================================
-- For each binary compliance cut c_b, the fraction of anchored plans with binary
-- compliance_pct >= c_b is a quantile q. The re-anchored graded cut is the graded
-- compliance value at that SAME quantile q in the anchored-plan graded distribution.
-- This preserves, for each tier, the share of anchored plans that clear it -> same
-- rank-order, same median anchor. If the graded distribution is not yet populated
-- (pre-dual-write / rollback-replay), fall back to comp_cut_binary (identity), so
-- the ladder never produces NULL graded cuts.
DO $reanchor$
DECLARE
    r record;
    q numeric;
    graded_cut numeric;
    n_anchored int;
    n_graded int;
BEGIN
    -- anchored-plan population: plan_journal rows that already carry an anchor_score
    -- (the frozen 103/105 set). Join to their day-weighted scorecard compliance.
    CREATE TEMP TABLE _anchored_plans ON COMMIT DROP AS
    SELECT pj.plan_id,
           sc.compliance_pct AS comp_binary,
           sc.total_stress_h,
           -- graded-attributable, day-weighted exactly like v_plan_window_scorecard
           -- compliance_pct, sourced from daily_summary.compliance_v2_attributable_pct.
           -- Only days that actually HAVE a graded value contribute (NULLs excluded),
           -- so a plan window with zero dual-written days returns NULL (-> binary
           -- fallback) rather than a spurious 0. Replay-safe.
           (SELECT CASE WHEN SUM(pd.day_weight) FILTER (WHERE ds.compliance_v2_attributable_pct IS NOT NULL) > 0
                        THEN SUM(pd.day_weight * ds.compliance_v2_attributable_pct)
                               FILTER (WHERE ds.compliance_v2_attributable_pct IS NOT NULL)
                             / SUM(pd.day_weight) FILTER (WHERE ds.compliance_v2_attributable_pct IS NOT NULL)
                        ELSE NULL END
              FROM v_plan_execution_intervals pei
              CROSS JOIN LATERAL generate_series(
                    pei.interval_start::date::timestamptz,
                    LEAST(pei.interval_end::date, CURRENT_DATE)::timestamptz,
                    interval '1 day') d(d)
              CROSS JOIN LATERAL (SELECT GREATEST(EXTRACT(epoch FROM
                    LEAST(pei.interval_end, d.d + interval '1 day') - GREATEST(pei.interval_start, d.d))/86400.0, 0) AS day_weight) pd
              LEFT JOIN daily_summary ds ON ds.date = d.d::date
             WHERE pei.plan_id = pj.plan_id) AS comp_graded
      FROM plan_journal pj
      JOIN v_plan_window_scorecard sc ON sc.plan_id = pj.plan_id
     WHERE pj.anchor_score IS NOT NULL;

    SELECT count(*), count(comp_graded) INTO n_anchored, n_graded FROM _anchored_plans;

    FOR r IN SELECT anchor, comp_cut_binary FROM plan_anchor_ladder ORDER BY anchor LOOP
        IF n_graded = 0 OR n_anchored = 0 THEN
            -- replay-safe identity fallback: graded cut = binary cut
            UPDATE plan_anchor_ladder
               SET comp_cut_graded = r.comp_cut_binary, derivation = 'binary_fallback'
             WHERE anchor = r.anchor;
            CONTINUE;
        END IF;

        -- quantile of anchored plans clearing the binary cut
        SELECT (count(*) FILTER (WHERE comp_binary >= r.comp_cut_binary))::numeric / NULLIF(count(*),0)
          INTO q
          FROM _anchored_plans
         WHERE comp_binary IS NOT NULL;

        -- the graded value at that same upper-tail quantile q. percentile q of plans
        -- clear the cut => the cut sits at the (1-q) percentile of the graded dist.
        SELECT percentile_cont(GREATEST(0, LEAST(1, 1 - q)))
                 WITHIN GROUP (ORDER BY comp_graded)
          INTO graded_cut
          FROM _anchored_plans
         WHERE comp_graded IS NOT NULL;

        UPDATE plan_anchor_ladder
           SET comp_cut_graded = ROUND(graded_cut, 1), derivation = 'quantile_match'
         WHERE anchor = r.anchor;
    END LOOP;

    -- Enforce monotonicity (higher anchor => higher graded cut), in case of ties.
    -- Walk from top tier down; each cut must be <= the tier above it.
    WITH ordered AS (
        SELECT anchor, comp_cut_graded,
               MIN(comp_cut_graded) OVER (ORDER BY anchor DESC) AS running_min
          FROM plan_anchor_ladder
    )
    UPDATE plan_anchor_ladder l
       SET comp_cut_graded = o.running_min
      FROM ordered o
     WHERE l.anchor = o.anchor AND l.comp_cut_graded > o.running_min;

    RAISE NOTICE 'Ladder re-anchor: % anchored plans (% with graded comp). derivation=%',
        n_anchored, n_graded, CASE WHEN n_graded=0 THEN 'binary_fallback' ELSE 'quantile_match' END;
END
$reanchor$;

-- =====================================================================
-- 147.3  Re-point fn_plan_anchor_score to the graded ladder  [G7]
-- =====================================================================
-- Reads compliance_v2_attributable_pct (day-weighted) + graded total stress; looks
-- up the tier in plan_anchor_ladder via comp_cut_graded. Same guardrail-penalty
-- subtraction + GREATEST(1, ...) clamp as the legacy function. FREEZE: this function
-- is only re-evaluated for plans that do not already have a frozen anchor_score; the
-- backfill (147.5) calls it only for NULL anchors.
CREATE OR REPLACE FUNCTION public.fn_plan_anchor_score(p_plan_id text)
RETURNS smallint
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
  v_comp numeric;        -- graded controller-attributable compliance (NULL if no *_v2 days)
  v_comp_binary numeric; -- legacy binary compliance (co-existence / replay fallback)
  v_str  numeric;
  v_str_binary numeric;
  v_frac numeric;
  v_anchor smallint;
  v_penalty smallint := 0;
  v_use_graded boolean;
BEGIN
  -- day-weighted compliance + stress for the plan window. Graded uses ONLY days
  -- that have a *_v2 value (NULL-excluded); binary is the full-window fallback.
  SELECT
    (CASE WHEN SUM(pd.day_weight) FILTER (WHERE ds.compliance_v2_attributable_pct IS NOT NULL) > 0
          THEN SUM(pd.day_weight * ds.compliance_v2_attributable_pct) FILTER (WHERE ds.compliance_v2_attributable_pct IS NOT NULL)
               / SUM(pd.day_weight) FILTER (WHERE ds.compliance_v2_attributable_pct IS NOT NULL)
          ELSE NULL END),
    (CASE WHEN SUM(pd.day_weight) > 0
          THEN SUM(pd.day_weight * COALESCE(ds.compliance_pct, 0)) / SUM(pd.day_weight)
          ELSE NULL END),
    -- graded total stress = sum of the 4 graded_stress_hours, day-weighted
    (CASE WHEN SUM(pd.day_weight) FILTER (WHERE ds.compliance_v2_attributable_pct IS NOT NULL) > 0
          THEN SUM(pd.day_weight * (
                 COALESCE(ds.graded_stress_hours_heat,0) + COALESCE(ds.graded_stress_hours_cold,0)
               + COALESCE(ds.graded_stress_hours_vpd_high,0) + COALESCE(ds.graded_stress_hours_vpd_low,0)))
               FILTER (WHERE ds.compliance_v2_attributable_pct IS NOT NULL)
               / SUM(pd.day_weight) FILTER (WHERE ds.compliance_v2_attributable_pct IS NOT NULL)
          ELSE NULL END),
    (CASE WHEN SUM(pd.day_weight) > 0
          THEN SUM(pd.day_weight * (
                 COALESCE(ds.stress_hours_heat,0) + COALESCE(ds.stress_hours_cold,0)
               + COALESCE(ds.stress_hours_vpd_high,0) + COALESCE(ds.stress_hours_vpd_low,0)))
               / SUM(pd.day_weight)
          ELSE NULL END),
    SUM(pd.day_weight)
    INTO v_comp, v_comp_binary, v_str, v_str_binary, v_frac
    FROM v_plan_execution_intervals pei
    CROSS JOIN LATERAL generate_series(
          pei.interval_start::date::timestamptz,
          LEAST(pei.interval_end::date, CURRENT_DATE)::timestamptz,
          interval '1 day') d(d)
    CROSS JOIN LATERAL (SELECT GREATEST(EXTRACT(epoch FROM
          LEAST(pei.interval_end, d.d + interval '1 day') - GREATEST(pei.interval_start, d.d))/86400.0, 0) AS day_weight) pd
    LEFT JOIN daily_summary ds ON ds.date = d.d::date
   WHERE pei.plan_id = p_plan_id;

  IF v_frac IS NULL OR v_frac < 0.05 THEN
    RETURN NULL;
  END IF;

  -- Use graded ladder when graded data exists; otherwise fall back to the binary
  -- compliance + binary ladder cut (co-existence / replay window). This keeps the
  -- frozen anchors reproducible before the dual-write history is populated.
  v_use_graded := (v_comp IS NOT NULL AND v_str IS NOT NULL);
  IF NOT v_use_graded THEN
    v_comp := v_comp_binary;
    v_str  := v_str_binary;
  END IF;
  IF v_comp IS NULL OR v_str IS NULL THEN
    RETURN NULL;
  END IF;

  -- highest tier whose compliance + stress gates are both satisfied. Use the
  -- graded cut when grading, else the binary cut.
  SELECT l.anchor
    INTO v_anchor
    FROM plan_anchor_ladder l
   WHERE v_comp >= (CASE WHEN v_use_graded THEN COALESCE(l.comp_cut_graded, l.comp_cut_binary) ELSE l.comp_cut_binary END)
     AND (l.stress_cut IS NULL OR v_str < l.stress_cut)
   ORDER BY l.anchor DESC
   LIMIT 1;

  v_anchor := COALESCE(v_anchor, 1);

  SELECT COALESCE(guardrail_penalty, 0)
    INTO v_penalty
    FROM v_plan_guardrail_scorecard
   WHERE plan_id = p_plan_id;

  RETURN GREATEST(1, v_anchor - COALESCE(v_penalty, 0));
END;
$function$;

COMMENT ON FUNCTION public.fn_plan_anchor_score(text) IS
'Re-anchored (migration 147): day-weighted compliance_v2_attributable_pct + graded total stress, tiered '
'via plan_anchor_ladder.comp_cut_graded (quantile-matched to the frozen anchors). Same guardrail-penalty '
'+ GREATEST(1,...) clamp. Existing anchor_score rows are FROZEN; only NULL anchors are backfilled.';

-- =====================================================================
-- 147.4  Re-point the reward views to compliance_v2_attributable_pct  [G7]
-- =====================================================================
-- Keep the exact comp/100*80 + cost*20 form; comp just becomes the attributable
-- column. Binary columns stay exposed for one cycle (rollback). The graded total
-- stress replaces the binary stress in the *graded* columns; binary stress columns
-- remain for continuity.
-- NOTE on column order: v_plan_window_scorecard depends on this view (by column
-- NAME), and CREATE OR REPLACE VIEW forbids renaming/reordering existing columns.
-- We therefore PRESERVE the exact existing 15-column order (date ... planner_score)
-- and only APPEND the 3 new context columns at the end. The reward swap happens by
-- changing what FEEDS compliance_pct (col 7), not by inserting columns.
CREATE OR REPLACE VIEW v_planner_performance AS
WITH daily AS (
    SELECT d.date,
        COALESCE(d.graded_stress_hours_heat, d.stress_hours_heat, 0) AS heat_stress_h,
        COALESCE(d.graded_stress_hours_cold, d.stress_hours_cold, 0) AS cold_stress_h,
        COALESCE(d.graded_stress_hours_vpd_high, d.stress_hours_vpd_high, 0) AS vpd_high_stress_h,
        COALESCE(d.graded_stress_hours_vpd_low, d.stress_hours_vpd_low, 0) AS vpd_low_stress_h,
        (COALESCE(d.graded_stress_hours_heat, d.stress_hours_heat, 0)
         + COALESCE(d.graded_stress_hours_cold, d.stress_hours_cold, 0)
         + COALESCE(d.graded_stress_hours_vpd_high, d.stress_hours_vpd_high, 0)
         + COALESCE(d.graded_stress_hours_vpd_low, d.stress_hours_vpd_low, 0)) AS total_stress_h,
        -- REWARD COLUMN: controller-attributable graded compliance (fallback to binary
        -- during the dual-write co-existence / replay window so no score goes NULL).
        COALESCE(d.compliance_v2_attributable_pct, d.compliance_pct, 0) AS compliance_pct,
        COALESCE(d.graded_temp_compliance_pct, d.temp_compliance_pct, 0) AS temp_compliance_pct,
        COALESCE(d.graded_vpd_compliance_pct, d.vpd_compliance_pct, 0) AS vpd_compliance_pct,
        COALESCE(d.cost_total, 0) AS cost_total,
        COALESCE(d.cost_electric, 0) AS cost_electric,
        COALESCE(d.cost_gas, 0) AS cost_gas,
        COALESCE(d.cost_water, 0) AS cost_water,
        -- context (not scored), appended at end:
        COALESCE(d.compliance_pct, 0) AS compliance_binary_pct,
        COALESCE(d.compliance_v2_raw_pct, 0) AS compliance_raw_graded_pct,
        COALESCE(d.compliance_v2_unachievable_frac, 0) AS unachievable_frac
      FROM daily_summary d
     WHERE d.date IS NOT NULL
)
SELECT date,
    heat_stress_h, cold_stress_h, vpd_high_stress_h, vpd_low_stress_h, total_stress_h,
    round(compliance_pct::numeric, 1) AS compliance_pct,
    round(temp_compliance_pct::numeric, 1) AS temp_compliance_pct,
    round(vpd_compliance_pct::numeric, 1) AS vpd_compliance_pct,
    cost_total, cost_electric, cost_gas, cost_water,
    CASE WHEN total_stress_h > 0 THEN round((cost_total / total_stress_h)::numeric, 2) ELSE NULL END AS cost_per_stress_hour,
    round((compliance_pct / 100.0 * 80 + GREATEST(0, 1.0 - LEAST(cost_total / 15.0, 1.0)) * 20)::numeric, 1) AS planner_score,
    -- appended context columns (positions 16-18):
    round(compliance_binary_pct::numeric, 1) AS compliance_binary_pct,
    round(compliance_raw_graded_pct::numeric, 1) AS compliance_raw_graded_pct,
    round(unachievable_frac::numeric, 4) AS unachievable_frac
   FROM daily;

COMMENT ON VIEW v_planner_performance IS
'Re-pointed (migration 147): compliance_pct = controller-attributable graded (compliance_v2_attributable_pct, '
'fallback binary during co-existence). planner_score keeps comp/100*80 + cost*20. binary + raw-graded + '
'unachievable_frac exposed as unscored context. Stress = graded-deficit (fallback binary).';

-- Re-point v_daily_kpi.planner_score + compliance_pct to the attributable column.
CREATE OR REPLACE VIEW v_daily_kpi AS
SELECT date,
    round(COALESCE(compliance_v2_attributable_pct, compliance_pct, 0)::numeric, 1) AS compliance_pct,
    round(COALESCE(graded_temp_compliance_pct, temp_compliance_pct, 0)::numeric, 1) AS temp_compliance_pct,
    round(COALESCE(graded_vpd_compliance_pct, vpd_compliance_pct, 0)::numeric, 1) AS vpd_compliance_pct,
    round(COALESCE(graded_stress_hours_heat, stress_hours_heat, 0)::numeric, 2) AS heat_stress_h,
    round(COALESCE(graded_stress_hours_cold, stress_hours_cold, 0)::numeric, 2) AS cold_stress_h,
    round(COALESCE(graded_stress_hours_vpd_high, stress_hours_vpd_high, 0)::numeric, 2) AS vpd_high_stress_h,
    round(COALESCE(graded_stress_hours_vpd_low, stress_hours_vpd_low, 0)::numeric, 2) AS vpd_low_stress_h,
    round((COALESCE(graded_stress_hours_heat, stress_hours_heat, 0) + COALESCE(graded_stress_hours_cold, stress_hours_cold, 0)
         + COALESCE(graded_stress_hours_vpd_high, stress_hours_vpd_high, 0) + COALESCE(graded_stress_hours_vpd_low, stress_hours_vpd_low, 0))::numeric, 2) AS total_stress_h,
    round(COALESCE(kwh_total, kwh_estimated, 0)::numeric, 2) AS kwh,
    round(COALESCE(therms_estimated, gas_used_therms, 0)::numeric, 3) AS therms,
    round(COALESCE(water_used_gal, 0)::numeric, 0) AS water_gal,
    round(COALESCE(mister_water_gal, 0)::numeric, 0) AS mister_water_gal,
    round(COALESCE(cost_electric, 0)::numeric, 2) AS cost_electric,
    round(COALESCE(cost_gas, 0)::numeric, 2) AS cost_gas,
    round(COALESCE(cost_water, 0)::numeric, 2) AS cost_water,
    round(COALESCE(cost_total, 0)::numeric, 2) AS cost_total,
    round(temp_min::numeric, 1) AS temp_min,
    round(temp_max::numeric, 1) AS temp_max,
    round(temp_avg::numeric, 1) AS temp_avg,
    round(vpd_min::numeric, 2) AS vpd_min,
    round(vpd_max::numeric, 2) AS vpd_max,
    round(vpd_avg::numeric, 2) AS vpd_avg,
    round(dli_final::numeric, 1) AS dli,
    round(min_dp_margin_f::numeric, 1) AS dp_margin_min_f,
    round(COALESCE(dp_risk_hours, 0)::numeric, 1) AS dp_risk_hours,
    round((COALESCE(compliance_v2_attributable_pct, compliance_pct, 0) / 100.0 * 80
         + GREATEST(0, 1.0 - LEAST(COALESCE(cost_total, 0) / 15.0, 1.0)) * 20)::numeric, 1) AS planner_score
   FROM daily_summary
  WHERE date IS NOT NULL
  ORDER BY date;

COMMENT ON VIEW v_daily_kpi IS
'Re-pointed (migration 147): compliance_pct + planner_score driven by compliance_v2_attributable_pct '
'(fallback binary during co-existence). Same comp/100*80 + cost*20 form. Stress = graded-deficit.';

-- =====================================================================
-- 147.5  Backfill ONLY null/in-flight anchors; FREEZE the rest  [G7]
-- =====================================================================
-- Do NOT re-run backfill-plan-evaluations.py over history. Here we set anchor_score
-- ONLY for plan_journal rows where anchor_score IS NULL AND a defined plan window
-- exists (the 2 null/in-flight + optionally the outcome-without-anchor population).
-- The frozen anchored rows are untouched. outcome_score is never recomputed.
UPDATE plan_journal pj
   SET anchor_score = fn_plan_anchor_score(pj.plan_id)
 WHERE pj.anchor_score IS NULL
   AND fn_plan_anchor_score(pj.plan_id) IS NOT NULL;

-- =====================================================================
-- 147.6  VERIFICATION: ordinal stability (>=90% of anchored plans reproduce anchor)
-- =====================================================================
-- Re-derive each anchored plan's anchor under the re-anchored graded ladder and
-- compare to its FROZEN anchor_score. Acceptance: >=90% reproduce (exact tier).
-- This is a read-only check (RAISE NOTICE), not a hard failure, because in the
-- rollback-replay the graded *_v2 columns are empty (binary_fallback identity) and
-- the real validation runs in Phase 2 against the dual-written history. The query
-- is emitted so operators can run it against the live (dual-written) DB.
DO $verify$
DECLARE
    n_total int;
    n_match int;
    pct numeric;
    v_derivation text;
BEGIN
    SELECT max(derivation) INTO v_derivation FROM plan_anchor_ladder WHERE derivation = 'quantile_match';

    SELECT count(*),
           count(*) FILTER (WHERE pj.anchor_score = fn_plan_anchor_score(pj.plan_id))
      INTO n_total, n_match
      FROM plan_journal pj
     WHERE pj.anchor_score IS NOT NULL;

    IF n_total = 0 THEN
        RAISE NOTICE 'Ladder verification: no anchored plans to check.';
    ELSE
        pct := ROUND(100.0 * n_match / n_total, 1);
        RAISE NOTICE 'Ladder ordinal stability: %/% anchored plans reproduce anchor (%%%). derivation=%. (Acceptance >=90%% against dual-written history.)',
            n_match, n_total, pct, COALESCE(v_derivation, 'binary_fallback');
    END IF;
END
$verify$;

-- Validator verification query (run against the LIVE dual-written DB, not the replay):
--   SELECT count(*) AS n_anchored,
--          count(*) FILTER (WHERE anchor_score = fn_plan_anchor_score(plan_id)) AS n_reproduced,
--          ROUND(100.0 * count(*) FILTER (WHERE anchor_score = fn_plan_anchor_score(plan_id))
--                / NULLIF(count(*),0), 1) AS pct_ordinal_stable
--     FROM plan_journal WHERE anchor_score IS NOT NULL;
-- Acceptance: pct_ordinal_stable >= 90.0.
