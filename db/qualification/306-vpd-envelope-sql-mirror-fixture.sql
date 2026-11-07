-- Run only inside an explicit rollback-only transaction after exact306 (#1433), as the superuser owner:
-- scripts/qualify-migration-successor.py --native-fixture runs it at the end of its final session. It reads the 14
-- days of deep-night hours before 2026-11-07 12:00 MST with no moisture equipment on, exactly as API-06's achievable
-- read selects them (verdify_services/vpd_envelope.py ENVELOPE_RANGE_SQL), fits them with fn_vpd_envelope_fit and
-- evaluates fn_vpd_envelope_expected and fn_vpd_envelope_achievable_band at twelve fixed points. Checks: the fit
-- exists with 24 to 336 hours and an RMSE under 10 °F, every ribbon is ordered and non-negative, and the achievable
-- band of 0.74-1.17 kPa contains it. The report carries the training rows, the fit and the ribbons, so
-- tests/test_306_vpd_envelope_delivery.py can recompute them with verdify_schemas.vpd_envelope and require the same
-- bits from production's database. Nothing outside the rollback is written. The last line reports the result.
SET LOCAL extra_float_digits = 1;

CREATE TEMP TABLE envelope_training ON COMMIT DROP AS
WITH s AS (
    SELECT equipment, ts, state, lead(ts) OVER (PARTITION BY equipment ORDER BY ts) AS nts
    FROM public.equipment_state
    WHERE equipment = ANY(ARRAY['vent', 'fan1', 'fan2', 'fog', 'mister_center', 'mister_south', 'mister_west'])
      AND ts >= TIMESTAMPTZ '2026-10-24 19:00:00+00' - interval '1 day'
      AND ts < TIMESTAMPTZ '2026-11-07 19:00:00+00'
), busy AS (
    SELECT DISTINCT gs AS b
    FROM s CROSS JOIN LATERAL generate_series(
        date_trunc('hour', s.ts),
        date_trunc('hour', LEAST(COALESCE(s.nts, TIMESTAMPTZ '2026-11-07 19:00:00+00'), s.ts + interval '6 hours')),
        interval '1 hour') AS gs
    WHERE s.state
), h AS (
    SELECT time_bucket('1 hour', ts) AS b, avg(temp_avg) AS t_in, avg(dew_point) AS td_in,
           avg(outdoor_temp_f) AS t_out, avg(outdoor_rh_pct) AS rh_out, avg(solar_altitude_deg) AS alt
    FROM public.climate
    WHERE greenhouse_id = 'vallery'
      AND ts >= TIMESTAMPTZ '2026-10-24 19:00:00+00' AND ts < TIMESTAMPTZ '2026-11-07 19:00:00+00'
    GROUP BY 1
)
SELECT b, t_in::double precision AS t_in, td_in::double precision AS td_in, t_out::double precision AS t_out,
       rh_out::double precision AS rh_out
FROM h
WHERE alt < -6.0 AND t_in IS NOT NULL AND td_in IS NOT NULL AND t_out IS NOT NULL AND rh_out > 0
  AND NOT EXISTS (SELECT 1 FROM busy WHERE busy.b = h.b);

CREATE TEMP TABLE envelope_fit ON COMMIT DROP AS
SELECT f.*
  FROM (SELECT array_agg(t_in ORDER BY b) AS t_in, array_agg(td_in ORDER BY b) AS td_in,
               array_agg(t_out ORDER BY b) AS t_out, array_agg(rh_out ORDER BY b) AS rh_out
          FROM envelope_training) t
 CROSS JOIN LATERAL public.fn_vpd_envelope_fit(t.t_in, t.td_in, t.t_out, t.rh_out) f;

CREATE TEMP TABLE envelope_points ON COMMIT DROP AS
SELECT p.temp_f, p.t_out, p.rh_out, public.fn_vpd_envelope_outdoor_dewpoint_f(p.t_out, p.rh_out) AS td_out,
       r.p25, r.p75, a.vpd_low, a.vpd_high
  FROM (VALUES (58.0::double precision, 30.0::double precision, 40.0::double precision), (58.0, 30.0, 85.0),
               (58.0, 43.0, 40.0), (58.0, 43.0, 85.0), (62.0, 30.0, 40.0), (62.0, 30.0, 85.0),
               (62.0, 43.0, 40.0), (62.0, 43.0, 85.0), (66.0, 30.0, 40.0), (66.0, 30.0, 85.0),
               (66.0, 43.0, 40.0), (66.0, 43.0, 85.0)) AS p(temp_f, t_out, rh_out)
 CROSS JOIN envelope_fit f
 CROSS JOIN LATERAL public.fn_vpd_envelope_expected(f.a, f.b, f.c, f.e, f.rmse_f, p.temp_f, p.t_out,
     public.fn_vpd_envelope_outdoor_dewpoint_f(p.t_out, p.rh_out)) r
 CROSS JOIN LATERAL public.fn_vpd_envelope_achievable_band(0.74::double precision, 1.17::double precision,
     r.p25, r.p75) a;

DO $fixture$
BEGIN
    IF (SELECT count(*) FROM envelope_fit) <> 1
       OR NOT EXISTS(SELECT 1 FROM envelope_fit WHERE hours BETWEEN 24 AND 336 AND rmse_f > 0 AND rmse_f < 10)
       OR (SELECT count(*) FROM envelope_points) <> 12
       OR EXISTS(SELECT 1 FROM envelope_points
                  WHERE NOT (0 <= p25 AND p25 <= p75)
                     OR vpd_low > LEAST(0.74::double precision, p25) OR vpd_high < GREATEST(1.17::double precision, p75))
    THEN RAISE EXCEPTION '306 VPD envelope native fixture failed'; END IF;
END $fixture$;

SELECT 'QUALIFY native_fixture ' || json_build_object(
    'checks', json_build_array('fit_exists', 'fit_hours_24_to_336', 'fit_rmse_under_10f', 'ribbons_ordered',
                               'achievable_band_contains_ribbon'),
    'training', (SELECT json_agg(json_build_array(t_in, td_in, t_out, rh_out) ORDER BY b) FROM envelope_training),
    'fit', (SELECT json_build_object('coef', json_build_array(a, b, c, e), 'rmse_f', rmse_f, 'hours', hours,
                                     'dewpoint_p10_f', dewpoint_p10_f) FROM envelope_fit),
    'points', (SELECT json_agg(json_build_array(temp_f, t_out, td_out, p25, p75, vpd_low, vpd_high)
                               ORDER BY temp_f, t_out, rh_out) FROM envelope_points),
    'all_passed', true)::text;
