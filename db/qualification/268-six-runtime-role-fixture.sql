DO $guard$ BEGIN IF current_database()<>'verdify_rehearsal' OR inet_server_addr() IS NOT NULL OR current_setting('listen_addresses')<>'' THEN RAISE EXCEPTION 'socket-only isolated restored fixture required'; END IF; END $guard$;

-- Independent actual role flags, exact membership and effective CREATE denial.
DO $identity$
DECLARE duty text; login text; role_row record;
BEGIN
 FOREACH duty IN ARRAY ARRAY['grafana','planner','setpoint_server','ha_backfill','lab_publisher','vision'] LOOP
  login := 'verdify_'||duty||'_runtime_login';
  SELECT * INTO STRICT role_row FROM pg_catalog.pg_roles WHERE rolname=login;
  IF NOT role_row.rolcanlogin OR NOT role_row.rolinherit OR role_row.rolsuper OR role_row.rolcreatedb OR role_row.rolcreaterole OR role_row.rolreplication OR role_row.rolbypassrls
   OR pg_catalog.has_database_privilege(login,current_database(),'CREATE') OR pg_catalog.has_schema_privilege(login,'public','CREATE')
   OR (SELECT count(*) FROM pg_catalog.pg_auth_members WHERE member=role_row.oid)<>1
   OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles d ON d.oid=m.roleid WHERE m.member=role_row.oid AND d.rolname='verdify_'||duty||'_runtime' AND NOT m.admin_option AND m.inherit_option AND m.set_option AND NOT d.rolcanlogin AND NOT d.rolinherit)
   OR NOT ('search_path=verdify_'||duty||'_runtime, pg_catalog, public, pg_temp'=ANY(role_row.rolconfig))
  THEN RAISE EXCEPTION 'runtime identity/default contract mismatch for %',login; END IF;
 END LOOP;
END $identity$;

CREATE FUNCTION pg_temp.expect_denied(statement text) RETURNS void LANGUAGE plpgsql SECURITY INVOKER AS $test$
BEGIN
 BEGIN EXECUTE statement; EXCEPTION WHEN insufficient_privilege THEN RETURN; END;
 RAISE EXCEPTION 'unexpected permission: %',statement;
END $test$;

DO $temp_grant$
DECLARE temporary_schema text;
BEGIN
 SELECT nspname INTO STRICT temporary_schema FROM pg_catalog.pg_namespace WHERE oid=pg_catalog.pg_my_temp_schema();
 EXECUTE format('GRANT USAGE ON SCHEMA %I TO verdify_planner_runtime_login, verdify_setpoint_server_runtime_login, verdify_ha_backfill_runtime_login, verdify_vision_runtime_login, verdify_lab_publisher_runtime_login, verdify_grafana_runtime_login',temporary_schema);
END $temp_grant$;

GRANT EXECUTE ON FUNCTION pg_temp.expect_denied(text) TO verdify_planner_runtime_login, verdify_setpoint_server_runtime_login, verdify_ha_backfill_runtime_login, verdify_vision_runtime_login, verdify_lab_publisher_runtime_login, verdify_grafana_runtime_login;

SET SESSION AUTHORIZATION verdify_planner_runtime_login;

-- SET SESSION AUTHORIZATION does not load ALTER ROLE login defaults.
-- Bind the exact declared login search_path for this session-auth fixture.
SET search_path = verdify_planner_runtime, pg_catalog, public, pg_temp;

SELECT * FROM climate LIMIT 1;

SELECT * FROM fn_planner_scorecard(CURRENT_DATE);

INSERT INTO public.planner_graph_runs(trigger_id,thread_id,status,run_mode) VALUES('aaaaaaaa-2680-4000-8000-000000000001','aaaaaaaa-2680-4000-8000-000000000002','queued','qualification');

UPDATE public.planner_graph_runs SET status='completed' WHERE trigger_id='aaaaaaaa-2680-4000-8000-000000000001';

INSERT INTO public.planner_graph_runs(trigger_id,thread_id,status,run_mode)
VALUES('aaaaaaaa-2680-4000-8000-000000000001','aaaaaaaa-2680-4000-8000-000000000002','completed','qualification')
ON CONFLICT(trigger_id) DO UPDATE SET status=EXCLUDED.status;

INSERT INTO public.planner_memory_items(memory_id,greenhouse_id,memory_type,source_type,title,summary,body,content_hash)
VALUES('aaaaaaaa-2680-4000-8000-000000000003','vallery','lesson','qualification','isolated','isolated','isolated','role643-fixture')
ON CONFLICT(greenhouse_id,memory_type,content_hash) DO UPDATE SET summary=EXCLUDED.summary;

INSERT INTO public.planner_memory_retrievals(retrieval_id,greenhouse_id,strategy,query_text)
VALUES('aaaaaaaa-2680-4000-8000-000000000004','vallery','qualification','isolated');

