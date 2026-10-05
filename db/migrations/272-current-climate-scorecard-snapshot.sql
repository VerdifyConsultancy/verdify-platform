-- #371 bounded current climate snapshot; resource cache policy unchanged.
-- Qualified in a rollback-only production catalog rehearsal; no rows rewritten.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations, public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $preflight$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=270 AND sha256='672d1afa92e37f243d5893fbd57cd2b84e86ea19c1c79d3975c04dfd39663532' AND stamp_method='runner')
 OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=272)
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'ad619765f93959500d7ed438f90000ceaf614b2f744553d4a4e269a7b15103d3'
 OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('ad619765f93959500d7ed438f90000ceaf614b2f744553d4a4e269a7b15103d3','hex'))
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '8bf588e5381e236f68aabc6672f61d982321a444f418973089d6a10a5a1efcf6'
 OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('8bf588e5381e236f68aabc6672f61d982321a444f418973089d6a10a5a1efcf6','hex'))
 OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '81836c70a76578da82b77899da5d1cafee4597ea819e35ee68a3fd6cf669fa45'
 OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('81836c70a76578da82b77899da5d1cafee4597ea819e35ee68a3fd6cf669fa45','hex'))
 THEN RAISE EXCEPTION '272 refuses unqualified predecessor'; END IF; END $preflight$;
-- #371 binary and graded climate metrics share one current row/snapshot.
-- Preserve original facade columns, OIDs, ownership and ACLs. No data rewrite.
CREATE OR REPLACE VIEW public.v_scorecard_climate_diagnostics AS
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
,
    compliance_pct, temp_compliance_pct, vpd_compliance_pct,
    stress_hours_heat, stress_hours_cold, stress_hours_vpd_high, stress_hours_vpd_low
   FROM public.daily_summary;
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
    UNION ALL SELECT 'compliance_pct', round((d.compliance_pct)::numeric, 1) FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'temp_compliance_pct', round((d.temp_compliance_pct)::numeric, 1) FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'vpd_compliance_pct', round((d.vpd_compliance_pct)::numeric, 1) FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'total_stress_h', round((d.stress_hours_heat + d.stress_hours_cold + d.stress_hours_vpd_high + d.stress_hours_vpd_low)::numeric, 2) FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'heat_stress_h', round((d.stress_hours_heat)::numeric, 2) FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'cold_stress_h', round((d.stress_hours_cold)::numeric, 2) FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'vpd_high_stress_h', round((d.stress_hours_vpd_high)::numeric, 2) FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
    UNION ALL SELECT 'vpd_low_stress_h', round((d.stress_hours_vpd_low)::numeric, 2) FROM public.v_scorecard_climate_diagnostics d WHERE d.date = p_date
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
    UNION ALL SELECT '7d_avg_compliance', round(avg(d.compliance_pct)::numeric, 1) FROM public.v_scorecard_climate_diagnostics d WHERE d.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_cost', round(avg(k.cost_total), 2) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_kwh', round(avg(k.kwh), 1) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_therms', round(avg(k.therms), 3) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_water_gal', round(avg(k.water_gal), 0) FROM public.mv_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1;
END;
$function$;
DO $postflight$ BEGIN
 IF false
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '751699a763970db4179a7f001f697620e649efb09302b911d25650743850badc'
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'a25d6b81b70be3b7b61a9a2e3725451adb59ecb409850f209867180cc7dc892f'
 OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM 'fb8c7119f87cd66451805470c018566759962949e653554cc4c0c1a063e4787a'
 THEN RAISE EXCEPTION '272 refuses unqualified successor'; END IF;
 UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('751699a763970db4179a7f001f697620e649efb09302b911d25650743850badc','hex'),captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
 UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('a25d6b81b70be3b7b61a9a2e3725451adb59ecb409850f209867180cc7dc892f','hex'),captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
 UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('fb8c7119f87cd66451805470c018566759962949e653554cc4c0c1a063e4787a','hex') WHERE singleton;
END $postflight$;
