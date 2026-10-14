-- #643: remove owner credentials from two ordinary scheduled duties.
-- Reserved 2026-10-13 by ROOT; source only until owner qualification/delivery.
-- Preserve all applied functions, nine ordinary roles and three catalog seals.
DO $preflight$
BEGIN
 IF current_user <> 'verdify'
 OR (SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations') IS DISTINCT FROM 275
 OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
   AND filename='db/migrations/275-lighting-minutes-policy-bounded-jit.sql'
   AND sha256='f4f8da110236461680f41770bad766a7e28b1a6db1982ecc632e93930a375dc4' AND stamp_method='runner')
 OR public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login') IS DISTINCT FROM decode('5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba','hex')
 OR public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login') IS DISTINCT FROM decode('8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743','hex')
 OR public.fn_mcp_runtime_boundary_digest() IS DISTINCT FROM decode('7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68','hex')
 OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
 OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
   WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
 OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
 OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
   AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
 OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ('verdify_writer_watchdog_runtime','verdify_writer_watchdog_runtime_login','verdify_matview_refresh_runtime','verdify_matview_refresh_runtime_login'))
 OR to_regnamespace('verdify_writer_watchdog_runtime') IS NOT NULL
 OR to_regnamespace('verdify_matview_refresh_runtime') IS NOT NULL
 THEN RAISE EXCEPTION '276 exact predecessor or new-role custody mismatch'; END IF;
END $preflight$;
CREATE TEMP TABLE role643_maintenance_predecessors ON COMMIT DROP AS
 SELECT login_name,boundary_sha256 FROM public.runtime_ordinary_login_attestation_receipts
 UNION ALL SELECT 'verdify_mcp_runtime_login',boundary_sha256 FROM public.mcp_runtime_boundary_receipt;

CREATE ROLE verdify_writer_watchdog_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE verdify_writer_watchdog_runtime_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT verdify_writer_watchdog_runtime TO verdify_writer_watchdog_runtime_login WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;
CREATE SCHEMA verdify_writer_watchdog_runtime AUTHORIZATION verdify;
REVOKE ALL ON SCHEMA verdify_writer_watchdog_runtime FROM PUBLIC;
GRANT USAGE ON SCHEMA verdify_writer_watchdog_runtime TO verdify_writer_watchdog_runtime;
ALTER ROLE verdify_writer_watchdog_runtime_login SET search_path=verdify_writer_watchdog_runtime,pg_catalog,public,pg_temp;

CREATE ROLE verdify_matview_refresh_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE verdify_matview_refresh_runtime_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT verdify_matview_refresh_runtime TO verdify_matview_refresh_runtime_login WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;
CREATE SCHEMA verdify_matview_refresh_runtime AUTHORIZATION verdify;
REVOKE ALL ON SCHEMA verdify_matview_refresh_runtime FROM PUBLIC;
GRANT USAGE ON SCHEMA verdify_matview_refresh_runtime TO verdify_matview_refresh_runtime;
ALTER ROLE verdify_matview_refresh_runtime_login SET search_path=verdify_matview_refresh_runtime,pg_catalog,public,pg_temp;

