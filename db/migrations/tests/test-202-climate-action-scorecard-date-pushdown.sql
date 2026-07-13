-- Equivalence contract for the #498 single-day scorecard function.
--
-- Proves fn_climate_action_daily_scorecard(d) is row-identical to
-- `SELECT * FROM v_climate_action_daily_scorecard WHERE date = d` for EVERY
-- date the view emits, on fixtures that exercise the shapes where the two
-- implementations could diverge:
--
--   1. a conflicting same-timestamp equipment_state pair (fn_equip_at's
--      index scan returns the smallest-ctid duplicate; the function's
--      DISTINCT ON ... ctid dedup must pick the same row — the hand-computed
--      37.50% wet duty below is wrong (62.50%) if the tie resolves the other
--      way);
--   2. carry-in relay state from before the day (vent ON across midnight);
--   3. a relay pulse crossing local midnight (both adjacent days read it);
--   4. an expired setpoint row (expiry boundary falls between two actions'
--      after-lookup instants);
--   5. a NULL-greenhouse_id action row (NULL group key; setpoints resolve
--      NULL through greenhouse_id = NULL, exactly like fn_setpoint_at);
--   6. an action with no after-window climate sample (NULL deltas averaged
--      with non-NULL siblings in the same group);
--   7. the rolling 14-day cutoff: two actions share a local date but only
--      the one inside now() - 14 days may aggregate (the GREATEST bound);
--   8. an empty day and an out-of-window historical day (zero rows).
--
-- Fixture days are now()-relative because fn_climate_action_effectiveness
-- bounds its scan to a rolling 14-day window; fixed dates would age out.
--
-- enable_seqscan is disabled inside the transaction so fn_equip_at /
-- fn_setpoint_at resolve through the same composite-index scans they use in
-- prod (on a table this small the planner would otherwise seqscan+sort,
-- which does not pin the equal-timestamp tie order this test asserts).

\set ON_ERROR_STOP on

BEGIN;

SET LOCAL enable_seqscan = off;

CREATE TABLE public.greenhouses (id text PRIMARY KEY);
INSERT INTO public.greenhouses(id) VALUES ('vallery');

CREATE TABLE public.climate (
    ts timestamptz NOT NULL,
    greenhouse_id text DEFAULT 'vallery',
    temp_avg double precision,
    vpd_avg double precision,
    mister_water_today double precision,
    outdoor_temp_f double precision,
    outdoor_rh_pct double precision,
    dew_point double precision,
    solar_irradiance_w_m2 double precision
);
CREATE INDEX ON public.climate (ts);

CREATE TABLE public.setpoint_changes (
    ts timestamptz NOT NULL,
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    parameter text NOT NULL,
    value double precision,
    expired_at timestamptz
);
CREATE INDEX ON public.setpoint_changes (greenhouse_id, parameter, ts DESC);

CREATE TABLE public.equipment_state (
    ts timestamptz NOT NULL,
    equipment text NOT NULL,
    state boolean NOT NULL,
    greenhouse_id text DEFAULT 'vallery'
);
CREATE INDEX ON public.equipment_state (equipment, ts DESC);

-- Prod bodies (db/schema.sql) — the exact functions migration 142 binds to.
CREATE FUNCTION public.fn_setpoint_at(p_greenhouse_id text, p_param text, p_ts timestamp with time zone)
RETURNS double precision
LANGUAGE sql STABLE
AS $function$
    SELECT value
      FROM setpoint_changes
     WHERE greenhouse_id = p_greenhouse_id
       AND parameter = p_param
       AND ts <= p_ts
       AND (expired_at IS NULL OR expired_at > p_ts)
     ORDER BY ts DESC
     LIMIT 1;
$function$;

CREATE FUNCTION public.fn_equip_at(p_equip text, p_ts timestamp with time zone)
RETURNS boolean
LANGUAGE sql STABLE
AS $function$
    SELECT state FROM equipment_state
    WHERE equipment = p_equip AND ts <= p_ts
    ORDER BY ts DESC LIMIT 1;
$function$;

\i db/migrations/142-climate-action-log.sql
\i db/migrations/202-climate-action-scorecard-date-pushdown.sql

-- ── Fixtures ────────────────────────────────────────────────────────────────
-- day1 = two local days ago, day2 = yesterday. All instants are built as
-- Denver-local wall clock, matching the view's date bucketing.

CREATE TEMP TABLE fx AS
SELECT (now() AT TIME ZONE 'America/Denver')::date - 2 AS day1,
       (now() AT TIME ZONE 'America/Denver')::date - 1 AS day2;

