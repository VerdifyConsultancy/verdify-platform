-- 166-firmware-v2-telemetry-columns.sql
-- =============================================================================
-- Firmware v2 (#327): land the on-chip telemetry the ESP32 now publishes so the
-- dashboards-v2 solar-band panels can graph the per-zone target/delta lines and
-- the house thermal/VPD band that the firmware enforces on-chip (OTA'd
-- 2026-06-11). This is the DB-side data layer only — the new ESPHome object_ids
-- already flow off the chip; the ingestor mapping lands alongside this migration
-- (ingestor/entity_map.py), and the dashboards consume the columns.
--
-- WHAT THE CHIP PUBLISHES (firmware/greenhouse/hardware.yaml "Firmware-v2
-- EVIDENCE SURFACE"):
--   NUMERIC (sensor, published every 60s) -> climate columns:
--     gh_solar_phase            -> solar_phase          (dimensionless [0,4))
--     gh_solar_sunrise_min      -> solar_sunrise_min    (minutes, local)
--     gh_solar_noon_min         -> solar_noon_min       (minutes, local)
--     gh_solar_sunset_min       -> solar_sunset_min     (minutes, local)
--     gh_house_temp_target      -> house_temp_target_f  (°F)
--     gh_house_temp_delta       -> house_temp_delta_f   (°F)
--     gh_house_vpd_target       -> house_vpd_target     (kPa)
--     gh_house_vpd_delta        -> house_vpd_delta      (kPa)
--     gh_zone_vpd_target_{zone} -> vpd_target_{zone}    (kPa; center/south/west/east)
--     gh_zone_vpd_delta_{zone}  -> vpd_delta_{zone}     (kPa; center/south/west/east)
--   TEXT (text_sensor, publish-on-change) -> diagnostics columns:
--     gh_zone_wet_granted       -> zone_wet_granted     (which zone the arbiter wet)
--     gh_band_source            -> band_source          (on-chip solar curve vs other)
--
-- Live table shapes (inspected on dev — a nightly prod replica — 2026-06-11):
--   climate:      TimescaleDB hypertable. Gains 16 nullable numeric columns.
--   diagnostics:  TimescaleDB hypertable. Gains 2 nullable text columns.
--   sensor_registry: PK (sensor_id); columns sensor_id, entity_id, type, zone,
--     position, source_table, source_column, unit, expected_interval_s (NOT NULL,
--     CHECK > 0), active (default true), notes, ... . Convention observed live:
--     sensor_id = 'climate.<col>' for climate rows and 'diag.<col>' for
--     diagnostics rows; entity_id = the ESPHome object_id (sanitized friendly
--     NAME, not the YAML id:). 18 registry rows are inserted (16 numeric + 2
--     text) so the staleness/coverage tooling tracks the new surfaces.
--
-- Compressed-hypertable note: every ADD COLUMN below is nullable with NO
-- default — metadata-only, which TimescaleDB permits on hypertables with
-- compressed chunks (no chunk rewrite). No new indexes: the columns are sparse
-- until the ingestor mapping is live, and dashboard access paths stay (ts) via
-- the existing hypertable indexes.
--
-- #23 rollback-replay safety: NON-self-transactional — no top-level
-- BEGIN;/COMMIT; and no commit-forcing statement (plain ALTER TABLE ... ADD
-- COLUMN / INSERT / COMMENT are all transactional). SAFE to wrap in an outer
-- `BEGIN; ... ROLLBACK;` for rollback validation. Idempotent: ADD COLUMN IF NOT
-- EXISTS + INSERT ... ON CONFLICT (sensor_id) DO NOTHING.
--
-- RESTARTS (CLAUDE.md rule 7): this migration does not touch verdify_schemas/**,
-- ingestor/entity_map.py, or mcp/server.py — no restart obligation from the SQL
-- itself. (The companion entity_map.py change in this PR carries its own
-- restart note: bounce verdify-ingestor so the new CLIMATE_MAP/DIAGNOSTIC_MAP
-- entries load at startup.)
--
-- ROLLBACK (documented; for a disposable validation DB, never live):
--   ALTER TABLE public.diagnostics DROP COLUMN IF EXISTS band_source;
--   ALTER TABLE public.diagnostics DROP COLUMN IF EXISTS zone_wet_granted;
--   ALTER TABLE public.climate DROP COLUMN IF EXISTS vpd_delta_east;
--   ... (each climate column) ...
--   DELETE FROM public.sensor_registry WHERE sensor_id IN (
--     'climate.solar_phase', ..., 'diag.zone_wet_granted', 'diag.band_source');
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────
-- 166.1  climate: 16 firmware-v2 numeric telemetry columns (all nullable)
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS solar_phase        double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS solar_sunrise_min  integer;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS solar_noon_min     integer;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS solar_sunset_min   integer;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS house_temp_target_f double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS house_temp_delta_f  double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS house_vpd_target   double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS house_vpd_delta    double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS vpd_target_center  double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS vpd_target_south   double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS vpd_target_west    double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS vpd_target_east    double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS vpd_delta_center   double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS vpd_delta_south    double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS vpd_delta_west     double precision;
ALTER TABLE public.climate ADD COLUMN IF NOT EXISTS vpd_delta_east     double precision;

COMMENT ON COLUMN public.climate.solar_phase IS
'Firmware-v2 on-chip solar phase in [0,4): 0=sunrise, 1=solar noon, 2=sunset, '
'3=solar midnight (contract B1). Mirror of fn_solar_phase().';
COMMENT ON COLUMN public.climate.solar_sunrise_min IS
'Firmware-v2 sunrise time, minutes after local midnight (America/Denver).';
COMMENT ON COLUMN public.climate.solar_noon_min IS
'Firmware-v2 solar-noon time, minutes after local midnight (America/Denver).';
COMMENT ON COLUMN public.climate.solar_sunset_min IS
'Firmware-v2 sunset time, minutes after local midnight (America/Denver).';
COMMENT ON COLUMN public.climate.house_temp_target_f IS
'Firmware-v2 on-chip house thermal band target (°F) at the current solar phase.';
COMMENT ON COLUMN public.climate.house_temp_delta_f IS
'Firmware-v2 on-chip house thermal band half-width (°F): band = target -/+ delta.';
COMMENT ON COLUMN public.climate.house_vpd_target IS
'Firmware-v2 on-chip house VPD band target (kPa) at the current solar phase.';
COMMENT ON COLUMN public.climate.house_vpd_delta IS
'Firmware-v2 on-chip house VPD band half-width (kPa): band = target -/+ delta.';
COMMENT ON COLUMN public.climate.vpd_target_center IS
'Firmware-v2 on-chip center-zone VPD target (kPa) at the current solar phase.';
COMMENT ON COLUMN public.climate.vpd_target_south IS
'Firmware-v2 on-chip south-zone VPD target (kPa) at the current solar phase.';
COMMENT ON COLUMN public.climate.vpd_target_west IS
'Firmware-v2 on-chip west-zone VPD target (kPa) at the current solar phase.';
COMMENT ON COLUMN public.climate.vpd_target_east IS
'Firmware-v2 on-chip east-zone VPD target (kPa) at the current solar phase.';
COMMENT ON COLUMN public.climate.vpd_delta_center IS
'Firmware-v2 on-chip center-zone VPD band half-width (kPa).';
COMMENT ON COLUMN public.climate.vpd_delta_south IS
'Firmware-v2 on-chip south-zone VPD band half-width (kPa).';
COMMENT ON COLUMN public.climate.vpd_delta_west IS
'Firmware-v2 on-chip west-zone VPD band half-width (kPa).';
COMMENT ON COLUMN public.climate.vpd_delta_east IS
'Firmware-v2 on-chip east-zone VPD band half-width (kPa).';

-- ─────────────────────────────────────────────────────────────────────
-- 166.2  diagnostics: 2 firmware-v2 text telemetry columns (nullable)
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE public.diagnostics ADD COLUMN IF NOT EXISTS zone_wet_granted text;
ALTER TABLE public.diagnostics ADD COLUMN IF NOT EXISTS band_source      text;

COMMENT ON COLUMN public.diagnostics.zone_wet_granted IS
'Firmware-v2 evidence surface: which zone the priority arbiter granted wetting '
'to this cycle (or none).';
COMMENT ON COLUMN public.diagnostics.band_source IS
'Firmware-v2 evidence surface: source of the served band (on-chip solar curve '
'vs other).';

-- ─────────────────────────────────────────────────────────────────────
-- 166.3  sensor_registry: 18 rows (16 numeric climate + 2 text diagnostics).
--   sensor_id   = '<prefix>.<source_column>' ('climate.' / 'diag.' per the live
--                 convention); the natural key for ON CONFLICT.
--   entity_id   = the ESPHome object_id (sanitized friendly NAME).
--   zone        = NULL for house/solar; center/south/west/east for zone bands.
--   expected_interval_s = 60 for the every-60s numeric sensors; 604800 for the
--                 publish-on-change text sensors (matches the system_state text
--                 convention so the staleness monitor does not false-alarm).
-- ─────────────────────────────────────────────────────────────────────
INSERT INTO public.sensor_registry
    (sensor_id, entity_id, type, zone, source_table, source_column, unit, expected_interval_s, active, notes)
VALUES
    ('climate.solar_phase',        'solar_phase',               'derived',     NULL,     'climate', 'solar_phase',        NULL,  60, true, 'Firmware-v2 #327 on-chip solar phase [0,4)'),
    ('climate.solar_sunrise_min',  'solar_sunrise__min_local_', 'derived',     NULL,     'climate', 'solar_sunrise_min',  'min', 60, true, 'Firmware-v2 #327 on-chip sunrise (min local)'),
    ('climate.solar_noon_min',     'solar_noon__min_local_',    'derived',     NULL,     'climate', 'solar_noon_min',     'min', 60, true, 'Firmware-v2 #327 on-chip solar noon (min local)'),
    ('climate.solar_sunset_min',   'solar_sunset__min_local_',  'derived',     NULL,     'climate', 'solar_sunset_min',   'min', 60, true, 'Firmware-v2 #327 on-chip sunset (min local)'),
    ('climate.house_temp_target_f','house_temp_target_f',       'temperature', NULL,     'climate', 'house_temp_target_f','°F',  60, true, 'Firmware-v2 #327 on-chip house temp band target'),
    ('climate.house_temp_delta_f', 'house_temp_delta_f',        'temperature', NULL,     'climate', 'house_temp_delta_f', '°F',  60, true, 'Firmware-v2 #327 on-chip house temp band half-width'),
    ('climate.house_vpd_target',   'house_vpd_target_kpa',      'vpd',         NULL,     'climate', 'house_vpd_target',   'kPa', 60, true, 'Firmware-v2 #327 on-chip house VPD band target'),
    ('climate.house_vpd_delta',    'house_vpd_delta_kpa',       'vpd',         NULL,     'climate', 'house_vpd_delta',    'kPa', 60, true, 'Firmware-v2 #327 on-chip house VPD band half-width'),
    ('climate.vpd_target_center',  'zone_vpd_target_center',    'vpd',         'center', 'climate', 'vpd_target_center',  'kPa', 60, true, 'Firmware-v2 #327 on-chip center-zone VPD target'),
    ('climate.vpd_target_south',   'zone_vpd_target_south',     'vpd',         'south',  'climate', 'vpd_target_south',   'kPa', 60, true, 'Firmware-v2 #327 on-chip south-zone VPD target'),
    ('climate.vpd_target_west',    'zone_vpd_target_west',      'vpd',         'west',   'climate', 'vpd_target_west',    'kPa', 60, true, 'Firmware-v2 #327 on-chip west-zone VPD target'),
    ('climate.vpd_target_east',    'zone_vpd_target_east',      'vpd',         'east',   'climate', 'vpd_target_east',    'kPa', 60, true, 'Firmware-v2 #327 on-chip east-zone VPD target'),
    ('climate.vpd_delta_center',   'zone_vpd_delta_center',     'vpd',         'center', 'climate', 'vpd_delta_center',   'kPa', 60, true, 'Firmware-v2 #327 on-chip center-zone VPD band half-width'),
    ('climate.vpd_delta_south',    'zone_vpd_delta_south',      'vpd',         'south',  'climate', 'vpd_delta_south',    'kPa', 60, true, 'Firmware-v2 #327 on-chip south-zone VPD band half-width'),
    ('climate.vpd_delta_west',     'zone_vpd_delta_west',       'vpd',         'west',   'climate', 'vpd_delta_west',     'kPa', 60, true, 'Firmware-v2 #327 on-chip west-zone VPD band half-width'),
    ('climate.vpd_delta_east',     'zone_vpd_delta_east',       'vpd',         'east',   'climate', 'vpd_delta_east',     'kPa', 60, true, 'Firmware-v2 #327 on-chip east-zone VPD band half-width'),
    ('diag.zone_wet_granted',      'zone_wet_granted',          'diagnostic',  NULL,     'diagnostics', 'zone_wet_granted', 'text', 604800, true, 'Firmware-v2 #327 evidence: zone granted wetting'),
    ('diag.band_source',           'band_source',               'diagnostic',  NULL,     'diagnostics', 'band_source',      'text', 604800, true, 'Firmware-v2 #327 evidence: served band source')
ON CONFLICT (sensor_id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- 166.4  Verification: 16 climate cols + 2 diagnostics cols + 18 registry rows.
-- ─────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    n_climate int;
    n_diag    int;
    n_reg     int;
BEGIN
    SELECT count(*) INTO n_climate
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'climate'
       AND column_name IN
           ('solar_phase','solar_sunrise_min','solar_noon_min','solar_sunset_min',
            'house_temp_target_f','house_temp_delta_f','house_vpd_target','house_vpd_delta',
            'vpd_target_center','vpd_target_south','vpd_target_west','vpd_target_east',
            'vpd_delta_center','vpd_delta_south','vpd_delta_west','vpd_delta_east');
    IF n_climate <> 16 THEN
        RAISE EXCEPTION 'migration 166: climate firmware-v2 columns incomplete (found %/16)', n_climate;
    END IF;

    SELECT count(*) INTO n_diag
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'diagnostics'
       AND column_name IN ('zone_wet_granted','band_source')
       AND data_type = 'text';
    IF n_diag <> 2 THEN
        RAISE EXCEPTION 'migration 166: diagnostics firmware-v2 text columns incomplete (found %/2)', n_diag;
    END IF;

    SELECT count(*) INTO n_reg
      FROM public.sensor_registry
     WHERE sensor_id IN
           ('climate.solar_phase','climate.solar_sunrise_min','climate.solar_noon_min',
            'climate.solar_sunset_min','climate.house_temp_target_f','climate.house_temp_delta_f',
            'climate.house_vpd_target','climate.house_vpd_delta','climate.vpd_target_center',
            'climate.vpd_target_south','climate.vpd_target_west','climate.vpd_target_east',
            'climate.vpd_delta_center','climate.vpd_delta_south','climate.vpd_delta_west',
            'climate.vpd_delta_east','diag.zone_wet_granted','diag.band_source');
    IF n_reg <> 18 THEN
        RAISE EXCEPTION 'migration 166: sensor_registry firmware-v2 rows incomplete (found %/18)', n_reg;
    END IF;

    RAISE NOTICE 'migration 166 OK: climate(16 numeric) + diagnostics(zone_wet_granted, band_source) columns present; sensor_registry has 18 firmware-v2 rows.';
END $$;
