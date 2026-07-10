-- 191-realized-solar-night-dryout-episodes.sql
--
-- Issue #410: realized, solar-phase evidence for overnight dry-out.  This is
-- deliberately an outcome surface, not a projected-intent score.  Episodes are
-- contiguous one-minute periods where measured VPD is below the actually served
-- house VPD low edge during solar night.  Historical cfg readback wins over a
-- current-anchor recomputation, with the current function only as fallback.
-- Relay truth determines admission;
-- observed climate 10-20 minutes after episode start determines response.
--
-- The function exposes blocked and incomplete episodes as first-class rows,
-- records temperature/AH constraints and stop reasons, and counts any actual
-- daytime dry-relay admission on the same local date as a gate violation.  It
-- never creates a daytime episode.  The convenience view covers the latest 30
-- nights; bounded callers should use the function directly.
--
-- Non-self-transactional: functions/view only.  Safe for an outer rollback
-- proof.  Rollback: drop v_realized_solar_night_dryout, then both functions.

CREATE OR REPLACE FUNCTION public.fn_absolute_humidity_g_m3(
    temp_f double precision,
    rh_pct double precision
)
RETURNS double precision
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT CASE
        WHEN rh_pct <= 0 OR rh_pct > 100 THEN NULL
        ELSE 216.7
             * (6.112 * exp(
                 (17.67 * ((temp_f - 32.0) / 1.8))
                 / (((temp_f - 32.0) / 1.8) + 243.5)
             ) * rh_pct / 100.0)
             / (((temp_f - 32.0) / 1.8) + 273.15)
    END;
$$;

COMMENT ON FUNCTION public.fn_absolute_humidity_g_m3(double precision, double precision) IS
'Observed absolute humidity in g/m3 from Fahrenheit and RH. Used by realized '
'solar-night dry-out evidence; returns NULL for invalid RH.';

