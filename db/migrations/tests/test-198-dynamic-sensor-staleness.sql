-- Representative rollback fixture for migration 198.
-- Proves a registry row for a column OUTSIDE the old hardcoded map (e.g.
-- solar_phase) reads fresh when the column carries data, that a NULL-only
-- column still reads stale, and that expected-interval staleness still trips.
-- Intended for a disposable PostgreSQL database.

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE public.climate (
    ts timestamptz NOT NULL,
    temp_avg double precision,
    solar_phase double precision,
    leaf_wetness_north double precision
);

CREATE TABLE public.equipment_state (
    ts timestamptz NOT NULL,
    equipment text NOT NULL,
    state boolean NOT NULL
);

CREATE TABLE public.system_state (
    ts timestamptz NOT NULL,
    entity text NOT NULL,
    value text
);

CREATE TABLE public.diagnostics (
    ts timestamptz NOT NULL
);

CREATE TABLE public.sensor_registry (
    sensor_id text PRIMARY KEY,
    type text,
    zone text,
    expected_interval_s integer NOT NULL,
    source_table text NOT NULL,
    source_column text NOT NULL,
    active boolean NOT NULL DEFAULT true
);

INSERT INTO public.sensor_registry
    (sensor_id, type, zone, expected_interval_s, source_table, source_column) VALUES
    ('climate.solar_phase',        'derived', NULL, 60, 'climate', 'solar_phase'),
    ('climate.temp_avg',           'climate', NULL, 60, 'climate', 'temp_avg'),
    ('climate.leaf_wetness_north', 'leaf',    NULL, 60, 'climate', 'leaf_wetness_north'),
    ('climate.no_such_column',     'ghost',   NULL, 60, 'climate', 'no_such_column');

-- solar_phase + temp_avg fresh; leaf_wetness_north permanently NULL (dead probe).
INSERT INTO public.climate (ts, temp_avg, solar_phase, leaf_wetness_north) VALUES
    (now() - interval '3 minutes', 75.0, 0.41, NULL),
    (now() - interval '1 minute',  75.2, 0.42, NULL);

\i db/migrations/198-dynamic-sensor-staleness.sql

DO $$
DECLARE
    v_stale boolean;
    v_ratio numeric;
BEGIN
    -- 1. The zombie class: solar_phase is OUTSIDE the old hardcoded map but
    --    carries data — must now read fresh.
    SELECT is_stale INTO v_stale FROM public.v_sensor_staleness
     WHERE sensor_id = 'climate.solar_phase';
    IF v_stale THEN
        RAISE EXCEPTION 'solar_phase reads stale despite live data (zombie class persists)';
    END IF;

    -- 2. Previously-mapped columns keep working.
    SELECT is_stale INTO v_stale FROM public.v_sensor_staleness
     WHERE sensor_id = 'climate.temp_avg';
    IF v_stale THEN
        RAISE EXCEPTION 'temp_avg regression: reads stale with fresh data';
    END IF;

    -- 3. A NULL-only column still reads stale (dead probe), ratio NULL.
    SELECT is_stale, staleness_ratio INTO v_stale, v_ratio
      FROM public.v_sensor_staleness WHERE sensor_id = 'climate.leaf_wetness_north';
    IF NOT v_stale OR v_ratio IS NOT NULL THEN
        RAISE EXCEPTION 'dead-probe detection broken: stale=% ratio=%', v_stale, v_ratio;
    END IF;

    -- 4. A registry row whose column does not exist reads stale (drift signal),
    --    not an error.
    SELECT is_stale INTO v_stale FROM public.v_sensor_staleness
     WHERE sensor_id = 'climate.no_such_column';
    IF NOT v_stale THEN
        RAISE EXCEPTION 'nonexistent-column registry drift must read stale';
    END IF;
END $$;

-- 5. Interval staleness still trips: age the data beyond 2x interval.
UPDATE public.climate SET ts = ts - interval '10 minutes';

DO $$
DECLARE
    v_stale boolean;
BEGIN
    SELECT is_stale INTO v_stale FROM public.v_sensor_staleness
     WHERE sensor_id = 'climate.solar_phase';
    IF NOT v_stale THEN
        RAISE EXCEPTION 'interval staleness no longer trips after dynamic rewrite';
    END IF;
END $$;

SELECT sensor_id, is_stale, staleness_ratio FROM public.v_sensor_staleness ORDER BY sensor_id;

ROLLBACK;
