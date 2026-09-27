-- Run in a disposable PostgreSQL database, never in production.
-- The server-computed band_house_* audit must not become device evidence.
\set ON_ERROR_STOP on
BEGIN;

CREATE TABLE public.setpoint_snapshot (
    ts timestamptz NOT NULL, parameter text NOT NULL, value double precision NOT NULL,
    greenhouse_id text NOT NULL DEFAULT 'vallery'
);
CREATE TABLE public.climate (
    ts timestamptz NOT NULL, greenhouse_id text NOT NULL DEFAULT 'vallery',
    house_temp_target_f double precision, house_vpd_target double precision
);
CREATE TABLE public.diagnostics (
    ts timestamptz NOT NULL, greenhouse_id text NOT NULL DEFAULT 'vallery',
    band_source text
);
CREATE FUNCTION public.fn_band_setpoints(timestamptz)
RETURNS TABLE(temp_low double precision, temp_high double precision,
              vpd_low double precision, vpd_high double precision,
              temp_target double precision, vpd_target double precision)
LANGUAGE sql STABLE ROWS 1
AS $$ SELECT 72.25, 82.25, 0.85, 1.28, 77.25, 1.03 $$;
CREATE FUNCTION public.fn_house_vpd_control_band(timestamptz)
RETURNS TABLE(house_vpd_low double precision, house_vpd_high double precision)
LANGUAGE sql STABLE ROWS 1 AS $$ SELECT 0.64, 1.19 $$;

INSERT INTO public.setpoint_snapshot(ts, parameter, value) VALUES
    (now() - interval '1 minute', 'temp_low', 40.0),
    (now() - interval '1 minute', 'temp_high', 95.0),
    (now() - interval '1 minute', 'vpd_low', 0.35),
    (now() - interval '1 minute', 'vpd_high', 2.80),
    (now() - interval '30 seconds', 'band_house_temp_low', 72.25),
    (now() - interval '30 seconds', 'band_house_temp_high', 82.25),
    (now() - interval '30 seconds', 'band_house_vpd_low', 0.85),
    (now() - interval '30 seconds', 'band_house_vpd_high', 1.28);
INSERT INTO public.climate(ts, house_temp_target_f, house_vpd_target)
VALUES (now() - interval '30 seconds', 77.25, 1.03);
INSERT INTO public.diagnostics(ts, band_source)
VALUES (now() - interval '30 seconds', 'onchip_curve');

\i db/migrations/251-band-divergence-provenance.sql

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.v_band_device_divergence
        WHERE band_source <> 'onchip_curve'
           OR device_temp_low IS NOT NULL OR device_vpd_low IS NOT NULL
           OR max_temp_abs_diff IS NOT NULL OR max_vpd_abs_diff IS NOT NULL
           OR device_age IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'server audit was misclassified as an on-chip device readback';
    END IF;
END $$;

-- Actual legacy scalar state remains comparable, and the wide defaults are
-- correctly reported as divergent from the served control envelope.
INSERT INTO public.diagnostics(ts, band_source)
VALUES (now(), 'dispatcher_legacy');
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.v_band_device_divergence
        WHERE band_source = 'dispatcher_legacy'
          AND device_temp_low = 40.0 AND device_vpd_low = 0.35
          AND max_temp_abs_diff > 3.0 AND max_vpd_abs_diff > 0.40
          AND device_age IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'legacy cfg drift was hidden';
    END IF;
END $$;

-- Unknown branch is unobservable; a preceding legacy row is not authority.
DELETE FROM public.diagnostics WHERE band_source = 'dispatcher_legacy';
INSERT INTO public.diagnostics(ts, band_source) VALUES (now(), 'unknown');
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.v_band_device_divergence
        WHERE band_source <> 'unobservable'
           OR device_temp_low IS NOT NULL OR max_temp_abs_diff IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'unknown branch borrowed legacy scalar proof';
    END IF;
END $$;

-- A stale branch observation cannot silently select either comparison.
DELETE FROM public.diagnostics;
INSERT INTO public.diagnostics(ts, band_source)
VALUES (now() - interval '16 minutes', 'dispatcher_legacy');
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.v_band_device_divergence
        WHERE band_source <> 'unobservable'
           OR device_temp_low IS NOT NULL OR max_temp_abs_diff IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'stale branch selected a device comparison';
    END IF;
END $$;

ROLLBACK;
