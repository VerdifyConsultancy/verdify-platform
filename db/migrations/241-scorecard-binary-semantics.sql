-- #371: separate binary reading fractions from graded controller credit.
-- Forward-only repair: do not edit applied migration 206.
-- Same view/matview columns, OIDs, owners and ACLs; no DROP/CASCADE.
-- Wrap-safe: normal refresh participates in the migration runner transaction.
-- planner_score and all resource eligibility/estimate rules remain unchanged.
-- Historical daily_summary fractions use the available house average against
-- desired setpoint history, NOT duration-weighted fixed-panel crop compliance.
-- Legacy stress counts assume one minute per scored reading. Coverage and
-- firmware-consumed target lineage are not established by this repair.

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
            e.water_ok AND e.energy_ok AND e.cost_total IS NOT NULL AS resource_ok,
            CASE WHEN e.water_ok THEN e.quality_filtered_meter_gal
                 WHEN e.date < CURRENT_DATE THEN e.water_used_gal
            END AS water_gal_source
           FROM evidence e
        )
 SELECT date,
    round(compliance_pct::numeric, 1) AS compliance_pct,
    round(temp_compliance_pct::numeric, 1) AS temp_compliance_pct,
    round(vpd_compliance_pct::numeric, 1) AS vpd_compliance_pct,
    round(stress_hours_heat::numeric, 2) AS heat_stress_h,
    round(stress_hours_cold::numeric, 2) AS cold_stress_h,
    round(stress_hours_vpd_high::numeric, 2) AS vpd_high_stress_h,
    round(stress_hours_vpd_low::numeric, 2) AS vpd_low_stress_h,
    round((stress_hours_heat + stress_hours_cold + stress_hours_vpd_high + stress_hours_vpd_low)::numeric, 2) AS total_stress_h,
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
    -- ===== migration 204/206: ungated ESTIMATE columns (display surfaces) =====
    round(modeled_kwh::numeric, 2) AS kwh_modeled,
    round(COALESCE(therms_estimated, gas_used_therms)::numeric, 3) AS therms_modeled,
    round(water_gal_source::numeric, 0) AS water_gal_est,
    COALESCE(round((modeled_kwh * 0.111)::numeric, 2), round(cost_electric::numeric, 2)) AS cost_electric_est,
    COALESCE(round((COALESCE(therms_estimated, gas_used_therms) * 0.83)::numeric, 2), round(cost_gas::numeric, 2)) AS cost_gas_est,
    COALESCE(round((water_gal_source * 0.00484)::numeric, 2), round(cost_water::numeric, 2)) AS cost_water_est,
    CASE
        WHEN COALESCE(modeled_kwh * 0.111, cost_electric) IS NOT NULL THEN
            round((COALESCE(modeled_kwh * 0.111, cost_electric)
                 + COALESCE(COALESCE(therms_estimated, gas_used_therms) * 0.83, cost_gas, 0::double precision)
                 + COALESCE(water_gal_source * 0.00484, cost_water, 0::double precision))::numeric, 2)
        ELSE NULL::numeric
    END AS cost_total_est
   FROM normalized
  ORDER BY date;

-- Refresh atomically so version 2 cannot describe cached version-1 grades.
REFRESH MATERIALIZED VIEW public.mv_daily_kpi;

