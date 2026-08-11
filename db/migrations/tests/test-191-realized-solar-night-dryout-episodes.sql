-- Admission, blocking, stopping, incomplete evidence, and daytime-exclusion
-- fixture for migration 191.

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE public.climate (
    ts timestamptz NOT NULL,
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    temp_avg double precision,
    vpd_avg double precision,
    rh_avg double precision,
    outdoor_temp_f double precision,
    outdoor_rh_pct double precision
);

CREATE TABLE public.climate_action_log (
    ts timestamptz NOT NULL,
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    climate_action text,
    priority_axis text,
    source_system_state jsonb DEFAULT '{}'::jsonb,
    relay_truth jsonb DEFAULT '{}'::jsonb
);

CREATE TABLE public.setpoint_snapshot (
    ts timestamptz NOT NULL,
    parameter text NOT NULL,
    value double precision NOT NULL,
    greenhouse_id text NOT NULL DEFAULT 'vallery'
);

CREATE FUNCTION public.fn_solar_phase(target_ts timestamptz)
RETURNS double precision
LANGUAGE sql STABLE
AS $$
    SELECT CASE
        WHEN extract(hour FROM target_ts AT TIME ZONE 'America/Denver') < 6
          OR extract(hour FROM target_ts AT TIME ZONE 'America/Denver') >= 18
        THEN 3.0 ELSE 1.0 END
$$;

CREATE FUNCTION public.fn_band_setpoints(target_ts timestamptz)
RETURNS TABLE(
    temp_low double precision,
    temp_high double precision,
    vpd_low double precision,
    vpd_high double precision,
    temp_target double precision,
    vpd_target double precision
)
LANGUAGE sql STABLE ROWS 1
AS $$
    SELECT
        CASE
            WHEN target_ts >= '2026-01-12 00:00:00+00'::timestamptz
                THEN NULL::double precision
            ELSE 63.0
        END,
        80.0, 0.50, 1.20, 70.0, 0.90
$$;

CREATE FUNCTION public.fn_house_vpd_control_band(timestamptz)
RETURNS TABLE(
    crop_vpd_low double precision,
    crop_vpd_high double precision,
    vpd_target_south double precision,
    vpd_target_west double precision,
    vpd_target_east double precision,
    vpd_target_center double precision,
    zone_vpd_min double precision,
    zone_vpd_median double precision,
    zone_vpd_max double precision,
    house_vpd_low double precision,
    house_vpd_high double precision,
    house_vpd_min_width_kpa double precision,
    house_vpd_low_margin_kpa double precision
)
LANGUAGE sql STABLE ROWS 1
AS $$ SELECT 0.50, 1.20, 0.90, 0.90, 0.90, 0.90,
             0.90, 0.90, 0.90, 0.50, 1.20, 0.40, 0.10 $$;

-- Historical served readbacks intentionally differ from the current-function
-- fallback above. The fixture demand/floor assertions therefore fail if the
-- evidence function retroactively recomputes old bands from current anchors.
INSERT INTO public.setpoint_snapshot(ts, parameter, value) VALUES
    ('2026-01-10 18:00:00+00', 'temp_low', 64.0),
    ('2026-01-10 18:00:00+00', 'vpd_low', 0.80);

\i db/migrations/191-realized-solar-night-dryout-episodes.sql

-- Admitted episode: 22:00-22:09 local, with measured recovery in the
-- 10-20-minute response window.
INSERT INTO public.climate(ts, temp_avg, vpd_avg, rh_avg, outdoor_temp_f, outdoor_rh_pct)
SELECT
    gs,
    CASE WHEN gs >= '2026-01-11 05:10:00+00' THEN 66.5 ELSE 66.0 END,
    CASE
        WHEN gs >= '2026-01-11 05:00:00+00' AND gs < '2026-01-11 05:10:00+00' THEN 0.60
        ELSE 0.86
    END,
    CASE
        WHEN gs >= '2026-01-11 05:00:00+00' AND gs < '2026-01-11 05:10:00+00' THEN 72.0
        ELSE 65.0
    END,
    40.0,
    30.0