SELECT pg_temp.expect_denied('DELETE FROM public.planner_graph_runs');
SELECT pg_temp.expect_denied('INSERT INTO public.daily_plan_archive_audit(date,page_path) VALUES(''2099-01-02'',''forbidden'')');


SELECT pg_temp.expect_denied('SELECT * FROM public.control_assignments LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.control_arm_resolutions LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.effective_policy_vectors LIMIT 1');

SELECT pg_temp.expect_denied('INSERT INTO public.alert_log DEFAULT VALUES');

SELECT pg_temp.expect_denied('CREATE TABLE public.role643_forbidden(id int)');

SELECT pg_temp.expect_denied('SET ROLE verdify');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_setpoint_server_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_ha_backfill_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_vision_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_lab_publisher_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_grafana_runtime.blinded_exposure_coverage LIMIT 1');

SELECT json_build_object('role',current_user,'duty_fixture','allow_and_deny_complete');

RESET SESSION AUTHORIZATION;

SET SESSION AUTHORIZATION verdify_setpoint_server_runtime_login;

-- SET SESSION AUTHORIZATION does not load ALTER ROLE login defaults.
-- Bind the exact declared login search_path for this session-auth fixture.
SET search_path = verdify_setpoint_server_runtime, pg_catalog, public, pg_temp;

SELECT * FROM fn_band_setpoints(now());

SELECT * FROM fn_house_vpd_control_band(now());

SELECT * FROM fn_zone_vpd_targets(now());

SELECT * FROM fn_lighting_minutes_policy(now(),'vallery');

INSERT INTO v_runtime_equipment_state_write(ts,equipment,state) VALUES(now()-interval '1 day','grow_light_main',false);

SELECT pg_temp.expect_denied('SELECT * FROM public.control_assignments LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.control_arm_resolutions LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.effective_policy_vectors LIMIT 1');

SELECT pg_temp.expect_denied('INSERT INTO public.alert_log DEFAULT VALUES');

SELECT pg_temp.expect_denied('CREATE TABLE public.role643_forbidden(id int)');

SELECT pg_temp.expect_denied('SET ROLE verdify');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_planner_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_ha_backfill_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_vision_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_lab_publisher_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_grafana_runtime.blinded_exposure_coverage LIMIT 1');

SELECT pg_temp.expect_denied('INSERT INTO v_runtime_equipment_state_write(ts,equipment,state) VALUES(now(),''heat1'',true)');

SELECT json_build_object('role',current_user,'duty_fixture','allow_and_deny_complete');

RESET SESSION AUTHORIZATION;

SET SESSION AUTHORIZATION verdify_ha_backfill_runtime_login;

-- SET SESSION AUTHORIZATION does not load ALTER ROLE login defaults.
-- Bind the exact declared login search_path for this session-auth fixture.
SET search_path = verdify_ha_backfill_runtime, pg_catalog, public, pg_temp;

SELECT * FROM climate LIMIT 1;

INSERT INTO climate(ts,temp_avg,greenhouse_id) VALUES(now()-interval '1 day',70,'vallery');

INSERT INTO diagnostics(ts,greenhouse_id) VALUES(now()-interval '1 day','vallery');

INSERT INTO energy(ts,watts_total) VALUES(now()-interval '1 day',0);

INSERT INTO system_state(ts,entity,value,greenhouse_id) VALUES(now()-interval '1 day','qualification',0,'vallery');

INSERT INTO setpoint_snapshot(ts,parameter,value,greenhouse_id) VALUES(now()-interval '1 day','qualification',0,'vallery');

INSERT INTO equipment_state(ts,equipment,state,greenhouse_id) SELECT now()-interval '1 day','grow_light_main',false,'vallery' WHERE NOT EXISTS(SELECT 1 FROM equipment_state WHERE ts=now()-interval '1 day' AND equipment='grow_light_main');

SELECT pg_temp.expect_denied('SELECT * FROM public.control_assignments LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.control_arm_resolutions LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.effective_policy_vectors LIMIT 1');

SELECT pg_temp.expect_denied('INSERT INTO public.alert_log DEFAULT VALUES');

SELECT pg_temp.expect_denied('CREATE TABLE public.role643_forbidden(id int)');

SELECT pg_temp.expect_denied('SET ROLE verdify');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_planner_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_setpoint_server_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_vision_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_lab_publisher_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_grafana_runtime.blinded_exposure_coverage LIMIT 1');

SELECT json_build_object('role',current_user,'duty_fixture','allow_and_deny_complete');

RESET SESSION AUTHORIZATION;

SET SESSION AUTHORIZATION verdify_vision_runtime_login;

