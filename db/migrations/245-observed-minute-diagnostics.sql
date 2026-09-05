-- #371 additive diagnostics; existing binary/graded/experiment formulas unchanged.
-- Requires revision capture 244. Capture layout v2 is not measurement eligibility.
ALTER TABLE public.daily_summary ADD COLUMN climate_observed_minute_metrics jsonb;
ALTER TABLE public.daily_summary ADD CONSTRAINT daily_observed_minute_diagnostic_only CHECK (
    climate_observed_minute_metrics IS NULL OR COALESCE(
        jsonb_typeof(climate_observed_minute_metrics) = 'object'
        AND climate_observed_minute_metrics->>'definition' = 'house-average-observed-minute-v1'
        AND climate_observed_minute_metrics->'fixed_sensor_panel' = 'false'::jsonb
        AND climate_observed_minute_metrics->'duration_weighted' = 'false'::jsonb
        AND climate_observed_minute_metrics->'physical_proof_eligible' = 'false'::jsonb
        AND climate_observed_minute_metrics->'crop_outcome_eligible' = 'false'::jsonb
        AND climate_observed_minute_metrics->'experiment_endpoint_eligible' = 'false'::jsonb,
        false)
);
COMMENT ON COLUMN public.daily_summary.climate_observed_minute_metrics IS
'Observed UTC-minute diagnostics against setpoint-log events, not physical duration, '
'fixed-panel crop compliance or a trial endpoint. Input hash binds scoped samples '
'and target events. Missing eligible axes remain null. Existing legacy fields are unchanged.';

ALTER TABLE public.daily_climate_metric_revisions
    DROP CONSTRAINT daily_climate_metric_revisions_capture_schema_check;
ALTER TABLE public.daily_climate_metric_revisions
    ADD CONSTRAINT daily_climate_metric_revisions_capture_schema_check
    CHECK (capture_schema IN ('daily-summary-capture-v1', 'daily-summary-capture-v2'));
ALTER TABLE public.daily_climate_metric_revisions ALTER COLUMN capture_schema
    SET DEFAULT 'daily-summary-capture-v2';

-- Replace in place: keep the function OID, owner and ACLs from 244. No rename
-- that could leave existing trigger plans bound to an obsolete payload function.
CREATE OR REPLACE FUNCTION public.fn_daily_climate_metric_payload(d public.daily_summary)
RETURNS jsonb LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, public, pg_temp
AS $function$
    SELECT jsonb_build_object(
        'binary', jsonb_build_object(
            'compliance_pct', d.compliance_pct,
            'temp_compliance_pct', d.temp_compliance_pct,
            'vpd_compliance_pct', d.vpd_compliance_pct,
            'stress_hours_heat', d.stress_hours_heat,
            'stress_hours_cold', d.stress_hours_cold,
            'stress_hours_vpd_high', d.stress_hours_vpd_high,
            'stress_hours_vpd_low', d.stress_hours_vpd_low),
        'graded', jsonb_build_object(
            'compliance_v2_raw_pct', d.compliance_v2_raw_pct,
            'compliance_v2_attributable_pct', d.compliance_v2_attributable_pct,
            'compliance_v2_unachievable_frac', d.compliance_v2_unachievable_frac,
            'graded_temp_compliance_pct', d.graded_temp_compliance_pct,
            'graded_vpd_compliance_pct', d.graded_vpd_compliance_pct,
            'graded_stress_hours_heat', d.graded_stress_hours_heat,
            'graded_stress_hours_cold', d.graded_stress_hours_cold,
            'graded_stress_hours_vpd_high', d.graded_stress_hours_vpd_high,
            'graded_stress_hours_vpd_low', d.graded_stress_hours_vpd_low),
        'observed_minute_diagnostic', d.climate_observed_minute_metrics
    );
$function$;
