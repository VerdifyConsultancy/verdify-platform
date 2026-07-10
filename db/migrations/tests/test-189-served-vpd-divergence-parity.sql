-- Representative rollback fixture for migration 189.
-- Proves the raw-anchor false near-miss disappears while real served/readback
-- drift remains visible.  Intended for a disposable PostgreSQL database.

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE public.setpoint_snapshot (
    ts timestamptz NOT NULL,
    parameter text NOT NULL,
    value double precision NOT NULL,
    greenhouse_id text NOT NULL DEFAULT 'vallery'
);

CREATE TABLE public.climate (
    ts timestamptz NOT NULL,
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    house_temp_target_f double precision,
    house_vpd_target double precision
);

CREATE FUNCTION public.fn_band_setpoints(timestamptz)
RETURNS TABLE(
    temp_low double precision,
    temp_high double precision,
    vpd_low double precision,
    vpd_high double precision,
    temp_target double precision,
    vpd_target double precision
)
LANGUAGE sql STABLE ROWS 1
AS $$ SELECT 70.0, 80.0, 0.82, 1.31, 75.0, 0.90 $$;

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
AS $$ SELECT 0.82, 1.31, 0.90, 0.90, 0.90, 0.90,
             0.90, 0.90, 0.90, 0.50, 1.10, 0.55, 0.20 $$;

INSERT INTO public.setpoint_snapshot(ts, parameter, value) VALUES
    (now() - interval '1 minute', 'temp_low', 70.02),
    (now() - interval '1 minute', 'temp_high', 79.98),
    (now() - interval '1 minute', 'vpd_low', 0.51),
    (now() - interval '1 minute', 'vpd_high', 1.09);

INSERT INTO public.climate(ts, house_temp_target_f, house_vpd_target)
VALUES (now() - interval '1 minute', 75.01, 0.91);

\i db/migrations/189-served-vpd-divergence-parity.sql

DO $$
DECLARE
    raw_anchor_near_miss double precision;
    served_diff double precision;
BEGIN
    SELECT abs(v.device_vpd_low - b.vpd_low), abs(v.vpd_low_diff)
      INTO raw_anchor_near_miss, served_diff
      FROM public.v_band_device_divergence v
      CROSS JOIN LATERAL public.fn_band_setpoints(now()) b;

    IF raw_anchor_near_miss < 0.30 THEN
        RAISE EXCEPTION 'fixture invalid: raw-anchor near-miss should exceed 0.30, got %',
            raw_anchor_near_miss;
    END IF;
    IF served_diff > 0.02 THEN
        RAISE EXCEPTION 'false near-miss remains after served-band repoint: %', served_diff;
    END IF;
END $$;

SELECT
    round(abs(v.device_vpd_low - b.vpd_low)::numeric, 3)
        AS raw_anchor_false_near_miss_kpa,
    round(abs(v.vpd_low_diff)::numeric, 3) AS served_low_diff_kpa
FROM public.v_band_device_divergence v
CROSS JOIN LATERAL public.fn_band_setpoints(now()) b;

-- A newer readback that genuinely differs from the served envelope must still
-- trip the view rather than being hidden by the repoint.
INSERT INTO public.setpoint_snapshot(ts, parameter, value) VALUES
    (now(), 'vpd_low', 0.90),
    (now(), 'vpd_high', 1.50);

DO $$
DECLARE
    actual double precision;
BEGIN
    SELECT max_vpd_abs_diff INTO actual
      FROM public.v_band_device_divergence;
    IF actual < 0.39 THEN
        RAISE EXCEPTION 'true served/readback drift was not detected: %', actual;
    END IF;
END $$;

SELECT
    round(max_vpd_abs_diff::numeric, 3) AS real_served_readback_drift_kpa
FROM public.v_band_device_divergence;

ROLLBACK;
