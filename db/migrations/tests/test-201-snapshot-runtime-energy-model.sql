-- Hand-counted contract for the #389 snapshot read path (migrations 199+200+201).
--
-- Proves, on a recent completed local day (inside the migration-199 45-day
-- window):
--   1. mv_equipment_runtime_daily starts equal an INDEPENDENT hand count of
--      TRUE rising edges computed straight from equipment_state (mister_center);
--   2. the materialized snapshot is row-identical to the live view for a
--      completed day (the outcome_kpi fast path serves the same truth);
--   3. current-local-day rows are partial/ineligible and future-dated rows do
--      not exist in the snapshot;
--   4. the migration-201 v_runtime_energy_daily (joined to the snapshot)
--      reproduces a hand-computed modeled_kwh for the completed day and stays
--      NULL / non-scorable for the current partial day.
--
-- Fixture days are now()-relative because migration 199 bounds the day series
-- to a rolling 45-day window; fixed historical dates would fall outside it.

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE public.equipment_state (
    ts timestamptz NOT NULL,
    equipment text NOT NULL,
    state boolean NOT NULL,
    greenhouse_id text DEFAULT 'vallery'
);

CREATE TABLE public.daily_summary (
    date date NOT NULL,
    greenhouse_id text NOT NULL DEFAULT 'vallery'
);

CREATE TABLE public.equipment (
    id integer PRIMARY KEY,
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    slug text NOT NULL,
    is_active boolean NOT NULL DEFAULT true
);

CREATE TABLE public.resource_coefficients (
    id integer PRIMARY KEY,
    equipment_id integer NOT NULL,
    resource_kind text NOT NULL,
    nominal_value double precision NOT NULL,
    lower_bound double precision NOT NULL,
    upper_bound double precision NOT NULL,
    coefficient_source text NOT NULL DEFAULT 'nameplate',
    revision text NOT NULL DEFAULT 'test-r1',
    evidence_ref text NOT NULL DEFAULT 'test-fixture',
    unit text NOT NULL DEFAULT 'W',
    valid_from timestamptz NOT NULL DEFAULT '2024-01-01 00:00:00+00',
    valid_to timestamptz
);

\i db/migrations/199-bounded-equipment-runtime-view.sql
\i db/migrations/200-materialized-equipment-runtime.sql
\i db/migrations/201-snapshot-runtime-energy-model.sql

-- day1 = two local days ago (completed, inside the 45-day window).
-- Carry anchors the evening before give every circuit a known day-start state.
INSERT INTO public.equipment_state(ts, equipment, state)
SELECT (((now() AT TIME ZONE 'America/Denver')::date - 3)::timestamp
            + interval '18 hours') AT TIME ZONE 'America/Denver',
       e,
       false
FROM unnest(ARRAY[
    'fan1', 'fan2', 'heat1', 'fog', 'vent',
    'grow_light_main', 'grow_light_grow', 'mister_center'
]) AS e;

-- One clean pulse per energy circuit on day1 (offsets are seconds after local
-- midnight), plus two mister_center pulses for the rising-edge hand count:
--   fan1  08:00-09:00 (60 min)   fan2 09:00-09:30 (30 min)
--   heat1 05:00-05:12 (12 min)   fog  10:00-10:06 (6 min)
--   vent  11:00-12:00 (60 min)   glm  06:00-07:00 (60 min)
--   glg   06:00-07:30 (90 min)
--   mister_center 08:00-08:04 (4 min) and 09:00-09:00:30 (0.5 min)
CREATE TEMP TABLE fixture_pulses (equipment text, start_s int, end_s int);
INSERT INTO fixture_pulses VALUES
    ('fan1', 28800, 32400),
    ('fan2', 32400, 34200),
    ('heat1', 18000, 18720),
    ('fog', 36000, 36360),
    ('vent', 39600, 43200),
    ('grow_light_main', 21600, 25200),
    ('grow_light_grow', 21600, 27000),
    ('mister_center', 28800, 29040),
    ('mister_center', 32400, 32430);