CREATE OR REPLACE FUNCTION public.fn_realized_solar_night_dryout(
    p_start_night date,
    p_end_night date,
    p_greenhouse_id text DEFAULT 'vallery'
)
RETURNS TABLE(
    night_date date,
    episode_id bigint,
    episode_started_at timestamptz,
    episode_ended_at timestamptz,
    duration_min double precision,
    admission_status text,
    admission_reason text,
    block_reason text,
    stop_reason text,
    sample_minutes integer,
    below_served_low_minutes integer,
    action_evidence_minutes integer,
    hold_admitted_minutes integer,
    action_coverage_pct double precision,
    indoor_ah_avg_g_m3 double precision,
    outdoor_ah_avg_g_m3 double precision,
    ah_advantage_avg_g_m3 double precision,
    ah_advantage_min_g_m3 double precision,
    indoor_ah_before_g_m3 double precision,
    indoor_ah_after_10_20m_g_m3 double precision,
    observed_indoor_ah_delta_10_20m_g_m3 double precision,
    served_temp_floor_f double precision,
    min_temp_f double precision,
    vpd_before_kpa double precision,
    vpd_after_10_20m_kpa double precision,
    observed_vpd_delta_10_20m_kpa double precision,
    observed_temp_delta_10_20m_f double precision,
    vent_duty_pct double precision,
    fan_duty_pct double precision,
    heat1_duty_pct double precision,
    heat2_duty_pct double precision,
    daytime_dry_action_samples integer,
    daytime_hold_admission_samples integer,
    safety_gate_status text,
    gate_violations text[],
    evidence_status text,
    dryout_disposition text
)
LANGUAGE sql
STABLE
AS $$
WITH bounds AS (
    SELECT
        p_start_night::timestamp AT TIME ZONE 'America/Denver' AS start_ts,
        (p_end_night + 2)::timestamp AT TIME ZONE 'America/Denver' AS end_ts
),
climate_minute AS (
    SELECT
        date_trunc('minute', c.ts) AS bucket,
        avg(c.temp_avg)::double precision AS temp_f,
        avg(c.vpd_avg)::double precision AS vpd_kpa,
        avg(c.rh_avg)::double precision AS rh_pct,
        avg(c.outdoor_temp_f)::double precision AS outdoor_temp_f,
        avg(c.outdoor_rh_pct)::double precision AS outdoor_rh_pct
    FROM public.climate c
    CROSS JOIN bounds b
    WHERE c.greenhouse_id = p_greenhouse_id
      AND c.ts >= b.start_ts
      AND c.ts < b.end_ts
      AND c.temp_avg IS NOT NULL
      AND c.vpd_avg IS NOT NULL
      AND c.rh_avg IS NOT NULL
    GROUP BY date_trunc('minute', c.ts)
),
action_rows AS (
    SELECT
        date_trunc('minute', l.ts) AS bucket,
        l.climate_action,
        l.priority_axis,
        NULLIF(
            l.source_system_state -> 'climate_moisture_exchange' ->> 'action',
            ''
        ) AS mx_action,
        NULLIF(
            l.source_system_state -> 'climate_moisture_exchange' ->> 'reason',
            ''
        ) AS mx_reason,
        COALESCE((l.relay_truth ->> 'vent')::boolean, false) AS vent_on,
        COALESCE((l.relay_truth ->> 'fan1')::boolean, false)
            OR COALESCE((l.relay_truth ->> 'fan2')::boolean, false) AS fan_on,
        COALESCE((l.relay_truth ->> 'heat1')::boolean, false) AS heat1_on,
        COALESCE((l.relay_truth ->> 'heat2')::boolean, false) AS heat2_on,
        COALESCE(
            (l.source_system_state -> 'climate_moisture_exchange'
                ->> 'hold_required')::boolean,
            false
        ) OR COALESCE((
            l.source_system_state -> 'climate_moisture_exchange' ->> 'reason'
                = 'vent_plus_heat_hold'
        ), false) AS hold_flavor
    FROM public.climate_action_log l
    CROSS JOIN bounds b
    WHERE l.greenhouse_id = p_greenhouse_id
      AND l.ts >= b.start_ts
      AND l.ts < b.end_ts
),
classified_action_rows AS (
    SELECT
        a.*,
        (
            (a.priority_axis = 'vpd' OR a.climate_action = 'DEHUM_VENT')
            AND (
                (a.climate_action = 'DEHUM_VENT' AND a.vent_on AND a.fan_on)
                OR (a.mx_action = 'heat_assist' AND a.heat1_on)
                OR (
                    a.mx_reason = 'vent_plus_heat_hold'
                    AND a.vent_on AND a.fan_on AND a.heat1_on
                )
            )
        ) AS dry_action_admitted,
        (
            a.hold_flavor
            AND a.climate_action = 'DEHUM_VENT'
            AND a.vent_on
            AND a.fan_on
            AND a.heat1_on
        ) AS hold_action_admitted
    FROM action_rows a
),
action_minute AS (
    SELECT
        bucket,
        true AS action_sample_present,
        mode() WITHIN GROUP (ORDER BY climate_action) AS climate_action,
        mode() WITHIN GROUP (ORDER BY priority_axis) AS priority_axis,
        mode() WITHIN GROUP (ORDER BY mx_action) AS mx_action,
        mode() WITHIN GROUP (ORDER BY mx_reason) AS mx_reason,
        bool_or(vent_on) AS vent_on,
        bool_or(fan_on) AS fan_on,
        bool_or(heat1_on) AS heat1_on,
        bool_or(heat2_on) AS heat2_on,
        bool_or(dry_action_admitted) AS dry_action_admitted,
        bool_or(hold_action_admitted) AS hold_action_admitted,
        mode() WITHIN GROUP (
            ORDER BY COALESCE(mx_reason, mx_action, climate_action)
        ) FILTER (WHERE dry_action_admitted) AS dry_admission_reason
    FROM classified_action_rows
    GROUP BY bucket
),
setpoint_event_rows AS (
    SELECT s.greenhouse_id, s.parameter, s.ts, s.value::double precision AS value
    FROM public.setpoint_snapshot s
    CROSS JOIN bounds b
    WHERE s.greenhouse_id = p_greenhouse_id
      AND s.parameter IN ('temp_low', 'vpd_low')
      AND s.ts >= b.start_ts
      AND s.ts < b.end_ts

    UNION ALL

    (SELECT DISTINCT ON (s.greenhouse_id, s.parameter)
        s.greenhouse_id, s.parameter, s.ts, s.value::double precision
     FROM public.setpoint_snapshot s
     CROSS JOIN bounds b
     WHERE s.greenhouse_id = p_greenhouse_id
       AND s.parameter IN ('temp_low', 'vpd_low')
       AND s.ts < b.start_ts
     ORDER BY s.greenhouse_id, s.parameter, s.ts DESC)
),
setpoint_events AS (
    SELECT greenhouse_id, parameter, ts, max(value)::double precision AS value
    FROM setpoint_event_rows
    GROUP BY greenhouse_id, parameter, ts
),
setpoint_intervals AS (
    SELECT
        e.*,
        lead(e.ts, 1, b.end_ts) OVER (
            PARTITION BY e.greenhouse_id, e.parameter ORDER BY e.ts
        ) AS next_ts
    FROM setpoint_events e
    CROSS JOIN bounds b
),
resolved AS (
    SELECT
        c.*,
        public.fn_solar_phase(c.bucket) AS solar_phase,
        COALESCE(temp_readback.value, b.temp_low) AS served_temp_low,
        COALESCE(vpd_readback.value, h.house_vpd_low) AS served_vpd_low,
        public.fn_absolute_humidity_g_m3(c.temp_f, c.rh_pct) AS indoor_ah_g_m3,
        public.fn_absolute_humidity_g_m3(c.outdoor_temp_f, c.outdoor_rh_pct)
            AS outdoor_ah_g_m3,
        COALESCE(a.action_sample_present, false) AS action_sample_present,
        a.climate_action,
        a.priority_axis,
        a.mx_action,
        a.mx_reason,
        COALESCE(a.vent_on, false) AS vent_on,
        COALESCE(a.fan_on, false) AS fan_on,
        COALESCE(a.heat1_on, false) AS heat1_on,
        COALESCE(a.heat2_on, false) AS heat2_on,
        COALESCE(a.dry_action_admitted, false) AS dry_action_admitted,
        COALESCE(a.hold_action_admitted, false) AS hold_action_admitted,
        a.dry_admission_reason
    FROM climate_minute c
    CROSS JOIN LATERAL public.fn_band_setpoints(c.bucket) b
    CROSS JOIN LATERAL public.fn_house_vpd_control_band(c.bucket) h
    LEFT JOIN setpoint_intervals temp_readback
      ON temp_readback.greenhouse_id = p_greenhouse_id
     AND temp_readback.parameter = 'temp_low'
     AND c.bucket >= temp_readback.ts
     AND c.bucket < temp_readback.next_ts
    LEFT JOIN setpoint_intervals vpd_readback
      ON vpd_readback.greenhouse_id = p_greenhouse_id
     AND vpd_readback.parameter = 'vpd_low'
     AND c.bucket >= vpd_readback.ts
     AND c.bucket < vpd_readback.next_ts
    LEFT JOIN action_minute a USING (bucket)
),
classified AS (
    SELECT
        r.*,
        CASE
            WHEN (r.bucket AT TIME ZONE 'America/Denver')::time < time '12:00'
                THEN (r.bucket AT TIME ZONE 'America/Denver')::date - 1
            ELSE (r.bucket AT TIME ZONE 'America/Denver')::date
        END AS night_date,
        r.solar_phase >= 2.0 AS is_solar_night,
        r.vpd_kpa < r.served_vpd_low AS dry_demand,
        r.vpd_kpa < r.served_vpd_low OR r.dry_action_admitted AS episode_active
    FROM resolved r
),
daytime_admission AS (
    SELECT
        (bucket AT TIME ZONE 'America/Denver')::date AS local_date,
        count(*) FILTER (WHERE dry_action_admitted)::int AS dry_action_samples,
        count(*) FILTER (WHERE hold_action_admitted)::int AS hold_action_samples
    FROM action_minute
    WHERE public.fn_solar_phase(bucket) < 2.0
    GROUP BY (bucket AT TIME ZONE 'America/Denver')::date
),
night_ordered AS (
    SELECT
        c.*,
        lag(episode_active) OVER (
            PARTITION BY night_date ORDER BY bucket
        ) AS previous_episode_active,
        lag(bucket) OVER (
            PARTITION BY night_date ORDER BY bucket
        ) AS previous_bucket
    FROM classified c
    WHERE is_solar_night
      AND night_date BETWEEN p_start_night AND p_end_night
),
night_tagged AS (
    SELECT
        n.*,
        sum(CASE
            WHEN episode_active
             AND (
                 previous_episode_active IS DISTINCT FROM true
                 OR previous_bucket IS NULL
                 OR bucket - previous_bucket > interval '2 minutes'
             )
            THEN 1 ELSE 0
        END) OVER (
            PARTITION BY night_date ORDER BY bucket
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )::bigint AS episode_id
    FROM night_ordered n
),
episodes AS (
    SELECT
        night_date,
        episode_id,
        min(bucket) AS episode_started_at,
        max(bucket) + interval '1 minute' AS episode_ended_at,
        extract(epoch FROM (max(bucket) + interval '1 minute' - min(bucket))) / 60.0
            AS duration_min,
        count(*)::int AS sample_minutes,
        count(*) FILTER (WHERE dry_demand)::int AS below_served_low_minutes,
        count(*) FILTER (WHERE action_sample_present)::int AS action_evidence_minutes,
        count(*) FILTER (WHERE dry_action_admitted)::int AS admitted_minutes,
        count(*) FILTER (WHERE hold_action_admitted)::int AS hold_admitted_minutes,
        mode() WITHIN GROUP (
            ORDER BY dry_admission_reason
        ) FILTER (WHERE dry_action_admitted) AS admission_reason,
        avg(indoor_ah_g_m3)::double precision AS indoor_ah_avg_g_m3,
        avg(outdoor_ah_g_m3)::double precision AS outdoor_ah_avg_g_m3,
        avg(indoor_ah_g_m3 - outdoor_ah_g_m3)::double precision
            AS ah_advantage_avg_g_m3,
        min(indoor_ah_g_m3 - outdoor_ah_g_m3)::double precision
            AS ah_advantage_min_g_m3,
        count(outdoor_ah_g_m3)::int AS outdoor_evidence_minutes,
        min(served_temp_low)::double precision AS served_temp_floor_f,
        min(temp_f)::double precision AS min_temp_f,
        count(*) FILTER (WHERE vent_on)::int AS vent_minutes,
        count(*) FILTER (WHERE fan_on)::int AS fan_minutes,
        count(*) FILTER (WHERE heat1_on)::int AS heat1_minutes,
        count(*) FILTER (WHERE heat2_on)::int AS heat2_minutes
    FROM night_tagged
    WHERE episode_active
    GROUP BY night_date, episode_id
),
with_response AS (
    SELECT
        e.*,
        before.vpd_kpa AS vpd_before_kpa,
        before.temp_f AS temp_before_f,
        before.samples AS before_samples,
        before.indoor_ah_g_m3 AS indoor_ah_before_g_m3,
        before.ah_samples AS before_ah_samples,
        after.vpd_kpa AS vpd_after_kpa,
        after.temp_f AS temp_after_f,
        after.samples AS after_samples,
        after.indoor_ah_g_m3 AS indoor_ah_after_g_m3,
        after.ah_samples AS after_ah_samples,
        next_sample.solar_phase AS next_solar_phase,
        next_sample.vpd_kpa AS next_vpd_kpa,
        next_sample.served_vpd_low AS next_served_vpd_low,
        next_sample.temp_f AS next_temp_f,
        next_sample.served_temp_low AS next_served_temp_low,
        next_sample.indoor_ah_g_m3 - next_sample.outdoor_ah_g_m3
            AS next_ah_advantage_g_m3,
        next_sample.bucket AS next_bucket,
        COALESCE(d.dry_action_samples, 0)::int AS daytime_dry_action_samples,
        COALESCE(d.hold_action_samples, 0)::int
            AS daytime_hold_admission_samples
    FROM episodes e
    LEFT JOIN LATERAL (
        SELECT
            avg(c.vpd_kpa)::double precision AS vpd_kpa,
            avg(c.temp_f)::double precision AS temp_f,
            count(*)::int AS samples,
            avg(c.indoor_ah_g_m3)::double precision AS indoor_ah_g_m3,
            count(c.indoor_ah_g_m3)::int AS ah_samples
        FROM resolved c
        -- Outcome baseline is the first five measured minutes of the demand
        -- episode, not the preceding in-band period.  Compare that realized
        -- onset with the observed 10-20 minute window.
        WHERE c.bucket >= e.episode_started_at
          AND c.bucket < e.episode_started_at + interval '5 minutes'
    ) before ON true
    LEFT JOIN LATERAL (
        SELECT
            avg(c.vpd_kpa)::double precision AS vpd_kpa,
            avg(c.temp_f)::double precision AS temp_f,
            count(*)::int AS samples,
            avg(c.indoor_ah_g_m3)::double precision AS indoor_ah_g_m3,
            count(c.indoor_ah_g_m3)::int AS ah_samples
        FROM resolved c
        WHERE c.bucket >= e.episode_started_at + interval '10 minutes'
          AND c.bucket < e.episode_started_at + interval '20 minutes'
    ) after ON true
    LEFT JOIN LATERAL (
        SELECT s.*
        FROM resolved s
        WHERE s.bucket >= e.episode_ended_at
        ORDER BY s.bucket
        LIMIT 1
    ) next_sample ON true
    LEFT JOIN LATERAL (
        -- A night owns both adjacent daylight windows: the local-date hours
        -- before sunset and the following local-date hours after sunrise.
        SELECT
            COALESCE(sum(dry_action_samples), 0)::int AS dry_action_samples,
            COALESCE(sum(hold_action_samples), 0)::int AS hold_action_samples
        FROM daytime_admission
        WHERE local_date IN (e.night_date, e.night_date + 1)
    ) d ON true
)
SELECT
    r.night_date,
    r.episode_id,
    r.episode_started_at,
    r.episode_ended_at,
    round(r.duration_min::numeric, 1)::double precision AS duration_min,
    CASE WHEN r.admitted_minutes > 0 THEN 'admitted' ELSE 'blocked' END
        AS admission_status,
    r.admission_reason,
    CASE
        WHEN r.admitted_minutes > 0 THEN NULL
        WHEN r.action_evidence_minutes < greatest(1, r.sample_minutes / 2)
            THEN 'action_evidence_missing'
        WHEN r.outdoor_evidence_minutes < greatest(1, r.sample_minutes / 2)
            THEN 'outdoor_evidence_missing'
        WHEN r.ah_advantage_avg_g_m3 <= 0 THEN 'no_outdoor_ah_advantage'
        WHEN r.min_temp_f <= r.served_temp_floor_f THEN 'temperature_floor'
        ELSE 'controller_no_admission'
    END AS block_reason,
    CASE
        WHEN r.next_bucket IS NULL THEN 'telemetry_incomplete'
        WHEN r.next_bucket - r.episode_ended_at > interval '2 minutes'
            THEN 'telemetry_gap'
        WHEN r.next_solar_phase < 2.0 THEN 'sunrise'
        WHEN r.below_served_low_minutes > 0
          AND r.next_vpd_kpa >= r.next_served_vpd_low THEN 'vpd_recovered'
        WHEN r.next_temp_f <= r.next_served_temp_low THEN 'temperature_floor'
        WHEN r.next_ah_advantage_g_m3 <= 0 THEN 'outdoor_advantage_lost'
        ELSE 'controller_stopped'
    END AS stop_reason,
    r.sample_minutes,
    r.below_served_low_minutes,
    r.action_evidence_minutes,
    r.hold_admitted_minutes,
    round((100.0 * r.action_evidence_minutes / NULLIF(r.sample_minutes, 0))::numeric, 1)::double precision
        AS action_coverage_pct,
    round(r.indoor_ah_avg_g_m3::numeric, 2)::double precision,
    round(r.outdoor_ah_avg_g_m3::numeric, 2)::double precision,
    round(r.ah_advantage_avg_g_m3::numeric, 2)::double precision,
    round(r.ah_advantage_min_g_m3::numeric, 2)::double precision,
    round(r.indoor_ah_before_g_m3::numeric, 2)::double precision,
    round(r.indoor_ah_after_g_m3::numeric, 2)::double precision,
    round((r.indoor_ah_after_g_m3 - r.indoor_ah_before_g_m3)::numeric, 2)
        ::double precision,
    round(r.served_temp_floor_f::numeric, 2)::double precision,
    round(r.min_temp_f::numeric, 2)::double precision,
    round(r.vpd_before_kpa::numeric, 3)::double precision,
    round(r.vpd_after_kpa::numeric, 3)::double precision,
    round((r.vpd_after_kpa - r.vpd_before_kpa)::numeric, 3)::double precision,
    round((r.temp_after_f - r.temp_before_f)::numeric, 2)::double precision,
    round((100.0 * r.vent_minutes / NULLIF(r.sample_minutes, 0))::numeric, 1)::double precision,
    round((100.0 * r.fan_minutes / NULLIF(r.sample_minutes, 0))::numeric, 1)::double precision,
    round((100.0 * r.heat1_minutes / NULLIF(r.sample_minutes, 0))::numeric, 1)::double precision,
    round((100.0 * r.heat2_minutes / NULLIF(r.sample_minutes, 0))::numeric, 1)::double precision,
    r.daytime_dry_action_samples,
    r.daytime_hold_admission_samples,
    CASE
        WHEN r.daytime_hold_admission_samples > 0 OR r.heat2_minutes > 0
            THEN 'fail'
        ELSE 'pass'
    END AS safety_gate_status,
    array_remove(ARRAY[
        CASE WHEN r.daytime_hold_admission_samples > 0
            THEN 'daytime_hold_admission' END,
        CASE WHEN r.heat2_minutes > 0 THEN 'heat2_forbidden' END
    ]::text[], NULL) AS gate_violations,
    CASE
        WHEN r.daytime_hold_admission_samples > 0 OR r.heat2_minutes > 0
            THEN 'gate_failed'
        WHEN r.action_evidence_minutes < greatest(1, r.sample_minutes * 4 / 5)
          OR r.outdoor_evidence_minutes < greatest(1, r.sample_minutes / 2)
          OR r.before_samples < 3 OR r.after_samples < 5
          OR r.before_ah_samples < 3 OR r.after_ah_samples < 5
            THEN 'incomplete'
        ELSE 'complete'
    END AS evidence_status,
    CASE
        WHEN r.daytime_hold_admission_samples > 0 OR r.heat2_minutes > 0
            THEN 'ineffective'
        WHEN r.action_evidence_minutes < greatest(1, r.sample_minutes * 4 / 5)
          OR r.outdoor_evidence_minutes < greatest(1, r.sample_minutes / 2)
          OR r.before_samples < 3 OR r.after_samples < 5
          OR r.before_ah_samples < 3 OR r.after_ah_samples < 5
            THEN 'insufficient_evidence'
        WHEN r.admitted_minutes = 0 THEN 'blocked'
        WHEN r.vpd_after_kpa - r.vpd_before_kpa >= 0.05
         AND r.indoor_ah_after_g_m3 - r.indoor_ah_before_g_m3 <= -0.05
            THEN 'effective'
        ELSE 'ineffective'
    END AS dryout_disposition
