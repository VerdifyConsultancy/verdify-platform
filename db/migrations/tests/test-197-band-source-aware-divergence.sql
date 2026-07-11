-- Representative rollback fixture for migration 197.
-- Proves the post-OTA false alarm (legacy wide-default scalars vs DB band)
-- disappears in onchip_curve mode, that a REAL on-chip curve skew still
-- trips the view, and that legacy mode retains the migration-189 behavior.
-- Intended for a disposable PostgreSQL database.

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

CREATE TABLE public.diagnostics (
    ts timestamptz NOT NULL,
    band_source text
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
AS $$ SELECT 72.25, 82.25, 0.85, 1.28, 77.25, 1.03 $$;

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
AS $$ SELECT 0.85, 1.28, 1.00, 1.00, 1.00, 1.00,
             1.00, 1.00, 1.00, 0.64, 1.19, 0.55, 0.20 $$;

-- The 2026-07-10 post-OTA prod reality: legacy scalars cold-started to the
-- wide restore_value:no defaults, on-chip effective band tracking the DB
-- anchor curve exactly, band_source reporting onchip_curve.
INSERT INTO public.setpoint_snapshot(ts, parameter, value) VALUES
    (now() - interval '1 minute', 'temp_low', 40.0),
    (now() - interval '1 minute', 'temp_high', 95.0),
    (now() - interval '1 minute', 'vpd_low', 0.35),
    (now() - interval '1 minute', 'vpd_high', 2.80),
    (now() - interval '30 seconds', 'band_house_temp_low', 72.25),
    (now() - interval '30 seconds', 'band_house_temp_high', 82.25),
    (now() - interval '30 seconds', 'band_house_vpd_low', 0.847),
    (now() - interval '30 seconds', 'band_house_vpd_high', 1.278);

INSERT INTO public.climate(ts, house_temp_target_f, house_vpd_target)
VALUES (now() - interval '1 minute', 77.26, 1.03);

INSERT INTO public.diagnostics(ts, band_source)
VALUES (now() - interval '30 seconds', 'onchip_curve');

\i db/migrations/197-band-source-aware-divergence.sql

-- 1. onchip_curve mode: the alert-7803 false alarm must be gone (effective
--    band matches DB) even though the legacy scalars are at 40/95.
DO $$
DECLARE
    v_temp double precision;
    v_vpd double precision;
    v_source text;
BEGIN
    SELECT max_temp_abs_diff, max_vpd_abs_diff, band_source
      INTO v_temp, v_vpd, v_source
      FROM public.v_band_device_divergence;

    IF v_source <> 'onchip_curve' THEN
        RAISE EXCEPTION 'expected onchip_curve mode, got %', v_source;
    END IF;
    IF v_temp > 3.0 OR v_vpd > 0.40 THEN
        RAISE EXCEPTION
            'false alarm persists in onchip_curve mode: dT=% dVPD=% (alert-7803 shape)',
            v_temp, v_vpd;
    END IF;
END $$;

-- 2. A REAL on-chip curve skew must still trip the thresholds.
INSERT INTO public.setpoint_snapshot(ts, parameter, value) VALUES
    (now(), 'band_house_temp_low', 62.0),
    (now(), 'band_house_temp_high', 92.0);

DO $$
DECLARE
    v_temp double precision;
BEGIN
    SELECT max_temp_abs_diff INTO v_temp FROM public.v_band_device_divergence;
    IF v_temp <= 3.0 THEN
        RAISE EXCEPTION 'real curve skew hidden by repoint: dT=%', v_temp;
    END IF;
END $$;

-- 3. Legacy/rollback mode: the wide-default scalars must alarm again (the
--    dispatcher is REQUIRED to push them in this mode), preserving the
--    sw_onchip_band_enabled-flipped-off detection.
INSERT INTO public.diagnostics(ts, band_source) VALUES (now(), 'legacy');

DO $$
DECLARE
    v_temp double precision;
    v_source text;
BEGIN
    SELECT max_temp_abs_diff, band_source INTO v_temp, v_source
      FROM public.v_band_device_divergence;
    IF v_source <> 'legacy' THEN
        RAISE EXCEPTION 'expected legacy mode, got %', v_source;
    END IF;
    IF v_temp <= 3.0 THEN
        RAISE EXCEPTION
            'legacy mode must compare the legacy scalars (40/95 defaults): dT=%',
            v_temp;
    END IF;
END $$;

SELECT band_source,
       round(max_temp_abs_diff::numeric, 2) AS max_temp_abs_diff,
       round(max_vpd_abs_diff::numeric, 3) AS max_vpd_abs_diff
FROM public.v_band_device_divergence;

ROLLBACK;