-- Denver-local instant helper.
CREATE FUNCTION pg_temp.at_local(d date, hms interval) RETURNS timestamptz
LANGUAGE sql STABLE
AS $$ SELECT (d::timestamp + hms) AT TIME ZONE 'America/Denver' $$;

-- Setpoints: base bands ten days before day1; one expired temp_high override
-- active only during (day1 06:00, day1 06:30]; one mid-day2 temp_low change.
INSERT INTO public.setpoint_changes (ts, parameter, value, expired_at)
SELECT pg_temp.at_local(day1 - 10, interval '0'), p.parameter, p.value, NULL
FROM fx, (VALUES
    ('temp_low', 65.0), ('temp_high', 85.0),
    ('vpd_low', 0.4), ('vpd_high', 1.4)
) AS p(parameter, value);
INSERT INTO public.setpoint_changes (ts, parameter, value, expired_at)
SELECT pg_temp.at_local(day1, interval '6 hours'), 'temp_high', 99.0,
       pg_temp.at_local(day1, interval '6 hours 30 minutes')
FROM fx;
INSERT INTO public.setpoint_changes (ts, parameter, value, expired_at)
SELECT pg_temp.at_local(day2, interval '12 hours'), 'temp_low', 60.0, NULL
FROM fx;

-- Climate: one row every 5 minutes across day1 00:00 .. day2 24:00 local.
-- mister_water_today accumulates 0.05 gal per row within each local day.
INSERT INTO public.climate (ts, temp_avg, vpd_avg, mister_water_today)
SELECT pg_temp.at_local(d.day, make_interval(mins => 5 * g)),
       70.0, 0.8, 0.05 * g
FROM fx, LATERAL (VALUES (fx.day1), (fx.day2)) AS d(day),
     generate_series(0, 287) AS g;
-- NULL-sensor rows are skipped by the c0/c1 temp/vpd IS NOT NULL filters.
UPDATE public.climate c
SET temp_avg = NULL
FROM fx
WHERE c.ts = pg_temp.at_local(fx.day1, interval '9 hours');

-- Relay timeline.
INSERT INTO public.equipment_state (ts, equipment, state)
SELECT pg_temp.at_local(day1, interval '-6 hours'), e, false
FROM fx, unnest(ARRAY[
    'fog', 'mister_south', 'mister_west', 'mister_center',
    'vent', 'fan1', 'fan2'
]) AS e;
INSERT INTO public.equipment_state (ts, equipment, state)
SELECT pg_temp.at_local(day1, t), e, s FROM fx, (VALUES
    -- carry-in: vent ON from before midnight, OFF at 00:20 (shape 2)
    (interval '-1 hour',              'vent',          true),
    (interval '20 minutes',           'vent',          false),
    -- fog pulse 08:03:30 -> 08:07:30 (samples 08:04..08:07 of the 08:00 action)
    (interval '8 hours 3 minutes 30 seconds', 'fog',   true),
    (interval '8 hours 7 minutes 30 seconds', 'fog',   false),
    -- mister_center pulse 08:09 -> 08:11 (samples 08:09, 08:10)
    (interval '8 hours 9 minutes',    'mister_center', true),
    (interval '8 hours 11 minutes',   'mister_center', false),
    -- mister_south pulse crossing local midnight (shapes 2+3)
    (interval '23 hours 55 minutes',  'mister_south',  true),
    (interval '24 hours 5 minutes',   'mister_south',  false)
) AS v(t, e, s);
-- Shape 1: conflicting duplicate-timestamp pair at day1 08:02:00. The row
-- inserted FIRST (state=false) has the smaller ctid, so fn_equip_at's
-- (equipment, ts DESC) index scan — equal keys are TID-ordered — returns
-- FALSE. If either implementation resolved the tie the other way,
-- mister_center would read ON for samples 08:02..08:08 and the 08:00
-- action's wet duty would be 62.50, not the asserted 37.50.
INSERT INTO public.equipment_state (ts, equipment, state)
SELECT pg_temp.at_local(day1, interval '8 hours 2 minutes'), 'mister_center', false FROM fx;
INSERT INTO public.equipment_state (ts, equipment, state)
SELECT pg_temp.at_local(day1, interval '8 hours 2 minutes'), 'mister_center', true FROM fx;
-- day2: fan1 ON 10:00 -> 10:30 (the 10:05 action sees 100% vent duty).
INSERT INTO public.equipment_state (ts, equipment, state)
SELECT pg_temp.at_local(day2, interval '10 hours'), 'fan1', true FROM fx;
INSERT INTO public.equipment_state (ts, equipment, state)
SELECT pg_temp.at_local(day2, interval '10 hours 30 minutes'), 'fan1', false FROM fx;

