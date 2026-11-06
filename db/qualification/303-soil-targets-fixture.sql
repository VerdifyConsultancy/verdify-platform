-- Run only inside an explicit rollback-only transaction after exact 303 (#1345), as the superuser owner:
-- scripts/qualify-migration-successor.py --native-fixture runs it at the end of its final session. The probe map
-- is the three rows SOIL_DRYOUT_PROBES held, each naming a live climate column; no slot keeps a crop label. The
-- dashboard login attests and edits a target as API-SOIL-02 does (thresholds plus attribution), and the order
-- CHECK refuses wilt above min; it cannot insert, delete or move a probe. The MCP login attests and edits one the
-- same way. The ingestor login reads the edited wilt, which is what soil_dryout pages on. The api login and PUBLIC
-- reach nothing new. The rollback discards all of it. The last line reports the result to the qualifier.
DO $catalog$
BEGIN
    IF (SELECT count(*) FROM public.soil_moisture_targets
         WHERE (zone, probe_column, sensor_id, occupancy_zone) IN (
               ('south_1', 'soil_moisture_south_1', 'soil.south_1', 'south'),
               ('south_2', 'soil_moisture_south_2', 'soil.south_2', 'south'),
               ('west', 'soil_moisture_west', 'soil.west', 'west'))) <> 3
        OR EXISTS(SELECT 1 FROM public.soil_moisture_targets t WHERE t.probe_column IS NOT NULL
            AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid = 'public.climate'::regclass
                AND a.attname = t.probe_column AND NOT a.attisdropped))
        OR EXISTS(SELECT 1 FROM public.irrigation_slot WHERE label ILIKE '%vanda%')
        OR (SELECT count(*) FROM public.irrigation_slot WHERE label = 'sensor-fault backup') <> 3
    THEN RAISE EXCEPTION '303 probe map or slot labels are not exact'; END IF;
    IF NOT has_table_privilege('verdify_dashboard_runtime', 'public.soil_moisture_targets', 'SELECT')
        OR NOT has_table_privilege('verdify_mcp_runtime', 'public.soil_moisture_targets', 'SELECT')
        OR has_table_privilege('verdify_dashboard_runtime', 'public.soil_moisture_targets', 'INSERT,DELETE,TRUNCATE')
        OR has_table_privilege('verdify_mcp_runtime', 'public.soil_moisture_targets', 'INSERT,DELETE,TRUNCATE')
        OR NOT has_column_privilege('verdify_dashboard_runtime', 'public.soil_moisture_targets', 'wilt_pct', 'UPDATE')
        OR NOT has_column_privilege('verdify_mcp_runtime', 'public.soil_moisture_targets', 'reason', 'UPDATE')
        OR has_column_privilege('verdify_dashboard_runtime', 'public.soil_moisture_targets', 'probe_column', 'UPDATE')
        OR has_column_privilege('verdify_mcp_runtime', 'public.soil_moisture_targets', 'zone', 'UPDATE')
        OR has_table_privilege('verdify_api_runtime_login', 'public.soil_moisture_targets',
                               'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_table_privilege('verdify_ingestor_runtime_login', 'public.soil_moisture_targets',
                               'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_table_privilege('public', 'public.soil_moisture_targets', 'SELECT')
    THEN RAISE EXCEPTION '303 soil_moisture_targets access is not exact'; END IF;
END $catalog$;

-- SET SESSION AUTHORIZATION needs the superuser owner; the rollback undoes it.
SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;
DO $dashboard$
BEGIN
    IF verdify_dashboard_runtime.fn_attest_login() IS NOT TRUE THEN
        RAISE EXCEPTION '303 dashboard login does not attest';
    END IF;
    UPDATE public.soil_moisture_targets
       SET wilt_pct = 22, min_pct = 38, max_pct = 58, saturation_pct = 78, notes = '303 qualification',
           updated_at = now(), updated_by = '303-qualification', updated_source = 'dashboard',
           reason = '303 native qualification'
     WHERE greenhouse_id = 'vallery' AND zone = 'west';
    IF NOT FOUND THEN RAISE EXCEPTION '303 dashboard login could not edit a target'; END IF;
    BEGIN
        UPDATE public.soil_moisture_targets SET wilt_pct = 60 WHERE zone = 'south_1';
        RAISE EXCEPTION '303 order check let wilt pass min';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        UPDATE public.soil_moisture_targets SET probe_column = 'soil_moisture_west' WHERE zone = 'south_1';
        RAISE EXCEPTION '303 dashboard login moved a probe';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        DELETE FROM public.soil_moisture_targets WHERE zone = 'south_2';
        RAISE EXCEPTION '303 dashboard login deleted a target';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $dashboard$;
RESET SESSION AUTHORIZATION;

SET SESSION AUTHORIZATION verdify_mcp_runtime_login;
DO $mcp$
BEGIN
    IF public.fn_mcp_runtime_attest_ordinary_login() IS NOT TRUE THEN
        RAISE EXCEPTION '303 MCP login does not attest';
    END IF;
    UPDATE public.soil_moisture_targets
       SET wilt_pct = 19, updated_at = now(), updated_by = '303-qualification', updated_source = 'agent:qualification',
           reason = '303 native qualification'
     WHERE greenhouse_id = 'vallery' AND zone = 'south_2';
    IF NOT FOUND THEN RAISE EXCEPTION '303 MCP login could not edit a target'; END IF;
    PERFORM 1 FROM public.soil_moisture_targets WHERE zone = 'west' AND wilt_pct = 22;
    IF NOT FOUND THEN RAISE EXCEPTION '303 MCP login does not read the dashboard edit'; END IF;
END $mcp$;
RESET SESSION AUTHORIZATION;

SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $ingestor$
BEGIN
    -- soil_dryout's read: the probe map and each zone's wilt, with both edits in it.
    IF (SELECT count(*) FROM public.soil_moisture_targets
         WHERE probe_column IS NOT NULL AND wilt_pct IS NOT NULL) <> 3
        OR (SELECT wilt_pct FROM public.soil_moisture_targets WHERE zone = 'west') <> 22
        OR (SELECT wilt_pct FROM public.soil_moisture_targets WHERE zone = 'south_2') <> 19
    THEN RAISE EXCEPTION '303 ingestor login does not read the edited targets'; END IF;
END $ingestor$;
RESET SESSION AUTHORIZATION;

SELECT 'QUALIFY native_fixture ' || json_build_object(
    'checks', json_build_array('probe_map_backfilled_to_live_columns', 'no_crop_slot_label',
                               'grants_select_and_threshold_update_only', 'api_and_public_denied',
                               'dashboard_login_attests_and_edits', 'order_check_refuses',
                               'dashboard_cannot_move_probe_or_delete', 'mcp_login_attests_and_edits',
                               'ingestor_reads_edited_wilt'),
    'all_passed', true,
    'attested', json_build_object('dashboard', true, 'mcp', true))::text;