FROM generate_series(
    '2026-01-11 04:55:00+00'::timestamptz,
    '2026-01-11 05:30:00+00'::timestamptz,
    interval '1 minute'
) gs;

INSERT INTO public.climate_action_log(
    ts, climate_action, priority_axis, source_system_state, relay_truth
)
SELECT
    gs,
    'DEHUM_VENT',
    'vpd',
    '{"climate_moisture_exchange":{"action":"vent_dehum","reason":"vent_plus_heat_hold"}}',
    '{"vent":true,"fan1":true,"fan2":false,"heat1":true,"heat2":false,"fog":false,"mister_south":false,"mister_west":false,"mister_center":false}'
FROM generate_series(
    '2026-01-11 05:00:00+00'::timestamptz,
    '2026-01-11 05:09:00+00'::timestamptz,
    interval '1 minute'
) gs;

-- Blocked episode with no outdoor evidence.
INSERT INTO public.climate(ts, temp_avg, vpd_avg, rh_avg, outdoor_temp_f, outdoor_rh_pct)
SELECT
    gs,
    66.0,
    CASE
        WHEN gs >= '2026-01-11 06:00:00+00' AND gs < '2026-01-11 06:05:00+00' THEN 0.62
        ELSE 0.86
    END,
    72.0,
    NULL,
    NULL
FROM generate_series(
    '2026-01-11 05:55:00+00'::timestamptz,
    '2026-01-11 06:20:00+00'::timestamptz,
    interval '1 minute'
) gs;

INSERT INTO public.climate_action_log(ts, climate_action, priority_axis, relay_truth)
SELECT gs, 'IDLE', 'vpd',
       '{"vent":false,"fan1":false,"fan2":false,"heat1":false,"heat2":false,"fog":false,"mister_south":false,"mister_west":false,"mister_center":false}'
FROM generate_series(
    '2026-01-11 06:00:00+00'::timestamptz,
    '2026-01-11 06:04:00+00'::timestamptz,
    interval '1 minute'
) gs;

-- Reliable blocked episode at the temperature floor: complete action/outdoor
-- evidence, but no dry relay admission because measured temperature is below
-- the served floor.
INSERT INTO public.climate(ts, temp_avg, vpd_avg, rh_avg, outdoor_temp_f, outdoor_rh_pct)
SELECT
    gs,
    CASE
        WHEN gs >= '2026-01-11 08:00:00+00' AND gs < '2026-01-11 08:05:00+00' THEN 62.0
        ELSE 65.0
    END,
    CASE
        WHEN gs >= '2026-01-11 08:00:00+00' AND gs < '2026-01-11 08:05:00+00' THEN 0.62
        ELSE 0.86
    END,
    70.0,
    40.0,
    30.0
FROM generate_series(
    '2026-01-11 07:55:00+00'::timestamptz,
    '2026-01-11 08:20:00+00'::timestamptz,
    interval '1 minute'
) gs;

-- Deliberately place mutually inconsistent action/relay rows in each minute:
-- IDLE carries vent+fan relay truth while two DEHUM_VENT decisions have those
-- relays off. A minute-level mix-and-match would falsely admit dehumidification;
-- row-level admission must keep this episode blocked.
INSERT INTO public.climate_action_log(ts, climate_action, priority_axis, relay_truth)
SELECT gs, 'IDLE', 'vpd',
       '{"vent":true,"fan1":true,"fan2":false,"heat1":false,"heat2":false,"fog":false,"mister_south":false,"mister_west":false,"mister_center":false}'
FROM generate_series(
    '2026-01-11 08:00:00+00'::timestamptz,
    '2026-01-11 08:04:00+00'::timestamptz,
    interval '1 minute'
) gs;

