-- 186-noaa-solar-phase-parity.sql
--
-- Fix the DB solar helper chain used by fn_solar_phase()/fn_band_setpoints().
-- The old fn_solar_altitude() used:
--
--   hour_angle := RADIANS(15.0 * (local_hour - 13.0));
--
-- That hardcoded solar noon at 13:00 local all year. It is accidentally close
-- near June in MDT, but it makes winter sunrise/noon/sunset roughly one hour
-- late versus the firmware/ingestor NOAA contract.
--
-- This migration mirrors ingestor/solar.py and firmware greenhouse_solar.h:
-- Longmont constants, NOAA equation-of-time + declination approximation, and
-- sunrise/sunset zenith 90.833 degrees. fn_solar_phase() itself stays the same
-- smooth Hermite phase mapper; it inherits corrected SR/SS/noon anchors from
-- the helper functions.
--
-- Non-self-transactional (CREATE OR REPLACE FUNCTION only; no top-level COMMIT,
-- no CONCURRENTLY) -> safe to rollback-validate under BEGIN; ... ROLLBACK;.
-- Rollback: restore the previous fn_solar_altitude()/sunrise/sunset bodies from
-- db/schema.sql before this migration. After applying live, refresh any cached
-- band-curve materialized surfaces that depend on fn_solar_phase().

CREATE OR REPLACE FUNCTION public.fn_solar_altitude(target_ts timestamp with time zone)
RETURNS double precision
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    lat_rad double precision := RADIANS(40.167);
    lon_deg double precision := -105.102;
    local_ts timestamp without time zone;
    utc_ts timestamp without time zone;
    local_date date;
    local_hour double precision;
    utc_minutes double precision;
    year_int integer;
    doy double precision;
    days_in_year double precision;
    gamma double precision;
    eqtime double precision;
    decl double precision;
    true_solar_time double precision;
    hour_angle double precision;
BEGIN
    local_ts := target_ts AT TIME ZONE 'America/Denver';
    utc_ts := target_ts AT TIME ZONE 'UTC';
    local_date := local_ts::date;
    year_int := EXTRACT(year FROM local_date)::integer;
    doy := EXTRACT(doy FROM local_date)::double precision;
    local_hour := EXTRACT(hour FROM local_ts)
                + EXTRACT(minute FROM local_ts) / 60.0
                + EXTRACT(second FROM local_ts) / 3600.0;
    utc_minutes := EXTRACT(hour FROM utc_ts) * 60.0
                 + EXTRACT(minute FROM utc_ts)
                 + EXTRACT(second FROM utc_ts) / 60.0;
    days_in_year := CASE
        WHEN year_int % 4 = 0 AND (year_int % 100 <> 0 OR year_int % 400 = 0) THEN 366.0
        ELSE 365.0
    END;

    -- NOAA fractional year for solar position at the requested local time.
    gamma := 2.0 * pi() / days_in_year * (doy - 1.0 + (local_hour - 12.0) / 24.0);
    eqtime := 229.18 * (
          0.000075
        + 0.001868 * COS(gamma)
        - 0.032077 * SIN(gamma)
        - 0.014615 * COS(2.0 * gamma)
        - 0.040849 * SIN(2.0 * gamma)
    );
    decl := 0.006918
          - 0.399912 * COS(gamma)
          + 0.070257 * SIN(gamma)
          - 0.006758 * COS(2.0 * gamma)
          + 0.000907 * SIN(2.0 * gamma)
          - 0.002697 * COS(3.0 * gamma)
          + 0.001480 * SIN(3.0 * gamma);

    -- Using UTC minutes avoids a separate DST term:
    -- local_minutes + eqtime + 4*lon - 60*timezone == utc_minutes + eqtime + 4*lon.
    true_solar_time := utc_minutes + eqtime + 4.0 * lon_deg;
    true_solar_time := true_solar_time - FLOOR(true_solar_time / 1440.0) * 1440.0;
    hour_angle := true_solar_time / 4.0 - 180.0;

    RETURN DEGREES(ASIN(
        SIN(lat_rad) * SIN(decl) +
        COS(lat_rad) * COS(decl) * COS(RADIANS(hour_angle))
    ));
END;
$$;

COMMENT ON FUNCTION public.fn_solar_altitude(timestamp with time zone) IS
'NOAA solar altitude for the Longmont greenhouse. Uses equation-of-time, longitude, '
'DST-aware timestamp handling, and no hardcoded local solar noon; mirrors the '
'firmware/ingestor solar contract closely enough for band phase and lighting analysis.';

CREATE OR REPLACE FUNCTION public.fn_solar_sunrise_hour(target_ts timestamp with time zone)
RETURNS double precision
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    lat_rad double precision := RADIANS(40.167);
    lon_deg double precision := -105.102;
    zenith_rad double precision := RADIANS(90.833);
    local_ts timestamp without time zone;
    utc_ts timestamp without time zone;
    local_date date;
    year_int integer;
    doy double precision;
    days_in_year double precision;
    utc_offset_min double precision;
    gamma double precision;
    eqtime double precision;
    decl double precision;
    cos_ha double precision;
    ha_deg double precision;
    sunrise_min double precision;