CREATE FUNCTION verdify_writer_watchdog_runtime.fn_attest_login() RETURNS boolean
 LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $attest$
 SELECT current_user=session_user AND current_user='verdify_writer_watchdog_runtime_login'
 AND r.rolcanlogin AND r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
 AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls
 AND NOT has_database_privilege(current_user,current_database(),'CREATE')
 AND NOT has_schema_privilege(current_user,'public','CREATE')
 AND NOT has_schema_privilege(current_user,'verdify_writer_watchdog_runtime','CREATE')
 AND (SELECT count(*) FROM pg_auth_members m WHERE m.member=r.oid)=1
 AND EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles d ON d.oid=m.roleid
   WHERE m.member=r.oid AND d.rolname='verdify_writer_watchdog_runtime' AND NOT m.admin_option
   AND m.inherit_option AND m.set_option AND NOT d.rolcanlogin AND NOT d.rolinherit
   AND NOT d.rolsuper AND NOT d.rolcreatedb AND NOT d.rolcreaterole
   AND NOT d.rolreplication AND NOT d.rolbypassrls)
 AND (SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='verdify_writer_watchdog_runtime'))=0
 AND (SELECT count(*) FROM pg_auth_members WHERE roleid=(SELECT oid FROM pg_roles WHERE rolname='verdify_writer_watchdog_runtime'))=1
 AND NOT has_table_privilege(current_user,'public.alert_log','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.alert_log','SELECT,INSERT,UPDATE,REFERENCES')
 AND NOT has_table_privilege(current_user,'public.climate','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.climate','SELECT,INSERT,UPDATE,REFERENCES')
 AND NOT has_table_privilege(current_user,'public.control_assignments','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.control_assignments','SELECT,INSERT,UPDATE,REFERENCES')
 AND NOT has_table_privilege(current_user,'public.effective_policy_vectors','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.effective_policy_vectors','SELECT,INSERT,UPDATE,REFERENCES')
 AND NOT has_table_privilege(current_user,'public.policy_delivery_outbox','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.policy_delivery_outbox','SELECT,INSERT,UPDATE,REFERENCES')
 AND NOT has_table_privilege(current_user,'public.policy_exposures','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.policy_exposures','SELECT,INSERT,UPDATE,REFERENCES')
 FROM pg_roles r WHERE r.rolname=current_user
 $attest$;
 REVOKE ALL ON FUNCTION verdify_writer_watchdog_runtime.fn_attest_login() FROM PUBLIC;
 GRANT EXECUTE ON FUNCTION verdify_writer_watchdog_runtime.fn_attest_login() TO verdify_writer_watchdog_runtime;

CREATE FUNCTION verdify_matview_refresh_runtime.fn_attest_login() RETURNS boolean
 LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $attest$
 SELECT current_user=session_user AND current_user='verdify_matview_refresh_runtime_login'
 AND r.rolcanlogin AND r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
 AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls
 AND NOT has_database_privilege(current_user,current_database(),'CREATE')
 AND NOT has_schema_privilege(current_user,'public','CREATE')
 AND NOT has_schema_privilege(current_user,'verdify_matview_refresh_runtime','CREATE')
 AND (SELECT count(*) FROM pg_auth_members m WHERE m.member=r.oid)=1
 AND EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles d ON d.oid=m.roleid
   WHERE m.member=r.oid AND d.rolname='verdify_matview_refresh_runtime' AND NOT m.admin_option
   AND m.inherit_option AND m.set_option AND NOT d.rolcanlogin AND NOT d.rolinherit
   AND NOT d.rolsuper AND NOT d.rolcreatedb AND NOT d.rolcreaterole
   AND NOT d.rolreplication AND NOT d.rolbypassrls)
 AND (SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='verdify_matview_refresh_runtime'))=0
 AND (SELECT count(*) FROM pg_auth_members WHERE roleid=(SELECT oid FROM pg_roles WHERE rolname='verdify_matview_refresh_runtime'))=1
 AND NOT has_table_privilege(current_user,'public.alert_log','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.alert_log','SELECT,INSERT,UPDATE,REFERENCES')
 AND NOT has_table_privilege(current_user,'public.climate','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.climate','SELECT,INSERT,UPDATE,REFERENCES')
 AND NOT has_table_privilege(current_user,'public.control_assignments','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.control_assignments','SELECT,INSERT,UPDATE,REFERENCES')
 AND NOT has_table_privilege(current_user,'public.effective_policy_vectors','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.effective_policy_vectors','SELECT,INSERT,UPDATE,REFERENCES')
 AND NOT has_table_privilege(current_user,'public.policy_delivery_outbox','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.policy_delivery_outbox','SELECT,INSERT,UPDATE,REFERENCES')
 AND NOT has_table_privilege(current_user,'public.policy_exposures','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.policy_exposures','SELECT,INSERT,UPDATE,REFERENCES')
 FROM pg_roles r WHERE r.rolname=current_user
 $attest$;
 REVOKE ALL ON FUNCTION verdify_matview_refresh_runtime.fn_attest_login() FROM PUBLIC;
 GRANT EXECUTE ON FUNCTION verdify_matview_refresh_runtime.fn_attest_login() TO verdify_matview_refresh_runtime;