INSERT INTO public.climate_action_log(ts, climate_action, priority_axis, relay_truth)
SELECT gs, 'DEHUM_VENT', 'vpd',
       '{"vent":false,"fan1":false,"fan2":false,"heat1":false,"heat2":false,"fog":false,"mister_south":false,"mister_west":false,"mister_center":false}'
FROM generate_series(
    '2026-01-11 08:00:00+00'::timestamptz,
    '2026-01-11 08:04:00+00'::timestamptz,
    interval '1 minute'
) gs
CROSS JOIN (VALUES (1), (2)) duplicate_decisions(n);

-- A daytime fake dry action is counted as a violation but must never become an
-- episode.  19:00Z = 12:00 America/Denver on this date.
INSERT INTO public.climate(ts, temp_avg, vpd_avg, rh_avg, outdoor_temp_f, outdoor_rh_pct)
VALUES ('2026-01-10 19:00:00+00', 70, 0.60, 70, 40, 30);
INSERT INTO public.climate_action_log(ts, climate_action, priority_axis, relay_truth)
VALUES (
    '2026-01-10 19:00:00+00', 'DEHUM_VENT', 'vpd',
    '{"vent":true,"fan1":true,"fan2":false,"heat1":false,"heat2":false,"fog":false,"mister_south":false,"mister_west":false,"mister_center":false}'
);

-- Projected hold intent is not realized hold. Vent+fan make this a physical dry
-- action, but heat1 is off, so hold_required must not fail the daytime hold gate.
INSERT INTO public.climate_action_log(
    ts, climate_action, priority_axis, source_system_state, relay_truth
) VALUES (
    '2026-01-10 19:02:00+00', 'DEHUM_VENT', 'vpd',
    '{"climate_moisture_exchange":{"action":"vent_dehum","reason":"vent_plus_heat_hold","hold_required":true}}',
    '{"vent":true,"fan1":true,"fan2":false,"heat1":false,"heat2":false,"fog":false,"mister_south":false,"mister_west":false,"mister_center":false}'
);

-- A separate greenhouse proves the held-temperature flavor is different from
-- legitimate general daytime dehumidification. Its night action is otherwise
-- effective, but an actual held-temp DEHUM admission during solar day must fail
-- the safety gate and force the four-state disposition to ineffective.
INSERT INTO public.setpoint_snapshot(ts, parameter, value, greenhouse_id) VALUES
    ('2026-01-10 18:00:00+00', 'temp_low', 64.0, 'violation'),
    ('2026-01-10 18:00:00+00', 'vpd_low', 0.80, 'violation');

INSERT INTO public.climate(
    ts, greenhouse_id, temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
)
SELECT
    ts, 'violation', temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
FROM public.climate
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 04:55:00+00'
  AND ts <= '2026-01-11 05:30:00+00';

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
)
SELECT
    ts, 'violation', climate_action, priority_axis,
    source_system_state, relay_truth
FROM public.climate_action_log
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 05:00:00+00'
  AND ts <= '2026-01-11 05:09:00+00';

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
) VALUES (
    '2026-01-10 19:01:00+00', 'violation', 'DEHUM_VENT', 'vpd',
    '{"climate_moisture_exchange":{"action":"vent_dehum","reason":"vent_plus_heat_hold","hold_required":true}}',
    '{"vent":true,"fan1":true,"fan2":false,"heat1":true,"heat2":true,"fog":false,"mister_south":false,"mister_west":false,"mister_center":false}'
);

-- A VPD rise caused without indoor absolute-humidity removal is not dry-out.
-- Preserve the admitted action and VPD response, but hold RH constant so the
-- slight temperature increase raises AH; disposition must be ineffective.
INSERT INTO public.setpoint_snapshot(ts, parameter, value, greenhouse_id) VALUES
    ('2026-01-10 18:00:00+00', 'temp_low', 64.0, 'heat_only'),
    ('2026-01-10 18:00:00+00', 'vpd_low', 0.80, 'heat_only');

