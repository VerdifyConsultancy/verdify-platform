-- 167-band-curve-matview-cache.sql
-- ============================================================================
-- Cache the deterministic solar band + per-zone VPD target curves in a
-- materialized view so the Grafana compliance panels read pre-computed rows
-- instead of calling fn_crop_band_value / fn_zone_vpd_targets live per render
-- (each call runs a 30-iteration sunrise/sunset bisection; the 144h panel
-- queries were ~1-2s). The curves are pure-deterministic (functions of solar
-- phase + the crop_band_anchors), so a periodic refresh is exact.
--
-- mv_band_curve covers a WIDE window (now-4d .. now+4d) at 15-min resolution so
-- it serves the dashboards' default now-72h..now+72h view plus zoom headroom.
-- The ingestor matview_refresh task runs REFRESH MATERIALIZED VIEW
-- CONCURRENTLY every 5 min (ingestor/tasks/ha.py); it also adds the band
-- refresh that mv_zone_band_grade was missing on prod.
--
-- v_band_curve is the stable read surface the panels query (time-filtered).
--
-- SAFETY: non-self-transactional (CREATE MATERIALIZED VIEW / VIEW / INDEX, no
-- COMMIT). The CREATE MATERIALIZED VIEW ... WITH DATA populates once inline.
-- Idempotent via DROP ... IF EXISTS guards. Rollback-validate under an outer
-- BEGIN; ... ROLLBACK;.
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_band_curve CASCADE;

CREATE MATERIALIZED VIEW mv_band_curve AS
SELECT
    g.ts AS ts,
    'vallery'::text AS greenhouse_id,
    fn_crop_band_value('house', 'temp_low',    g.ts) AS temp_low,
    fn_crop_band_value('house', 'temp_target', g.ts) AS temp_target,
    fn_crop_band_value('house', 'temp_high',   g.ts) AS temp_high,
    fn_crop_band_value('house', 'vpd_low',     g.ts) AS vpd_low,
    fn_crop_band_value('house', 'vpd_target',  g.ts) AS vpd_target,
    fn_crop_band_value('house', 'vpd_high',    g.ts) AS vpd_high,
    z.vpd_target_center,
    z.vpd_target_south,
    z.vpd_target_west,
    z.vpd_target_east,
    fn_solar_phase(g.ts) AS solar_phase
FROM generate_series(
        date_trunc('hour', now()) - interval '4 days',
        date_trunc('hour', now()) + interval '4 days',
        interval '15 minutes') g(ts)
CROSS JOIN LATERAL fn_zone_vpd_targets(g.ts) AS z
WITH DATA;

-- UNIQUE index required for REFRESH ... CONCURRENTLY.
CREATE UNIQUE INDEX IF NOT EXISTS mv_band_curve_ts_uidx ON mv_band_curve (ts, greenhouse_id);

-- Stable read surface for the panels.
CREATE OR REPLACE VIEW v_band_curve AS SELECT * FROM mv_band_curve;

DO $$
DECLARE
    n integer; peak_hr integer;
BEGIN
    SELECT count(*) INTO n FROM mv_band_curve;
    SELECT extract(hour FROM (ts AT TIME ZONE 'America/Denver'))::int INTO peak_hr
    FROM mv_band_curve
    WHERE ts BETWEEN now() - interval '12 hours' AND now() + interval '12 hours'
    ORDER BY temp_high DESC NULLS LAST LIMIT 1;
    RAISE NOTICE 'migration 167 OK: mv_band_curve has % rows; temp_high peaks at local hour % (expect ~13 solar noon).', n, peak_hr;
END $$;
