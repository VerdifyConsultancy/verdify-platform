-- Run only inside an explicit rollback-only transaction after exact305 (#1365), as the superuser owner:
-- scripts/qualify-migration-successor.py --native-fixture runs it at the end of its final session. The migration has
-- filled the dry-out window of every stored day. The matview-refresh login attests and runs the CronJob's call, which
-- now stores the window with the rest of the day. Then: every stored day before today has its window, at most its
-- graded minutes at the floor, and night_dried_out exactly when it holds 240 minutes or more; the 25-hour day
-- 2026-11-01 grades 420 minutes (00:00 to 06:00 MDT/MST is 7 hours) and an ordinary day 360; no night from
-- 2026-10-26 to 2026-11-06 dried out (read only on 2026-11-06, before the dry-out had run a full night: 0 to 115
-- minutes at the floor); today's window is null until 06:00 local; and fn_band_dryout_night agrees with the stored
-- minutes for the last 7 days. Nothing outside the rollback is written. The last line reports the result.
SET SESSION AUTHORIZATION verdify_matview_refresh_runtime_login;
DO $refresh$
DECLARE started timestamptz := clock_timestamp();
BEGIN
    IF verdify_matview_refresh_runtime.fn_attest_login() IS NOT TRUE THEN
        RAISE EXCEPTION '305 matview-refresh login does not attest';
    END IF;
    PERFORM set_config('dryout305.days', verdify_matview_refresh_runtime.fn_refresh_band_compliance()::text, true);
    PERFORM set_config('dryout305.ms', round(extract(epoch FROM clock_timestamp() - started) * 1000)::text, true);
END $refresh$;
RESET SESSION AUTHORIZATION;

CREATE TEMP TABLE dryout_fixture ON COMMIT DROP AS
SELECT b.date, b.night_dryout_graded_min, b.night_dryout_min, b.night_dried_out, n.graded_min, n.floor_min
  FROM public.daily_band_compliance b
  LEFT JOIN LATERAL public.fn_band_dryout_night(b.date) n ON b.date >= (now() AT TIME ZONE 'America/Denver')::date - 7;

DO $fixture$
DECLARE
    today date := (now() AT TIME ZONE 'America/Denver')::date;
    closed boolean := now() >= (today::timestamp + interval '6 hours') AT TIME ZONE 'America/Denver';
BEGIN
    IF current_setting('dryout305.days')::integer < 2
       OR EXISTS(SELECT 1 FROM dryout_fixture WHERE date < today AND night_dryout_graded_min IS NULL)
       OR EXISTS(SELECT 1 FROM dryout_fixture
                  WHERE night_dryout_min > night_dryout_graded_min
                     OR night_dried_out IS DISTINCT FROM (night_dryout_min >= 240))
       OR NOT EXISTS(SELECT 1 FROM dryout_fixture WHERE date = DATE '2026-11-01' AND night_dryout_graded_min = 420)
       OR NOT EXISTS(SELECT 1 FROM dryout_fixture WHERE date = DATE '2026-11-04' AND night_dryout_graded_min = 360)
       OR EXISTS(SELECT 1 FROM dryout_fixture
                  WHERE date BETWEEN DATE '2026-10-26' AND DATE '2026-11-06' AND night_dried_out IS NOT FALSE)
       OR EXISTS(SELECT 1 FROM dryout_fixture
                  WHERE date = today AND (night_dryout_graded_min IS NULL) = closed)
       OR EXISTS(SELECT 1 FROM dryout_fixture
                  WHERE date >= today - 7
                    AND (graded_min, floor_min) IS DISTINCT FROM (night_dryout_graded_min, night_dryout_min))
    THEN RAISE EXCEPTION '305 nightly dry-out native fixture failed'; END IF;
END $fixture$;

SELECT 'QUALIFY native_fixture ' || json_build_object(
    'checks', json_build_array('refresh_login_attests', 'refresh_runs', 'every_past_day_has_its_window',
                               'minutes_add_up', 'flag_is_four_hours', 'dst_day_420_min', 'ordinary_day_360_min',
                               'no_night_dried_out_before_the_dryout', 'today_null_until_06_local',
                               'function_matches_stored_last_7_days'),
    'refreshed_days', current_setting('dryout305.days')::integer,
    'refresh_ms', current_setting('dryout305.ms')::numeric,
    'nights', (SELECT json_agg(json_build_array(date, night_dryout_graded_min, night_dryout_min, night_dried_out)
                               ORDER BY date)
                 FROM dryout_fixture WHERE date >= DATE '2026-10-26'),
    'all_passed', true,
    'attested', json_build_object('matview_refresh', true))::text;