INSERT INTO public.climate(
    ts, greenhouse_id, temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
)
SELECT
    ts, 'heat_only', temp_avg, vpd_avg, 72.0,
    outdoor_temp_f, outdoor_rh_pct
FROM public.climate
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 04:55:00+00'
  AND ts <= '2026-01-11 05:30:00+00';

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
)
SELECT
    ts, 'heat_only', climate_action, priority_axis,
    source_system_state, relay_truth
FROM public.climate_action_log
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 05:00:00+00'
  AND ts <= '2026-01-11 05:09:00+00';

-- Physical admission and safety qualification are separate. This night row is
-- still an admitted held-temp action, while forbidden heat2 must independently
-- fail the safety gate rather than making the action disappear.
INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
) VALUES (
    '2026-01-11 05:00:30+00', 'violation', 'DEHUM_VENT', 'vpd',
    '{"climate_moisture_exchange":{"action":"vent_dehum","reason":"vent_plus_heat_hold","hold_required":true}}',
    '{"vent":true,"fan1":true,"fan2":false,"heat1":true,"heat2":true,"fog":false,"mister_south":false,"mister_west":false,"mister_center":false}'
);

-- Independent edge-case greenhouses cover each evidence qualification that
-- previously allowed a false effective result.
INSERT INTO public.setpoint_snapshot(ts, parameter, value, greenhouse_id)
SELECT ts, parameter, value, greenhouse_id
FROM (VALUES
    ('2026-01-10 18:00:00+00'::timestamptz, 'temp_low', 64.0, 'missing_relay'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'vpd_low', 0.80, 'missing_relay'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'temp_low', 64.0, 'floor_breach'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'vpd_low', 0.80, 'floor_breach'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'temp_low', 64.0, 'gappy'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'vpd_low', 0.80, 'gappy'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'temp_low', 64.0, 'weather_confounded'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'vpd_low', 0.80, 'weather_confounded'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'temp_low', 64.0, 'wet_confounded'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'vpd_low', 0.80, 'wet_confounded'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'temp_low', 64.0, 'weather_interval_confounded'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'vpd_low', 0.80, 'weather_interval_confounded'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'temp_low', 64.0, 'response_gappy'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'vpd_low', 0.80, 'response_gappy'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'temp_low', 64.0, 'response_floor'),
    ('2026-01-10 18:00:00+00'::timestamptz, 'vpd_low', 0.80, 'response_floor'),
    ('2026-01-11 18:00:00+00'::timestamptz, 'vpd_low', 0.80, 'null_floor')
) AS fixture(ts, parameter, value, greenhouse_id);

-- An empty relay payload is missing evidence, never evidence that all relays
-- were off.
INSERT INTO public.climate(
    ts, greenhouse_id, temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
)
SELECT
    ts, 'missing_relay', temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
FROM public.climate
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 04:55:00+00'
  AND ts <= '2026-01-11 05:30:00+00';

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
)
SELECT
    ts, 'missing_relay', climate_action, priority_axis,
    source_system_state, '{}'::jsonb
FROM public.climate_action_log
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 05:00:00+00'
  AND ts <= '2026-01-11 05:09:00+00';

-- An admitted episode that crosses its actually served floor must fail even if
-- the independently aggregated minimum floor would hide the same-row breach.
INSERT INTO public.climate(
    ts, greenhouse_id, temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
)
SELECT
    ts,
    'floor_breach',
    CASE WHEN ts = '2026-01-11 05:05:00+00' THEN 63.0 ELSE temp_avg END,
    vpd_avg,
    rh_avg,
    outdoor_temp_f,
    outdoor_rh_pct
FROM public.climate
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 04:55:00+00'
  AND ts <= '2026-01-11 05:30:00+00';

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
)
SELECT
    ts, 'floor_breach', climate_action, priority_axis,
    source_system_state, relay_truth
FROM public.climate_action_log
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 05:00:00+00'
  AND ts <= '2026-01-11 05:09:00+00';