-- Match direct dashboard consumers, retaining their score/resource policy.
CREATE OR REPLACE VIEW public.v_planner_performance AS
WITH daily AS (
    SELECT
        d.*,
        COALESCE(w.available_for_scoring, false) AS water_ok,
        COALESCE(e.available_for_scoring, false) AS energy_ok
    FROM public.daily_summary d
    LEFT JOIN public.v_water_attribution_daily w
      ON w.date = d.date AND w.greenhouse_id = d.greenhouse_id
    LEFT JOIN public.v_runtime_energy_daily e
      ON e.date = d.date AND e.greenhouse_id = d.greenhouse_id
    WHERE d.date IS NOT NULL
), scored AS (
    SELECT
        d.date,
        d.stress_hours_heat AS heat_stress_h,
        d.stress_hours_cold AS cold_stress_h,
        d.stress_hours_vpd_high AS vpd_high_stress_h,
        d.stress_hours_vpd_low AS vpd_low_stress_h,
        COALESCE(d.compliance_v2_attributable_pct, d.compliance_pct, 0) AS score_compliance,
        d.compliance_pct,
        d.temp_compliance_pct AS temp_compliance_pct,
        d.vpd_compliance_pct AS vpd_compliance_pct,
        CASE WHEN d.water_ok AND d.energy_ok THEN d.cost_total END AS cost_total,
        CASE WHEN d.energy_ok THEN d.cost_electric END AS cost_electric,
        CASE
            WHEN d.water_ok AND d.energy_ok AND d.cost_total IS NOT NULL
            THEN d.cost_gas
        END AS cost_gas,
        CASE WHEN d.water_ok THEN d.cost_water END AS cost_water,
        d.compliance_pct AS compliance_binary_pct,
        d.compliance_v2_raw_pct AS compliance_raw_graded_pct,
        d.compliance_v2_unachievable_frac AS unachievable_frac,
        d.water_ok AND d.energy_ok AND d.cost_total IS NOT NULL AS resource_ok
    FROM daily d
)
SELECT
    date,
    heat_stress_h,
    cold_stress_h,
    vpd_high_stress_h,
    vpd_low_stress_h,
    heat_stress_h + cold_stress_h + vpd_high_stress_h + vpd_low_stress_h
        AS total_stress_h,
    round(compliance_pct::numeric, 1) AS compliance_pct,
    round(temp_compliance_pct::numeric, 1) AS temp_compliance_pct,
    round(vpd_compliance_pct::numeric, 1) AS vpd_compliance_pct,
    cost_total,
    cost_electric,
    cost_gas,
    cost_water,
    CASE
        WHEN heat_stress_h + cold_stress_h + vpd_high_stress_h + vpd_low_stress_h > 0
          AND cost_total IS NOT NULL
        THEN round((cost_total / (
            heat_stress_h + cold_stress_h + vpd_high_stress_h + vpd_low_stress_h
        ))::numeric, 2)
    END AS cost_per_stress_hour,
    round((CASE
        WHEN resource_ok THEN score_compliance / 100.0 * 80
          + GREATEST(0, 1.0 - LEAST(cost_total / 15.0, 1.0)) * 20
        ELSE score_compliance
    END)::numeric, 1) AS planner_score,
    round(compliance_binary_pct::numeric, 1) AS compliance_binary_pct,
    round(compliance_raw_graded_pct::numeric, 1) AS compliance_raw_graded_pct,
    round(unachievable_frac::numeric, 4) AS unachievable_frac,
    CASE WHEN resource_ok THEN 20::numeric ELSE 0::numeric END
        AS planner_score_resource_weight_pct,
    resource_ok AS resource_terms_available
FROM scored;

COMMENT ON VIEW public.v_planner_performance IS
'Contract 2: binary reading fractions and nominal stress, not graded credit. '
'planner_score remains a separate historical controller-credit diagnostic; '
'resource eligibility is unchanged. Coverage/consumed-target lineage unverified.';

-- Narrow owner-rights read facade: API keeps no direct daily_summary access.
CREATE VIEW public.v_scorecard_climate_diagnostics AS
SELECT date,
       compliance_v2_raw_pct,
       compliance_v2_attributable_pct,
       compliance_v2_unachievable_frac,
       graded_temp_compliance_pct,
       graded_vpd_compliance_pct,
       graded_stress_hours_heat,
       graded_stress_hours_cold,
       graded_stress_hours_vpd_high,
       graded_stress_hours_vpd_low
FROM public.daily_summary;
REVOKE ALL ON public.v_scorecard_climate_diagnostics FROM PUBLIC;
GRANT SELECT ON public.v_scorecard_climate_diagnostics
TO verdify_api_runtime, verdify_ingestor_runtime;

CREATE OR REPLACE FUNCTION public.fn_planner_scorecard(p_date date DEFAULT CURRENT_DATE)
RETURNS TABLE(metric text, value numeric)
LANGUAGE plpgsql
STABLE
AS $function$
BEGIN
    RETURN QUERY
    SELECT 'scorecard_contract_version'::text, 2::numeric
    UNION ALL SELECT 'planner_score'::text, k.planner_score FROM public.mv_daily_kpi k WHERE k.date = p_date
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
    UNION ALL SELECT 'compliance_v2_raw_pct', d.compliance_v2_raw_pct::numeric FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'compliance_v2_attributable_pct', d.compliance_v2_attributable_pct::numeric FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'compliance_v2_unachievable_frac', d.compliance_v2_unachievable_frac::numeric FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'graded_temp_compliance_pct', d.graded_temp_compliance_pct::numeric FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'graded_vpd_compliance_pct', d.graded_vpd_compliance_pct::numeric FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'graded_heat_stress_h', d.graded_stress_hours_heat::numeric FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'graded_cold_stress_h', d.graded_stress_hours_cold::numeric FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'graded_vpd_high_stress_h', d.graded_stress_hours_vpd_high::numeric FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'graded_vpd_low_stress_h', d.graded_stress_hours_vpd_low::numeric FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT '7d_avg_score', round(avg(k.planner_score), 1) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_compliance', round(avg(k.compliance_pct), 1) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_cost', round(avg(k.cost_total), 2) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_kwh', round(avg(k.kwh), 1) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_therms', round(avg(k.therms), 3) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_water_gal', round(avg(k.water_gal), 0) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1;
END;
$function$;

COMMENT ON FUNCTION public.fn_planner_scorecard(date) IS
'Contract 2: compliance_pct and axis fields are legacy binary reading fractions; '
'stress fields are nominal-minute binary counts. Graded credit is separately named. '
'Not fixed-panel crop time-in-band; target lineage/coverage remain unverified. '
'Resource/planner_score rules unchanged. Resource path reads mv_daily_kpi only.';
