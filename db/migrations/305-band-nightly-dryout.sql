-- 305 (#1365): the nightly dry-out flag in the 290 rollup.
-- Jason, 2026-11-05 (docs/design/dashboard/decisions.md): the night floor stays at least 0.74 kPa, and the goal is
-- a nightly dry-out, at least 4 h at or above the floor between 00:00 and 06:00 America/Denver. The floor is the one
-- the firmware's night dry-out uses (#1385, night_dryout_floor_kpa): the served band's vpd_low, never below
-- 0.74 kPa. 305 adds to daily_band_compliance, for the dry-out window that opens at the day's own local midnight:
--   night_dryout_graded_min  5-minute house-average buckets with a VPD reading in the window (360, or 300/420 on
--                            the DST days), null until the window has closed;
--   night_dryout_min         of those, the minutes at or above max(vpd_low of the band in force, 0.74 kPa);
--   night_dried_out          generated: night_dryout_min >= 240, null while the minutes are null.
-- fn_band_dryout_night(date) computes the window from fn_band_compliance_5m (the same buckets and band in force as
-- the rest of the rollup, the reconstructed band where an audit row is missing); fn_band_compliance_store fills the
-- columns with the rest of the day, so the matview-refresh CronJob keeps them current, and the rows already stored
-- (2026-09-01 on) are filled here. Nothing else in the rollup, daily_summary or v_daily_scorecard changes.
-- No runtime seal moves: the table keeps no ACL entry, the new function is verdify's with PUBLIC's EXECUTE revoked
-- and is reached only through the refresh duty's SECURITY DEFINER entry point. Both blocks pin exact304's seals.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=304 AND filename='db/migrations/304-water-day-local-end.sql'
            AND sha256='2abfdb7625c5ee53a58255e94d47cdb4aa669ac07bafe171a709c103b63d1485'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>304)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM 'af3f7ef2493b94b11e9e7c3f5ead6bf61c8542e477d26da117f3930b0530d873'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR to_regclass('public.daily_band_compliance') IS NULL
        OR to_regprocedure('public.fn_band_dryout_night(date)') IS NOT NULL
        OR EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='public.daily_band_compliance'::regclass
            AND attname IN ('night_dryout_graded_min','night_dryout_min','night_dried_out') AND NOT attisdropped)
    THEN RAISE EXCEPTION '305 refuses an unqualified predecessor'; END IF;
END $predecessor$;

CREATE FUNCTION public.fn_band_dryout_night(p_day date)
RETURNS TABLE (graded_min integer, floor_min integer)
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $dryout$
WITH w AS (
    SELECT p_day::timestamp AT TIME ZONE 'America/Denver' AS opens,
           (p_day::timestamp + interval '6 hours') AT TIME ZONE 'America/Denver' AS closes
)
SELECT CASE WHEN now() >= w.closes THEN (5 * count(g.vpd_kpa))::integer END,
       CASE WHEN now() >= w.closes
            THEN (5 * count(*) FILTER (WHERE g.vpd_kpa >= greatest(g.vpd_low, 0.74::double precision)))::integer
       END
  FROM w
  LEFT JOIN LATERAL public.fn_band_compliance_5m(w.opens, w.closes) g ON true
 GROUP BY w.closes
$dryout$;
COMMENT ON FUNCTION public.fn_band_dryout_night(date) IS
    'The nightly dry-out window of one America/Denver day (305, #1365): 00:00 to 06:00 local, graded with '
    'fn_band_compliance_5m. graded_min: minutes with a house VPD reading; floor_min: minutes at or above '
    'max(vpd_low of the band in force, 0.74 kPa), the firmware''s night dry-out floor. Both null until 06:00 local.';

ALTER TABLE public.daily_band_compliance
    ADD COLUMN night_dryout_graded_min integer,
    ADD COLUMN night_dryout_min integer;