-- Alternating-minute telemetry has 100% row-relative action coverage but only
-- about 56% wall-clock climate coverage and two-minute gaps.
INSERT INTO public.climate(
    ts, greenhouse_id, temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
)
SELECT
    ts, 'gappy', temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
FROM public.climate
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 04:55:00+00'
  AND ts <= '2026-01-11 05:30:00+00'
  AND extract(minute FROM ts)::int % 2 = 0;

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
)
SELECT
    ts, 'gappy', climate_action, priority_axis,
    source_system_state, relay_truth
FROM public.climate_action_log
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 05:00:00+00'
  AND ts <= '2026-01-11 05:09:00+00'
  AND extract(minute FROM ts)::int % 2 = 0;

-- A VPD/AH response is confounded when outside air is wetter than inside.
INSERT INTO public.climate(
    ts, greenhouse_id, temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
)
SELECT
    ts, 'weather_confounded', temp_avg, vpd_avg, rh_avg, 80.0, 90.0
FROM public.climate
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 04:55:00+00'
  AND ts <= '2026-01-11 05:30:00+00';

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
)
SELECT
    ts, 'weather_confounded', climate_action, priority_axis,
    source_system_state, relay_truth
FROM public.climate_action_log
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 05:00:00+00'
  AND ts <= '2026-01-11 05:09:00+00';

-- Simultaneous VPD mister activity invalidates attribution to dry-out.
INSERT INTO public.climate(
    ts, greenhouse_id, temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
)
SELECT
    ts, 'wet_confounded', temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
FROM public.climate
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 04:55:00+00'
  AND ts <= '2026-01-11 05:30:00+00';

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
)
SELECT
    ts, 'wet_confounded', climate_action, priority_axis,
    source_system_state,
    jsonb_set(relay_truth, '{mister_center}', 'true'::jsonb)
FROM public.climate_action_log
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 05:00:00+00'
  AND ts <= '2026-01-11 05:09:00+00';

-- A single outside-wetter minute invalidates the weather attribution even when
-- the episode-average AH advantage remains positive.
INSERT INTO public.climate(
    ts, greenhouse_id, temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
)
SELECT
    ts,
    'weather_interval_confounded',
    temp_avg,
    vpd_avg,
    rh_avg,
    CASE WHEN ts = '2026-01-11 05:05:00+00' THEN 80.0 ELSE outdoor_temp_f END,
    CASE WHEN ts = '2026-01-11 05:05:00+00' THEN 90.0 ELSE outdoor_rh_pct END
FROM public.climate
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 04:55:00+00'
  AND ts <= '2026-01-11 05:30:00+00';

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
)
SELECT
    ts, 'weather_interval_confounded', climate_action, priority_axis,
    source_system_state, relay_truth
FROM public.climate_action_log
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 05:00:00+00'
  AND ts <= '2026-01-11 05:09:00+00';

-- Eight samples are not sufficient when they are clustered around a three-
-- minute hole in the anchored ten-minute response window.
INSERT INTO public.climate(
    ts, greenhouse_id, temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
)
SELECT
    ts, 'response_gappy', temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
FROM public.climate
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 04:55:00+00'
  AND ts <= '2026-01-11 05:30:00+00'
  AND ts NOT IN (
      '2026-01-11 05:13:00+00'::timestamptz,
      '2026-01-11 05:14:00+00'::timestamptz
  );

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
)
SELECT
    ts, 'response_gappy', climate_action, priority_axis,
    source_system_state, relay_truth
FROM public.climate_action_log
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 05:00:00+00'
  AND ts <= '2026-01-11 05:09:00+00';

-- Cooling below the served floor after an admitted action is a safety failure,
-- even when the episode itself stayed above the floor.
INSERT INTO public.climate(
    ts, greenhouse_id, temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
)
SELECT
    ts,
    'response_floor',
    CASE WHEN ts = '2026-01-11 05:15:00+00' THEN 63.0 ELSE temp_avg END,
    vpd_avg,
    rh_avg,
    outdoor_temp_f,
    outdoor_rh_pct
