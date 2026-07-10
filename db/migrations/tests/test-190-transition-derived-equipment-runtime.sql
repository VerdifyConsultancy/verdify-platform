-- Hand-counted raw-transition fixture for migration 190.

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE public.equipment_state (
    ts timestamptz NOT NULL,
    equipment text NOT NULL,
    state boolean NOT NULL,
    greenhouse_id text DEFAULT 'vallery'
);

\i db/migrations/190-transition-derived-equipment-runtime.sql

-- 2026-01-16 America/Denver is MST (UTC-7).  mister_center carries ON
-- across midnight, closes at 00:02, then has a 30-second pulse and a six-minute
-- pulse.  The duplicate OFF at 00:02 must not add a transition.
INSERT INTO public.equipment_state(ts, equipment, state) VALUES
    ('2026-01-16 06:50:00+00', 'mister_center', false),
    ('2026-01-16 06:58:00+00', 'mister_center', true),
    ('2026-01-16 07:02:00+00', 'mister_center', false),
    ('2026-01-16 07:02:00+00', 'mister_center', false),
    ('2026-01-16 08:00:00+00', 'mister_center', true),
    ('2026-01-16 08:00:30+00', 'mister_center', false),
    ('2026-01-16 09:00:00+00', 'mister_center', true),
    ('2026-01-16 09:06:00+00', 'mister_center', false);

-- An open fog pulse crosses the end boundary and is later confirmed OFF.
INSERT INTO public.equipment_state(ts, equipment, state) VALUES
    ('2026-01-16 06:00:00+00', 'fog', false),
    ('2026-01-16 10:00:00+00', 'fog', true),
    ('2026-01-17 07:10:00+00', 'fog', false);

-- Independent light circuits and representative fans/mister source rows.
INSERT INTO public.equipment_state(ts, equipment, state) VALUES
    ('2026-01-16 06:00:00+00', 'grow_light_main', false),
    ('2026-01-16 14:00:00+00', 'grow_light_main', true),
    ('2026-01-16 16:00:00+00', 'grow_light_main', false),
    ('2026-01-16 06:00:00+00', 'grow_light_grow', false),
    ('2026-01-16 15:00:00+00', 'grow_light_grow', true),
    ('2026-01-16 18:00:00+00', 'grow_light_grow', false),
    ('2026-01-16 06:00:00+00', 'fan1', false),
    ('2026-01-16 12:00:00+00', 'fan1', true),
    ('2026-01-16 12:04:00+00', 'fan1', false),
    ('2026-01-16 06:00:00+00', 'fan2', false),
    ('2026-01-16 13:00:00+00', 'fan2', true),
    ('2026-01-16 13:00:00+00', 'fan2', false),
    ('2026-01-16 06:00:00+00', 'mister_south', false),
    ('2026-01-16 11:00:00+00', 'mister_south', true),
    ('2026-01-16 11:01:00+00', 'mister_south', false);

DO $$
DECLARE
    r record;