INSERT INTO public.equipment_state(ts, equipment, state)
SELECT (((now() AT TIME ZONE 'America/Denver')::date - 2)::timestamp
            + make_interval(secs => start_s)) AT TIME ZONE 'America/Denver',
       equipment, true
FROM fixture_pulses
UNION ALL
SELECT (((now() AT TIME ZONE 'America/Denver')::date - 2)::timestamp
            + make_interval(secs => end_s)) AT TIME ZONE 'America/Denver',
       equipment, false
FROM fixture_pulses;

-- Current-day partial evidence and one future-dated row (must never surface
-- as a comparison day).
INSERT INTO public.equipment_state(ts, equipment, state) VALUES
    (now() - interval '30 minutes', 'vent', true),
    (now() + interval '2 days', 'vent', false);

INSERT INTO public.daily_summary(date, greenhouse_id) VALUES
    ((now() AT TIME ZONE 'America/Denver')::date - 2, 'vallery'),
    ((now() AT TIME ZONE 'America/Denver')::date, 'vallery');

INSERT INTO public.equipment(id, slug) VALUES
    (1, 'fan1'), (2, 'fan2'), (3, 'heat1'), (4, 'fog'),
    (5, 'vent'), (6, 'grow_light_main'), (7, 'grow_light_grow');

-- Equal bounds => no uncertainty => scoring-eligible coefficients.
INSERT INTO public.resource_coefficients
    (id, equipment_id, resource_kind, nominal_value, lower_bound, upper_bound)
VALUES
    (1, 1, 'electric_watts', 200, 200, 200),
    (2, 2, 'electric_watts', 200, 200, 200),
    (3, 3, 'electric_watts', 100, 100, 100),
    (4, 4, 'electric_watts', 50, 50, 50),
    (5, 5, 'electric_watts', 40, 40, 40),
    (6, 6, 'electric_watts', 600, 600, 600),
    (7, 7, 'electric_watts', 600, 600, 600);

-- The snapshot was created WITH DATA before the fixture rows existed; bring it
-- current the same way the 10-minute refresh cron does.
REFRESH MATERIALIZED VIEW public.mv_equipment_runtime_daily;

-- 1. Snapshot starts == independent hand count of TRUE rising edges.
DO $$
DECLARE
    day1 date := (now() AT TIME ZONE 'America/Denver')::date - 2;
    snap record;
    hand_edges bigint;
BEGIN
    SELECT * INTO snap
      FROM public.mv_equipment_runtime_daily
     WHERE day = day1 AND equipment = 'mister_center';

    WITH collapsed AS (
        SELECT ts, bool_or(state) AS state
        FROM public.equipment_state
        WHERE equipment = 'mister_center'
        GROUP BY ts
    ), lagged AS (
        SELECT ts, state,
               lag(state) OVER (ORDER BY ts) AS prev_state
        FROM collapsed
    )
    SELECT count(*) INTO hand_edges
      FROM lagged
     WHERE state IS TRUE AND prev_state IS FALSE
       AND (ts AT TIME ZONE 'America/Denver')::date = day1;

    IF snap.starts IS DISTINCT FROM hand_edges OR hand_edges <> 2 THEN
        RAISE EXCEPTION 'snapshot starts % != hand-counted rising edges % (expected 2)',
            snap.starts, hand_edges;
    END IF;
    IF snap.on_minutes <> 4.5 OR NOT snap.is_deploy_gate_eligible
       OR NOT snap.is_complete_day THEN
        RAISE EXCEPTION 'mister_center snapshot contract mismatch: %',
            row_to_json(snap);
    END IF;
END $$;

-- 2. For the completed day the snapshot is row-identical to the live view.
DO $$
DECLARE
    day1 date := (now() AT TIME ZONE 'America/Denver')::date - 2;
    diff_count bigint;
