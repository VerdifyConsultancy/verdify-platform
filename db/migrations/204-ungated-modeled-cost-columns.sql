-- 204-ungated-modeled-cost-columns.sql
--
-- Follow-through on the 2026-07-14/15 graphs sprint: every cost/kWh panel on
-- graphs.verdify.ai has been blank since the panels moved onto v_daily_kpi
-- (site-home on 06-29), because the ADR-0004 scoring gate is structurally
-- unsatisfiable in prod: v_runtime_energy_daily.available_for_scoring
-- requires NOT bool_or(has_uncertainty), and every electric_watts row in
-- resource_coefficients was deliberately seeded by migration 193 with
-- provisional ±10-20% bounds "until circuit-isolated measurement" — so
-- energy_ok has been false for all 345 days and kwh/cost_* fail closed to
-- NULL. PR #437 (2026-07-09) then put the ingestor's legacy
-- daily_summary.cost_electric/cost_total writes behind the SAME gate, which
-- is why the last legacy estimates froze at 2026-07-08. The model itself is
-- healthy (e.g. 2026-07-13: modeled_kwh 16.892, full runtime coverage).
--
-- This migration does NOT touch the gate, planner_score, resource_ok, or
-- any scoring/deploy-gate semantics (whether provisional-uncertainty
-- coefficients should become scoreable is an ADR-0004 decision, tracked in
-- the sprint report). It only APPENDS explicitly-labeled, ungated estimate
-- columns to v_daily_kpi so display surfaces can show the modeled numbers
-- as estimates:
--   kwh_modeled        — runtime-modeled kWh (v_runtime_energy_daily), any day
--   therms_modeled     — therms_estimated fallback gas_used_therms
--   water_gal_est      — quality-filtered meter gal, else raw meter gal
--   cost_electric_est  — modeled kWh × $0.111, else frozen legacy estimate
--   cost_gas_est       — modeled therms × $0.83, else legacy
--   cost_water_est     — est gal × $0.00484, else legacy
--   cost_total_est     — sum (needs electric term present)
-- Price constants intentionally mirror ingestor/tasks/daily.py
-- _apply_resource_cost_gate ($0.111/kWh, $0.83/therm, $0.00484/gal).
--
-- mv_daily_kpi is rebuilt (its stored query captured the pre-204 column
-- list at creation), keeping the migration-203 unique index contract.
--
-- SELF-TRANSACTIONAL (own BEGIN/COMMIT): the DROP + re-CREATE of
-- mv_daily_kpi must be atomic so fn_planner_scorecard and the repointed
-- dashboard panels never observe a missing matview. Per the migration
-- safety rules: do NOT wrap in an outer BEGIN..ROLLBACK; rollback-validate
-- by swapping the trailing COMMIT for ROLLBACK.

BEGIN;