-- Actions.
INSERT INTO public.climate_action_log
    (ts, greenhouse_id, climate_action, priority_axis,
     temp_band_error_f, vpd_band_error_kpa,
     wet_assist_block_reason, fog_block_reason)
SELECT pg_temp.at_local(day1, t), 'vallery', action, axis, terr, verr, wet_block, fog_block
FROM fx, (VALUES
    -- local-midnight boundary row; carry-in vent gives 100% vent duty
    (interval '0',                    'IDLE',        'temp', 0.5,  0.02, NULL,        'none'),
    -- after-lookup lands inside the expired-setpoint window (temp_high 99)
    (interval '5 hours 50 minutes',   'HEAT',        'temp', -1.5, -0.05, NULL,       NULL),
    -- after-lookup past the expiry instant (temp_high back to 85)
    (interval '7 hours',              'SAFETY_HEAT', 'safety', -2.5, 0.0, NULL,       NULL),
    -- the hand-checked duty row (fog pulse + tie pair + mister pulse)
    (interval '8 hours',              'VENT_COOL',   'temp', 3.0,  0.30, 'dew_margin', NULL),
    -- no climate row in [21:46, 21:49] -> NULL deltas inside the IDLE group
    (interval '21 hours 31 minutes',  'IDLE',        'vpd',  1.0,  0.10, 'dew_margin', 'occupancy'),
    -- last-of-day row whose sample grid crosses midnight (mister_south ON)
    (interval '23 hours 59 minutes 30 seconds', 'SEALED_FOG', 'vpd', 0.0, 0.15, NULL, 'none')
) AS v(t, action, axis, terr, verr, wet_block, fog_block);
INSERT INTO public.climate_action_log
    (ts, greenhouse_id, climate_action, priority_axis,
     temp_band_error_f, vpd_band_error_kpa,
     wet_assist_block_reason, fog_block_reason)
SELECT pg_temp.at_local(day2, t), gh, action, axis, terr, verr, NULL, fog_block
FROM fx, (VALUES
    -- midnight sibling of the crossing pulse (ON for samples 00:00..00:04,
    -- OFF at the 00:05 edge which applies at its own instant)
    (interval '0',                   'vallery'::text, 'SEALED_FOG',  'vpd',  0.0, 0.12, 'none'),
    -- NULL greenhouse_id row: NULL group key, NULL setpoints (shape 5)
    (interval '9 hours',             NULL,            'DEHUM_VENT',  'vpd',  0.2, 0.25, NULL),
    -- fan1 pulse fully covers the sample grid: 100.00 vent duty
    (interval '10 hours 5 minutes',  'vallery',       'SAFETY_COOL', 'safety', 4.0, 0.0, NULL),
    -- resolves the mid-day temp_low change (60, not 65)
    (interval '13 hours',            'vallery',       'VENT_COOL_MIST_ASSIST', 'temp', -6.0, 0.1, 'wet_cutoff')
) AS v(t, gh, action, axis, terr, verr, fog_block);
-- Shape 7: same local date, split by the rolling now()-14d cutoff. Only the
-- newer row may aggregate; a day-start lower bound (instead of GREATEST)
-- would include both and fail the per-date comparison below.
INSERT INTO public.climate_action_log
    (ts, greenhouse_id, climate_action, priority_axis, temp_band_error_f, vpd_band_error_kpa)
VALUES
    (now() - interval '14 days' + interval '5 minutes',
     'vallery', 'VENT_COOL_FOG_ASSIST', 'temp', 1.0, 0.0),
    (now() - interval '14 days' - interval '5 minutes',
     'vallery', 'VENT_COOL_FOG_ASSIST', 'temp', 1.0, 0.0);
-- Shape 8: far outside the window — invisible to both paths.
INSERT INTO public.climate_action_log
    (ts, greenhouse_id, climate_action, priority_axis, temp_band_error_f, vpd_band_error_kpa)
SELECT pg_temp.at_local(day1 - 18, interval '12 hours'), 'vallery', 'IDLE', 'temp', 1.0, 0.0
FROM fx;

-- Guard against fixture rot: the two rows must straddle now() - 14 days.
-- (They share a Denver-local date except when the test runs within five
-- minutes of that local midnight; the conditional hand-check below and the
-- per-date comparison handle both layouts.)
DO $$
BEGIN
    IF now() - interval '14 days' - interval '5 minutes' >= now() - interval '14 days'
       OR now() - interval '14 days' + interval '5 minutes' <= now() - interval '14 days' THEN
        RAISE EXCEPTION 'cutoff fixtures do not straddle now()-14d';
    END IF;