BEGIN
    local_ts := target_ts AT TIME ZONE 'America/Denver';
    utc_ts := target_ts AT TIME ZONE 'UTC';
    local_date := local_ts::date;
    year_int := EXTRACT(year FROM local_date)::integer;
    doy := EXTRACT(doy FROM local_date)::double precision;
    days_in_year := CASE
        WHEN year_int % 4 = 0 AND (year_int % 100 <> 0 OR year_int % 400 = 0) THEN 366.0
        ELSE 365.0
    END;
    utc_offset_min := EXTRACT(epoch FROM (local_ts - utc_ts)) / 60.0;

    -- Match ingestor/solar.py: event calculation evaluated at local midday.
    gamma := 2.0 * pi() / days_in_year * (doy - 1.0 + 0.5);
    eqtime := 229.18 * (
          0.000075
        + 0.001868 * COS(gamma)
        - 0.032077 * SIN(gamma)
        - 0.014615 * COS(2.0 * gamma)
        - 0.040849 * SIN(2.0 * gamma)
    );
    decl := 0.006918
          - 0.399912 * COS(gamma)
          + 0.070257 * SIN(gamma)
          - 0.006758 * COS(2.0 * gamma)
          + 0.000907 * SIN(2.0 * gamma)
          - 0.002697 * COS(3.0 * gamma)
          + 0.001480 * SIN(3.0 * gamma);

    cos_ha := COS(zenith_rad) / (COS(lat_rad) * COS(decl)) - TAN(lat_rad) * TAN(decl);
    cos_ha := GREATEST(-1.0, LEAST(1.0, cos_ha));
    ha_deg := DEGREES(ACOS(cos_ha));
    sunrise_min := 720.0 - 4.0 * (lon_deg + ha_deg) - eqtime + utc_offset_min;
    RETURN sunrise_min / 60.0;
END;
$$;

COMMENT ON FUNCTION public.fn_solar_sunrise_hour(timestamp with time zone) IS
'NOAA sunrise hour after local midnight for the Longmont greenhouse, using zenith '
'90.833 degrees and the timestamp''s America/Denver UTC offset. Mirrors '
'ingestor/solar.py compute_solar_times().';

CREATE OR REPLACE FUNCTION public.fn_solar_sunset_hour(target_ts timestamp with time zone)
RETURNS double precision
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    lat_rad double precision := RADIANS(40.167);
    lon_deg double precision := -105.102;
    zenith_rad double precision := RADIANS(90.833);
    local_ts timestamp without time zone;
    utc_ts timestamp without time zone;
    local_date date;
    year_int integer;
    doy double precision;
    days_in_year double precision;
    utc_offset_min double precision;
    gamma double precision;
    eqtime double precision;
    decl double precision;
    cos_ha double precision;
    ha_deg double precision;
    sunset_min double precision;
BEGIN
    local_ts := target_ts AT TIME ZONE 'America/Denver';
    utc_ts := target_ts AT TIME ZONE 'UTC';
    local_date := local_ts::date;
    year_int := EXTRACT(year FROM local_date)::integer;
    doy := EXTRACT(doy FROM local_date)::double precision;
    days_in_year := CASE
        WHEN year_int % 4 = 0 AND (year_int % 100 <> 0 OR year_int % 400 = 0) THEN 366.0
        ELSE 365.0
    END;
    utc_offset_min := EXTRACT(epoch FROM (local_ts - utc_ts)) / 60.0;

    -- Match ingestor/solar.py: event calculation evaluated at local midday.
    gamma := 2.0 * pi() / days_in_year * (doy - 1.0 + 0.5);
    eqtime := 229.18 * (
          0.000075
        + 0.001868 * COS(gamma)
        - 0.032077 * SIN(gamma)
        - 0.014615 * COS(2.0 * gamma)
        - 0.040849 * SIN(2.0 * gamma)
    );
    decl := 0.006918
          - 0.399912 * COS(gamma)
          + 0.070257 * SIN(gamma)
          - 0.006758 * COS(2.0 * gamma)
          + 0.000907 * SIN(2.0 * gamma)
          - 0.002697 * COS(3.0 * gamma)
          + 0.001480 * SIN(3.0 * gamma);

    cos_ha := COS(zenith_rad) / (COS(lat_rad) * COS(decl)) - TAN(lat_rad) * TAN(decl);
    cos_ha := GREATEST(-1.0, LEAST(1.0, cos_ha));
    ha_deg := DEGREES(ACOS(cos_ha));
    sunset_min := 720.0 - 4.0 * (lon_deg - ha_deg) - eqtime + utc_offset_min;
    RETURN sunset_min / 60.0;
END;
$$;

COMMENT ON FUNCTION public.fn_solar_sunset_hour(timestamp with time zone) IS
'NOAA sunset hour after local midnight for the Longmont greenhouse, using zenith '
'90.833 degrees and the timestamp''s America/Denver UTC offset. Mirrors '
'ingestor/solar.py compute_solar_times().';

COMMENT ON FUNCTION public.fn_solar_phase(timestamp with time zone) IS
'Contract-B1 solar phase in [0,4): 0=sunrise, 1=solar noon, 2=sunset, 3=solar '
'midnight. DB mirror of the ESP32 solar_phase(); uses NOAA sunrise/sunset helpers '
'with equation-of-time, longitude, and DST-aware America/Denver offset handling.';
