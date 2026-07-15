-- 203-materialized-daily-kpi-scorecard.sql
--
-- 2026-07-14/15 incident: verdify-db-0 in a cgroup OOM-kill crash loop
-- (postgres backends killed by signal 9 → postmaster reinit → every client
-- sees "FATAL: the database system is in recovery mode"; kills observed
-- 02:15:04Z and 02:15:39Z among others, RESTARTS=2 on the pod). Every
-- Grafana panel across graphs.verdify.ai errored during each recovery, and
-- verdify-api /health/detailed + verdify-mcp readiness flapped with it.
--
-- The killer: fn_planner_scorecard(date) makes 27 separate scans of
-- v_daily_kpi (21 single-date + 6 seven-day-window branches). v_daily_kpi
-- does NOT push the date predicate down — the filter is applied after
-- full-history joins over v_water_attribution_daily / v_runtime_energy_daily
-- / v_dli_daily window aggregations (plan cost ~195M; ONE bounded evaluation
-- measured 13.8 s). The site-evidence-planning-quality dashboard fires 7
-- panels calling the function concurrently on every load/refresh
-- => 7 × 27 inlined copies of that plan, with parallel hash joins, inside
-- the pod's 6Gi limit => OOM.
--
-- Fix (same pattern as migration 200 / the 2026-07-12 "fans not running"
-- incident): materialize v_daily_kpi as mv_daily_kpi, refreshed every 10 min
-- by the verdify-band-curve-refresh CronJob, and repoint
-- fn_planner_scorecard at the matview (signature and output unchanged, so
-- dashboards, mcp outcome_kpi, gather-plan-context.sh and verdify-metrics.py
-- need no changes). 27 index scans of a 345-row matview replace 27
-- full-history view evaluations. Values are at most ~10 min stale — the
-- same, accepted tradeoff as mv_equipment_runtime_daily; anything needing
-- current-second truth must read v_daily_kpi live (nothing does today).
--
-- Non-self-transactional: CREATE MATERIALIZED VIEW ... WITH DATA + plain
-- CREATE UNIQUE INDEX + CREATE OR REPLACE FUNCTION (no CONCURRENTLY, no
-- top-level COMMIT). Safe for an outer BEGIN..ROLLBACK proof.
-- Functional rollback: DROP MATERIALIZED VIEW public.mv_daily_kpi and
-- re-create fn_planner_scorecard reading public.v_daily_kpi (definition in
-- migration 194 / pg_get_functiondef prior to this migration).

CREATE MATERIALIZED VIEW public.mv_daily_kpi AS
SELECT * FROM public.v_daily_kpi
WITH DATA;

-- Required for REFRESH MATERIALIZED VIEW CONCURRENTLY (the cron path).
-- v_daily_kpi is one row per date (single greenhouse; verified 345/345
-- distinct at creation).
CREATE UNIQUE INDEX mv_daily_kpi_pk ON public.mv_daily_kpi (date);

COMMENT ON MATERIALIZED VIEW public.mv_daily_kpi IS
'Dashboard/scorecard snapshot of v_daily_kpi (migration 203), refreshed '
'every 10 min by the verdify-band-curve-refresh CronJob. fn_planner_scorecard '
'and panels must read THIS, not the live view — 27 live-view scans per '
'scorecard call OOM-crashed the DB on 2026-07-14/15 (v_daily_kpi has no date '
'pushdown; one evaluation is ~14 s). Live-truth consumers must use '
'v_daily_kpi explicitly and bound their own memory/time.';

CREATE OR REPLACE FUNCTION public.fn_planner_scorecard(p_date date DEFAULT CURRENT_DATE)
RETURNS TABLE(metric text, value numeric)
LANGUAGE plpgsql
STABLE
AS $function$
BEGIN
    RETURN QUERY
    SELECT 'planner_score'::text, k.planner_score FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'planner_score_resource_weight_pct', k.planner_score_resource_weight_pct FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'resource_terms_available', CASE WHEN k.resource_terms_available THEN 1::numeric ELSE 0::numeric END FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'compliance_pct', k.compliance_pct FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'temp_compliance_pct', k.temp_compliance_pct FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'vpd_compliance_pct', k.vpd_compliance_pct FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'total_stress_h', k.total_stress_h FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'heat_stress_h', k.heat_stress_h FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'cold_stress_h', k.cold_stress_h FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'vpd_high_stress_h', k.vpd_high_stress_h FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'vpd_low_stress_h', k.vpd_low_stress_h FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'kwh', k.kwh FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'therms', k.therms FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'water_gal', k.water_gal FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'mister_water_gal', k.mister_water_gal FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'cost_electric', k.cost_electric FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'cost_gas', k.cost_gas FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'cost_water', k.cost_water FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'cost_total', k.cost_total FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'dp_margin_min_f', k.dp_margin_min_f FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'dp_risk_hours', k.dp_risk_hours FROM public.mv_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT '7d_avg_score', round(avg(k.planner_score), 1) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_compliance', round(avg(k.compliance_pct), 1) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_cost', round(avg(k.cost_total), 2) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_kwh', round(avg(k.kwh), 1) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_therms', round(avg(k.therms), 3) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_water_gal', round(avg(k.water_gal), 0) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1;
END;
$function$;

COMMENT ON FUNCTION public.fn_planner_scorecard(date) IS
'Planner/climate daily scorecard (metric, value) for a date. Since migration '
'203 this reads mv_daily_kpi (10-min snapshot, refreshed by the '
'verdify-band-curve-refresh CronJob) — NOT the live v_daily_kpi. The live '
'view has no date pushdown; 27 live scans per call OOM-crashed the DB on '
'2026-07-14/15 under concurrent Grafana panels.';