BEGIN
    SELECT count(*) INTO diff_count FROM (
        (SELECT equipment, on_minutes, starts, cycles, short_cycles_under_5m,
                open_pulses_at_cutoff, is_complete_day, is_deploy_gate_eligible,
                quality
           FROM public.mv_equipment_runtime_daily WHERE day = day1
         EXCEPT
         SELECT equipment, on_minutes, starts, cycles, short_cycles_under_5m,
                open_pulses_at_cutoff, is_complete_day, is_deploy_gate_eligible,
                quality
           FROM public.v_equipment_runtime_daily WHERE day = day1)
        UNION ALL
        (SELECT equipment, on_minutes, starts, cycles, short_cycles_under_5m,
                open_pulses_at_cutoff, is_complete_day, is_deploy_gate_eligible,
                quality
           FROM public.v_equipment_runtime_daily WHERE day = day1
         EXCEPT
         SELECT equipment, on_minutes, starts, cycles, short_cycles_under_5m,
                open_pulses_at_cutoff, is_complete_day, is_deploy_gate_eligible,
                quality
           FROM public.mv_equipment_runtime_daily WHERE day = day1)
    ) diffs;
    IF diff_count <> 0 THEN
        RAISE EXCEPTION 'snapshot diverges from live view on completed day (% rows)',
            diff_count;
    END IF;
END $$;

-- 3. Partial current day is flagged/ineligible; future-dated rows are absent.
DO $$
DECLARE
    current_day date := (now() AT TIME ZONE 'America/Denver')::date;
    partial_ok int;
    future_count int;
BEGIN
    SELECT count(*) INTO partial_ok
      FROM public.mv_equipment_runtime_daily
     WHERE equipment = 'vent' AND day = current_day
       AND quality = 'partial_day'
       AND NOT is_complete_day
       AND NOT is_deploy_gate_eligible;
    SELECT count(*) INTO future_count
      FROM public.mv_equipment_runtime_daily
     WHERE day > current_day;
    IF partial_ok <> 1 OR future_count <> 0 THEN
        RAISE EXCEPTION 'partial/future snapshot exclusion mismatch: partial %, future %',
            partial_ok, future_count;
    END IF;
END $$;

-- 4. Migration-201 energy model: hand-computed kWh on the completed day,
--    NULL / non-scorable on the current partial day.
--    60m*200W + 30m*200W + 12m*100W + 6m*50W + 60m*40W + 60m*600W + 90m*600W
--    = 0.2 + 0.1 + 0.02 + 0.005 + 0.04 + 0.6 + 0.9 = 1.865 kWh
DO $$
DECLARE
    day1 date := (now() AT TIME ZONE 'America/Denver')::date - 2;
    current_day date := (now() AT TIME ZONE 'America/Denver')::date;
    r record;
BEGIN
    SELECT * INTO r FROM public.v_runtime_energy_daily
     WHERE date = day1 AND greenhouse_id = 'vallery';
    IF r.modeled_kwh IS NULL OR abs(r.modeled_kwh - 1.865) > 1e-9
       OR r.model_quality <> 'ok' OR NOT r.available_for_scoring THEN
        RAISE EXCEPTION 'completed-day energy model mismatch: kwh %, quality %, scoring %',
            r.modeled_kwh, r.model_quality, r.available_for_scoring;
    END IF;

    SELECT * INTO r FROM public.v_runtime_energy_daily
     WHERE date = current_day AND greenhouse_id = 'vallery';
    IF r.modeled_kwh IS NOT NULL OR r.model_quality <> 'incomplete_runtime'
       OR r.available_for_scoring THEN
        RAISE EXCEPTION 'partial-day energy model must be excluded: kwh %, quality %, scoring %',
            r.modeled_kwh, r.model_quality, r.available_for_scoring;
    END IF;
END $$;

SELECT day, equipment, on_minutes, starts, quality, is_deploy_gate_eligible
FROM public.mv_equipment_runtime_daily
WHERE day = (now() AT TIME ZONE 'America/Denver')::date - 2
ORDER BY equipment;

ROLLBACK;
