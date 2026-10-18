-- #410 getter-only atomic successor; candidate-file SHA256 557b6804f0943363719bf34780b7f705d02947107f0ebbad2d9e7b18a965ce55
-- Rollback after commit requires a new qualified forward migration, receipt and ledger history.
-- Restart: none; unchanged function signature is visible on subsequent statements.
-- Production-only277 predecessor guard. The owning runner supplies one transaction
-- and verdify-schema-migrations advisory lock. This template executes nothing.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations, public.runtime_ordinary_login_attestation_receipts,
 public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $getter277_preflight$
DECLARE subject oid := to_regprocedure('public.fn_realized_solar_night_dryout(date,date,text)');
BEGIN
 IF current_database() <> 'verdify' OR current_user <> 'verdify' OR session_user <> 'verdify'
 OR pg_is_in_recovery()
 OR (SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations') IS DISTINCT FROM 276
 OR NOT EXISTS (SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
   AND filename='db/migrations/276-ordinary-maintenance-runtime-boundaries.sql' AND seq=276
   AND sha256='7509d3f84e64c92ef0539be15c3a64c4fb60f9f84bb2e053b4246d9ae4331764'
   AND stamp_method='runner')
 OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=277)
 OR subject IS NULL
 OR (SELECT encode(sha256(convert_to(pg_get_functiondef(subject),'UTF8')),'hex'))
      IS DISTINCT FROM 'b9626fd5882392bcf00c080550b6ab5000cc5ed8a63ea8dceb806e76aae6d3a6'
 OR NOT EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid=subject AND NOT p.prosecdef
   AND p.provolatile='s' AND p.prokind='f' AND p.proconfig IS NULL
   AND pg_get_userbyid(p.proowner)='verdify' AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql'))
 OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
 OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
 OR public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login')
      IS DISTINCT FROM decode('5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba','hex')
 OR public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login')
      IS DISTINCT FROM decode('8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743','hex')
 OR public.fn_mcp_runtime_boundary_digest()
      IS DISTINCT FROM decode('7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68','hex')
 OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login'
   AND boundary_sha256=decode('5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba','hex'))
 OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login'
   AND boundary_sha256=decode('8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743','hex'))
 OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
   AND boundary_sha256=decode('7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68','hex'))
 THEN RAISE EXCEPTION '277 refuses an unqualified production276 predecessor'; END IF;
 PERFORM set_config('verdify.getter277_proc_before',
  (SELECT (to_jsonb(p)-'prosrc')::text FROM pg_proc p WHERE p.oid=subject),true);
 PERFORM set_config('verdify.getter277_api_receipt_before',
  (SELECT to_jsonb(r)::text FROM public.runtime_ordinary_login_attestation_receipts r WHERE login_name='verdify_api_runtime_login'),true);
 PERFORM set_config('verdify.getter277_ingestor_receipt_before',
  (SELECT to_jsonb(r)::text FROM public.runtime_ordinary_login_attestation_receipts r WHERE login_name='verdify_ingestor_runtime_login'),true);
 PERFORM set_config('verdify.getter277_mcp_receipt_before',
  (SELECT to_jsonb(r)::text FROM public.mcp_runtime_boundary_receipt r WHERE singleton),true);
