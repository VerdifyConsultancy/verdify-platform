-- Run only inside an explicit rollback-only transaction after exact 290 (#1358), as the superuser
-- owner: scripts/qualify-migration-successor.py --native-fixture runs it at the end of its final
-- session. The matview-refresh login attests and runs the CronJob's call, which grades today,
-- yesterday and the 31 newest missing days against the band the controller served. Then: every
-- stored day adds up, daily_summary carries the copies, the acceptance week 2026-10-29..11-04 reads
-- temperature >= 90 % and VPD 20-70 % in band (#1358 measured ~92-99 % and ~33-63 %), the scorecard
-- has a day and night split for every graded day of the last 30, a 30-day read stays under 5 s, and
-- the dashboard login reads every scorecard column. The rollback discards all of it. The last line
-- reports the result to the qualifier.
-- SET SESSION AUTHORIZATION needs the superuser owner; the rollback undoes it. The login keeps its
-- result in transaction-local settings, so it needs no table of its own.
SET SESSION AUTHORIZATION verdify_matview_refresh_runtime_login;
DO $refresh$
DECLARE started timestamptz := clock_timestamp();
BEGIN
    IF verdify_matview_refresh_runtime.fn_attest_login() IS NOT TRUE THEN
        RAISE EXCEPTION '290 matview-refresh login does not attest';
    END IF;
    PERFORM set_config('band290.days', verdify_matview_refresh_runtime.fn_refresh_band_compliance()::text, true);
    PERFORM set_config('band290.ms', round(extract(epoch FROM clock_timestamp() - started) * 1000)::text, true);
END $refresh$;
RESET SESSION AUTHORIZATION;
CREATE TEMP TABLE band_refresh_fixture ON COMMIT DROP AS
SELECT current_setting('band290.days')::integer AS days, current_setting('band290.ms')::numeric AS ms;

CREATE TEMP TABLE band_scorecard_fixture ON COMMIT DROP AS
SELECT v.*, clock_timestamp() AS read_at, statement_timestamp() AS started_at
  FROM public.v_daily_scorecard v
 WHERE v.date >= (now() AT TIME ZONE 'America/Denver')::date - 29;
DO $fixture$
BEGIN
 IF (SELECT days FROM band_refresh_fixture) < 2
  OR NOT EXISTS(SELECT 1 FROM public.daily_band_compliance
                 WHERE date = (now() AT TIME ZONE 'America/Denver')::date - 1 AND graded_min > 0)
  OR EXISTS(SELECT 1 FROM public.daily_band_compliance b JOIN public.daily_summary s USING (date)
             WHERE s.temp_compliance_pct IS DISTINCT FROM b.temp_in_band_pct
                OR s.vpd_compliance_pct IS DISTINCT FROM b.vpd_in_band_pct
                OR s.compliance_pct IS DISTINCT FROM b.both_in_band_pct
                OR s.stress_hours_cold IS DISTINCT FROM
                   CASE WHEN b.temp_graded_min > 0 THEN round(b.temp_below_min / 60.0, 2)::double precision END
                OR s.stress_hours_vpd_low IS DISTINCT FROM
                   CASE WHEN b.vpd_graded_min > 0 THEN round(b.vpd_below_min / 60.0, 2)::double precision END)
  OR (SELECT count(*) FROM public.daily_band_compliance
       WHERE date BETWEEN DATE '2026-10-29' AND DATE '2026-11-04') <> 7
  OR EXISTS(SELECT 1 FROM public.daily_band_compliance
             WHERE date BETWEEN DATE '2026-10-29' AND DATE '2026-11-04'
               AND (temp_in_band_pct IS NULL OR temp_in_band_pct < 90
                    OR vpd_in_band_pct IS NULL OR vpd_in_band_pct NOT BETWEEN 20 AND 70))
  OR EXISTS(SELECT 1 FROM band_scorecard_fixture f JOIN public.daily_band_compliance b USING (date)
             WHERE b.day_both_graded_min > 0 AND b.night_both_graded_min > 0
               AND (f.time_in_band_day_pct IS NULL OR f.time_in_band_night_pct IS NULL
                    OR f.band_reason IS NOT NULL))
  OR EXISTS(SELECT 1 FROM band_scorecard_fixture
             WHERE band_reason IS NOT NULL
               AND band_reason NOT IN ('not graded against the served band', 'no graded readings'))
  OR (SELECT max(read_at - started_at) FROM band_scorecard_fixture) > interval '5 seconds'
 THEN RAISE EXCEPTION '290 band compliance native fixture failed'; END IF;
END $fixture$;

-- SET SESSION AUTHORIZATION needs the superuser owner; the rollback undoes it.
SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;
DO $dashboard$
BEGIN
    IF verdify_dashboard_runtime.fn_attest_login() IS NOT TRUE THEN
        RAISE EXCEPTION '290 dashboard login does not attest';
    END IF;
    -- Every column: a view's unused columns plan away, and with them the reads they make.
    PERFORM * FROM public.v_daily_scorecard v
      WHERE v.date >= (now() AT TIME ZONE 'America/Denver')::date - 3;
    IF NOT FOUND THEN RAISE EXCEPTION '290 dashboard login read no scorecard rows'; END IF;
END $dashboard$;
RESET SESSION AUTHORIZATION;

SELECT 'QUALIFY native_fixture ' || json_build_object(
    'checks', json_build_array('refresh_login_attests', 'refresh_runs', 'yesterday_graded', 'summary_copied',
                               'acceptance_week_in_range', 'day_night_split_complete', 'reasons_known',
                               'read_within_5_s', 'dashboard_login_attests', 'dashboard_login_reads_every_column'),
    'refreshed_days', (SELECT days FROM band_refresh_fixture),
    'refresh_ms', (SELECT ms FROM band_refresh_fixture),
    'acceptance_week', (SELECT json_agg(json_build_array(date, temp_in_band_pct, vpd_in_band_pct,
                                                         vpd_in_band_day_pct, vpd_in_band_night_pct) ORDER BY date)
                          FROM public.daily_band_compliance
                         WHERE date BETWEEN DATE '2026-10-29' AND DATE '2026-11-04'),
    'split_days', (SELECT count(*) FROM band_scorecard_fixture WHERE band_reason IS NULL),
    'read_ms', (SELECT round(extract(epoch FROM max(read_at - started_at)) * 1000) FROM band_scorecard_fixture),
    'all_passed', true,
    'attested', json_build_object('matview_refresh', true, 'dashboard', true))::text;
