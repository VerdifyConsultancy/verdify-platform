-- Run only inside an explicit rollback-only transaction after exact 282 (VD-56).
-- Reads only. Over the last 30 days the scorecard keeps one row per daily_summary date,
-- copies the whole-day compliance, gives every null split and every null gallon a reason,
-- and answers a full 30-day read well inside the dashboard login's 10 s statement timeout.
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
 THEN RAISE EXCEPTION 'daily scorecard native fixture failed'; END IF;
END $fixture$;
SELECT json_build_object(
    'days', (SELECT count(*) FROM scorecard_fixture),
    'split_days', (SELECT count(*) FROM scorecard_fixture WHERE band_reason IS NULL),
    'null_gallon_days', (SELECT count(*) FROM scorecard_fixture WHERE water_gal IS NULL),
    'read_ms', (SELECT round(extract(epoch FROM max(read_at - started_at)) * 1000) FROM scorecard_fixture),
    'all_passed', true);