CREATE OR REPLACE VIEW public.v_daily_kpi AS
WITH evidence AS (
         SELECT d.date, d.cycles_fan1, d.cycles_fan2, d.cycles_heat1,
            d.cycles_heat2, d.cycles_fog, d.cycles_vent, d.cycles_dehum,
            d.cycles_safety_dehum, d.runtime_fan1_min, d.runtime_fan2_min,
            d.runtime_heat1_min, d.runtime_heat2_min, d.runtime_fog_min,
            d.runtime_vent_min, d.runtime_mister_south_h,
            d.runtime_mister_west_h, d.runtime_mister_center_h,
            d.water_used_gal, d.mister_water_gal, d.dli_final, d.captured_at,
            d.kwh_total, d.kwh_heat, d.kwh_fans, d.kwh_other, d.peak_kw,
            d.gas_used_therms, d.runtime_grow_light_min, d.cycles_grow_light,
            d.runtime_drip_wall_h, d.runtime_drip_center_h, d.kwh_estimated,
            d.therms_estimated, d.cost_electric, d.cost_gas, d.cost_water,
            d.cost_total, d.temp_min, d.temp_max, d.temp_avg, d.rh_min,
            d.rh_max, d.rh_avg, d.vpd_min, d.vpd_max, d.vpd_avg, d.co2_avg,
            d.outdoor_temp_min, d.outdoor_temp_max, d.stress_hours_heat,
            d.stress_hours_cold, d.stress_hours_vpd_high,
            d.stress_hours_vpd_low, d.notes, d.greenhouse_id,
            d.min_dp_margin_f, d.dp_risk_hours, d.compliance_pct,
            d.temp_compliance_pct, d.vpd_compliance_pct,
            d.mister_fairness_overrides_today, d.cycles_mister_south,
            d.cycles_mister_west, d.cycles_mister_center, d.cycles_drip_wall,
            d.cycles_drip_center, d.runtime_drip_wall_fert_h,
            d.runtime_drip_center_fert_h, d.runtime_mister_south_fert_h,
            d.runtime_mister_west_fert_h, d.runtime_fert_master_h,
            d.runtime_irrigation_clean_h, d.runtime_irrigation_fert_h,
            d.runtime_irrigation_total_h, d.cycles_drip_wall_fert,
            d.cycles_drip_center_fert, d.cycles_mister_south_fert,
            d.cycles_mister_west_fert, d.cycles_fert_master,
            d.irrigation_water_gal, d.fertigation_water_gal,
            d.compliance_v2_raw_pct, d.compliance_v2_attributable_pct,
            d.compliance_v2_unachievable_frac, d.graded_temp_compliance_pct,
            d.graded_vpd_compliance_pct, d.graded_stress_hours_heat,
            d.graded_stress_hours_cold, d.graded_stress_hours_vpd_high,
            d.graded_stress_hours_vpd_low, d.feasibility_unknown_min,
            d.dev_temp_norm_median_day, d.dev_temp_norm_median_night,
            d.dev_temp_norm_p95, d.dev_vpd_norm_median_day,
            d.dev_vpd_norm_median_night, d.dev_vpd_norm_p95,
            d.runtime_grow_light_main_min, d.runtime_grow_light_grow_min,
            w.quality_filtered_meter_gal, w.climate_wetting_gal,
            COALESCE(w.available_for_scoring, false) AS water_ok,
            e.modeled_kwh,
            COALESCE(e.available_for_scoring, false) AS energy_ok,
            dl.crop_dli_mol_m2_day,
            dl.availability AS dli_availability,
            dl.unavailable_reason AS dli_unavailable_reason,
            dl.provenance AS dli_provenance,
            dl.validity_revision AS dli_validity_revision,
            dl.valid_from AS dli_valid_from,
            dl.valid_to AS dli_valid_to
           FROM daily_summary d
             LEFT JOIN v_water_attribution_daily w ON w.date = d.date AND w.greenhouse_id = d.greenhouse_id
             LEFT JOIN v_runtime_energy_daily e ON e.date = d.date AND e.greenhouse_id = d.greenhouse_id
             LEFT JOIN v_dli_daily dl ON dl.date = d.date AND dl.greenhouse_id = COALESCE(d.greenhouse_id, 'vallery'::text)
          WHERE d.date IS NOT NULL
        ), normalized AS (
         SELECT e.*,
            COALESCE(e.compliance_v2_attributable_pct, e.compliance_pct, 0::double precision) AS score_compliance,
            e.water_ok AND e.energy_ok AND e.cost_total IS NOT NULL AS resource_ok
           FROM evidence e
        )
 SELECT date,
    round(score_compliance::numeric, 1) AS compliance_pct,
    round(COALESCE(graded_temp_compliance_pct, temp_compliance_pct, 0::double precision)::numeric, 1) AS temp_compliance_pct,
    round(COALESCE(graded_vpd_compliance_pct, vpd_compliance_pct, 0::double precision)::numeric, 1) AS vpd_compliance_pct,
    round(COALESCE(graded_stress_hours_heat, stress_hours_heat, 0::double precision)::numeric, 2) AS heat_stress_h,
    round(COALESCE(graded_stress_hours_cold, stress_hours_cold, 0::double precision)::numeric, 2) AS cold_stress_h,
    round(COALESCE(graded_stress_hours_vpd_high, stress_hours_vpd_high, 0::double precision)::numeric, 2) AS vpd_high_stress_h,
    round(COALESCE(graded_stress_hours_vpd_low, stress_hours_vpd_low, 0::double precision)::numeric, 2) AS vpd_low_stress_h,
    round((COALESCE(graded_stress_hours_heat, stress_hours_heat, 0::double precision) + COALESCE(graded_stress_hours_cold, stress_hours_cold, 0::double precision) + COALESCE(graded_stress_hours_vpd_high, stress_hours_vpd_high, 0::double precision) + COALESCE(graded_stress_hours_vpd_low, stress_hours_vpd_low, 0::double precision))::numeric, 2) AS total_stress_h,
        CASE
            WHEN energy_ok THEN round(modeled_kwh::numeric, 2)
            ELSE NULL::numeric
        END AS kwh,
        CASE
            WHEN resource_ok THEN round(COALESCE(therms_estimated, gas_used_therms)::numeric, 3)
            ELSE NULL::numeric
        END AS therms,
        CASE
            WHEN water_ok THEN round(quality_filtered_meter_gal::numeric, 0)
            ELSE NULL::numeric
        END AS water_gal,
        CASE
            WHEN water_ok THEN round(climate_wetting_gal::numeric, 0)
            ELSE NULL::numeric
        END AS mister_water_gal,
        CASE
            WHEN energy_ok THEN round(cost_electric::numeric, 2)
            ELSE NULL::numeric
        END AS cost_electric,
        CASE
            WHEN resource_ok THEN round(cost_gas::numeric, 2)
            ELSE NULL::numeric
        END AS cost_gas,
        CASE
            WHEN water_ok THEN round(cost_water::numeric, 2)
            ELSE NULL::numeric
        END AS cost_water,
        CASE
            WHEN resource_ok THEN round(cost_total::numeric, 2)
            ELSE NULL::numeric
        END AS cost_total,
    round(temp_min::numeric, 1) AS temp_min,
    round(temp_max::numeric, 1) AS temp_max,
    round(temp_avg::numeric, 1) AS temp_avg,
    round(vpd_min::numeric, 2) AS vpd_min,
    round(vpd_max::numeric, 2) AS vpd_max,
    round(vpd_avg::numeric, 2) AS vpd_avg,
    round(crop_dli_mol_m2_day::numeric, 1) AS dli,
    round(min_dp_margin_f::numeric, 1) AS dp_margin_min_f,
    round(COALESCE(dp_risk_hours, 0::double precision)::numeric, 1) AS dp_risk_hours,
    round(
        CASE
            WHEN resource_ok THEN score_compliance / 100.0::double precision * 80::double precision + GREATEST(0::double precision, 1.0::double precision - LEAST(cost_total / 15.0::double precision, 1.0::double precision)) * 20::double precision
            ELSE score_compliance
        END::numeric, 1) AS planner_score,
        CASE
            WHEN resource_ok THEN 20::numeric
            ELSE 0::numeric
        END AS planner_score_resource_weight_pct,
    resource_ok AS resource_terms_available,
    dli_availability,
    dli_unavailable_reason,
    dli_provenance,
    dli_validity_revision,
    dli_valid_from,
    dli_valid_to,
    -- ===== migration 204: ungated ESTIMATE columns (display surfaces) =====
    round(modeled_kwh::numeric, 2) AS kwh_modeled,
    round(COALESCE(therms_estimated, gas_used_therms)::numeric, 3) AS therms_modeled,
    round(CASE WHEN water_ok THEN quality_filtered_meter_gal ELSE water_used_gal END::numeric, 0) AS water_gal_est,
    COALESCE(round((modeled_kwh * 0.111)::numeric, 2), round(cost_electric::numeric, 2)) AS cost_electric_est,
    COALESCE(round((COALESCE(therms_estimated, gas_used_therms) * 0.83)::numeric, 2), round(cost_gas::numeric, 2)) AS cost_gas_est,
    COALESCE(round((CASE WHEN water_ok THEN quality_filtered_meter_gal ELSE water_used_gal END * 0.00484)::numeric, 2), round(cost_water::numeric, 2)) AS cost_water_est,
    CASE
        WHEN COALESCE(modeled_kwh * 0.111, cost_electric) IS NOT NULL THEN
            round((COALESCE(modeled_kwh * 0.111, cost_electric)
                 + COALESCE(COALESCE(therms_estimated, gas_used_therms) * 0.83, cost_gas, 0::double precision)
                 + COALESCE(CASE WHEN water_ok THEN quality_filtered_meter_gal ELSE water_used_gal END * 0.00484, cost_water, 0::double precision))::numeric, 2)
        ELSE NULL::numeric
    END AS cost_total_est
   FROM normalized
  ORDER BY date;

-- Rebuild the matview: its stored query froze the pre-204 column list.
DROP MATERIALIZED VIEW public.mv_daily_kpi;

CREATE MATERIALIZED VIEW public.mv_daily_kpi AS
SELECT * FROM public.v_daily_kpi
WITH DATA;

CREATE UNIQUE INDEX mv_daily_kpi_pk ON public.mv_daily_kpi (date);

COMMENT ON MATERIALIZED VIEW public.mv_daily_kpi IS
'Dashboard/scorecard snapshot of v_daily_kpi (migrations 203/204), refreshed '
'every 10 min by the verdify-band-curve-refresh CronJob. fn_planner_scorecard '
'and panels must read THIS, not the live view — 27 live-view scans per '
'scorecard call OOM-crashed the DB on 2026-07-14/15 (v_daily_kpi has no date '
'pushdown; one evaluation is ~14 s). The *_est/*_modeled columns (204) are '
'ungated display estimates; the gated kwh/cost_* columns remain the ADR-0004 '
'evidence tier.';

COMMIT;