-- SET SESSION AUTHORIZATION does not load ALTER ROLE login defaults.
-- Bind the exact declared login search_path for this session-auth fixture.
SET search_path = verdify_vision_runtime, pg_catalog, public, pg_temp;

SELECT * FROM climate LIMIT 1;

SELECT * FROM crops LIMIT 1;

SELECT * FROM camera_zone_map LIMIT 1;

INSERT INTO image_observations(ts,camera,zone,image_path,model,raw_response,crops_observed) VALUES(now(),'greenhouse_1','south','qualification-only','qualification-only','{}','[]') RETURNING id;

INSERT INTO observations(ts,obs_type,source,notes) VALUES(now(),'visual_health','gemini-vision','isolated qualification only') RETURNING id;

SELECT pg_temp.expect_denied('SELECT * FROM public.control_assignments LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.control_arm_resolutions LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.effective_policy_vectors LIMIT 1');

SELECT pg_temp.expect_denied('INSERT INTO public.alert_log DEFAULT VALUES');

SELECT pg_temp.expect_denied('CREATE TABLE public.role643_forbidden(id int)');

SELECT pg_temp.expect_denied('SET ROLE verdify');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_planner_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_setpoint_server_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_ha_backfill_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_lab_publisher_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_grafana_runtime.blinded_exposure_coverage LIMIT 1');

SELECT pg_temp.expect_denied('UPDATE image_observations SET model=''forbidden''');

SELECT pg_temp.expect_denied('DELETE FROM observations');

SELECT pg_temp.expect_denied('INSERT INTO observations(ts,obs_type,source,notes) VALUES(now(),''general'',''manual'',''forbidden'')');

SELECT json_build_object('role',current_user,'duty_fixture','allow_and_deny_complete');

RESET SESSION AUTHORIZATION;

SET SESSION AUTHORIZATION verdify_lab_publisher_runtime_login;

-- SET SESSION AUTHORIZATION does not load ALTER ROLE login defaults.
-- Bind the exact declared login search_path for this session-auth fixture.
SET search_path = verdify_lab_publisher_runtime, pg_catalog, public, pg_temp;

SELECT * FROM v_active_plan LIMIT 1;

SELECT * FROM v_crop_catalog_with_profiles LIMIT 1;

SELECT * FROM fn_setpoint_at('temp_low',now());

INSERT INTO public.daily_plan_archive_audit(date,page_path) VALUES('2099-01-01','qualification-only') ON CONFLICT(date) DO UPDATE SET page_path=EXCLUDED.page_path;

SELECT pg_temp.expect_denied('SELECT * FROM public.control_assignments LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.control_arm_resolutions LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.effective_policy_vectors LIMIT 1');

SELECT pg_temp.expect_denied('INSERT INTO public.alert_log DEFAULT VALUES');

SELECT pg_temp.expect_denied('CREATE TABLE public.role643_forbidden(id int)');

SELECT pg_temp.expect_denied('SET ROLE verdify');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_planner_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_setpoint_server_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_ha_backfill_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_vision_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_grafana_runtime.blinded_exposure_coverage LIMIT 1');

SELECT json_build_object('role',current_user,'duty_fixture','allow_and_deny_complete');

RESET SESSION AUTHORIZATION;

SET SESSION AUTHORIZATION verdify_grafana_runtime_login;

-- SET SESSION AUTHORIZATION does not load ALTER ROLE login defaults.
-- Bind the exact declared login search_path for this session-auth fixture.
SET search_path = verdify_grafana_runtime, pg_catalog, public, pg_temp;

SELECT * FROM climate LIMIT 1;

SELECT * FROM blinded_exposure_coverage LIMIT 1;

SELECT * FROM blinded_experiment_lifecycle LIMIT 1;

SELECT * FROM blinded_activation_identity LIMIT 1;

SELECT * FROM blinded_activation_lineage LIMIT 1;

SELECT * FROM blinded_experiment_events LIMIT 1;

SELECT * FROM fn_band_timeline(now()-interval '1 hour',now());

SELECT pg_temp.expect_denied('SELECT * FROM public.control_assignments LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.control_arm_resolutions LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM public.effective_policy_vectors LIMIT 1');

SELECT pg_temp.expect_denied('INSERT INTO public.alert_log DEFAULT VALUES');

SELECT pg_temp.expect_denied('CREATE TABLE public.role643_forbidden(id int)');

SELECT pg_temp.expect_denied('SET ROLE verdify');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_planner_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_setpoint_server_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_ha_backfill_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_vision_runtime.climate LIMIT 1');

SELECT pg_temp.expect_denied('SELECT * FROM verdify_lab_publisher_runtime.climate LIMIT 1');

SELECT json_build_object('role',current_user,'duty_fixture','allow_and_deny_complete');

RESET SESSION AUTHORIZATION;