END $getter277_preflight$;

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
    climate_coverage_pct double precision,
    max_climate_gap_s integer,
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
    min_temp_floor_margin_f double precision,
    vpd_before_kpa double precision,
    vpd_after_10_20m_kpa double precision,
    observed_vpd_delta_10_20m_kpa double precision,
    observed_temp_delta_10_20m_f double precision,
    vent_duty_pct double precision,
    fan_duty_pct double precision,
    heat1_duty_pct double precision,
    heat2_duty_pct double precision,
    wet_relay_duty_pct double precision,
    response_wet_relay_minutes integer,
    response_sample_minutes integer,
    response_max_gap_s integer,
    response_min_temp_floor_margin_f double precision,
    daytime_dry_action_samples integer,
    daytime_hold_admission_samples integer,
    safety_gate_status text,
    gate_violations text[],
    confound_reasons text[],
    evidence_status text,
    dryout_disposition text
)
LANGUAGE sql
STABLE
AS $$
WITH bounds AS NOT MATERIALIZED (
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
        COALESCE((l.relay_truth ->> 'fog')::boolean, false)
            OR COALESCE((l.relay_truth ->> 'mister_south')::boolean, false)
            OR COALESCE((l.relay_truth ->> 'mister_west')::boolean, false)
            OR COALESCE((l.relay_truth ->> 'mister_center')::boolean, false)
            AS wet_relay_on,
        l.relay_truth ?& ARRAY[
            'vent', 'fan1', 'fan2', 'heat1', 'heat2',
            'fog', 'mister_south', 'mister_west', 'mister_center'
        ]::text[] AS relay_evidence_present,
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
            a.relay_evidence_present
            AND
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
            a.relay_evidence_present
            AND a.hold_flavor
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
        bool_or(wet_relay_on) AS wet_relay_on,
        bool_and(relay_evidence_present) AS relay_evidence_complete,
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
readback_points AS (
    SELECT bucket AS ts, NULL::text AS parameter, NULL::double precision AS value
    FROM climate_minute
    UNION ALL SELECT ts, parameter, value FROM setpoint_events
),
readback_timeline AS (
    SELECT ts, parameter, value,
      max(ts) FILTER (WHERE parameter='temp_low') OVER (
        ORDER BY ts GROUPS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS temp_ts,
      max(ts) FILTER (WHERE parameter='vpd_low') OVER (
        ORDER BY ts GROUPS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS vpd_ts
    FROM readback_points
),
readback_values AS (
    SELECT ts, parameter,
      max(value) FILTER (WHERE parameter='temp_low') OVER (
        PARTITION BY temp_ts) AS temp_value,
      max(value) FILTER (WHERE parameter='vpd_low') OVER (
        PARTITION BY vpd_ts) AS vpd_value
    FROM readback_timeline
),
readback_climate AS (
    SELECT ts AS bucket, temp_value, vpd_value
    FROM readback_values WHERE parameter IS NULL
),
resolved AS MATERIALIZED (
    SELECT
        c.*,
        public.fn_solar_phase(c.bucket) AS solar_phase,
        COALESCE(rb.temp_value, b.temp_low) AS served_temp_low,
        COALESCE(rb.vpd_value, h.house_vpd_low) AS served_vpd_low,
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
        COALESCE(a.wet_relay_on, false) AS wet_relay_on,
        COALESCE(a.relay_evidence_complete, false) AS relay_evidence_complete,
        COALESCE(a.dry_action_admitted, false) AS dry_action_admitted,
        COALESCE(a.hold_action_admitted, false) AS hold_action_admitted,
        a.dry_admission_reason
    FROM climate_minute c
    JOIN readback_climate rb USING (bucket)
    CROSS JOIN LATERAL public.fn_band_setpoints(c.bucket) b
    CROSS JOIN LATERAL public.fn_house_vpd_control_band(c.bucket) h
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
daytime_admission AS MATERIALIZED (
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
        COALESCE(max(extract(epoch FROM (bucket - previous_bucket))) FILTER (
            WHERE previous_episode_active IS TRUE
        ), 0)::int AS max_climate_gap_s,
        count(*) FILTER (WHERE dry_demand)::int AS below_served_low_minutes,
        count(*) FILTER (
            WHERE action_sample_present AND relay_evidence_complete
        )::int AS action_evidence_minutes,
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
        min(temp_f - served_temp_low)::double precision
            AS min_temp_floor_margin_f,
        count(*) FILTER (WHERE vent_on)::int AS vent_minutes,
        count(*) FILTER (WHERE fan_on)::int AS fan_minutes,
        count(*) FILTER (WHERE heat1_on)::int AS heat1_minutes,
        count(*) FILTER (WHERE heat2_on)::int AS heat2_minutes,
        count(*) FILTER (WHERE wet_relay_on)::int AS wet_relay_minutes
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
        after.wet_relay_minutes AS response_wet_relay_minutes,
        after.response_max_gap_s,
        after.min_temp_floor_margin_f AS response_min_temp_floor_margin_f,
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
            count(c.indoor_ah_g_m3)::int AS ah_samples,
            count(*) FILTER (WHERE c.wet_relay_on)::int AS wet_relay_minutes
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
            count(c.indoor_ah_g_m3)::int AS ah_samples,
            count(*) FILTER (WHERE c.wet_relay_on)::int AS wet_relay_minutes,
            min(c.temp_f - c.served_temp_low)::double precision
                AS min_temp_floor_margin_f,
            CASE
                WHEN count(*) = 0 THEN 600
                ELSE greatest(
                    extract(epoch FROM (
                        min(c.bucket)
                        - (e.episode_started_at + interval '10 minutes')
                    ))::int,
                    COALESCE(max(extract(epoch FROM (
                        c.bucket - c.previous_response_bucket
                    ))) FILTER (
                        WHERE c.previous_response_bucket IS NOT NULL
                    ), 0)::int,
                    extract(epoch FROM (
                        e.episode_started_at + interval '20 minutes'
                        - (max(c.bucket) + interval '1 minute')
                    ))::int
                )
            END AS response_max_gap_s
        FROM (
            SELECT
                response.*,
                lag(response.bucket) OVER (ORDER BY response.bucket)
                    AS previous_response_bucket
            FROM resolved response
            WHERE response.bucket
                    >= e.episode_started_at + interval '10 minutes'
              AND response.bucket
                    < e.episode_started_at + interval '20 minutes'
        ) c
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
        WHEN r.min_temp_floor_margin_f <= 0 THEN 'temperature_floor'
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
    round((100.0 * r.sample_minutes / NULLIF(ceil(r.duration_min), 0))::numeric, 1)
        ::double precision AS climate_coverage_pct,
    r.max_climate_gap_s,
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
    round(r.min_temp_floor_margin_f::numeric, 2)::double precision,
    round(r.vpd_before_kpa::numeric, 3)::double precision,
    round(r.vpd_after_kpa::numeric, 3)::double precision,
    round((r.vpd_after_kpa - r.vpd_before_kpa)::numeric, 3)::double precision,
    round((r.temp_after_f - r.temp_before_f)::numeric, 2)::double precision,
    round((100.0 * r.vent_minutes / NULLIF(r.sample_minutes, 0))::numeric, 1)::double precision,
    round((100.0 * r.fan_minutes / NULLIF(r.sample_minutes, 0))::numeric, 1)::double precision,
    round((100.0 * r.heat1_minutes / NULLIF(r.sample_minutes, 0))::numeric, 1)::double precision,
    round((100.0 * r.heat2_minutes / NULLIF(r.sample_minutes, 0))::numeric, 1)::double precision,
    round((100.0 * r.wet_relay_minutes / NULLIF(r.sample_minutes, 0))::numeric, 1)::double precision,
    r.response_wet_relay_minutes,
    r.after_samples,
    r.response_max_gap_s,
    round(r.response_min_temp_floor_margin_f::numeric, 2)::double precision,
    r.daytime_dry_action_samples,
    r.daytime_hold_admission_samples,
    CASE
        WHEN r.daytime_hold_admission_samples > 0
          OR r.heat2_minutes > 0
          OR (
              r.admitted_minutes > 0
              AND r.min_temp_floor_margin_f <= 0
          )
          OR (
              r.admitted_minutes > 0
              AND r.response_min_temp_floor_margin_f <= 0
          )
            THEN 'fail'
        WHEN r.min_temp_floor_margin_f IS NULL
          OR r.response_min_temp_floor_margin_f IS NULL
            THEN 'incomplete'
        ELSE 'pass'
    END AS safety_gate_status,
    array_remove(ARRAY[
        CASE WHEN r.daytime_hold_admission_samples > 0
            THEN 'daytime_hold_admission' END,
        CASE WHEN r.heat2_minutes > 0 THEN 'heat2_forbidden' END,
        CASE
            WHEN r.admitted_minutes > 0
             AND r.min_temp_floor_margin_f <= 0
                THEN 'temperature_floor_breach'
        END,
        CASE
            WHEN r.admitted_minutes > 0
             AND r.response_min_temp_floor_margin_f <= 0
                THEN 'response_temperature_floor_breach'
        END
    ]::text[], NULL) AS gate_violations,
    array_remove(ARRAY[
        CASE WHEN r.admitted_minutes > 0 AND r.ah_advantage_min_g_m3 <= 0
            THEN 'no_positive_outdoor_ah_advantage' END,
        CASE WHEN r.wet_relay_minutes > 0 THEN 'simultaneous_wetting' END,
        CASE WHEN r.response_wet_relay_minutes > 0
            THEN 'response_window_wetting' END
    ]::text[], NULL) AS confound_reasons,
    CASE
        WHEN r.daytime_hold_admission_samples > 0
          OR r.heat2_minutes > 0
          OR (
              r.admitted_minutes > 0
              AND r.min_temp_floor_margin_f <= 0
          )
          OR (
              r.admitted_minutes > 0
              AND r.response_min_temp_floor_margin_f <= 0
          )
            THEN 'gate_failed'
        WHEN r.min_temp_floor_margin_f IS NULL
          OR r.response_min_temp_floor_margin_f IS NULL
          OR r.sample_minutes < ceil(r.duration_min * 0.8)
          OR r.max_climate_gap_s > 90
          OR r.response_max_gap_s > 90
          OR r.action_evidence_minutes < ceil(r.sample_minutes * 0.8)
          OR r.outdoor_evidence_minutes < ceil(r.sample_minutes * 0.5)
          OR r.before_samples < 4 OR r.after_samples < 8
          OR r.before_ah_samples < 4 OR r.after_ah_samples < 8
            THEN 'incomplete'
        WHEN (r.admitted_minutes > 0 AND r.ah_advantage_min_g_m3 <= 0)
          OR r.wet_relay_minutes > 0
          OR r.response_wet_relay_minutes > 0
            THEN 'confounded'
        ELSE 'complete'
    END AS evidence_status,
    CASE
        WHEN r.daytime_hold_admission_samples > 0
          OR r.heat2_minutes > 0
          OR (
              r.admitted_minutes > 0
              AND r.min_temp_floor_margin_f <= 0
          )
          OR (
              r.admitted_minutes > 0
              AND r.response_min_temp_floor_margin_f <= 0
          )
            THEN 'ineffective'
        WHEN r.min_temp_floor_margin_f IS NULL
          OR r.response_min_temp_floor_margin_f IS NULL
          OR r.sample_minutes < ceil(r.duration_min * 0.8)
          OR r.max_climate_gap_s > 90
          OR r.response_max_gap_s > 90
          OR r.action_evidence_minutes < ceil(r.sample_minutes * 0.8)
          OR r.outdoor_evidence_minutes < ceil(r.sample_minutes * 0.5)
          OR r.before_samples < 4 OR r.after_samples < 8
          OR r.before_ah_samples < 4 OR r.after_ah_samples < 8
            THEN 'insufficient_evidence'
        WHEN (r.admitted_minutes > 0 AND r.ah_advantage_min_g_m3 <= 0)
          OR r.wet_relay_minutes > 0
          OR r.response_wet_relay_minutes > 0
            THEN 'insufficient_evidence'
        WHEN r.admitted_minutes = 0 THEN 'blocked'
        WHEN r.ah_advantage_min_g_m3 > 0
         AND r.vpd_after_kpa - r.vpd_before_kpa >= 0.05
         AND r.indoor_ah_after_g_m3 - r.indoor_ah_before_g_m3 <= -0.05
            THEN 'effective'
        ELSE 'ineffective'
    END AS dryout_disposition
FROM with_response r
ORDER BY r.night_date, r.episode_started_at;
$$;

DO $getter277_postshape$
BEGIN
 IF (SELECT to_jsonb(p)-'prosrc' FROM pg_proc p
       WHERE p.oid='public.fn_realized_solar_night_dryout(date,date,text)'::regprocedure)
    IS DISTINCT FROM current_setting('verdify.getter277_proc_before')::jsonb
 OR public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login')
    IS DISTINCT FROM decode('5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba','hex')
 OR public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login')
    IS DISTINCT FROM decode('8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743','hex')
 OR (SELECT to_jsonb(r) FROM public.runtime_ordinary_login_attestation_receipts r WHERE login_name='verdify_api_runtime_login')
    IS DISTINCT FROM current_setting('verdify.getter277_api_receipt_before')::jsonb
 OR (SELECT to_jsonb(r) FROM public.runtime_ordinary_login_attestation_receipts r WHERE login_name='verdify_ingestor_runtime_login')
    IS DISTINCT FROM current_setting('verdify.getter277_ingestor_receipt_before')::jsonb
 OR (SELECT to_jsonb(r) FROM public.mcp_runtime_boundary_receipt r WHERE singleton)
    IS DISTINCT FROM current_setting('verdify.getter277_mcp_receipt_before')::jsonb
 THEN RAISE EXCEPTION '277 changed metadata or a receipt outside its getter-body scope'; END IF;
END $getter277_postshape$;

-- Actual production rollback-qualification receipt SHA256 7e342bb788cb77ce7dc80391b4fce46b60ea4953707acf62240a7ae789a62b6e
DO $getter277_qualified_successor$
BEGIN
 IF encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02'
 OR encode(sha256(convert_to(pg_get_functiondef('public.fn_realized_solar_night_dryout(date,date,text)'::regprocedure),'UTF8')),'hex')
   IS DISTINCT FROM '89782089fd6ae3ecfeecdfbc1b730f6ab6a272c0da7b35ef562788eddbfa42c0'
 OR encode(sha256(convert_to((SELECT prosrc FROM pg_proc WHERE oid='public.fn_realized_solar_night_dryout(date,date,text)'::regprocedure),'UTF8')),'hex')
   IS DISTINCT FROM '578a07bed56714cd2dfe2308ca30ced1f6fc6916fd7db9d79630a7b82b261655'
 THEN RAISE EXCEPTION '277 refuses an unqualified getter/MCP successor'; END IF;
 UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02','hex')
  WHERE singleton AND boundary_sha256=decode('7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68','hex');
 IF NOT FOUND THEN RAISE EXCEPTION '277 MCP predecessor receipt disappeared'; END IF;
 IF (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
 OR (SELECT to_jsonb(r)-'boundary_sha256' FROM public.mcp_runtime_boundary_receipt r WHERE singleton)
   IS DISTINCT FROM (current_setting('verdify.getter277_mcp_receipt_before')::jsonb-'boundary_sha256')
 OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
   AND boundary_sha256=decode('1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02','hex') AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
 THEN RAISE EXCEPTION '277 MCP literal receipt successor failed'; END IF;
END $getter277_qualified_successor$;
