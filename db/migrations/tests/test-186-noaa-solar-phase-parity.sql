-- test-186-noaa-solar-phase-parity.sql
--
-- Manual/CI psql fixture for migration 186.
--
-- Usage from the repository root against a disposable DB or rollback-wrapped
-- live proof:
--
--   psql -v ON_ERROR_STOP=1 -f db/migrations/tests/test-186-noaa-solar-phase-parity.sql
--
-- The fixture loads migration 186 inside a transaction, verifies representative
-- equinox/solstice solar events against the firmware/ingestor NOAA contract,
-- then rolls back.

\set ON_ERROR_STOP on

BEGIN;

-- Self-contained disposable-DB bootstrap.  On a full Verdify schema these are
-- byte-equivalent replacements and roll back with the fixture.
CREATE OR REPLACE FUNCTION public.fn_hermite_phase(
    v double precision,
    a double precision,
    b double precision,
    pa double precision,
    da double precision,
    db double precision
)
RETURNS double precision
LANGUAGE sql IMMUTABLE
AS $$
    SELECT CASE
        WHEN b <= a THEN pa
        ELSE pa + (3.0*u*u - 2.0*u*u*u)
                + da*len*(u*u*u - 2.0*u*u + u)
                + db*len*(u*u*u - u*u)
    END
    FROM (SELECT (b - a) AS len, (v - a) / NULLIF(b - a, 0) AS u) q
$$;

CREATE OR REPLACE FUNCTION public.fn_solar_phase(target_ts timestamptz)
RETURNS double precision
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    h double precision;
    sr double precision;
    ss double precision;
    sm double precision;
    nsr double precision;
    smid double precision;
    hh double precision;
    r0 double precision; r1 double precision; r2 double precision; r3 double precision;
    d_sr double precision; d_sm double precision; d_ss double precision; d_mid double precision;
BEGIN
    h := EXTRACT(epoch FROM (target_ts AT TIME ZONE 'America/Denver')
         - date_trunc('day', target_ts AT TIME ZONE 'America/Denver')) / 3600.0;
    sr := public.fn_solar_sunrise_hour(target_ts);
    ss := public.fn_solar_sunset_hour(target_ts);
    sm := (sr + ss) / 2.0;
    nsr := sr + 24.0;
    smid := (ss + nsr) / 2.0;
    hh := CASE WHEN h < sr THEN h + 24.0 ELSE h END;
    r0 := 1.0 / GREATEST(sm - sr, 1e-6);
    r1 := 1.0 / GREATEST(ss - sm, 1e-6);
    r2 := 1.0 / GREATEST(smid - ss, 1e-6);
    r3 := 1.0 / GREATEST(nsr - smid, 1e-6);
    d_sr := 0.5 * (r3 + r0);
    d_sm := 0.5 * (r0 + r1);
    d_ss := 0.5 * (r1 + r2);
    d_mid := 0.5 * (r2 + r3);
    IF hh <= sm THEN
        RETURN public.fn_hermite_phase(hh, sr, sm, 0.0, d_sr, d_sm);
    ELSIF hh <= ss THEN
        RETURN public.fn_hermite_phase(hh, sm, ss, 1.0, d_sm, d_ss);
    ELSIF hh <= smid THEN
        RETURN public.fn_hermite_phase(hh, ss, smid, 2.0, d_ss, d_mid);
    END IF;
    RETURN LEAST(
        public.fn_hermite_phase(hh, smid, nsr, 3.0, d_mid, d_sr),
        3.9999999
    );
END;
$$;

\i db/migrations/186-noaa-solar-phase-parity.sql

CREATE TEMP TABLE expected_solar_events (
    local_date date PRIMARY KEY,
    sunrise_hour double precision NOT NULL,
    solar_noon_hour double precision NOT NULL,
    sunset_hour double precision NOT NULL
) ON COMMIT DROP;

INSERT INTO expected_solar_events(local_date, sunrise_hour, solar_noon_hour, sunset_hour) VALUES
    ('2026-03-20', 7.0 + 5.0 / 60.0, 13.0 + 8.0 / 60.0, 19.0 + 12.0 / 60.0),
    ('2026-06-21', 5.0 + 31.0 / 60.0, 13.0 + 2.0 / 60.0, 20.0 + 33.0 / 60.0),
    ('2026-09-22', 6.0 + 47.0 / 60.0, 12.0 + 53.0 / 60.0, 18.0 + 59.0 / 60.0),
    ('2026-12-21', 7.0 + 19.0 / 60.0, 11.0 + 58.0 / 60.0, 16.0 + 38.0 / 60.0);

