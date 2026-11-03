-- Run only inside an explicit rollback-only transaction after exact 285 (VD-56), as the
-- superuser owner: scripts/qualify-migration-successor.py --native-fixture runs it at the end
-- of its final session. Reads only. Over the last 30 days the scorecard keeps one row per
-- daily_summary date, copies the whole-day compliance, gives every null split and every null
-- gallon a reason, and answers a full 30-day read well inside the dashboard login's 10 s
-- statement timeout. Its only grant is the dashboard duty's SELECT, so neither digest takes it
-- in. The dashboard login attests, reads every column and runs the view's one helper. The last
-- line reports the result to the qualifier.
CREATE TEMP TABLE scorecard_fixture ON COMMIT DROP AS
SELECT v.*, clock_timestamp() AS read_at, statement_timestamp() AS started_at
  FROM public.v_daily_scorecard v
 WHERE v.date >= (now() AT TIME ZONE 'America/Denver')::date - 29;
DO $fixture$
BEGIN
 IF (SELECT count(*) FROM scorecard_fixture)
      <> (SELECT count(*) FROM public.daily_summary
           WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - 29)
  OR EXISTS(SELECT 1 FROM scorecard_fixture f JOIN public.daily_summary s USING (date)
             WHERE f.temp_in_band_pct IS DISTINCT FROM s.temp_compliance_pct
                OR f.vpd_in_band_pct IS DISTINCT FROM s.vpd_compliance_pct
                OR f.water_gal IS DISTINCT FROM s.water_used_gal)
  OR EXISTS(SELECT 1 FROM scorecard_fixture
             WHERE band_reason IS DISTINCT FROM NULL AND band_reason <> 'band grade kept 3 days')
  OR EXISTS(SELECT 1 FROM scorecard_fixture
             WHERE band_reason IS NOT NULL
               AND (time_in_band_day_pct IS NOT NULL OR time_in_band_night_pct IS NOT NULL))
  OR EXISTS(SELECT 1 FROM scorecard_fixture
             WHERE (water_gal IS NULL) <> (water_reason IS NOT NULL)
                OR water_reason NOT IN ('partial_day', 'unavailable', 'incomplete'))
  OR EXISTS(SELECT 1 FROM scorecard_fixture
             WHERE iris_required_written < 0 OR iris_required_missed < 0
                OR alerts_critical < 0 OR alerts_warning < 0 OR alerts_info < 0)
  OR (SELECT max(read_at - started_at) FROM scorecard_fixture) > interval '5 seconds'
  OR EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
             WHERE c.oid = 'public.v_daily_scorecard'::regclass AND a.grantee <> c.relowner
               AND (a.grantee <> 'verdify_dashboard_runtime'::regrole
                    OR a.privilege_type <> 'SELECT' OR a.is_grantable))
  OR NOT has_table_privilege('verdify_dashboard_runtime_login', 'public.v_daily_scorecard', 'SELECT')
 THEN RAISE EXCEPTION 'daily scorecard native fixture failed'; END IF;
END $fixture$;

-- SET SESSION AUTHORIZATION needs the superuser owner; the rollback undoes it.
SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;
DO $dashboard$
BEGIN
    IF verdify_dashboard_runtime.fn_attest_login() IS NOT TRUE THEN
        RAISE EXCEPTION '285 dashboard login does not attest';
    END IF;
    -- Every column: a view's unused columns plan away, and with them the EXECUTE check on what they call.
    PERFORM * FROM public.v_daily_scorecard v
      WHERE v.date >= (now() AT TIME ZONE 'America/Denver')::date - 3;
    IF NOT FOUND THEN RAISE EXCEPTION '285 dashboard login read no scorecard rows'; END IF;
    -- The view's one helper, and the helpers beneath it, through PUBLIC's EXECUTE.
    PERFORM public.fn_solar_phase(now());
END $dashboard$;
RESET SESSION AUTHORIZATION;

SELECT 'QUALIFY native_fixture ' || json_build_object(
    'checks', json_build_array('one_row_per_day', 'compliance_copied', 'reasons_complete', 'counts_not_negative',
                               'read_within_5_s', 'grant_exact', 'dashboard_login_attests',
                               'dashboard_login_reads_every_column', 'solar_phase_through_public'),
    'days', (SELECT count(*) FROM scorecard_fixture),
    'split_days', (SELECT count(*) FROM scorecard_fixture WHERE band_reason IS NULL),
    'null_gallon_days', (SELECT count(*) FROM scorecard_fixture WHERE water_gal IS NULL),
    'read_ms', (SELECT round(extract(epoch FROM max(read_at - started_at)) * 1000) FROM scorecard_fixture),
    'all_passed', true,
    'attested', json_build_object('dashboard', true))::text;
