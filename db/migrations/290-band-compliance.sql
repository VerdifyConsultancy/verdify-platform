-- 290 (#1358, #1314): one definition of time in band, graded against the band the controller served.
-- A 5-minute bucket of the house average (climate temp_avg, vpd_avg, greenhouse vallery) is in band when its mean
-- lies inside the band in force at that bucket, inclusive at both edges. The band in force is the newest
-- band_house_* audit row of setpoint_snapshot at or before the bucket's end and at most 600 s older than its start
-- (API-06's carry; the dispatcher writes one every ~5 min). Where any of the four rows is missing the bucket is
-- graded against fn_band_setpoints(bucket) and counted as reconstructed. Temperature and VPD are graded on their
-- own; "both" needs both. Day is fn_solar_phase < 2 at the bucket's midpoint (sunrise to sunset).
-- Until now daily_summary.temp_compliance_pct graded against setpoint_changes temp_*/vpd_* rows last written on
-- 2026-09-06 (a fixed 73.2-83.2 F / 0.44-0.99 kPa box), and mv_zone_band_grade, which v_daily_scorecard read for
-- its day/night split, was never refreshed by anything but the one-shot reconciler of 2026-06-12 and grades
-- against crop_target_profiles. 290 adds:
--   fn_band_compliance_5m(start, end)  the definition, one row per graded bucket (invoker, no runtime grant);
--   fn_band_compliance_day(date)       its America/Denver-day rollup in minutes;
--   daily_band_compliance              the persisted rollup with its shares (generated columns);
--   fn_band_compliance_store(date)     upserts one day and copies whole-day shares and below/above hours into
--                                      daily_summary's legacy compliance_pct, temp_/vpd_compliance_pct and
--                                      stress_hours_* columns, which MCP and the planner read;
--   verdify_matview_refresh_runtime.fn_refresh_band_compliance()
--                                      the refresh the band-curve CronJob calls every 30 minutes: today,
--                                      yesterday, and up to 31 missing days back to 2026-09-01 (the backfill).
-- v_daily_scorecard now reads daily_band_compliance (whole day, day, night, below and above) and stops reading
-- mv_zone_band_grade. daily_climate_metric_revisions keeps every overwritten legacy value.
-- No runtime seal moves: the new relations and functions are verdify's, PUBLIC's EXECUTE is revoked, and the
-- only EXECUTE goes to the matview-refresh duty, in its own schema, which neither ordinary login nor an MCP role
-- can use. Both blocks pin exact289's seals.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=289 AND filename='db/migrations/289-alert-silences.sql'
            AND sha256='e9173348a9476a69b6ca7285cdfd1e14820ac00af373fa37f6b0e32104314475'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>289)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'a752a89a07b5b90ecbe6ccaf44358492aa8da6c562d608c8880d24d77088af7e'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'b17c9da67fe6456c1fd470ac5dc82dfdd20320b3d808c482162b11952bd57011'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR to_regclass('public.daily_band_compliance') IS NOT NULL
        OR to_regclass('public.v_daily_scorecard') IS NULL
        OR to_regnamespace('verdify_matview_refresh_runtime') IS NULL
    THEN RAISE EXCEPTION '290 refuses an unqualified predecessor'; END IF;
END $predecessor$;

CREATE FUNCTION public.fn_band_compliance_5m(p_start timestamptz, p_end timestamptz)
RETURNS TABLE (bucket timestamptz, is_day boolean, band_source text,
               temp_f double precision, vpd_kpa double precision,
               temp_low double precision, temp_high double precision,
               vpd_low double precision, vpd_high double precision,
               temp_grade smallint, vpd_grade smallint)
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $grade$
WITH reading AS (
    SELECT date_bin('5 minutes', c.ts, p_start) AS bucket,
           avg(c.temp_avg) FILTER (WHERE c.temp_avg NOT IN ('NaN', 'Infinity', '-Infinity')) AS temp_f,
           avg(c.vpd_avg) FILTER (WHERE c.vpd_avg NOT IN ('NaN', 'Infinity', '-Infinity')) AS vpd_kpa
      FROM public.climate c
     WHERE c.greenhouse_id = 'vallery' AND c.ts >= p_start AND c.ts < p_end
     GROUP BY 1
), audited AS (
    SELECT r.bucket, r.temp_f, r.vpd_kpa, a.temp_low, a.temp_high, a.vpd_low, a.vpd_high, a.roles
      FROM reading r
     CROSS JOIN LATERAL (
        SELECT max(x.value) FILTER (WHERE x.parameter = 'band_house_temp_low') AS temp_low,
               max(x.value) FILTER (WHERE x.parameter = 'band_house_temp_high') AS temp_high,
               max(x.value) FILTER (WHERE x.parameter = 'band_house_vpd_low') AS vpd_low,
               max(x.value) FILTER (WHERE x.parameter = 'band_house_vpd_high') AS vpd_high,
               count(*) AS roles
          FROM (SELECT DISTINCT ON (s.parameter) s.parameter, s.value
                  FROM public.setpoint_snapshot s
                 WHERE s.parameter IN ('band_house_temp_low', 'band_house_temp_high',
                                       'band_house_vpd_low', 'band_house_vpd_high')
                   AND s.zone = 'house'
                   AND s.ts >= r.bucket - interval '600 seconds'
                   AND s.ts < r.bucket + interval '5 minutes'
                 ORDER BY s.parameter, s.ts DESC) x
     ) a
     WHERE r.temp_f IS NOT NULL OR r.vpd_kpa IS NOT NULL
), banded AS (
    SELECT a.bucket, 'audit'::text AS band_source, a.temp_f, a.vpd_kpa,
           a.temp_low, a.temp_high, a.vpd_low, a.vpd_high
      FROM audited a
     WHERE a.roles = 4
    UNION ALL
    SELECT a.bucket, 'reconstructed'::text, a.temp_f, a.vpd_kpa,
           f.temp_low, f.temp_high, f.vpd_low, f.vpd_high
      FROM audited a
      LEFT JOIN LATERAL public.fn_band_setpoints(a.bucket) f ON true
     WHERE a.roles < 4
)
SELECT b.bucket,
       public.fn_solar_phase(b.bucket + interval '150 seconds') < 2,
       b.band_source, b.temp_f, b.vpd_kpa, b.temp_low, b.temp_high, b.vpd_low, b.vpd_high,
       (CASE WHEN b.temp_f IS NULL OR b.temp_low IS NULL OR b.temp_high IS NULL OR b.temp_low > b.temp_high
                  THEN NULL
             WHEN b.temp_f < b.temp_low THEN -1
             WHEN b.temp_f > b.temp_high THEN 1
             ELSE 0 END)::smallint,
       (CASE WHEN b.vpd_kpa IS NULL OR b.vpd_low IS NULL OR b.vpd_high IS NULL OR b.vpd_low > b.vpd_high
                  THEN NULL
             WHEN b.vpd_kpa < b.vpd_low THEN -1
             WHEN b.vpd_kpa > b.vpd_high THEN 1
             ELSE 0 END)::smallint
  FROM banded b
 ORDER BY b.bucket
$grade$;
COMMENT ON FUNCTION public.fn_band_compliance_5m(timestamptz, timestamptz) IS
    'Time in band, the one definition (290, #1358): each 5-minute bucket of the house average in [p_start, p_end), '
    'graded against the band the controller served (band_house_* audit rows, newest at or before the bucket end '
    'and at most 600 s older than its start; fn_band_setpoints where a row is missing, band_source reconstructed). '
    'grade -1 below, 0 in band (edges inclusive), 1 above, null when ungraded. is_day: fn_solar_phase < 2.';

CREATE FUNCTION public.fn_band_compliance_day(p_day date)
RETURNS TABLE (graded_min integer, reconstructed_min integer,
               temp_graded_min integer, temp_in_min integer, temp_below_min integer, temp_above_min integer,
               vpd_graded_min integer, vpd_in_min integer, vpd_below_min integer, vpd_above_min integer,
               both_graded_min integer, both_in_min integer,
               day_temp_graded_min integer, day_temp_in_min integer,
               night_temp_graded_min integer, night_temp_in_min integer,
               day_vpd_graded_min integer, day_vpd_in_min integer,
               night_vpd_graded_min integer, night_vpd_in_min integer,
               day_both_graded_min integer, day_both_in_min integer,
               night_both_graded_min integer, night_both_in_min integer)
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $day$
SELECT (5 * count(*) FILTER (WHERE g.temp_grade IS NOT NULL OR g.vpd_grade IS NOT NULL))::integer,
       (5 * count(*) FILTER (WHERE g.band_source = 'reconstructed'
                               AND (g.temp_grade IS NOT NULL OR g.vpd_grade IS NOT NULL)))::integer,
       (5 * count(g.temp_grade))::integer,
       (5 * count(*) FILTER (WHERE g.temp_grade = 0))::integer,
       (5 * count(*) FILTER (WHERE g.temp_grade < 0))::integer,
       (5 * count(*) FILTER (WHERE g.temp_grade > 0))::integer,
       (5 * count(g.vpd_grade))::integer,
       (5 * count(*) FILTER (WHERE g.vpd_grade = 0))::integer,
       (5 * count(*) FILTER (WHERE g.vpd_grade < 0))::integer,
       (5 * count(*) FILTER (WHERE g.vpd_grade > 0))::integer,
       (5 * count(*) FILTER (WHERE g.temp_grade IS NOT NULL AND g.vpd_grade IS NOT NULL))::integer,
       (5 * count(*) FILTER (WHERE g.temp_grade = 0 AND g.vpd_grade = 0))::integer,
       (5 * count(g.temp_grade) FILTER (WHERE g.is_day))::integer,
       (5 * count(*) FILTER (WHERE g.is_day AND g.temp_grade = 0))::integer,
       (5 * count(g.temp_grade) FILTER (WHERE NOT g.is_day))::integer,
       (5 * count(*) FILTER (WHERE NOT g.is_day AND g.temp_grade = 0))::integer,
       (5 * count(g.vpd_grade) FILTER (WHERE g.is_day))::integer,
       (5 * count(*) FILTER (WHERE g.is_day AND g.vpd_grade = 0))::integer,
       (5 * count(g.vpd_grade) FILTER (WHERE NOT g.is_day))::integer,
       (5 * count(*) FILTER (WHERE NOT g.is_day AND g.vpd_grade = 0))::integer,
       (5 * count(*) FILTER (WHERE g.is_day AND g.temp_grade IS NOT NULL AND g.vpd_grade IS NOT NULL))::integer,
       (5 * count(*) FILTER (WHERE g.is_day AND g.temp_grade = 0 AND g.vpd_grade = 0))::integer,
       (5 * count(*) FILTER (WHERE NOT g.is_day AND g.temp_grade IS NOT NULL AND g.vpd_grade IS NOT NULL))::integer,
       (5 * count(*) FILTER (WHERE NOT g.is_day AND g.temp_grade = 0 AND g.vpd_grade = 0))::integer
  FROM public.fn_band_compliance_5m(p_day::timestamp AT TIME ZONE 'America/Denver',
                                    (p_day + 1)::timestamp AT TIME ZONE 'America/Denver') g
$day$;
COMMENT ON FUNCTION public.fn_band_compliance_day(date) IS
    'fn_band_compliance_5m rolled up over one America/Denver day, in graded minutes (290, #1358).';

CREATE TABLE public.daily_band_compliance (
    date                  date PRIMARY KEY,
    greenhouse_id         text NOT NULL DEFAULT 'vallery' REFERENCES public.greenhouses(id),
    definition            text NOT NULL,
    graded_min            integer NOT NULL,
    reconstructed_min     integer NOT NULL,
    temp_graded_min       integer NOT NULL,
    temp_in_min           integer NOT NULL,
    temp_below_min        integer NOT NULL,
    temp_above_min        integer NOT NULL,
    vpd_graded_min        integer NOT NULL,
    vpd_in_min            integer NOT NULL,
    vpd_below_min         integer NOT NULL,
    vpd_above_min         integer NOT NULL,
    both_graded_min       integer NOT NULL,
    both_in_min           integer NOT NULL,
    day_temp_graded_min   integer NOT NULL,
    day_temp_in_min       integer NOT NULL,
    night_temp_graded_min integer NOT NULL,
    night_temp_in_min     integer NOT NULL,
    day_vpd_graded_min    integer NOT NULL,
    day_vpd_in_min        integer NOT NULL,
    night_vpd_graded_min  integer NOT NULL,
    night_vpd_in_min      integer NOT NULL,
    day_both_graded_min   integer NOT NULL,
    day_both_in_min       integer NOT NULL,
    night_both_graded_min integer NOT NULL,
    night_both_in_min     integer NOT NULL,
    computed_at           timestamptz NOT NULL DEFAULT now(),
    temp_in_band_pct double precision GENERATED ALWAYS AS
        (round(100.0 * temp_in_min / NULLIF(temp_graded_min, 0), 1)::double precision) STORED,
    temp_below_band_pct double precision GENERATED ALWAYS AS
        (round(100.0 * temp_below_min / NULLIF(temp_graded_min, 0), 1)::double precision) STORED,
    temp_above_band_pct double precision GENERATED ALWAYS AS
        (round(100.0 * temp_above_min / NULLIF(temp_graded_min, 0), 1)::double precision) STORED,
    vpd_in_band_pct double precision GENERATED ALWAYS AS
        (round(100.0 * vpd_in_min / NULLIF(vpd_graded_min, 0), 1)::double precision) STORED,
    vpd_below_band_pct double precision GENERATED ALWAYS AS
        (round(100.0 * vpd_below_min / NULLIF(vpd_graded_min, 0), 1)::double precision) STORED,
    vpd_above_band_pct double precision GENERATED ALWAYS AS
        (round(100.0 * vpd_above_min / NULLIF(vpd_graded_min, 0), 1)::double precision) STORED,
    both_in_band_pct double precision GENERATED ALWAYS AS
        (round(100.0 * both_in_min / NULLIF(both_graded_min, 0), 1)::double precision) STORED,
    temp_in_band_day_pct double precision GENERATED ALWAYS AS
        (round(100.0 * day_temp_in_min / NULLIF(day_temp_graded_min, 0), 1)::double precision) STORED,
    temp_in_band_night_pct double precision GENERATED ALWAYS AS
        (round(100.0 * night_temp_in_min / NULLIF(night_temp_graded_min, 0), 1)::double precision) STORED,
    vpd_in_band_day_pct double precision GENERATED ALWAYS AS
        (round(100.0 * day_vpd_in_min / NULLIF(day_vpd_graded_min, 0), 1)::double precision) STORED,
    vpd_in_band_night_pct double precision GENERATED ALWAYS AS
        (round(100.0 * night_vpd_in_min / NULLIF(night_vpd_graded_min, 0), 1)::double precision) STORED,
    both_in_band_day_pct double precision GENERATED ALWAYS AS
        (round(100.0 * day_both_in_min / NULLIF(day_both_graded_min, 0), 1)::double precision) STORED,
    both_in_band_night_pct double precision GENERATED ALWAYS AS
        (round(100.0 * night_both_in_min / NULLIF(night_both_graded_min, 0), 1)::double precision) STORED,
    reconstructed_pct double precision GENERATED ALWAYS AS
        (round(100.0 * reconstructed_min / NULLIF(graded_min, 0), 1)::double precision) STORED,
    CONSTRAINT daily_band_compliance_minutes_add_up CHECK (
        graded_min BETWEEN 0 AND 1500 AND reconstructed_min BETWEEN 0 AND graded_min
        AND temp_in_min + temp_below_min + temp_above_min = temp_graded_min
        AND vpd_in_min + vpd_below_min + vpd_above_min = vpd_graded_min
        AND temp_graded_min <= graded_min AND vpd_graded_min <= graded_min
        AND both_in_min <= both_graded_min
        AND day_temp_graded_min + night_temp_graded_min = temp_graded_min
        AND day_vpd_graded_min + night_vpd_graded_min = vpd_graded_min
        AND day_both_graded_min + night_both_graded_min = both_graded_min
        AND day_temp_in_min + night_temp_in_min = temp_in_min
        AND day_vpd_in_min + night_vpd_in_min = vpd_in_min
        AND day_both_in_min + night_both_in_min = both_in_min)
);
COMMENT ON TABLE public.daily_band_compliance IS
    'Time in band per America/Denver day (290, #1358): fn_band_compliance_day persisted by fn_band_compliance_store. '
    'Minutes are graded 5-minute buckets; the shares are generated from them, null when nothing was graded. '
    'reconstructed_pct is the share graded against fn_band_setpoints because the band audit row was missing. '
    'Kept after setpoint_snapshot''s 90-day retention drops the audit rows it was graded against.';

CREATE FUNCTION public.fn_band_compliance_store(p_day date)
RETURNS public.daily_band_compliance
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $store$
DECLARE
    stored public.daily_band_compliance;
BEGIN
    IF p_day IS NULL OR p_day > (now() AT TIME ZONE 'America/Denver')::date THEN
        RAISE EXCEPTION 'band compliance day % is not a past or current local day', p_day USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.daily_band_compliance AS t (
        date, definition, graded_min, reconstructed_min,
        temp_graded_min, temp_in_min, temp_below_min, temp_above_min,
        vpd_graded_min, vpd_in_min, vpd_below_min, vpd_above_min,
        both_graded_min, both_in_min,
        day_temp_graded_min, day_temp_in_min, night_temp_graded_min, night_temp_in_min,
        day_vpd_graded_min, day_vpd_in_min, night_vpd_graded_min, night_vpd_in_min,
        day_both_graded_min, day_both_in_min, night_both_graded_min, night_both_in_min, computed_at)
    SELECT p_day, 'served-band-5m-v1', d.graded_min, d.reconstructed_min,
           d.temp_graded_min, d.temp_in_min, d.temp_below_min, d.temp_above_min,
           d.vpd_graded_min, d.vpd_in_min, d.vpd_below_min, d.vpd_above_min,
           d.both_graded_min, d.both_in_min,
           d.day_temp_graded_min, d.day_temp_in_min, d.night_temp_graded_min, d.night_temp_in_min,
           d.day_vpd_graded_min, d.day_vpd_in_min, d.night_vpd_graded_min, d.night_vpd_in_min,
           d.day_both_graded_min, d.day_both_in_min, d.night_both_graded_min, d.night_both_in_min, now()
      FROM public.fn_band_compliance_day(p_day) d
    ON CONFLICT (date) DO UPDATE SET
        definition = EXCLUDED.definition, graded_min = EXCLUDED.graded_min,
        reconstructed_min = EXCLUDED.reconstructed_min,
        temp_graded_min = EXCLUDED.temp_graded_min, temp_in_min = EXCLUDED.temp_in_min,
        temp_below_min = EXCLUDED.temp_below_min, temp_above_min = EXCLUDED.temp_above_min,
        vpd_graded_min = EXCLUDED.vpd_graded_min, vpd_in_min = EXCLUDED.vpd_in_min,
        vpd_below_min = EXCLUDED.vpd_below_min, vpd_above_min = EXCLUDED.vpd_above_min,
        both_graded_min = EXCLUDED.both_graded_min, both_in_min = EXCLUDED.both_in_min,
        day_temp_graded_min = EXCLUDED.day_temp_graded_min, day_temp_in_min = EXCLUDED.day_temp_in_min,
        night_temp_graded_min = EXCLUDED.night_temp_graded_min, night_temp_in_min = EXCLUDED.night_temp_in_min,
        day_vpd_graded_min = EXCLUDED.day_vpd_graded_min, day_vpd_in_min = EXCLUDED.day_vpd_in_min,
        night_vpd_graded_min = EXCLUDED.night_vpd_graded_min, night_vpd_in_min = EXCLUDED.night_vpd_in_min,
        day_both_graded_min = EXCLUDED.day_both_graded_min, day_both_in_min = EXCLUDED.day_both_in_min,
        night_both_graded_min = EXCLUDED.night_both_graded_min, night_both_in_min = EXCLUDED.night_both_in_min,
        computed_at = EXCLUDED.computed_at
    RETURNING t.* INTO stored;
    -- The legacy columns MCP, the planner and Grafana read become copies of this definition. A day with
    -- no summary row is left alone (the ingestor's daily task creates it); an unchanged day is not rewritten.
    UPDATE public.daily_summary s SET
        compliance_pct = stored.both_in_band_pct,
        temp_compliance_pct = stored.temp_in_band_pct,
        vpd_compliance_pct = stored.vpd_in_band_pct,
        stress_hours_heat = CASE WHEN stored.temp_graded_min > 0 THEN round(stored.temp_above_min / 60.0, 2) END,
        stress_hours_cold = CASE WHEN stored.temp_graded_min > 0 THEN round(stored.temp_below_min / 60.0, 2) END,
        stress_hours_vpd_high = CASE WHEN stored.vpd_graded_min > 0 THEN round(stored.vpd_above_min / 60.0, 2) END,
        stress_hours_vpd_low = CASE WHEN stored.vpd_graded_min > 0 THEN round(stored.vpd_below_min / 60.0, 2) END
     WHERE s.date = p_day
       AND (s.compliance_pct, s.temp_compliance_pct, s.vpd_compliance_pct,
            s.stress_hours_heat, s.stress_hours_cold, s.stress_hours_vpd_high, s.stress_hours_vpd_low)
           IS DISTINCT FROM
           (stored.both_in_band_pct, stored.temp_in_band_pct, stored.vpd_in_band_pct,
            CASE WHEN stored.temp_graded_min > 0 THEN round(stored.temp_above_min / 60.0, 2)::double precision END,
            CASE WHEN stored.temp_graded_min > 0 THEN round(stored.temp_below_min / 60.0, 2)::double precision END,
            CASE WHEN stored.vpd_graded_min > 0 THEN round(stored.vpd_above_min / 60.0, 2)::double precision END,
            CASE WHEN stored.vpd_graded_min > 0 THEN round(stored.vpd_below_min / 60.0, 2)::double precision END);
    RETURN stored;
END
$store$;
COMMENT ON FUNCTION public.fn_band_compliance_store(date) IS
    'Grade one America/Denver day with fn_band_compliance_day, upsert daily_band_compliance and copy the whole-day '
    'shares and below/above hours into daily_summary''s legacy compliance and stress-hour columns (290, #1358). '
    'Operator backfill, as verdify: SELECT public.fn_band_compliance_store(d::date) FROM generate_series(...) d.';

CREATE FUNCTION verdify_matview_refresh_runtime.fn_refresh_band_compliance() RETURNS integer
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $refresh$
 DECLARE
  v_today date := (now() AT TIME ZONE 'America/Denver')::date;
  v_day date;
  v_refreshed integer := 0;
 BEGIN
  IF session_user <> 'verdify_matview_refresh_runtime_login' THEN
   RAISE EXCEPTION 'refresh caller outside duty' USING ERRCODE='42501';
  END IF;
  -- Today and yesterday every run, so yesterday closes complete after midnight; then the newest 31
  -- days since 2026-09-01 that have no row, the bounded backfill.
  FOR v_day IN
   SELECT recent.d FROM (VALUES (v_today), (v_today - 1)) AS recent(d)
   UNION ALL
   (SELECT missing.d::date FROM generate_series(DATE '2026-09-01', v_today - 2, interval '1 day') AS missing(d)
     WHERE NOT EXISTS (SELECT 1 FROM public.daily_band_compliance b WHERE b.date = missing.d::date)
     ORDER BY missing.d DESC LIMIT 31)
  LOOP
   PERFORM public.fn_band_compliance_store(v_day);
   v_refreshed := v_refreshed + 1;
  END LOOP;
  RETURN v_refreshed;
 END $refresh$;
REVOKE ALL ON FUNCTION public.fn_band_compliance_5m(timestamptz, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_band_compliance_day(date) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_band_compliance_store(date) FROM PUBLIC;
REVOKE ALL ON FUNCTION verdify_matview_refresh_runtime.fn_refresh_band_compliance() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION verdify_matview_refresh_runtime.fn_refresh_band_compliance() TO verdify_matview_refresh_runtime;

-- The scorecard reads the one definition. Existing columns keep their names, order and types; the
-- direction, per-axis day/night and reconstructed shares are appended.
CREATE OR REPLACE VIEW public.v_daily_scorecard AS
WITH alert_origin AS MATERIALIZED (
    SELECT min(a.created_at) AS first_at FROM public.alert_log a
)
SELECT
    d.date,
    b.temp_in_band_pct,
    b.vpd_in_band_pct,
    b.both_in_band_day_pct AS time_in_band_day_pct,
    b.both_in_band_night_pct AS time_in_band_night_pct,
    CASE WHEN b.date IS NULL THEN 'not graded against the served band'
         WHEN b.both_in_band_day_pct IS NULL OR b.both_in_band_night_pct IS NULL THEN 'no graded readings'
    END AS band_reason,
    d.water_used_gal AS water_gal,
    -- daily_summary_live fills water_used_gal only when v_water_attribution_daily can score the
    -- day. v_water_meter_daily has no time bound (it windows every metered climate row and
    -- meter event: 2.5 s for a 30-day read on 2026-11-02), so the reason comes from bounded
    -- checks in its vocabulary: today is partial_day, a day with no meter reading is
    -- unavailable, and any other null day is incomplete.
    CASE WHEN d.water_used_gal IS NOT NULL THEN NULL
         WHEN d.date >= (now() AT TIME ZONE 'America/Denver')::date THEN 'partial_day'
         WHEN NOT EXISTS (SELECT 1 FROM public.climate m
                           WHERE m.ts >= d.day_start AND m.ts < d.day_end
                             AND m.water_total_gal IS NOT NULL) THEN 'unavailable'
         ELSE 'incomplete'
    END AS water_reason,
    (SELECT CASE WHEN count(*) > 0 THEN count(*) FILTER (WHERE l.status = 'plan_written') END
       FROM public.planner_trigger_ledger l
      WHERE l.event_type IN ('MIDNIGHT', 'SUNRISE', 'SUNSET')
        AND l.due_at >= d.day_start AND l.due_at < d.day_end)::integer AS iris_required_written,
    (SELECT CASE WHEN count(*) > 0 THEN count(*) FILTER (WHERE l.status IN (
                'acked', 'action_completed', 'neutral_fallback', 'wrong_action',
                'delivery_failed', 'timed_out', 'missed')) END
       FROM public.planner_trigger_ledger l
      WHERE l.event_type IN ('MIDNIGHT', 'SUNRISE', 'SUNSET')
        AND l.due_at >= d.day_start AND l.due_at < d.day_end)::integer AS iris_required_missed,
    CASE WHEN d.day_end > (SELECT o.first_at FROM alert_origin o) THEN
        (SELECT count(*) FROM public.alert_log a
          WHERE a.severity = 'critical' AND a.ts >= d.day_start AND a.ts < d.day_end)::integer
    END AS alerts_critical,
    CASE WHEN d.day_end > (SELECT o.first_at FROM alert_origin o) THEN
        (SELECT count(*) FROM public.alert_log a
          WHERE a.severity = 'warning' AND a.ts >= d.day_start AND a.ts < d.day_end)::integer
    END AS alerts_warning,
    CASE WHEN d.day_end > (SELECT o.first_at FROM alert_origin o) THEN
        (SELECT count(*) FROM public.alert_log a
          WHERE a.severity = 'info' AND a.ts >= d.day_start AND a.ts < d.day_end)::integer
    END AS alerts_info,
    d.captured_at,
    b.temp_below_band_pct,
    b.temp_above_band_pct,
    b.vpd_below_band_pct,
    b.vpd_above_band_pct,
    b.temp_in_band_day_pct,
    b.temp_in_band_night_pct,
    b.vpd_in_band_day_pct,
    b.vpd_in_band_night_pct,
    b.reconstructed_pct AS band_reconstructed_pct
  FROM (SELECT s.date, s.water_used_gal, s.captured_at,
               s.date::timestamp AT TIME ZONE 'America/Denver' AS day_start,
               (s.date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end
          FROM public.daily_summary s) d
  LEFT JOIN public.daily_band_compliance b ON b.date = d.date;

COMMENT ON VIEW public.v_daily_scorecard IS
    'Graphs scorecard (VD-56, API-04 daily.* keys): one row per daily_summary.date (America/Denver). '
    'Time in band from daily_band_compliance (290, #1358: graded against the band the controller served): '
    'temperature and VPD for the whole day with below and above, day and night per axis, and both axes by day '
    'and night (time_in_band_day_pct, time_in_band_night_pct), null with band_reason when the day is not graded; '
    'gallons with water_reason (partial_day, unavailable or incomplete); Iris required plans written and missed; '
    'alerts raised by severity. Null means no data, never 0.';
COMMENT ON MATERIALIZED VIEW public.mv_zone_band_grade IS
    'Retired by 290 (#1314, #1358): nothing schedules its refresh (only the one-shot reconciler of 2026-06-12 '
    'filled it) and it grades against crop_target_profiles, not the served band. Nothing user-facing reads it; '
    'daily_band_compliance is the day and night time in band.';

DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'a752a89a07b5b90ecbe6ccaf44358492aa8da6c562d608c8880d24d77088af7e'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'b17c9da67fe6456c1fd470ac5dc82dfdd20320b3d808c482162b11952bd57011'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR EXISTS(SELECT 1 FROM pg_depend dep
            WHERE dep.refobjid='public.mv_zone_band_grade'::regclass AND dep.classid='pg_rewrite'::regclass
              AND dep.objid IN (SELECT oid FROM pg_rewrite WHERE ev_class='public.v_daily_scorecard'::regclass))
        OR NOT EXISTS(SELECT 1 FROM pg_class c WHERE c.oid='public.daily_band_compliance'::regclass
            AND c.relkind='r' AND pg_get_userbyid(c.relowner)='verdify' AND c.relacl IS NULL)
        OR EXISTS(SELECT 1 FROM pg_proc p
            WHERE p.oid IN ('public.fn_band_compliance_5m(timestamptz,timestamptz)'::regprocedure,
                            'public.fn_band_compliance_day(date)'::regprocedure,
                            'public.fn_band_compliance_store(date)'::regprocedure,
                            'verdify_matview_refresh_runtime.fn_refresh_band_compliance()'::regprocedure)
              AND (pg_get_userbyid(p.proowner) <> 'verdify'
                   OR EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
                       WHERE a.grantee NOT IN (p.proowner,
                           (SELECT oid FROM pg_roles WHERE rolname='verdify_matview_refresh_runtime')))))
    THEN RAISE EXCEPTION '290 changed a runtime boundary'; END IF;
END $successor$;