BEGIN
    SELECT * INTO r
      FROM public.v_equipment_runtime_daily
     WHERE day = '2026-01-16' AND equipment = 'mister_center';

    IF r.on_minutes <> 8.5 OR r.starts <> 2 OR r.cycles <> 2 THEN
        RAISE EXCEPTION 'mister hand count mismatch: runtime %, starts %, cycles %',
            r.on_minutes, r.starts, r.cycles;
    END IF;
    IF r.cycles_under_1m <> 1 OR r.cycles_5m_to_15m <> 1 THEN
        RAISE EXCEPTION 'mister short-cycle buckets wrong: <1m %, 5-15m %',
            r.cycles_under_1m, r.cycles_5m_to_15m;
    END IF;
    IF r.same_timestamp_duplicate_rows <> 1
       OR r.peak_transitions_per_hour <> 2
       OR NOT r.is_deploy_gate_eligible THEN
        RAISE EXCEPTION 'mister quality mismatch: dup %, peak %, eligible %',
            r.same_timestamp_duplicate_rows, r.peak_transitions_per_hour,
            r.is_deploy_gate_eligible;
    END IF;

    SELECT * INTO r FROM public.v_equipment_runtime_daily
     WHERE day = '2026-01-16' AND equipment = 'fog';
    IF r.on_minutes <> 1260.0 OR r.starts <> 1 OR NOT r.open_at_end
       OR NOT r.is_deploy_gate_eligible THEN
        RAISE EXCEPTION 'open/carry fog mismatch: runtime %, starts %, open %, eligible %',
            r.on_minutes, r.starts, r.open_at_end, r.is_deploy_gate_eligible;
    END IF;

    SELECT * INTO r FROM public.v_equipment_runtime_daily
     WHERE day = '2026-01-17' AND equipment = 'fog';
    IF r.on_minutes <> 10.0 OR r.starts <> 0 OR r.start_state IS DISTINCT FROM true
       OR r.open_at_end OR NOT r.is_deploy_gate_eligible THEN
        RAISE EXCEPTION 'next-day carry mismatch: runtime %, starts %, start %, open %, eligible %',
            r.on_minutes, r.starts, r.start_state, r.open_at_end,
            r.is_deploy_gate_eligible;
    END IF;

    SELECT * INTO r FROM public.v_equipment_runtime_daily
     WHERE day = '2026-01-16' AND equipment = 'grow_light_main';
    IF r.on_minutes <> 120.0 OR r.starts <> 1 THEN
        RAISE EXCEPTION 'main light hand count mismatch';
    END IF;
    SELECT * INTO r FROM public.v_equipment_runtime_daily
     WHERE day = '2026-01-16' AND equipment = 'grow_light_grow';
    IF r.on_minutes <> 180.0 OR r.starts <> 1 THEN
        RAISE EXCEPTION 'grow light hand count mismatch';
    END IF;

    SELECT * INTO r FROM public.v_equipment_runtime_daily
     WHERE day = '2026-01-16' AND equipment = 'fan1';
    IF r.short_cycles_under_5m <> 1 THEN
        RAISE EXCEPTION 'fan1 four-minute cycle not bucketed';
    END IF;
    SELECT * INTO r FROM public.v_equipment_runtime_daily
     WHERE day = '2026-01-16' AND equipment = 'fan2';
    IF r.conflicting_timestamp_count <> 1 OR r.is_deploy_gate_eligible THEN
        RAISE EXCEPTION 'fan2 conflicting timestamp not quarantined';
    END IF;
END $$;

SELECT
    day,
    equipment,
    on_minutes,
    starts,
    short_cycles_under_5m,
    cycles_5m_to_15m,
    open_at_end,
    peak_transitions_per_hour,
    same_timestamp_duplicate_rows,
    conflicting_timestamp_count,
    quality,
    is_deploy_gate_eligible
FROM public.v_equipment_runtime_daily
WHERE day = '2026-01-16'
  AND equipment IN (
      'mister_center', 'mister_south', 'fog', 'fan1', 'fan2',
      'grow_light_main', 'grow_light_grow'
  )
ORDER BY equipment;

-- Current local day must be partial/ineligible; a future event must not create
-- a future comparison row.
INSERT INTO public.equipment_state(ts, equipment, state) VALUES
    (now() - interval '1 hour', 'vent', false),
    (now() - interval '30 minutes', 'vent', true),
    (now() + interval '2 days', 'vent', false);

DO $$
DECLARE
    current_day date := (now() AT TIME ZONE 'America/Denver')::date;
    partial_count int;
    future_count int;
BEGIN
    SELECT count(*) INTO partial_count
      FROM public.v_equipment_runtime_daily
     WHERE equipment = 'vent' AND day = current_day
       AND quality = 'partial_day' AND NOT is_deploy_gate_eligible;
    SELECT count(*) INTO future_count
      FROM public.v_equipment_runtime_daily
     WHERE equipment = 'vent' AND day > current_day;
    IF partial_count <> 1 OR future_count <> 0 THEN
        RAISE EXCEPTION 'partial/future exclusion mismatch: partial %, future %',
            partial_count, future_count;
    END IF;
END $$;

SELECT
    day,
    equipment,
    quality,
    is_complete_day,
    is_deploy_gate_eligible,
    open_at_end
FROM public.v_equipment_runtime_daily
WHERE equipment = 'vent'
ORDER BY day DESC
LIMIT 1;

ROLLBACK;
