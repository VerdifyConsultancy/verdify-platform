-- 200-materialized-equipment-runtime.sql
--
-- 2026-07-12 incident: the operator read "fans not running" off the site
-- during a 96°F afternoon — both fans were physically running (visually
-- confirmed; relay truth agreed). The graphs lied: every equipment/fan panel
-- on nine dashboards queries v_equipment_runtime_daily, which exceeds
-- Grafana's query timeout (~41 s even after the migration-199 bound; 200+ s
-- before it), so the panels render blank/zero.
--
-- Fix: dashboards read a MATERIALIZED snapshot refreshed every 10 minutes by
-- the verdify-band-curve-refresh CronJob (same pattern as mv_band_curve).
-- The ingestor daily-summary task and the firmware deploy preflight KEEP
-- reading the live view — their budgets tolerate it and the deploy gate
-- wants current-day truth, not a 10-minute-old snapshot.
--
-- Non-self-transactional: CREATE MATERIALIZED VIEW ... WITH DATA + plain
-- CREATE UNIQUE INDEX (no CONCURRENTLY anywhere). Safe for an outer rollback
-- proof. Functional rollback: DROP MATERIALIZED VIEW
-- mv_equipment_runtime_daily (and re-point the dashboards at the live view).

CREATE MATERIALIZED VIEW public.mv_equipment_runtime_daily AS
SELECT * FROM public.v_equipment_runtime_daily
WITH DATA;

-- Required for REFRESH MATERIALIZED VIEW CONCURRENTLY (the cron path).
CREATE UNIQUE INDEX mv_equipment_runtime_daily_pk
    ON public.mv_equipment_runtime_daily (greenhouse_id, equipment, day);

COMMENT ON MATERIALIZED VIEW public.mv_equipment_runtime_daily IS
'Dashboard snapshot of v_equipment_runtime_daily (migration 200), refreshed '
'every 10 min by the verdify-band-curve-refresh CronJob. Panels must read '
'THIS, not the live view — the live view exceeds Grafana query timeouts '
'(the 2026-07-12 "fans not running" false alarm). Code paths that need '
'current-day truth (daily_summary_live, firmware deploy preflight) keep '
'reading the live view.';