ALTER TABLE public.daily_band_compliance
    ADD COLUMN night_dried_out boolean GENERATED ALWAYS AS (night_dryout_min >= 240) STORED,
    ADD CONSTRAINT daily_band_compliance_dryout_minutes CHECK (
        (night_dryout_graded_min IS NULL) = (night_dryout_min IS NULL)
        AND night_dryout_graded_min BETWEEN 0 AND 420
        AND night_dryout_min BETWEEN 0 AND night_dryout_graded_min);
COMMENT ON COLUMN public.daily_band_compliance.night_dryout_graded_min IS
    'Minutes with a house VPD reading between 00:00 and 06:00 local (305, #1365); null until 06:00 local.';
COMMENT ON COLUMN public.daily_band_compliance.night_dryout_min IS
    'Minutes between 00:00 and 06:00 local with house VPD at or above max(served vpd_low, 0.74 kPa) (305, #1365).';
COMMENT ON COLUMN public.daily_band_compliance.night_dried_out IS
    'The nightly dry-out flag (305, #1365, Jason 2026-11-05): at least 4 h at or above the floor between 00:00 and '
    '06:00 local. Null when the window has not closed or was not graded.';

CREATE OR REPLACE FUNCTION public.fn_band_compliance_store(p_day date)
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
        day_both_graded_min, day_both_in_min, night_both_graded_min, night_both_in_min,
        night_dryout_graded_min, night_dryout_min, computed_at)
    SELECT p_day, 'served-band-5m-v1', d.graded_min, d.reconstructed_min,
           d.temp_graded_min, d.temp_in_min, d.temp_below_min, d.temp_above_min,
           d.vpd_graded_min, d.vpd_in_min, d.vpd_below_min, d.vpd_above_min,
           d.both_graded_min, d.both_in_min,
           d.day_temp_graded_min, d.day_temp_in_min, d.night_temp_graded_min, d.night_temp_in_min,
           d.day_vpd_graded_min, d.day_vpd_in_min, d.night_vpd_graded_min, d.night_vpd_in_min,
           d.day_both_graded_min, d.day_both_in_min, d.night_both_graded_min, d.night_both_in_min,
           n.graded_min, n.floor_min, now()
      FROM public.fn_band_compliance_day(p_day) d
     CROSS JOIN public.fn_band_dryout_night(p_day) n
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
        night_dryout_graded_min = EXCLUDED.night_dryout_graded_min, night_dryout_min = EXCLUDED.night_dryout_min,
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
    'Grade one America/Denver day with fn_band_compliance_day and its dry-out window with fn_band_dryout_night '
    '(305), upsert daily_band_compliance and copy the whole-day shares and below/above hours into daily_summary''s '
    'legacy compliance and stress-hour columns (290, #1358). '
    'Operator backfill, as verdify: SELECT public.fn_band_compliance_store(d::date) FROM generate_series(...) d.';
REVOKE ALL ON FUNCTION public.fn_band_dryout_night(date) FROM PUBLIC;

-- The rows already stored get their dry-out window; days whose window has not closed stay null.
UPDATE public.daily_band_compliance b
   SET (night_dryout_graded_min, night_dryout_min) =
       (SELECT n.graded_min, n.floor_min FROM public.fn_band_dryout_night(b.date) n);

DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM 'af3f7ef2493b94b11e9e7c3f5ead6bf61c8542e477d26da117f3930b0530d873'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR NOT EXISTS(SELECT 1 FROM pg_class c WHERE c.oid='public.daily_band_compliance'::regclass
            AND c.relkind='r' AND pg_get_userbyid(c.relowner)='verdify' AND c.relacl IS NULL)
        OR EXISTS(SELECT 1 FROM pg_proc p
            WHERE p.oid IN ('public.fn_band_dryout_night(date)'::regprocedure,
                            'public.fn_band_compliance_store(date)'::regprocedure)
              AND (pg_get_userbyid(p.proowner) <> 'verdify'
                   OR EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
                       WHERE a.grantee <> p.proowner)))
    THEN RAISE EXCEPTION '305 changed a runtime boundary'; END IF;
END $successor$;