FROM with_response r
ORDER BY r.night_date, r.episode_started_at;
$$;

COMMENT ON FUNCTION public.fn_realized_solar_night_dryout(date, date, text) IS
'Realized solar-night VPD-low opportunities or actual dry-action episodes from '
'measured climate, historical served cfg readback (with current-function '
'fallback), and actual relay '
'truth. Exposes admission/block/stop reasons, absolute-humidity advantage, '
'temperature floor, actuator duty, observed 10-20 minute VPD/temperature/indoor-'
'AH response, and an '
'explicit effective|ineffective|blocked|insufficient_evidence disposition. '
'The held-temp flavor is attributed separately: an actual daytime held-temp '
'admission or any heat2 duty fails the safety gate and is ineffective. General '
'daytime VPD dehumidification remains visible but is not mislabeled as the '
'solar-night held-temp violation. Never labels projected estimator intent as '
'outcome and never emits a daytime episode.';

CREATE OR REPLACE VIEW public.v_realized_solar_night_dryout AS
SELECT *
FROM public.fn_realized_solar_night_dryout(
    (now() AT TIME ZONE 'America/Denver')::date - 30,
    (now() AT TIME ZONE 'America/Denver')::date,
    'vallery'
);

COMMENT ON VIEW public.v_realized_solar_night_dryout IS
'Latest 30-night realized dry-out evidence. Bounded analytics should call '
'fn_realized_solar_night_dryout(start_night,end_night,greenhouse_id).';