-- Only this watchdog's two alert lifecycles; no raw table or sequence grants.
-- Numeric arguments come from the Kubernetes lease; telemetry age is measured here.
CREATE FUNCTION verdify_writer_watchdog_runtime.fn_sweep(p_lease_state text,p_lease_age_s bigint)
 RETURNS TABLE(climate_age_s bigint,writer_decision text,telemetry_decision text)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $sweep$
BEGIN
 IF session_user <> 'verdify_writer_watchdog_runtime_login' THEN
  RAISE EXCEPTION 'watchdog caller outside duty' USING ERRCODE='42501';
 END IF;
 IF p_lease_state IS NULL OR p_lease_state NOT IN ('found','absent','read_failed')
 OR (p_lease_state='found' AND (p_lease_age_s IS NULL OR p_lease_age_s<0))
 OR (p_lease_state<>'found' AND p_lease_age_s IS NOT NULL) THEN
  RAISE EXCEPTION 'invalid watchdog lease metadata' USING ERRCODE='22023';
 END IF;
 writer_decision:='skipped';
 IF p_lease_state='absent' OR (p_lease_state='found' AND p_lease_age_s>180) THEN
  INSERT INTO public.alert_log(alert_type,severity,category,message,details,source)
   SELECT 'writer_absent','critical','system',
    'WRITER ABSENT: verdify-ingestor-writer lease stale/missing (age_s='||coalesce(p_lease_age_s::text,'absent')||'). Telemetry, setpoint dispatch, and the in-ingestor alert engine are all down; the ESP32 holds its on-chip band.',
    json_build_object('lease_found',p_lease_state='found','lease_age_s',p_lease_age_s,'threshold_s',180)::jsonb,'writer_watchdog'
   WHERE NOT EXISTS(SELECT 1 FROM public.alert_log WHERE alert_type='writer_absent' AND resolved_at IS NULL);
  writer_decision:='critical';
 ELSIF p_lease_state='found' THEN
  UPDATE public.alert_log SET resolved_at=now(),disposition='resolved',resolved_by='writer_watchdog',resolution='writer lease fresh again'
   WHERE alert_type='writer_absent' AND source='writer_watchdog' AND resolved_at IS NULL;
  writer_decision:='resolved';
 END IF;
 SELECT round(extract(epoch FROM now()-max(ts)))::bigint INTO climate_age_s FROM public.climate;
 IF climate_age_s IS NOT NULL AND climate_age_s>600 THEN
  INSERT INTO public.alert_log(alert_type,severity,category,message,details,source)
   SELECT 'telemetry_stall','critical','system',
    'TELEMETRY STALL: newest climate row is '||climate_age_s::text||'s old (threshold 600s). Writer may be connected but not persisting.',
    json_build_object('climate_age_s',climate_age_s,'threshold_s',600)::jsonb,'writer_watchdog'
   WHERE NOT EXISTS(SELECT 1 FROM public.alert_log WHERE alert_type='telemetry_stall' AND resolved_at IS NULL);
  telemetry_decision:='critical';
 ELSE
  UPDATE public.alert_log SET resolved_at=now(),disposition='resolved',resolved_by='writer_watchdog',resolution='climate rows landing again'
   WHERE alert_type='telemetry_stall' AND source='writer_watchdog' AND resolved_at IS NULL;
  telemetry_decision:='resolved';
 END IF;
 RETURN NEXT;
