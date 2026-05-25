-- Structured climate controller action log and effectiveness views.
--
-- This closes the Climate Authority data gap: controller decisions must be
-- graphable without reconstructing "latest" state from system_state key/value
-- rows or scanning v_greenhouse_state for live planner context.

CREATE TABLE IF NOT EXISTS public.climate_action_log (
    ts timestamptz NOT NULL,
    greenhouse_id text DEFAULT 'vallery' REFERENCES public.greenhouses(id),
    climate_action text NOT NULL,
    priority_axis text NOT NULL,
    temp_low_f double precision,
    temp_target_f double precision,
    temp_high_f double precision,
    vpd_low_kpa double precision,
    vpd_target_kpa double precision,
    vpd_high_kpa double precision,
    temp_target_delta_f double precision,
    vpd_target_delta_kpa double precision,
    temp_band_error_f double precision,
    vpd_band_error_kpa double precision,
    moisture_assist_state text,
    moisture_zone text DEFAULT 'none',
    wet_assist_allowed boolean DEFAULT false,
    wet_assist_block_reason text,
    fog_allowed boolean DEFAULT false,
    fog_block_reason text,
    relay_truth jsonb DEFAULT '{}'::jsonb NOT NULL,
    resource_cost_estimate jsonb DEFAULT '{}'::jsonb NOT NULL,
    climate_intent_version text,
    plan_id text,
    trigger_id uuid,
    planner_instance text,
    sensor_status jsonb DEFAULT '{}'::jsonb NOT NULL,
    candidate_summary text,
    source_system_state jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT climate_action_log_action_check CHECK (
        climate_action = ANY (ARRAY[
            'SENSOR_FAULT',
            'SAFETY_HEAT',
            'SAFETY_COOL',
            'HEAT',
            'IDLE',
            'VENT_COOL',
            'VENT_COOL_MIST_ASSIST',
            'VENT_COOL_FOG_ASSIST',
            'SEALED_HUMIDIFY',
            'SEALED_FOG',
            'DEHUM_VENT'
        ])
    ),
    CONSTRAINT climate_action_log_priority_check CHECK (
        priority_axis = ANY (ARRAY['safety', 'temp', 'vpd', 'resource'])
    )
);

SELECT create_hypertable('public.climate_action_log', 'ts', if_not_exists => true);

CREATE INDEX IF NOT EXISTS idx_climate_action_log_ts
    ON public.climate_action_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_climate_action_log_action_ts
    ON public.climate_action_log (climate_action, ts DESC);
