-- 303 (#1345): soil-moisture targets become configurable and plant-agnostic, and the backup slots lose the crop name.
-- soil_moisture_targets was seeded once (2026-03-30) with a crop name and no edit path, and the ingestor kept the
-- probe -> zone map as a constant (ingestor/tasks/_common.py SOIL_DRYOUT_PROBES). 303 makes the table the one place
-- for both:
--   probe_column, sensor_id, occupancy_zone   which climate column a row's probe writes, its sensor id and the
--                                             crops.zone that gates it on occupancy; backfilled from the constant;
--   updated_at, updated_by, updated_source,   who last changed a target and why, as the operator API and MCP write
--   reason                                    it (API-SOIL-02, soil_target_set) beside the activity_log row;
--   one CHECK                                 0 <= wilt < min < max < saturation <= 100;
--   UNIQUE (greenhouse_id, zone)              a zone has one target row.
-- crop stays as it is, a free-text note that no decision path reads. The dashboard and MCP duties get SELECT and
-- UPDATE on the four thresholds, the notes and the attribution columns; neither inserts nor deletes. The three
-- backup-misting slots (283's fallback program) are relabelled "sensor-fault backup" from "vanda-degraded": the
-- compiled program never reads a label, so the program hash is unchanged.
-- All three seals move and every receipt is re-sealed: the api digest takes in soil_moisture_targets by name (217
-- protects it), the ingestor digest its new columns (the ingestor login reads the table), and the MCP digest the
-- table, its columns and the MCP grants. 303 chains from exact302; the rolled-back native qualification measured the
-- successor seals (tests/fixtures/303-soil-targets-native-qualification.json).
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=302 AND filename='db/migrations/302-planner-section-reads.sql'
            AND sha256='a18aeb0588b5f4e56b7a78ce3dea7c592873b9fae54f0701c45f6d9bbbd8f3bf'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>302)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM 'af3f7ef2493b94b11e9e7c3f5ead6bf61c8542e477d26da117f3930b0530d873'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
        OR to_regclass('public.soil_moisture_targets') IS NULL
        OR to_regclass('public.irrigation_slot') IS NULL
        OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid='public.soil_moisture_targets'::regclass
            AND attname IN ('probe_column', 'sensor_id', 'occupancy_zone', 'updated_at', 'updated_by',
                            'updated_source', 'reason') AND NOT attisdropped)
        OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='verdify_dashboard_runtime' AND NOT rolcanlogin)
        OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='verdify_mcp_runtime' AND NOT rolcanlogin)
    THEN RAISE EXCEPTION '303 refuses an unqualified predecessor'; END IF;
END $predecessor$;

ALTER TABLE public.soil_moisture_targets
    ADD COLUMN probe_column   text CHECK (probe_column ~ '^soil_moisture_[a-z0-9_]{1,40}$'),
    ADD COLUMN sensor_id      text CHECK (sensor_id ~ '^soil\.[a-z0-9_]{1,40}$'),
    ADD COLUMN occupancy_zone text CHECK (occupancy_zone ~ '^[a-z][a-z0-9_]{0,39}$'),
    ADD COLUMN updated_at     timestamptz,
    ADD COLUMN updated_by     text CHECK (length(updated_by) BETWEEN 1 AND 100),
    ADD COLUMN updated_source text CHECK (length(updated_source) BETWEEN 1 AND 100),
    ADD COLUMN reason         text CHECK (length(reason) BETWEEN 1 AND 500),
    ADD CONSTRAINT soil_moisture_targets_order
        CHECK (0 <= wilt_pct AND wilt_pct < min_pct AND min_pct < max_pct AND max_pct < saturation_pct
               AND saturation_pct <= 100),
    ADD CONSTRAINT soil_moisture_targets_probe_whole
        CHECK ((probe_column IS NULL) = (sensor_id IS NULL) AND (probe_column IS NULL) = (occupancy_zone IS NULL)),
    ADD CONSTRAINT soil_moisture_targets_zone_key UNIQUE (greenhouse_id, zone),
    ADD CONSTRAINT soil_moisture_targets_probe_key UNIQUE (probe_column);

-- The probe map the ingestor kept as SOIL_DRYOUT_PROBES (column, sensor id, crops.zone), now data.
UPDATE public.soil_moisture_targets t
   SET probe_column = m.probe_column, sensor_id = m.sensor_id, occupancy_zone = m.occupancy_zone
  FROM (VALUES ('south_1', 'soil_moisture_south_1', 'soil.south_1', 'south'),
               ('south_2', 'soil_moisture_south_2', 'soil.south_2', 'south'),
               ('west',    'soil_moisture_west',    'soil.west',    'west'))
       AS m(zone, probe_column, sensor_id, occupancy_zone)
 WHERE t.zone = m.zone;

COMMENT ON TABLE public.soil_moisture_targets IS
    'Soil-moisture targets per probe zone (303, #1345): the four thresholds, editable through the operator API '
    '(API-SOIL-01/02) and MCP (soil_targets, soil_target_set), and the probe map the ingestor''s soil_dryout rule '
    'reads (probe_column, sensor_id, occupancy_zone). crop is a free-text note no decision path reads.';
COMMENT ON COLUMN public.soil_moisture_targets.wilt_pct IS
    'soil_dryout pages when the live probe stays below this for more than 2 h in the day.';
COMMENT ON COLUMN public.soil_moisture_targets.probe_column IS
    'The climate column this zone''s probe writes; soil_dryout reads only rows that name one.';
COMMENT ON COLUMN public.soil_moisture_targets.occupancy_zone IS
    'The crops.zone whose open plantings let soil_dryout page for this probe.';

GRANT SELECT ON public.soil_moisture_targets TO verdify_dashboard_runtime, verdify_mcp_runtime;
GRANT UPDATE (min_pct, max_pct, saturation_pct, wilt_pct, notes, updated_at, updated_by, updated_source, reason)
    ON public.soil_moisture_targets TO verdify_dashboard_runtime, verdify_mcp_runtime;

-- The backup-misting slots carry no crop name (#1345); the compiled program never reads a label.
UPDATE public.irrigation_slot SET label = 'sensor-fault backup' WHERE label = 'vanda-degraded';

-- Successor seals: every digest moves (see the header), and each receipt is re-sealed to it.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '42091d8bc52adfff17ef589ca7d71ac3a97fe37af4460b551a938f313635bb8c'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'c7ad6a83e598b8f6da9ebd0086c008b13cfa2812647516ecdf8a7d1b32331940'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '2c6a1cbe628ea66fafe30784189f6c9ba7c33983792a46cd7383d7dae67dc245'
    THEN RAISE EXCEPTION '303 refuses an unqualified successor'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('42091d8bc52adfff17ef589ca7d71ac3a97fe37af4460b551a938f313635bb8c','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '303 missing api receipt'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('c7ad6a83e598b8f6da9ebd0086c008b13cfa2812647516ecdf8a7d1b32331940','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '303 missing ingestor receipt'; END IF;
    UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('2c6a1cbe628ea66fafe30784189f6c9ba7c33983792a46cd7383d7dae67dc245','hex') WHERE singleton;
    IF NOT FOUND THEN RAISE EXCEPTION '303 missing mcp receipt'; END IF;
END $successor$;