END $$;

-- ── 1. Per-date row equivalence: fn(d) == view slice, both directions, for
--       every date the view emits. ────────────────────────────────────────
DO $$
DECLARE
    d date;
    n_dates int := 0;
    fn_count bigint;
    view_count bigint;
    diff_count bigint;
BEGIN
    FOR d IN SELECT DISTINCT date FROM public.v_climate_action_daily_scorecard ORDER BY 1
    LOOP
        n_dates := n_dates + 1;
        SELECT count(*) INTO fn_count FROM public.fn_climate_action_daily_scorecard(d);
        SELECT count(*) INTO view_count
          FROM public.v_climate_action_daily_scorecard v WHERE v.date = d;
        IF fn_count <> view_count THEN
            RAISE EXCEPTION 'row-count mismatch on %: fn %, view %', d, fn_count, view_count;
        END IF;
        SELECT count(*) INTO diff_count FROM (
            (SELECT * FROM public.fn_climate_action_daily_scorecard(d)
             EXCEPT
             SELECT * FROM public.v_climate_action_daily_scorecard v WHERE v.date = d)
            UNION ALL
            (SELECT * FROM public.v_climate_action_daily_scorecard v WHERE v.date = d
             EXCEPT
             SELECT * FROM public.fn_climate_action_daily_scorecard(d))
        ) diffs;
        IF diff_count <> 0 THEN
            RAISE EXCEPTION 'fn/view divergence on % (% rows differ)', d, diff_count;
        END IF;
    END LOOP;
    -- day1, day2, and the cutoff-straddling date must all have been compared.
    IF n_dates < 3 THEN
        RAISE EXCEPTION 'expected >= 3 scorecard dates, saw %', n_dates;
    END IF;
END $$;

-- ── 2. Hand-computed cells (fixture-rot guard: these fail if the fixtures
--       stop exercising the shapes, even while fn == view). ───────────────
DO $$
DECLARE
    r record;
    fx_day1 date;
    fx_day2 date;
