-- Persist effective controller diagnostic readbacks and keep the tactical
-- outcome mart aligned with the current Tier 1 planner surface.

ALTER TABLE public.diagnostics
    ADD COLUMN IF NOT EXISTS effective_heat_target_f double precision,
    ADD COLUMN IF NOT EXISTS effective_cool_stage2_delta_f double precision,
    ADD COLUMN IF NOT EXISTS effective_vpd_hysteresis_kpa double precision,
    ADD COLUMN IF NOT EXISTS effective_dehum_aggressive_kpa double precision;

COMMENT ON COLUMN public.diagnostics.effective_heat_target_f IS
    'Controller diagnostic: validated effective heat target in deg F after crop-band width clamp and lower-quartile policy.';
COMMENT ON COLUMN public.diagnostics.effective_cool_stage2_delta_f IS
    'Controller diagnostic: validated effective stage-2 cooling delta in deg F after band/safety clamps.';
COMMENT ON COLUMN public.diagnostics.effective_vpd_hysteresis_kpa IS
    'Controller diagnostic: validated effective VPD hysteresis in kPa after house-band width clamps.';
COMMENT ON COLUMN public.diagnostics.effective_dehum_aggressive_kpa IS
    'Controller diagnostic: validated dehumidification aggressive margin in kPa after house-band clamps.';

CREATE OR REPLACE VIEW public.v_plan_tactical_outcome_daily AS
 SELECT sp.plan_id,
    ((min(sp.created_at) AT TIME ZONE 'America/Denver'::text))::date AS plan_date,
    sp.parameter,
    count(*) AS waypoints,
    round((avg(sp.value))::numeric, 3) AS avg_value,
    round((min(sp.value))::numeric, 3) AS min_value,
    round((max(sp.value))::numeric, 3) AS max_value,
    ds.compliance_pct,
    ds.temp_compliance_pct,
    ds.vpd_compliance_pct,
    ds.stress_hours_heat,
    ds.stress_hours_vpd_high,
    ds.stress_hours_cold,
    ds.stress_hours_vpd_low,
    ds.water_used_gal,
    ds.mister_water_gal,
    ds.cost_total
   FROM (public.setpoint_plan sp
     LEFT JOIN public.daily_summary ds ON ((ds.date = ((sp.ts AT TIME ZONE 'America/Denver'::text))::date)))
  WHERE (sp.parameter = ANY (ARRAY[
    'd_cool_stage_2'::text,
    'dwell_gate_ms'::text,
    'enthalpy_close'::text,
    'enthalpy_open'::text,
    'fog_escalation_kpa'::text,
    'heat_hysteresis'::text,
    'min_fog_off_s'::text,
    'min_fog_on_s'::text,
    'mist_backoff_s'::text,
    'mist_max_closed_vent_s'::text,
    'mist_thermal_relief_s'::text,
    'mister_all_delay_s'::text,
    'mister_all_kpa'::text,
    'mister_engage_delay_s'::text,
    'mister_engage_kpa'::text,
    'mister_pulse_gap_s'::text,
    'mister_pulse_on_s'::text,
    'mister_vpd_weight'::text,
    'mister_water_budget_gal'::text,
    'outdoor_staleness_max_s'::text,
    'sw_dwell_gate_enabled'::text,
    'sw_fog_closes_vent'::text,
    'sw_mister_closes_vent'::text,
    'sw_summer_vent_enabled'::text,
    'temp_hysteresis'::text,
    'vent_prefer_dp_delta_f'::text,
    'vent_prefer_temp_delta_f'::text,
    'vpd_hysteresis'::text,
    'vpd_watch_dwell_s'::text
  ]))
  GROUP BY sp.plan_id, sp.parameter, ds.date, ds.compliance_pct, ds.temp_compliance_pct, ds.vpd_compliance_pct, ds.stress_hours_heat, ds.stress_hours_vpd_high, ds.stress_hours_cold, ds.stress_hours_vpd_low, ds.water_used_gal, ds.mister_water_gal, ds.cost_total;

ALTER VIEW public.v_plan_tactical_outcome_daily OWNER TO verdify;

COMMENT ON VIEW public.v_plan_tactical_outcome_daily IS
    'Planner Tier 1 tactical parameter posture joined to same-day compliance, stress, water, and cost outcomes. Directional, not causal.';
