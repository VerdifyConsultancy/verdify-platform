-- VD-56 (#1169) M6: v_daily_scorecard, one row per daily_summary.date (an America/Denver day;
-- daily_summary_live keeps today's row current, and a day with no summary row is a gap) for
-- the Graphs scorecard's daily.* keys on API-04. Additive: one view owned by verdify and no
-- grant (M2, #1179, grants it to the dashboard login), so every runtime receipt keeps its
-- exact predecessor value; the two checks below prove all three seals before and after.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=279 AND filename='db/migrations/279-authentic-meter-source-clock-consumers.sql'
            AND sha256='e610493bc9c8b9ec376b520e0c304f7bbd9f4d73d6d98e765e54408758eb3114'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>279)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '9904555fb62c32503b99cd3cd447c4412afd0451782d7216cf7a21557339357e'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '0e03e19dd7ceaf65c54e14d8fa38dacffa8472e2173d0d7f41a6a03ea758d8f7'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR to_regclass('public.v_daily_scorecard') IS NOT NULL
    THEN RAISE EXCEPTION '282 refuses an unqualified predecessor'; END IF;
END $predecessor$;

-- Each column is a scalar subquery bounded to its day, so a read evaluates only the columns
-- it selects, and only for the days it asks for.
CREATE VIEW public.v_daily_scorecard AS
WITH band_cover AS MATERIALIZED (
    -- mv_zone_band_grade is rebuilt over the 3 days before each refresh: a day gets its
    -- day and night split only while those hourly buckets cover the whole day.
    SELECT min(g.bucket) AS first_bucket, max(g.bucket) + interval '1 hour' AS end_bucket
      FROM public.mv_zone_band_grade g
     WHERE g.zone = 'center'
), alert_origin AS MATERIALIZED (
    SELECT min(a.created_at) AS first_at FROM public.alert_log a
)
SELECT
    d.date,
    d.temp_compliance_pct AS time_in_band_temp_pct,
    d.vpd_compliance_pct AS time_in_band_vpd_pct,
    (SELECT round((100.0 * sum(g.sum_zone_score) / nullif(sum(g.n), 0))::numeric, 1)::double precision
       FROM public.mv_zone_band_grade g
      WHERE g.zone = 'center' AND g.bucket >= d.day_start AND g.bucket < d.day_end
        AND c.first_bucket <= d.day_start AND c.end_bucket >= d.day_end
        AND public.fn_solar_phase(g.bucket + interval '30 minutes') < 2) AS time_in_band_day_pct,
    (SELECT round((100.0 * sum(g.sum_zone_score) / nullif(sum(g.n), 0))::numeric, 1)::double precision
       FROM public.mv_zone_band_grade g
      WHERE g.zone = 'center' AND g.bucket >= d.day_start AND g.bucket < d.day_end
        AND c.first_bucket <= d.day_start AND c.end_bucket >= d.day_end
        AND public.fn_solar_phase(g.bucket + interval '30 minutes') >= 2) AS time_in_band_night_pct,
    CASE WHEN c.first_bucket <= d.day_start AND c.end_bucket >= d.day_end THEN NULL
         ELSE 'band grade kept 3 days' END AS band_reason,
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
    d.captured_at
  FROM (SELECT s.date, s.temp_compliance_pct, s.vpd_compliance_pct, s.water_used_gal, s.captured_at,
               s.date::timestamp AT TIME ZONE 'America/Denver' AS day_start,
               (s.date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end
          FROM public.daily_summary s) d
 CROSS JOIN band_cover c;

COMMENT ON VIEW public.v_daily_scorecard IS
    'Graphs scorecard (VD-56, API-04 daily.* keys): one row per daily_summary.date (America/Denver). '
    'Whole-day temperature and VPD time in band for every day (daily_summary); day and night time '
    'in band from mv_zone_band_grade zone center, null with band_reason for days the 3-day grade '
    'does not cover whole; gallons with water_reason (partial_day, unavailable or incomplete); '
    'Iris required plans written and missed; alerts raised by severity. Null means no data, never 0.';

DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '9904555fb62c32503b99cd3cd447c4412afd0451782d7216cf7a21557339357e'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '0e03e19dd7ceaf65c54e14d8fa38dacffa8472e2173d0d7f41a6a03ea758d8f7'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR NOT EXISTS(SELECT 1 FROM pg_class c WHERE c.oid='public.v_daily_scorecard'::regclass
            AND c.relkind='v' AND c.relacl IS NULL AND pg_get_userbyid(c.relowner)='verdify')
    THEN RAISE EXCEPTION '282 changed a runtime boundary'; END IF;
END $successor$;