BEGIN
    SELECT day1, day2 INTO fx_day1, fx_day2 FROM fx;

    -- Tie pair + fog/mister pulses: 6 of 16 samples wet => 37.50.
    SELECT * INTO r FROM public.fn_climate_action_daily_scorecard(fx_day1)
     WHERE climate_action = 'VENT_COOL';
    IF r.avg_wet_relay_duty_pct <> 37.50 THEN
        RAISE EXCEPTION 'VENT_COOL wet duty % (expected 37.50 — same-ts tie must resolve to the first-inserted row)',
            r.avg_wet_relay_duty_pct;
    END IF;

    -- IDLE group: NULL-delta averaging, carry-in vent duty, water sum,
    -- blocked counters.
    SELECT * INTO r FROM public.fn_climate_action_daily_scorecard(fx_day1)
     WHERE climate_action = 'IDLE';
    IF r.decisions <> 2
       OR r.avg_abs_temp_error_before_f <> 0.75
       OR r.avg_abs_vpd_error_before_kpa <> 0.060
       OR r.avg_temp_abs_error_delta_15m_f <> -0.50
       OR r.avg_vpd_abs_error_delta_15m_kpa <> -0.020
       OR r.avg_wet_relay_duty_pct <> 0.00
       OR r.avg_vent_fan_duty_pct <> 50.00
       OR r.mister_water_delta_gal <> 0.150
       OR r.wet_blocked_decisions <> 1
       OR r.fog_blocked_decisions <> 1 THEN
        RAISE EXCEPTION 'IDLE group contract mismatch: %', row_to_json(r);
    END IF;

    -- Expired setpoint: HEAT after-lookup at 06:05 sees the 99 override
    -- (delta -1.50); SAFETY_HEAT at 07:15 sees it expired (85 band, temp 70
    -- inside => delta -2.50).
    SELECT * INTO r FROM public.fn_climate_action_daily_scorecard(fx_day1)
     WHERE climate_action = 'HEAT';
    IF r.avg_temp_abs_error_delta_15m_f <> -1.50 THEN
        RAISE EXCEPTION 'HEAT expired-setpoint delta % (expected -1.50)',
            r.avg_temp_abs_error_delta_15m_f;
    END IF;
    SELECT * INTO r FROM public.fn_climate_action_daily_scorecard(fx_day1)
     WHERE climate_action = 'SAFETY_HEAT';
    IF r.avg_temp_abs_error_delta_15m_f <> -2.50 THEN
        RAISE EXCEPTION 'SAFETY_HEAT post-expiry delta % (expected -2.50)',
            r.avg_temp_abs_error_delta_15m_f;
    END IF;

    -- Midnight-crossing pulse: day1 tail 6/16, day2 head 5/16 (the OFF edge
    -- applies at its own 00:05:00 instant).
    SELECT * INTO r FROM public.fn_climate_action_daily_scorecard(fx_day1)
     WHERE climate_action = 'SEALED_FOG';
    IF r.avg_wet_relay_duty_pct <> 37.50 THEN
        RAISE EXCEPTION 'day1 SEALED_FOG wet duty % (expected 37.50)', r.avg_wet_relay_duty_pct;
    END IF;
    SELECT * INTO r FROM public.fn_climate_action_daily_scorecard(fx_day2)
     WHERE climate_action = 'SEALED_FOG';
    IF r.avg_wet_relay_duty_pct <> 31.25 THEN
        RAISE EXCEPTION 'day2 SEALED_FOG wet duty % (expected 31.25)', r.avg_wet_relay_duty_pct;
    END IF;

    -- Full-coverage fan pulse.
    SELECT * INTO r FROM public.fn_climate_action_daily_scorecard(fx_day2)
     WHERE climate_action = 'SAFETY_COOL';
    IF r.avg_vent_fan_duty_pct <> 100.00 THEN
        RAISE EXCEPTION 'SAFETY_COOL vent duty % (expected 100.00)', r.avg_vent_fan_duty_pct;
    END IF;

    -- NULL greenhouse_id group survives with NULL setpoint-driven deltas.
    SELECT * INTO r FROM public.fn_climate_action_daily_scorecard(fx_day2)
     WHERE climate_action = 'DEHUM_VENT';
    IF r.greenhouse_id IS NOT NULL OR r.decisions <> 1
       OR r.avg_temp_abs_error_delta_15m_f IS NOT NULL THEN
        RAISE EXCEPTION 'NULL-greenhouse group mismatch: %', row_to_json(r);
    END IF;

    -- Mid-day setpoint change: temp 70 vs [60, 85] => after error 0,
    -- delta -6.00.
    SELECT * INTO r FROM public.fn_climate_action_daily_scorecard(fx_day2)
     WHERE climate_action = 'VENT_COOL_MIST_ASSIST';
    IF r.avg_temp_abs_error_delta_15m_f <> -6.00 THEN
        RAISE EXCEPTION 'VENT_COOL_MIST_ASSIST delta % (expected -6.00)',
            r.avg_temp_abs_error_delta_15m_f;
    END IF;

    -- Rolling-cutoff date: only the row inside now()-14d aggregates. A
    -- day-start lower bound instead of GREATEST(day-start, now()-14d) would
    -- report 2 decisions here.
    IF ((now() - interval '14 days' - interval '5 minutes') AT TIME ZONE 'America/Denver')::date
       = ((now() - interval '14 days' + interval '5 minutes') AT TIME ZONE 'America/Denver')::date
    THEN
        SELECT * INTO r FROM public.fn_climate_action_daily_scorecard(
            ((now() - interval '14 days' + interval '5 minutes') AT TIME ZONE 'America/Denver')::date)
         WHERE climate_action = 'VENT_COOL_FOG_ASSIST';
        IF r.decisions <> 1 THEN
            RAISE EXCEPTION 'cutoff date decisions % (expected 1: GREATEST(now()-14d) bound)',
                r.decisions;
        END IF;
    END IF;
END $$;

-- ── 3. Empty shapes: no rows for a future day or an aged-out day. ─────────
DO $$
DECLARE
    fx_day1 date;
    n bigint;
BEGIN
    SELECT day1 INTO fx_day1 FROM fx;
    SELECT count(*) INTO n FROM public.fn_climate_action_daily_scorecard(fx_day1 + 30);
    IF n <> 0 THEN
        RAISE EXCEPTION 'future day returned % rows', n;
    END IF;
    SELECT count(*) INTO n FROM public.fn_climate_action_daily_scorecard(fx_day1 - 18);
    IF n <> 0 THEN
        RAISE EXCEPTION 'aged-out day returned % rows (14-day window not enforced)', n;
    END IF;
END $$;

SELECT date, greenhouse_id, climate_action, decisions,
       avg_wet_relay_duty_pct, avg_vent_fan_duty_pct
FROM fx, LATERAL public.fn_climate_action_daily_scorecard(fx.day1)
ORDER BY climate_action;

ROLLBACK;