FROM public.climate
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 04:55:00+00'
  AND ts <= '2026-01-11 05:30:00+00';

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
)
SELECT
    ts, 'response_floor', climate_action, priority_axis,
    source_system_state, relay_truth
FROM public.climate_action_log
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 05:00:00+00'
  AND ts <= '2026-01-11 05:09:00+00';

-- The current-band fallback deliberately returns a NULL temperature floor on
-- this later night. Missing floor evidence must never become a safety PASS or
-- an effective disposition.
INSERT INTO public.climate(
    ts, greenhouse_id, temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
)
SELECT
    ts + interval '1 day', 'null_floor', temp_avg, vpd_avg, rh_avg,
    outdoor_temp_f, outdoor_rh_pct
FROM public.climate
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 04:55:00+00'
  AND ts <= '2026-01-11 05:30:00+00';

INSERT INTO public.climate_action_log(
    ts, greenhouse_id, climate_action, priority_axis,
    source_system_state, relay_truth
)
SELECT
    ts + interval '1 day', 'null_floor', climate_action, priority_axis,
    source_system_state, relay_truth
FROM public.climate_action_log
WHERE greenhouse_id = 'vallery'
  AND ts >= '2026-01-11 05:00:00+00'
  AND ts <= '2026-01-11 05:09:00+00';

DO $$
DECLARE
    admitted record;
    blocked record;
    insufficient record;
    gate_failed record;
    heat_only record;
    missing_relay record;
    floor_breach record;
    gappy record;
    weather_confounded record;
    wet_confounded record;
    weather_interval_confounded record;
    response_gappy record;
    response_floor record;
    null_floor record;
    rows_found int;