CREATE INDEX IF NOT EXISTS idx_climate_action_log_greenhouse_ts
    ON public.climate_action_log (greenhouse_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_climate_action_log_plan
    ON public.climate_action_log (plan_id, ts DESC)
    WHERE plan_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_climate_action_log_trigger
    ON public.climate_action_log (trigger_id)
    WHERE trigger_id IS NOT NULL;

COMMENT ON TABLE public.climate_action_log IS
    'Durable ESP32 ClimateIntent controller decision snapshots. One row captures selected action, priority, target deltas, wet/fog authority, relay truth, and plan correlation for graphing and post-hoc validation.';
COMMENT ON COLUMN public.climate_action_log.climate_action IS
    'Executed controller action after firmware safety, dwell, and interlock resolution.';
COMMENT ON COLUMN public.climate_action_log.wet_assist_allowed IS
    'True when the selected climate action had a permitted climate wet-assist path; false when inactive or blocked.';
COMMENT ON COLUMN public.climate_action_log.wet_assist_block_reason IS
    'Named reason climate wet assist could not physically serve, e.g. dew_margin, occupancy, irrigation, water_budget, wet_cutoff, or direct_wet_window before firmware issue #5 lands.';
COMMENT ON COLUMN public.climate_action_log.relay_truth IS
    'Latest ingestor relay truth snapshot for climate relays at action-log write time.';

CREATE OR REPLACE FUNCTION public.fn_climate_action_effectiveness(p_window interval)
RETURNS TABLE (
    action_ts timestamptz,
    greenhouse_id text,
    climate_action text,
    priority_axis text,
    plan_id text,
    trigger_id uuid,
    planner_instance text,
    temp_band_error_before_f double precision,
    vpd_band_error_before_kpa double precision,
    temp_band_error_after_f double precision,
    vpd_band_error_after_kpa double precision,
    temp_abs_error_delta_f double precision,
    vpd_abs_error_delta_kpa double precision,
    recovered_within_window boolean,
    time_to_recover_s double precision,
    wet_relay_duty_pct double precision,
    vent_fan_duty_pct double precision,
    fog_duty_pct double precision,
    mister_water_delta_gal double precision,
    outdoor_temp_f double precision,
    outdoor_dewpoint_f double precision,
    solar_irradiance_w_m2 double precision,
    wet_assist_allowed boolean,
    wet_assist_block_reason text,
    fog_allowed boolean,
    fog_block_reason text,
    relay_truth jsonb,
    resource_cost_estimate jsonb
)
LANGUAGE sql
STABLE
AS $$
WITH base AS (
    SELECT
        l.*,
        c0.ts AS before_climate_ts,
        c0.temp_avg AS before_temp_f,
        c0.vpd_avg AS before_vpd_kpa,
        c0.mister_water_today AS before_mister_water_gal,
        c0.outdoor_temp_f,
        c0.outdoor_rh_pct,
        c0.dew_point AS before_dew_point_f,
        c0.solar_irradiance_w_m2,
        c1.ts AS after_climate_ts,
        c1.temp_avg AS after_temp_f,
        c1.vpd_avg AS after_vpd_kpa,
        c1.mister_water_today AS after_mister_water_gal,
        fn_setpoint_at(l.greenhouse_id, 'temp_low', COALESCE(c1.ts, l.ts)) AS after_temp_low,
        fn_setpoint_at(l.greenhouse_id, 'temp_high', COALESCE(c1.ts, l.ts)) AS after_temp_high,
        fn_setpoint_at(l.greenhouse_id, 'vpd_low', COALESCE(c1.ts, l.ts)) AS after_vpd_low,
        fn_setpoint_at(l.greenhouse_id, 'vpd_high', COALESCE(c1.ts, l.ts)) AS after_vpd_high
    FROM public.climate_action_log l
    LEFT JOIN LATERAL (
        SELECT c.*
        FROM public.climate c
        WHERE COALESCE(c.greenhouse_id, 'vallery') = COALESCE(l.greenhouse_id, 'vallery')
          AND c.temp_avg IS NOT NULL
          AND c.vpd_avg IS NOT NULL
          AND c.ts <= l.ts
        ORDER BY c.ts DESC
        LIMIT 1
    ) c0 ON true
    LEFT JOIN LATERAL (
        SELECT c.*
        FROM public.climate c
        WHERE COALESCE(c.greenhouse_id, 'vallery') = COALESCE(l.greenhouse_id, 'vallery')
          AND c.temp_avg IS NOT NULL
          AND c.vpd_avg IS NOT NULL
          AND c.ts >= l.ts + p_window
          AND c.ts <= l.ts + p_window + interval '3 minutes'
        ORDER BY c.ts ASC
        LIMIT 1
    ) c1 ON true
    WHERE l.ts >= now() - interval '14 days'
),
scored AS (
    SELECT
        b.*,
        CASE
            WHEN b.after_temp_f IS NULL OR b.after_temp_low IS NULL OR b.after_temp_high IS NULL THEN NULL
            WHEN b.after_temp_f < b.after_temp_low THEN b.after_temp_f - b.after_temp_low
            WHEN b.after_temp_f > b.after_temp_high THEN b.after_temp_f - b.after_temp_high
            ELSE 0.0
        END AS after_temp_band_error,
        CASE
            WHEN b.after_vpd_kpa IS NULL OR b.after_vpd_low IS NULL OR b.after_vpd_high IS NULL THEN NULL
            WHEN b.after_vpd_kpa < b.after_vpd_low THEN b.after_vpd_kpa - b.after_vpd_low
            WHEN b.after_vpd_kpa > b.after_vpd_high THEN b.after_vpd_kpa - b.after_vpd_high
            ELSE 0.0
        END AS after_vpd_band_error
    FROM base b
)
SELECT
    s.ts AS action_ts,
    s.greenhouse_id,
    s.climate_action,
    s.priority_axis,
    s.plan_id,
    s.trigger_id,
    s.planner_instance,
    s.temp_band_error_f AS temp_band_error_before_f,
    s.vpd_band_error_kpa AS vpd_band_error_before_kpa,
    s.after_temp_band_error AS temp_band_error_after_f,
    s.after_vpd_band_error AS vpd_band_error_after_kpa,
    CASE
        WHEN s.after_temp_band_error IS NULL OR s.temp_band_error_f IS NULL THEN NULL
        ELSE abs(s.after_temp_band_error) - abs(s.temp_band_error_f)
    END AS temp_abs_error_delta_f,
    CASE
        WHEN s.after_vpd_band_error IS NULL OR s.vpd_band_error_kpa IS NULL THEN NULL
        ELSE abs(s.after_vpd_band_error) - abs(s.vpd_band_error_kpa)
    END AS vpd_abs_error_delta_kpa,
    recovery.time_to_recover_s IS NOT NULL AS recovered_within_window,
    recovery.time_to_recover_s,
    duty.wet_relay_duty_pct,
    duty.vent_fan_duty_pct,
    duty.fog_duty_pct,
    CASE
        WHEN s.before_mister_water_gal IS NULL OR s.after_mister_water_gal IS NULL THEN NULL
        ELSE greatest(0.0, s.after_mister_water_gal - s.before_mister_water_gal)
    END AS mister_water_delta_gal,
    s.outdoor_temp_f,
    CASE
        WHEN s.outdoor_temp_f IS NULL OR s.outdoor_rh_pct IS NULL OR s.outdoor_rh_pct <= 0 THEN NULL
        ELSE (
            243.04 * (
                ln(s.outdoor_rh_pct / 100.0)
                + ((17.625 * ((s.outdoor_temp_f - 32.0) * 5.0 / 9.0))
                   / (243.04 + ((s.outdoor_temp_f - 32.0) * 5.0 / 9.0)))
            )
            / (
                17.625
                - ln(s.outdoor_rh_pct / 100.0)
                - ((17.625 * ((s.outdoor_temp_f - 32.0) * 5.0 / 9.0))
                   / (243.04 + ((s.outdoor_temp_f - 32.0) * 5.0 / 9.0)))
            )
        ) * 9.0 / 5.0 + 32.0
    END AS outdoor_dewpoint_f,
    s.solar_irradiance_w_m2,
    s.wet_assist_allowed,
    s.wet_assist_block_reason,
    s.fog_allowed,
    s.fog_block_reason,
    s.relay_truth,
    s.resource_cost_estimate
FROM scored s
LEFT JOIN LATERAL (
    SELECT extract(epoch FROM min(c.ts - s.ts))::double precision AS time_to_recover_s
    FROM public.climate c
    WHERE COALESCE(c.greenhouse_id, 'vallery') = COALESCE(s.greenhouse_id, 'vallery')
      AND c.ts >= s.ts
      AND c.ts <= s.ts + p_window
      AND c.temp_avg BETWEEN COALESCE(fn_setpoint_at(s.greenhouse_id, 'temp_low', c.ts), -1000)
                         AND COALESCE(fn_setpoint_at(s.greenhouse_id, 'temp_high', c.ts), 1000)
      AND c.vpd_avg BETWEEN COALESCE(fn_setpoint_at(s.greenhouse_id, 'vpd_low', c.ts), -1000)
                        AND COALESCE(fn_setpoint_at(s.greenhouse_id, 'vpd_high', c.ts), 1000)
) recovery ON true
LEFT JOIN LATERAL (
    SELECT
        round((avg((
            COALESCE(fn_equip_at('fog', sample_ts), false)
            OR COALESCE(fn_equip_at('mister_south', sample_ts), false)
            OR COALESCE(fn_equip_at('mister_west', sample_ts), false)
            OR COALESCE(fn_equip_at('mister_center', sample_ts), false)
        )::int) * 100.0)::numeric, 2)::double precision AS wet_relay_duty_pct,
        round((avg((
            COALESCE(fn_equip_at('vent', sample_ts), false)
            OR COALESCE(fn_equip_at('fan1', sample_ts), false)
            OR COALESCE(fn_equip_at('fan2', sample_ts), false)
        )::int) * 100.0)::numeric, 2)::double precision AS vent_fan_duty_pct,
        round((avg(COALESCE(fn_equip_at('fog', sample_ts), false)::int) * 100.0)::numeric, 2)::double precision
            AS fog_duty_pct
    FROM generate_series(s.ts, s.ts + p_window, interval '1 minute') AS sample(sample_ts)
) duty ON true;
$$;

COMMENT ON FUNCTION public.fn_climate_action_effectiveness(interval) IS
    'Returns per-action before/after temp and VPD error plus relay duty estimates for a requested analysis window.';

CREATE OR REPLACE VIEW public.v_climate_action_effectiveness_5m AS
    SELECT * FROM public.fn_climate_action_effectiveness(interval '5 minutes');

CREATE OR REPLACE VIEW public.v_climate_action_effectiveness_15m AS
    SELECT * FROM public.fn_climate_action_effectiveness(interval '15 minutes');

CREATE OR REPLACE VIEW public.v_climate_action_daily_scorecard AS
SELECT
    (action_ts AT TIME ZONE 'America/Denver')::date AS date,
    greenhouse_id,
    climate_action,
    count(*) AS decisions,
    round(avg(abs(temp_band_error_before_f))::numeric, 2) AS avg_abs_temp_error_before_f,
    round(avg(abs(vpd_band_error_before_kpa))::numeric, 3) AS avg_abs_vpd_error_before_kpa,
    round(avg(temp_abs_error_delta_f)::numeric, 2) AS avg_temp_abs_error_delta_15m_f,
    round(avg(vpd_abs_error_delta_kpa)::numeric, 3) AS avg_vpd_abs_error_delta_15m_kpa,
    round(avg(wet_relay_duty_pct)::numeric, 2) AS avg_wet_relay_duty_pct,
    round(avg(vent_fan_duty_pct)::numeric, 2) AS avg_vent_fan_duty_pct,
    round(sum(coalesce(mister_water_delta_gal, 0.0))::numeric, 3) AS mister_water_delta_gal,
    count(*) FILTER (WHERE wet_assist_block_reason IS NOT NULL) AS wet_blocked_decisions,
    count(*) FILTER (WHERE fog_block_reason IS NOT NULL AND fog_block_reason <> 'none') AS fog_blocked_decisions
FROM public.v_climate_action_effectiveness_15m
GROUP BY 1, 2, 3;

COMMENT ON VIEW public.v_climate_action_effectiveness_5m IS
    'Five-minute climate action effectiveness: before/after band error, recovery, relay duty, and weather context.';
COMMENT ON VIEW public.v_climate_action_effectiveness_15m IS
    'Fifteen-minute climate action effectiveness: before/after band error, recovery, relay duty, and weather context.';
COMMENT ON VIEW public.v_climate_action_daily_scorecard IS
    'Daily climate-action scorecard by action, showing compliance-error movement, relay duty, water, and block counts.';