END $sweep$;
REVOKE ALL ON FUNCTION verdify_writer_watchdog_runtime.fn_sweep(text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION verdify_writer_watchdog_runtime.fn_sweep(text,bigint) TO verdify_writer_watchdog_runtime;

CREATE FUNCTION verdify_matview_refresh_runtime.fn_refresh_mv_band_curve() RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $refresh$
 DECLARE populated boolean;
 BEGIN
  IF session_user <> 'verdify_matview_refresh_runtime_login' THEN
   RAISE EXCEPTION 'refresh caller outside duty' USING ERRCODE='42501';
  END IF;
  SELECT c.relispopulated INTO populated FROM pg_class c
   WHERE c.oid=to_regclass('public.mv_band_curve') AND c.relkind='m' AND pg_get_userbyid(c.relowner)='verdify';
  IF populated IS NULL THEN RAISE EXCEPTION 'fixed refresh target unavailable'; END IF;
  IF populated THEN REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_band_curve;
  ELSE REFRESH MATERIALIZED VIEW public.mv_band_curve; END IF;
  RETURN populated;
 END $refresh$;
 REVOKE ALL ON FUNCTION verdify_matview_refresh_runtime.fn_refresh_mv_band_curve() FROM PUBLIC;
 GRANT EXECUTE ON FUNCTION verdify_matview_refresh_runtime.fn_refresh_mv_band_curve() TO verdify_matview_refresh_runtime;

CREATE FUNCTION verdify_matview_refresh_runtime.fn_refresh_mv_equipment_runtime_daily() RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $refresh$
 DECLARE populated boolean;
 BEGIN
  IF session_user <> 'verdify_matview_refresh_runtime_login' THEN
   RAISE EXCEPTION 'refresh caller outside duty' USING ERRCODE='42501';
  END IF;
  SELECT c.relispopulated INTO populated FROM pg_class c
   WHERE c.oid=to_regclass('public.mv_equipment_runtime_daily') AND c.relkind='m' AND pg_get_userbyid(c.relowner)='verdify';
  IF populated IS NULL THEN RAISE EXCEPTION 'fixed refresh target unavailable'; END IF;
  IF populated THEN REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_equipment_runtime_daily;
  ELSE REFRESH MATERIALIZED VIEW public.mv_equipment_runtime_daily; END IF;
  RETURN populated;
 END $refresh$;
 REVOKE ALL ON FUNCTION verdify_matview_refresh_runtime.fn_refresh_mv_equipment_runtime_daily() FROM PUBLIC;
 GRANT EXECUTE ON FUNCTION verdify_matview_refresh_runtime.fn_refresh_mv_equipment_runtime_daily() TO verdify_matview_refresh_runtime;

CREATE FUNCTION verdify_matview_refresh_runtime.fn_refresh_mv_daily_kpi() RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $refresh$
 DECLARE populated boolean;
 BEGIN
  IF session_user <> 'verdify_matview_refresh_runtime_login' THEN
   RAISE EXCEPTION 'refresh caller outside duty' USING ERRCODE='42501';
  END IF;
  SELECT c.relispopulated INTO populated FROM pg_class c
   WHERE c.oid=to_regclass('public.mv_daily_kpi') AND c.relkind='m' AND pg_get_userbyid(c.relowner)='verdify';
  IF populated IS NULL THEN RAISE EXCEPTION 'fixed refresh target unavailable'; END IF;
  IF populated THEN REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_daily_kpi;
  ELSE REFRESH MATERIALIZED VIEW public.mv_daily_kpi; END IF;
  RETURN populated;
 END $refresh$;
 REVOKE ALL ON FUNCTION verdify_matview_refresh_runtime.fn_refresh_mv_daily_kpi() FROM PUBLIC;
 GRANT EXECUTE ON FUNCTION verdify_matview_refresh_runtime.fn_refresh_mv_daily_kpi() TO verdify_matview_refresh_runtime;

DO $postflight$
BEGIN
 IF EXISTS(SELECT 1 FROM pg_temp.role643_maintenance_predecessors s WHERE s.boundary_sha256 IS DISTINCT FROM
 CASE WHEN s.login_name='verdify_mcp_runtime_login' THEN public.fn_mcp_runtime_boundary_digest()
 ELSE public.fn_runtime_ordinary_boundary_digest(s.login_name) END)
 THEN RAISE EXCEPTION '276 changed an existing sealed runtime boundary'; END IF;
END $postflight$;
