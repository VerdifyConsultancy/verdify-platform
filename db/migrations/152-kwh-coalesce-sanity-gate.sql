-- Migration 152: Stop v_daily_kpi.kwh preferring the broken Shelly meter
--                (issue #41, audit §7-#5 / P1).
--
-- CONTEXT
-- v_daily_kpi.kwh historically computed:
--     round((COALESCE(kwh_total, kwh_estimated, 0))::numeric, 2) AS kwh
-- i.e. it blindly preferred the measured Shelly meter (daily_summary.kwh_total)
-- and only fell through to the runtime-derived estimate (kwh_estimated) when the
-- meter was NULL. The Shelly meter has been running 3-6x BELOW estimate
-- (2026-05-30: meter 6.8 kWh vs estimate 41.0 kWh). A broken/zero/partial meter
-- reading therefore FLOORS the KPI. The planner reads this view directly
-- (gather-plan-context.sh surfaces it in the 7-day PLANNER SCORE TREND), and any
-- dashboard consumer inherits the same broken floor. M3 only fixed the prose
-- warning in iris_planner.py and the ingestor log line; the DB view feeding the
-- planner trend was never corrected.
--
-- FIX
-- CREATE OR REPLACE the view so kwh GATES the meter behind a sanity check:
-- trust kwh_total ONLY when it is present, strictly positive, AND (there is no
-- estimate to compare against, OR the meter is at least HALF the estimate). Any
-- meter reading below ~half the estimate is rejected and the reliable estimate
-- wins:
--     CASE
--       WHEN kwh_total IS NOT NULL
--            AND kwh_total > 0
--            AND (kwh_estimated IS NULL OR kwh_total >= 0.5 * kwh_estimated)
--         THEN kwh_total
--       ELSE COALESCE(kwh_estimated, kwh_total, 0)
--     END
-- A sane meter (>= half estimate) is still preferred — the estimate is the
-- floor of last resort, never a silent override of a trustworthy meter. When
-- the meter is NULL/zero/low and there is no estimate either, the final
-- COALESCE keeps the legacy fallthrough (meter, then 0) so we never invent data.
--
-- Every OTHER output column of v_daily_kpi is reproduced VERBATIM from the
-- production definition (db/schema.sql), including the planner_score formula —
-- CREATE OR REPLACE preserves the full column set, order, and names, so the
-- planner and dashboards see an unchanged shape with a corrected kwh only.
--
-- IDEMPOTENCY
-- CREATE OR REPLACE VIEW is inherently idempotent: re-applying replaces the view
-- with the identical definition (no-op, no error). Additive / non-destructive:
-- no DROP, no table or row touched, no live data removed. Pure read-side change.
--
-- ROLLBACK (documented; see PR body) — restore the prior view definition:
--   CREATE OR REPLACE VIEW public.v_daily_kpi AS
--    SELECT date,
--       round((COALESCE(compliance_pct, (0)::double precision))::numeric, 1) AS compliance_pct,
--       round((COALESCE(temp_compliance_pct, (0)::double precision))::numeric, 1) AS temp_compliance_pct,
--       round((COALESCE(vpd_compliance_pct, (0)::double precision))::numeric, 1) AS vpd_compliance_pct,
--       round((COALESCE(stress_hours_heat, (0)::double precision))::numeric, 2) AS heat_stress_h,
--       round((COALESCE(stress_hours_cold, (0)::double precision))::numeric, 2) AS cold_stress_h,
--       round((COALESCE(stress_hours_vpd_high, (0)::double precision))::numeric, 2) AS vpd_high_stress_h,
--       round((COALESCE(stress_hours_vpd_low, (0)::double precision))::numeric, 2) AS vpd_low_stress_h,
--       round(((((COALESCE(stress_hours_heat, (0)::double precision) + COALESCE(stress_hours_cold, (0)::double precision)) + COALESCE(stress_hours_vpd_high, (0)::double precision)) + COALESCE(stress_hours_vpd_low, (0)::double precision)))::numeric, 2) AS total_stress_h,
--       round((COALESCE(kwh_total, kwh_estimated, (0)::double precision))::numeric, 2) AS kwh,
--       round((COALESCE(therms_estimated, gas_used_therms, (0)::double precision))::numeric, 3) AS therms,
--       round((COALESCE(water_used_gal, (0)::double precision))::numeric, 0) AS water_gal,
--       round((COALESCE(mister_water_gal, (0)::double precision))::numeric, 0) AS mister_water_gal,
--       round((COALESCE(cost_electric, (0)::double precision))::numeric, 2) AS cost_electric,
--       round((COALESCE(cost_gas, (0)::double precision))::numeric, 2) AS cost_gas,
--       round((COALESCE(cost_water, (0)::double precision))::numeric, 2) AS cost_water,
--       round((COALESCE(cost_total, (0)::double precision))::numeric, 2) AS cost_total,
--       round((temp_min)::numeric, 1) AS temp_min,
--       round((temp_max)::numeric, 1) AS temp_max,
--       round((temp_avg)::numeric, 1) AS temp_avg,
--       round((vpd_min)::numeric, 2) AS vpd_min,
--       round((vpd_max)::numeric, 2) AS vpd_max,
--       round((vpd_avg)::numeric, 2) AS vpd_avg,
--       round((dli_final)::numeric, 1) AS dli,
--       round((min_dp_margin_f)::numeric, 1) AS dp_margin_min_f,
--       round((COALESCE(dp_risk_hours, (0)::double precision))::numeric, 1) AS dp_risk_hours,
--       round(((((COALESCE(compliance_pct, (0)::double precision) / (100.0)::double precision) * (80)::double precision) + (GREATEST((0)::double precision, ((1.0)::double precision - LEAST((COALESCE(cost_total, (0)::double precision) / (15.0)::double precision), (1.0)::double precision))) * (20)::double precision)))::numeric, 1) AS planner_score
--      FROM public.daily_summary
--     WHERE (date IS NOT NULL)
--     ORDER BY date;
--
-- ROLLBACK-REPLAY SAFETY (issue #23)
-- This migration contains NO top-level COMMIT and no commit-forcing statement
-- (e.g. CREATE INDEX CONCURRENTLY). It is a single CREATE OR REPLACE VIEW, so the
-- rollback-validation harness can wrap it in an outer BEGIN..ROLLBACK without the
-- migration self-committing and defeating the dry-run.
--
-- RESTARTS (CLAUDE.md rule 7): this migration does NOT touch verdify_schemas/**,
-- ingestor/entity_map.py, or mcp/server.py, so no service-restart obligation is
-- triggered. Read-side view change on a plain table (daily_summary). No device
-- contact. The planner picks up the corrected kwh on its next read of the view.

CREATE OR REPLACE VIEW public.v_daily_kpi AS
 SELECT date,
    round((COALESCE(compliance_pct, (0)::double precision))::numeric, 1) AS compliance_pct,
    round((COALESCE(temp_compliance_pct, (0)::double precision))::numeric, 1) AS temp_compliance_pct,
    round((COALESCE(vpd_compliance_pct, (0)::double precision))::numeric, 1) AS vpd_compliance_pct,
    round((COALESCE(stress_hours_heat, (0)::double precision))::numeric, 2) AS heat_stress_h,
    round((COALESCE(stress_hours_cold, (0)::double precision))::numeric, 2) AS cold_stress_h,
    round((COALESCE(stress_hours_vpd_high, (0)::double precision))::numeric, 2) AS vpd_high_stress_h,
    round((COALESCE(stress_hours_vpd_low, (0)::double precision))::numeric, 2) AS vpd_low_stress_h,
    round(((((COALESCE(stress_hours_heat, (0)::double precision) + COALESCE(stress_hours_cold, (0)::double precision)) + COALESCE(stress_hours_vpd_high, (0)::double precision)) + COALESCE(stress_hours_vpd_low, (0)::double precision)))::numeric, 2) AS total_stress_h,
    round((
        CASE
            WHEN kwh_total IS NOT NULL
                 AND kwh_total > (0)::double precision
                 AND (kwh_estimated IS NULL OR kwh_total >= ((0.5)::double precision * kwh_estimated))
                THEN kwh_total
            ELSE COALESCE(kwh_estimated, kwh_total, (0)::double precision)
        END)::numeric, 2) AS kwh,
    round((COALESCE(therms_estimated, gas_used_therms, (0)::double precision))::numeric, 3) AS therms,
    round((COALESCE(water_used_gal, (0)::double precision))::numeric, 0) AS water_gal,
    round((COALESCE(mister_water_gal, (0)::double precision))::numeric, 0) AS mister_water_gal,
    round((COALESCE(cost_electric, (0)::double precision))::numeric, 2) AS cost_electric,
    round((COALESCE(cost_gas, (0)::double precision))::numeric, 2) AS cost_gas,
    round((COALESCE(cost_water, (0)::double precision))::numeric, 2) AS cost_water,
    round((COALESCE(cost_total, (0)::double precision))::numeric, 2) AS cost_total,
    round((temp_min)::numeric, 1) AS temp_min,
    round((temp_max)::numeric, 1) AS temp_max,
    round((temp_avg)::numeric, 1) AS temp_avg,
    round((vpd_min)::numeric, 2) AS vpd_min,
    round((vpd_max)::numeric, 2) AS vpd_max,
    round((vpd_avg)::numeric, 2) AS vpd_avg,
    round((dli_final)::numeric, 1) AS dli,
    round((min_dp_margin_f)::numeric, 1) AS dp_margin_min_f,
    round((COALESCE(dp_risk_hours, (0)::double precision))::numeric, 1) AS dp_risk_hours,
    round(((((COALESCE(compliance_pct, (0)::double precision) / (100.0)::double precision) * (80)::double precision) + (GREATEST((0)::double precision, ((1.0)::double precision - LEAST((COALESCE(cost_total, (0)::double precision) / (15.0)::double precision), (1.0)::double precision))) * (20)::double precision)))::numeric, 1) AS planner_score
   FROM public.daily_summary
  WHERE (date IS NOT NULL)
  ORDER BY date;

ALTER VIEW public.v_daily_kpi OWNER TO verdify;

COMMENT ON VIEW public.v_daily_kpi IS 'Daily KPI rollup over daily_summary. kwh GATES the measured Shelly meter (kwh_total) behind a sanity check (migration 152 / issue #41): the meter is trusted only when present, > 0, and >= half the runtime estimate (kwh_estimated); otherwise the estimate wins, so a broken/zero/low meter no longer floors the KPI the planner reads.';