DO $$
DECLARE
    max_event_error_min double precision;
BEGIN
    WITH actual AS (
        SELECT
            e.local_date,
            fn_solar_sunrise_hour((e.local_date::text || ' 12:00:00 America/Denver')::timestamptz) AS sunrise_hour,
            (
                fn_solar_sunrise_hour((e.local_date::text || ' 12:00:00 America/Denver')::timestamptz)
                + fn_solar_sunset_hour((e.local_date::text || ' 12:00:00 America/Denver')::timestamptz)
            ) / 2.0 AS solar_noon_hour,
            fn_solar_sunset_hour((e.local_date::text || ' 12:00:00 America/Denver')::timestamptz) AS sunset_hour
        FROM expected_solar_events e
    ),
    errors AS (
        SELECT abs((a.sunrise_hour - e.sunrise_hour) * 60.0) AS error_min
        FROM actual a JOIN expected_solar_events e USING (local_date)
        UNION ALL
        SELECT abs((a.solar_noon_hour - e.solar_noon_hour) * 60.0)
        FROM actual a JOIN expected_solar_events e USING (local_date)
        UNION ALL
        SELECT abs((a.sunset_hour - e.sunset_hour) * 60.0)
        FROM actual a JOIN expected_solar_events e USING (local_date)
    )
    SELECT max(error_min) INTO max_event_error_min FROM errors;

    IF max_event_error_min > 5.0 THEN
        RAISE EXCEPTION 'migration 186 solar event error %.2f min exceeds +/-5 min contract', max_event_error_min;
    END IF;
END $$;

DO $$
DECLARE
    max_phase_error double precision;
BEGIN
    WITH anchors AS (
        SELECT
            e.local_date,
            fn_solar_sunrise_hour((e.local_date::text || ' 12:00:00 America/Denver')::timestamptz) AS sunrise_hour,
            fn_solar_sunset_hour((e.local_date::text || ' 12:00:00 America/Denver')::timestamptz) AS sunset_hour
        FROM expected_solar_events e
    ),
    points AS (
        SELECT
            local_date,
            phase_expected,
            ((local_date::text || ' 00:00:00 America/Denver')::timestamptz
                + (hour_of_day || ' hours')::interval) AS ts
        FROM (
            SELECT local_date, 0.0::double precision AS phase_expected, sunrise_hour AS hour_of_day
            FROM anchors
            UNION ALL
            SELECT local_date, 1.0, (sunrise_hour + sunset_hour) / 2.0
            FROM anchors
            UNION ALL
            SELECT local_date, 2.0, sunset_hour
            FROM anchors
        ) q
    )
    SELECT max(LEAST(
        abs(fn_solar_phase(ts) - phase_expected),
        abs(fn_solar_phase(ts) - phase_expected - 4.0),
        abs(fn_solar_phase(ts) - phase_expected + 4.0)
    ))
    INTO max_phase_error
    FROM points;

    IF max_phase_error > 0.01 THEN
        RAISE EXCEPTION 'migration 186 solar phase anchor error % exceeds tolerance', max_phase_error;
    END IF;
END $$;

SELECT
    e.local_date,
    round(fn_solar_sunrise_hour(
        (e.local_date::text || ' 12:00:00 America/Denver')::timestamptz
    )::numeric, 3) AS source_sunrise_hour,
    round(((
        fn_solar_sunrise_hour(
            (e.local_date::text || ' 12:00:00 America/Denver')::timestamptz
        )
        + fn_solar_sunset_hour(
            (e.local_date::text || ' 12:00:00 America/Denver')::timestamptz
        )
    ) / 2.0)::numeric, 3) AS source_solar_noon_hour,
    round(fn_solar_sunset_hour(
        (e.local_date::text || ' 12:00:00 America/Denver')::timestamptz
    )::numeric, 3) AS source_sunset_hour
FROM expected_solar_events e
ORDER BY e.local_date;

ROLLBACK;
