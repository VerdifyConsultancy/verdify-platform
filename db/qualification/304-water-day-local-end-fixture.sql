-- Run only inside an explicit rollback-only transaction after exact304 (#1283), as the superuser owner:
-- scripts/qualify-migration-successor.py --native-fixture runs it at the end of its final session. Production runs
-- its sessions in UTC, the time zone that exposed the bug; the days are read in UTC and again 14 hours east of it
-- and must agree. No day's end is a fixed `+ 1 day` any more; the 25-hour day 2026-11-01 (06:00Z to 07:00Z the
-- next day) still reads complete, because its last sample (06:59:24Z) is inside the last 10 minutes of its own end; the twelve days 2026-10-25 to
-- 2026-11-05 read the completeness and quality they read before 304 (read 2026-11-06); and the dashboard login
-- attests and reads every column of the last 30 days within 10 s. Nothing is written. The last line reports the
-- result to the qualifier.
SET LOCAL TimeZone = 'UTC';
CREATE TEMP TABLE water_day_fixture ON COMMIT DROP AS
SELECT (m.day AT TIME ZONE 'America/Denver')::date AS local_day, m.day, m.last_raw_ts, m.is_complete_day, m.quality
  FROM public.v_water_meter_daily m
 WHERE m.day >= TIMESTAMPTZ '2026-10-25 06:00:00+00' AND m.day < TIMESTAMPTZ '2026-11-06 07:00:00+00';
-- The same days read in a session 14 hours east of UTC, where `day + 1 day` and day::date both differ.
SET LOCAL TimeZone = 'Pacific/Kiritimati';
CREATE TEMP TABLE water_day_east_fixture ON COMMIT DROP AS
SELECT (m.day AT TIME ZONE 'America/Denver')::date AS local_day, m.day, m.last_raw_ts, m.is_complete_day, m.quality
  FROM public.v_water_meter_daily m
 WHERE m.day >= TIMESTAMPTZ '2026-10-25 06:00:00+00' AND m.day < TIMESTAMPTZ '2026-11-06 07:00:00+00';
SET LOCAL TimeZone = 'UTC';

DO $fixture$
BEGIN
    IF (SELECT count(*) FROM water_day_fixture) <> 12
       OR EXISTS((TABLE water_day_fixture EXCEPT TABLE water_day_east_fixture)
                 UNION ALL (TABLE water_day_east_fixture EXCEPT TABLE water_day_fixture))
       OR EXISTS(SELECT 1 FROM water_day_fixture f
                  WHERE (f.local_day, f.is_complete_day, f.quality) NOT IN (
                        (DATE '2026-10-25', true, 'ok'), (DATE '2026-10-26', true, 'ok'),
                        (DATE '2026-10-27', true, 'ok'), (DATE '2026-10-28', true, 'ok'),
                        (DATE '2026-10-29', true, 'discontinuous'), (DATE '2026-10-30', true, 'discontinuous'),
                        (DATE '2026-10-31', true, 'ok'), (DATE '2026-11-01', true, 'discontinuous'),
                        (DATE '2026-11-02', true, 'ok'), (DATE '2026-11-03', true, 'discontinuous'),
                        (DATE '2026-11-04', true, 'ok'), (DATE '2026-11-05', false, 'discontinuous')))
       OR NOT EXISTS(SELECT 1 FROM water_day_fixture
                      WHERE local_day = DATE '2026-11-01' AND day = TIMESTAMPTZ '2026-11-01 06:00:00+00'
                        AND last_raw_ts > TIMESTAMPTZ '2026-11-02 06:50:00+00'
                        AND last_raw_ts < TIMESTAMPTZ '2026-11-02 07:00:00+00')
       OR position('''1 day''::interval' IN pg_get_viewdef('public.v_water_meter_daily'::regclass, true)) > 0
    THEN RAISE EXCEPTION '304 water day native fixture failed'; END IF;
END $fixture$;

-- SET SESSION AUTHORIZATION needs the superuser owner; the rollback undoes it. The login keeps its result in
-- transaction-local settings, so it needs no table of its own.
SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;
DO $dashboard$
DECLARE
    started timestamptz := clock_timestamp();
BEGIN
    IF verdify_dashboard_runtime.fn_attest_login() IS NOT TRUE THEN
        RAISE EXCEPTION '304 dashboard login does not attest';
    END IF;
    -- Every column: a view's unused columns plan away, and with them the reads they make.
    PERFORM * FROM public.v_water_meter_daily m WHERE m.day >= now() - interval '30 days';
    IF NOT FOUND THEN RAISE EXCEPTION '304 dashboard login read no water day'; END IF;
    PERFORM set_config('water304.ms', round(extract(epoch FROM clock_timestamp() - started) * 1000)::text, true);
END $dashboard$;
RESET SESSION AUTHORIZATION;

DO $timing$
BEGIN
    IF current_setting('water304.ms')::numeric > 10000 THEN
        RAISE EXCEPTION '304 water day read took % ms', current_setting('water304.ms');
    END IF;
END $timing$;

SELECT 'QUALIFY native_fixture ' || json_build_object(
    'checks', json_build_array('same_days_in_utc_and_utc_plus_14', 'no_fixed_one_day_end',
                               'twenty_five_hour_day_complete_by_its_own_end', 'known_days_unchanged',
                               'dashboard_login_attests', 'dashboard_login_reads_every_column', 'read_within_10_s'),
    'days', (SELECT json_agg(json_build_array(local_day, is_complete_day, quality) ORDER BY local_day)
               FROM water_day_fixture),
    'read_ms', current_setting('water304.ms')::numeric,
    'all_passed', true,
    'attested', json_build_object('dashboard', true))::text;
