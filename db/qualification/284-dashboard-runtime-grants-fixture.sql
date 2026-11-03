-- Run only inside an explicit rollback-only transaction after exact284, as the
-- superuser owner: scripts/qualify-migration-successor.py --native-fixture runs it
-- at the end of its final session. Native checks of M2b on the production catalog:
-- every dashboard grant exactly as listed, across 282, 283 and 284 (relations, columns,
-- sequences and functions), and a login that reaches nothing else. Then each runtime
-- login under SET SESSION AUTHORIZATION attests, the api login reaches nothing new,
-- and the dashboard login reads every column of each relation (LIMIT 0: the privilege
-- checks run, also on a function a view's columns call, and no scan does), calls each
-- function, writes its two append-only tables, edits one sentinel alert and one sentinel
-- anchor through its column grants, and is refused the rest. Each read and call runs in
-- its own block that ends by raising, so the locks planning takes on every chunk of a
-- hypertable end with it and the transaction never holds them all at once. Nothing
-- touches an existing row. The sentinels, the new rows, the queued notification and every
-- authorization change end with the rollback; sequences may skip a value. The last line
-- reports the result to the qualifier.
DO $catalog$
DECLARE
    duty constant regrole := 'verdify_dashboard_runtime'::regrole;
    login constant regrole := 'verdify_dashboard_runtime_login'::regrole;
    reached text[];
BEGIN
    -- Every relation and sequence the duty holds, by privilege: 282's sessions, 283's
    -- irrigation objects and 284's list. The login itself holds nothing directly.
    IF EXISTS (
        WITH expected(relname, privilege) AS (
            SELECT r, 'SELECT' FROM unnest(ARRAY[
                'activity_log', 'alert_log', 'climate', 'crop_band_anchors', 'daily_summary', 'dashboard_session',
                'data_gaps', 'desired_state', 'diagnostics', 'equipment_state', 'irrigation_command',
                'irrigation_ledger', 'irrigation_line', 'irrigation_push', 'irrigation_slot', 'plan_journal',
                'planner_trigger_ledger', 'setpoint_changes', 'setpoint_clamps', 'setpoint_plan',
                'setpoint_snapshot', 'slack_alert_runbooks', 'system_state', 'v_active_plan',
                'v_data_pipeline_health', 'v_desired_state_current', 'v_equipment_relay_map',
                'v_irrigation_day_coverage', 'v_irrigation_line_state', 'v_irrigation_minutes_daily',
                'v_open_alerts', 'v_planner_trigger_health', 'v_sensor_staleness', 'v_water_attribution_daily',
                'v_water_event_attribution', 'v_water_meter_daily', 'v_water_relay_runs', 'water_meter_events',
                'weather_forecast']) r
            UNION ALL
            VALUES ('desired_state', 'INSERT'), ('activity_log', 'INSERT'),
                   ('dashboard_session', 'INSERT'), ('dashboard_session', 'UPDATE'), ('dashboard_session', 'DELETE'),
                   ('irrigation_slot', 'INSERT'), ('irrigation_slot', 'UPDATE'), ('irrigation_slot', 'DELETE'),
                   ('irrigation_command', 'INSERT'),
                   ('desired_state_id_seq', 'USAGE'), ('activity_log_id_seq', 'USAGE'),
                   ('irrigation_slot_slot_id_seq', 'USAGE')
        ), actual AS (
            SELECT c.relname::text, a.privilege_type::text
            FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
            WHERE c.relnamespace = 'public'::regnamespace AND a.grantee = duty
        )
        (SELECT * FROM expected EXCEPT SELECT * FROM actual) UNION ALL (SELECT * FROM actual EXCEPT SELECT * FROM expected))
       OR EXISTS (
        WITH expected(relname, attname) AS (
            SELECT 'alert_log', c FROM unnest(ARRAY['disposition', 'acknowledged_at', 'acknowledged_by', 'notes',
                                                    'resolved_at', 'resolved_by', 'resolution']) c
            UNION ALL
            SELECT 'crop_band_anchors', c FROM unnest(ARRAY['value', 'width_below', 'width_above']) c
            UNION ALL
            SELECT 'irrigation_line', c FROM unnest(ARRAY['enabled', 'vpd_member', 'schedule_member', 'daily_cap_min',
                   'vpd_reserve_min', 'paused_until', 'manual', 'source', 'updated_by', 'updated_at', 'reason',
                   'checked_by', 'checked_at', 'check_note']) c
        ), actual AS (
            SELECT c.relname::text, t.attname::text
            FROM pg_attribute t JOIN pg_class c ON c.oid = t.attrelid CROSS JOIN LATERAL aclexplode(t.attacl) a
            WHERE c.relnamespace = 'public'::regnamespace AND t.attnum > 0 AND a.grantee = duty
              AND a.privilege_type = 'UPDATE'
        )
        (SELECT * FROM expected EXCEPT SELECT * FROM actual) UNION ALL (SELECT * FROM actual EXCEPT SELECT * FROM expected))
       OR EXISTS (SELECT 1 FROM pg_attribute t CROSS JOIN LATERAL aclexplode(t.attacl) a
                  WHERE t.attacl IS NOT NULL AND a.grantee IN (duty, login)
                    AND (a.grantee = login OR a.privilege_type <> 'UPDATE'))
       OR EXISTS (SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE a.grantee = login)
       OR EXISTS (
        WITH expected(fn) AS (
            VALUES ('public.fn_band_setpoints(timestamptz)'::regprocedure),
                   ('public.fn_crop_band_value(text,text,timestamptz,text,text,text)'::regprocedure),
                   ('public.fn_current_season()'::regprocedure),
                   ('public.fn_equip_at(text,timestamptz)'::regprocedure),
                   ('public.fn_irrigation_minutes_daily(date,date)'::regprocedure),
                   ('public.fn_mcp_armed_assignment_context()'::regprocedure),
                   ('public.fn_zone_vpd_targets(timestamptz)'::regprocedure),
                   ('verdify_dashboard_runtime.fn_attest_login()'::regprocedure)
        ), actual AS (
            SELECT p.oid::regprocedure AS fn
            FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
            WHERE a.grantee IN (duty, login)
        )
        (SELECT * FROM expected EXCEPT SELECT * FROM actual) UNION ALL (SELECT * FROM actual EXCEPT SELECT * FROM expected))
       OR EXISTS (SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
                  WHERE a.grantee IN (duty, login) AND n.nspname <> 'verdify_dashboard_runtime')
       OR EXISTS (SELECT 1 FROM pg_database d CROSS JOIN LATERAL aclexplode(d.datacl) a
                  WHERE a.grantee IN (duty, login))
       OR EXISTS (SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
                  WHERE a.grantee IN (duty, login) OR d.defaclrole IN (duty, login))
    THEN RAISE EXCEPTION '284 native grant check failed'; END IF;

    -- Through its own grants or PUBLIC's, the login reaches exactly these relations,
    -- sequences and definer functions, so not v_greenhouse_now, whose read would plan
    -- every hypertable beneath it. A failure names what it reaches.
    reached := ARRAY(
        SELECT object FROM (
            SELECT n.nspname || '.' || c.relname AS object
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
               AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
               AND NOT EXISTS (SELECT 1 FROM pg_depend d
                   WHERE d.classid = 'pg_namespace'::regclass AND d.objid = n.oid
                     AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e')
               AND NOT EXISTS (SELECT 1 FROM pg_depend d
                   WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
               AND CASE WHEN c.relkind = 'S'
                        THEN has_sequence_privilege(login, c.oid, 'USAGE,SELECT,UPDATE')
                        ELSE has_table_privilege(login, c.oid,
                                 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                             OR has_any_column_privilege(login, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES')
                   END
            UNION ALL
            SELECT 'function ' || n.nspname || '.' || p.proname
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE p.prosecdef
               AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
               AND NOT EXISTS (SELECT 1 FROM pg_depend d
                   WHERE d.classid = 'pg_namespace'::regclass AND d.objid = n.oid
                     AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e')
               AND NOT EXISTS (SELECT 1 FROM pg_depend d
                   WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
               AND has_function_privilege(login, p.oid, 'EXECUTE')
        ) reach ORDER BY object COLLATE "C");
    IF reached IS DISTINCT FROM ARRAY[
        'function public.fn_irrigation_minutes_daily', 'function public.fn_mcp_armed_assignment_context',
        'public.activity_log', 'public.activity_log_id_seq', 'public.alert_log', 'public.climate',
        'public.crop_band_anchors', 'public.daily_summary', 'public.dashboard_session', 'public.data_gaps',
        'public.desired_state', 'public.desired_state_id_seq', 'public.diagnostics', 'public.equipment_state',
        'public.irrigation_command', 'public.irrigation_ledger', 'public.irrigation_line', 'public.irrigation_push',
        'public.irrigation_slot', 'public.irrigation_slot_slot_id_seq', 'public.plan_journal',
        'public.planner_trigger_ledger', 'public.setpoint_changes', 'public.setpoint_clamps', 'public.setpoint_plan',
        'public.setpoint_snapshot', 'public.slack_alert_runbooks', 'public.system_state', 'public.v_active_plan',
        'public.v_data_pipeline_health', 'public.v_desired_state_current', 'public.v_equipment_relay_map',
        'public.v_irrigation_day_coverage', 'public.v_irrigation_line_state', 'public.v_irrigation_minutes_daily',
        'public.v_open_alerts', 'public.v_planner_trigger_health', 'public.v_sensor_staleness',
        'public.v_water_attribution_daily', 'public.v_water_event_attribution', 'public.v_water_meter_daily',
        'public.v_water_relay_runs', 'public.water_meter_events', 'public.weather_forecast'] THEN
        RAISE EXCEPTION '284 dashboard login reaches %', array_to_string(reached, ', ');
    END IF;

    -- The sentinels the login edits below; no existing row is touched.
    INSERT INTO public.alert_log (alert_type, severity, category, message, source)
    VALUES ('284_qualification', 'info', 'system', '284 native qualification', 'system');
    INSERT INTO public.crop_band_anchors (crop_type, series, anchor, value)
    VALUES ('284-qualification', 'temp_low', 'sr', 70);
END $catalog$;

-- After the re-seal every runtime login still attests, and the api login reaches
-- nothing new. SET SESSION AUTHORIZATION needs the superuser owner; the rollback undoes it.
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $api$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() IS NOT TRUE THEN
        RAISE EXCEPTION '284 api login does not attest';
    END IF;
    BEGIN
        PERFORM 1 FROM public.fn_mcp_armed_assignment_context();
        RAISE EXCEPTION '284 api login executed fn_mcp_armed_assignment_context';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $api$;
RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $ingestor$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() IS NOT TRUE THEN
        RAISE EXCEPTION '284 ingestor login does not attest';
    END IF;
END $ingestor$;
RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION verdify_mcp_runtime_login;
DO $mcp$
BEGIN
    IF public.fn_mcp_runtime_attest_ordinary_login() IS NOT TRUE THEN
        RAISE EXCEPTION '284 MCP login does not attest';
    END IF;
END $mcp$;
RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;
DO $dashboard$
DECLARE
    statement text;
    alert_id integer;
    anchor_id integer;
BEGIN
    IF verdify_dashboard_runtime.fn_attest_login() IS NOT TRUE THEN
        RAISE EXCEPTION '284 dashboard login does not attest';
    END IF;
    -- The band functions run as the caller, so their calls prove the helpers beneath them too.
    -- A missing privilege raises 42501, which leaves the block and fails the session.
    FOREACH statement IN ARRAY ARRAY[
        'SELECT * FROM public.activity_log LIMIT 0',
        'SELECT * FROM public.alert_log LIMIT 0',
        'SELECT * FROM public.climate LIMIT 0',
        'SELECT * FROM public.crop_band_anchors LIMIT 0',
        'SELECT * FROM public.daily_summary LIMIT 0',
        'SELECT * FROM public.data_gaps LIMIT 0',
        'SELECT * FROM public.desired_state LIMIT 0',
        'SELECT * FROM public.diagnostics LIMIT 0',
        'SELECT * FROM public.equipment_state LIMIT 0',
        'SELECT * FROM public.plan_journal LIMIT 0',
        'SELECT * FROM public.planner_trigger_ledger LIMIT 0',
        'SELECT * FROM public.setpoint_changes LIMIT 0',
        'SELECT * FROM public.setpoint_clamps LIMIT 0',
        'SELECT * FROM public.setpoint_plan LIMIT 0',
        'SELECT * FROM public.setpoint_snapshot LIMIT 0',
        'SELECT * FROM public.slack_alert_runbooks LIMIT 0',
        'SELECT * FROM public.system_state LIMIT 0',
        'SELECT * FROM public.water_meter_events LIMIT 0',
        'SELECT * FROM public.weather_forecast LIMIT 0',
        'SELECT * FROM public.v_active_plan LIMIT 0',
        'SELECT * FROM public.v_data_pipeline_health LIMIT 0',
        'SELECT * FROM public.v_desired_state_current LIMIT 0',
        'SELECT * FROM public.v_equipment_relay_map LIMIT 0',
        'SELECT * FROM public.v_irrigation_day_coverage LIMIT 0',
        'SELECT * FROM public.v_irrigation_minutes_daily LIMIT 0',
        'SELECT * FROM public.v_open_alerts LIMIT 0',
        'SELECT * FROM public.v_planner_trigger_health LIMIT 0',
        'SELECT * FROM public.v_sensor_staleness LIMIT 0',
        'SELECT * FROM public.v_water_attribution_daily LIMIT 0',
        'SELECT * FROM public.v_water_meter_daily LIMIT 0',
        'SELECT * FROM public.v_water_relay_runs LIMIT 0',
        'SELECT 1 FROM public.fn_band_setpoints(now())',
        'SELECT public.fn_crop_band_value(''house'', ''temp_low'', now())',
        'SELECT public.fn_current_season()',
        'SELECT public.fn_equip_at(''fan1'', now())',
        'SELECT 1 FROM public.fn_irrigation_minutes_daily(current_date, current_date)',
        'SELECT 1 FROM public.fn_mcp_armed_assignment_context()',
        'SELECT 1 FROM public.fn_zone_vpd_targets(now())'] LOOP
        BEGIN
            EXECUTE statement;
            RAISE EXCEPTION USING ERRCODE = 'VD284';
        EXCEPTION WHEN SQLSTATE 'VD284' THEN NULL;
        END;
    END LOOP;

    INSERT INTO public.desired_state (param, value, manual, source, actor, reason)
    VALUES ('284-qualification', 1, true, 'dashboard', '284-qualification', '284 native qualification');
    INSERT INTO public.activity_log (source, actor, action, target, reason)
    VALUES ('dashboard', '284-qualification', 'setting.write', '284-qualification', '284 native qualification');
    UPDATE public.alert_log SET disposition = 'acknowledged', acknowledged_at = now(),
           acknowledged_by = '284-qualification', notes = '284 native qualification'
     WHERE alert_type = '284_qualification' AND resolved_at IS NULL AND disposition = 'open'
    RETURNING id INTO alert_id;
    IF alert_id IS NULL THEN RAISE EXCEPTION '284 dashboard login could not acknowledge an alert'; END IF;
    UPDATE public.alert_log SET disposition = 'resolved', resolved_at = now(), resolved_by = '284-qualification',
           resolution = '284 native qualification'
     WHERE id = alert_id;
    IF NOT FOUND THEN RAISE EXCEPTION '284 dashboard login could not resolve an alert'; END IF;
    UPDATE public.crop_band_anchors SET value = 71, width_below = 1, width_above = 1
     WHERE crop_type = '284-qualification'
    RETURNING id INTO anchor_id;
    IF anchor_id IS NULL THEN RAISE EXCEPTION '284 dashboard login could not edit an anchor'; END IF;

    BEGIN
        INSERT INTO public.setpoint_changes (ts, parameter, value, source)
        VALUES (now(), '284-qualification', 0, 'manual');
        RAISE EXCEPTION '284 dashboard login inserted into setpoint_changes';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        INSERT INTO public.setpoint_plan (ts, parameter, value, plan_id, source)
        VALUES (now(), '284-qualification', 0, '284-qualification', 'iris');
        RAISE EXCEPTION '284 dashboard login inserted into setpoint_plan';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM public.control_experiments LIMIT 0;
        RAISE EXCEPTION '284 dashboard login read control_experiments';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE public.alert_log SET message = message WHERE id = alert_id;
        RAISE EXCEPTION '284 dashboard login edited an alert column it was not granted';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE public.crop_band_anchors SET series = series WHERE id = anchor_id;
        RAISE EXCEPTION '284 dashboard login edited an anchor column it was not granted';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        DELETE FROM public.desired_state WHERE param = '284-qualification';
        RAISE EXCEPTION '284 dashboard login deleted a desired_state row';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE public.activity_log SET reason = reason WHERE target = '284-qualification';
        RAISE EXCEPTION '284 dashboard login edited an activity_log row';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $dashboard$;
RESET SESSION AUTHORIZATION;

SELECT 'QUALIFY native_fixture ' || json_build_object(
    'checks', json_build_array('grants_exact', 'dashboard_reach', 'logins_attest', 'relations_read', 'functions_called',
                               'desired_state_and_activity_log_insert', 'alert_ack_and_resolve', 'anchor_edit',
                               'setpoint_changes_insert_denied', 'setpoint_plan_insert_denied',
                               'control_experiments_read_denied', 'other_columns_denied',
                               'append_only_tables_denied'),
    'all_passed', true,
    'attested', json_build_object('api', true, 'ingestor', true, 'mcp', true, 'dashboard', true))::text;