BEGIN
    SELECT count(*) INTO rows_found
      FROM public.fn_realized_solar_night_dryout('2026-01-10', '2026-01-10');
    IF rows_found <> 3 THEN
        RAISE EXCEPTION 'expected exactly three solar-night demand episodes, got %', rows_found;
    END IF;

    SELECT * INTO admitted
      FROM public.fn_realized_solar_night_dryout('2026-01-10', '2026-01-10')
     WHERE admission_status = 'admitted';
    IF admitted.admission_reason <> 'vent_plus_heat_hold'
       OR admitted.stop_reason <> 'vpd_recovered'
       OR admitted.dryout_disposition <> 'effective'
       OR admitted.observed_indoor_ah_delta_10_20m_g_m3 >= -0.05
       OR admitted.vent_duty_pct <> 100.0
       OR admitted.heat2_duty_pct <> 0.0 THEN
        RAISE EXCEPTION 'admitted realized episode mismatch: %', row_to_json(admitted);
    END IF;
    IF admitted.daytime_dry_action_samples <> 2 THEN
        RAISE EXCEPTION 'general daytime dry action was not counted';
    END IF;
    IF admitted.daytime_hold_admission_samples <> 0
       OR admitted.safety_gate_status <> 'pass' THEN
        RAISE EXCEPTION 'general daytime dehum was mislabeled as held-temp violation';
    END IF;

    SELECT * INTO blocked
      FROM public.fn_realized_solar_night_dryout('2026-01-10', '2026-01-10')
     WHERE admission_status = 'blocked' AND block_reason = 'temperature_floor';
    IF blocked.dryout_disposition <> 'blocked' THEN
        RAISE EXCEPTION 'blocked episode mismatch: %', row_to_json(blocked);
    END IF;

    SELECT * INTO insufficient
      FROM public.fn_realized_solar_night_dryout('2026-01-10', '2026-01-10')
     WHERE block_reason = 'outdoor_evidence_missing';
    IF insufficient.dryout_disposition <> 'insufficient_evidence' THEN
        RAISE EXCEPTION 'insufficient-evidence episode mismatch: %', row_to_json(insufficient);
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.fn_realized_solar_night_dryout('2026-01-10', '2026-01-10')
        WHERE episode_started_at = '2026-01-10 19:00:00+00'
    ) THEN
        RAISE EXCEPTION 'daytime action was incorrectly admitted as a night episode';
    END IF;

    SELECT * INTO gate_failed
      FROM public.fn_realized_solar_night_dryout(
          '2026-01-10', '2026-01-10', 'violation'
      );
    IF gate_failed.daytime_hold_admission_samples <> 1
       OR gate_failed.evidence_status <> 'gate_failed'
       OR gate_failed.safety_gate_status <> 'fail'
       OR gate_failed.dryout_disposition <> 'ineffective'
       OR gate_failed.hold_admitted_minutes = 0
       OR NOT ('daytime_hold_admission' = ANY(gate_failed.gate_violations))
       OR NOT ('heat2_forbidden' = ANY(gate_failed.gate_violations)) THEN
        RAISE EXCEPTION 'daytime held-temp safety gate mismatch: %',
            row_to_json(gate_failed);
    END IF;

    SELECT * INTO heat_only
      FROM public.fn_realized_solar_night_dryout(
          '2026-01-10', '2026-01-10', 'heat_only'
      );
    IF heat_only.observed_vpd_delta_10_20m_kpa < 0.05
       OR heat_only.observed_indoor_ah_delta_10_20m_g_m3 <= -0.05
       OR heat_only.safety_gate_status <> 'pass'
       OR heat_only.dryout_disposition <> 'ineffective' THEN
        RAISE EXCEPTION 'heat-only VPD rise was mislabeled as dry-out: %',
            row_to_json(heat_only);
    END IF;

    SELECT * INTO missing_relay
      FROM public.fn_realized_solar_night_dryout(
          '2026-01-10', '2026-01-10', 'missing_relay'
      );
    IF missing_relay.action_evidence_minutes <> 0
       OR missing_relay.evidence_status <> 'incomplete'
       OR missing_relay.dryout_disposition <> 'insufficient_evidence' THEN
        RAISE EXCEPTION 'empty relay payload was treated as complete: %',
            row_to_json(missing_relay);
    END IF;

    SELECT * INTO floor_breach
      FROM public.fn_realized_solar_night_dryout(
          '2026-01-10', '2026-01-10', 'floor_breach'
      );
    IF floor_breach.min_temp_floor_margin_f <> -1.0
       OR floor_breach.safety_gate_status <> 'fail'
       OR floor_breach.evidence_status <> 'gate_failed'
       OR floor_breach.dryout_disposition <> 'ineffective'
       OR NOT ('temperature_floor_breach' = ANY(floor_breach.gate_violations))
    THEN
        RAISE EXCEPTION 'temperature-floor breach did not fail gate: %',
            row_to_json(floor_breach);
    END IF;

    SELECT * INTO gappy
      FROM public.fn_realized_solar_night_dryout(
          '2026-01-10', '2026-01-10', 'gappy'
      );
    IF gappy.climate_coverage_pct >= 80.0
       OR gappy.max_climate_gap_s <> 120
       OR gappy.evidence_status <> 'incomplete'
       OR gappy.dryout_disposition <> 'insufficient_evidence' THEN
        RAISE EXCEPTION 'wall-clock telemetry gaps were hidden: %',
            row_to_json(gappy);
    END IF;

    SELECT * INTO weather_confounded
      FROM public.fn_realized_solar_night_dryout(
          '2026-01-10', '2026-01-10', 'weather_confounded'
      );
    IF weather_confounded.ah_advantage_avg_g_m3 > 0
       OR weather_confounded.evidence_status <> 'confounded'
       OR weather_confounded.dryout_disposition <> 'insufficient_evidence'
       OR NOT (
           'no_positive_outdoor_ah_advantage'
           = ANY(weather_confounded.confound_reasons)
       ) THEN
        RAISE EXCEPTION 'wetter outdoor air was not marked confounded: %',
            row_to_json(weather_confounded);
    END IF;

    SELECT * INTO wet_confounded
      FROM public.fn_realized_solar_night_dryout(
          '2026-01-10', '2026-01-10', 'wet_confounded'
      );
    IF wet_confounded.wet_relay_duty_pct <> 100.0
       OR wet_confounded.evidence_status <> 'confounded'
       OR wet_confounded.dryout_disposition <> 'insufficient_evidence'
       OR NOT ('simultaneous_wetting' = ANY(wet_confounded.confound_reasons))
    THEN
        RAISE EXCEPTION 'simultaneous mister activity was not confounded: %',
            row_to_json(wet_confounded);
    END IF;

    SELECT * INTO weather_interval_confounded
      FROM public.fn_realized_solar_night_dryout(
          '2026-01-10', '2026-01-10', 'weather_interval_confounded'
      );
    IF weather_interval_confounded.ah_advantage_avg_g_m3 <= 0
       OR weather_interval_confounded.ah_advantage_min_g_m3 > 0
       OR weather_interval_confounded.evidence_status <> 'confounded'
       OR weather_interval_confounded.dryout_disposition
            <> 'insufficient_evidence'
       OR NOT (
           'no_positive_outdoor_ah_advantage'
           = ANY(weather_interval_confounded.confound_reasons)
       ) THEN
        RAISE EXCEPTION 'outside-wetter interval was hidden by the average: %',
            row_to_json(weather_interval_confounded);
    END IF;

    SELECT * INTO response_gappy
      FROM public.fn_realized_solar_night_dryout(
          '2026-01-10', '2026-01-10', 'response_gappy'
      );
    IF response_gappy.response_sample_minutes <> 8
       OR response_gappy.response_max_gap_s <> 180
       OR response_gappy.evidence_status <> 'incomplete'
       OR response_gappy.dryout_disposition <> 'insufficient_evidence' THEN
        RAISE EXCEPTION 'response-window gap was hidden by sample count: %',
            row_to_json(response_gappy);
    END IF;

    SELECT * INTO response_floor
      FROM public.fn_realized_solar_night_dryout(
          '2026-01-10', '2026-01-10', 'response_floor'
      );
    IF response_floor.response_min_temp_floor_margin_f <> -1.0
       OR response_floor.safety_gate_status <> 'fail'
       OR response_floor.evidence_status <> 'gate_failed'
       OR response_floor.dryout_disposition <> 'ineffective'
       OR NOT (
           'response_temperature_floor_breach'
           = ANY(response_floor.gate_violations)
       ) THEN
        RAISE EXCEPTION 'post-action temperature-floor breach did not fail: %',
            row_to_json(response_floor);
    END IF;

    SELECT * INTO null_floor
      FROM public.fn_realized_solar_night_dryout(
          '2026-01-11', '2026-01-11', 'null_floor'
      );
    IF null_floor.min_temp_floor_margin_f IS NOT NULL
       OR null_floor.response_min_temp_floor_margin_f IS NOT NULL
       OR null_floor.safety_gate_status <> 'incomplete'
       OR null_floor.evidence_status <> 'incomplete'
       OR null_floor.dryout_disposition <> 'insufficient_evidence' THEN
        RAISE EXCEPTION 'missing temperature floor became false success: %',
            row_to_json(null_floor);
    END IF;
END $$;

SELECT
    episode_started_at,
    admission_status,
    admission_reason,
    block_reason,
    stop_reason,
    ah_advantage_avg_g_m3,
    observed_indoor_ah_delta_10_20m_g_m3,
    min_temp_f,
    observed_vpd_delta_10_20m_kpa,
    observed_temp_delta_10_20m_f,
    vent_duty_pct,
    heat1_duty_pct,
    heat2_duty_pct,
    evidence_status,
    dryout_disposition,
    daytime_dry_action_samples,
    daytime_hold_admission_samples,
    safety_gate_status,
    gate_violations
FROM public.fn_realized_solar_night_dryout('2026-01-10', '2026-01-10')
ORDER BY episode_started_at;

ROLLBACK;
