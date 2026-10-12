-- Owner's admitted integration target only, within psql --single-transaction.
-- Actual OID comes from the authoritative handoff; no production connection.
SELECT set_config('verdify.qualification_oid', :'actual_database_oid', true);
DO $guard$
BEGIN
 IF current_database()<>'verdify_rehearsal'
    OR current_setting('cluster_name')<>'verdify-cnpg-s2-integration'
    OR current_setting('server_version_num')<>'160013'
    OR pg_is_in_recovery()
    OR (SELECT oid::text FROM pg_database WHERE datname=current_database())<>
       current_setting('verdify.qualification_oid')
    OR NOT EXISTS(SELECT 1 FROM public.schema_migrations
        WHERE source='db/migrations' AND seq=275
          AND filename='db/migrations/275-lighting-minutes-policy-bounded-jit.sql'
          AND sha256='f4f8da110236461680f41770bad766a7e28b1a6db1982ecc632e93930a375dc4' AND stamp_method='runner')
    OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>275)
 THEN RAISE EXCEPTION 'refusing target identity or frozen schema drift'; END IF;
 IF EXISTS (
   SELECT 1 FROM (VALUES
      ('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor'),
      ('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle')
   ) expected(login_name,duty_name)
   LEFT JOIN pg_roles l ON l.rolname=expected.login_name
   LEFT JOIN pg_roles d ON d.rolname=expected.duty_name
   WHERE l.oid IS NULL OR d.oid IS NULL OR NOT l.rolinherit OR l.rolsuper
     OR l.rolcreatedb OR l.rolcreaterole OR l.rolreplication OR l.rolbypassrls
     OR d.rolcanlogin OR d.rolinherit OR d.rolsuper OR d.rolcreatedb
     OR d.rolcreaterole OR d.rolreplication OR d.rolbypassrls
     OR (SELECT count(*) FROM pg_auth_members m WHERE m.member=l.oid)<>1
     OR NOT EXISTS(SELECT 1 FROM pg_auth_members m
                    WHERE m.member=l.oid AND m.roleid=d.oid AND NOT m.admin_option)
 ) THEN RAISE EXCEPTION 'refusing restored role/membership drift; no grant repair'; END IF;
 IF EXISTS (
   SELECT 1 FROM (VALUES
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_resolve_readiness(uuid,uuid,bigint)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_resolve_randomized(uuid,uuid,bigint)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_resolve_recovery(uuid,uuid,bigint)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_executor_runtime(uuid,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_claim_executor_candidate(uuid,text,bigint,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_read_observation_window(uuid,uuid,uuid,text,bigint)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_record_work_event(uuid,uuid,text,jsonb,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_begin_delivery_bundle(uuid,uuid,uuid,text,text,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_read_delivery_bundle(uuid,uuid,text,text,bigint)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_record_component_outcome(uuid,uuid,uuid,integer,text,text,bigint,bigint,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_record_delivery_bundle(uuid,uuid,uuid,timestamptz,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_register_runtime_instance(uuid,text,uuid,bigint,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_record_observation_epoch(uuid,uuid,uuid,uuid,bytea,jsonb,text,text,text,text,bigint,bigint,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_record_preexposure_mismatch(uuid,uuid,uuid,text,uuid,bytea,jsonb,text,text,text,text,uuid,bigint,bigint,bigint,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_record_runtime_snapshot(uuid,text,uuid,bytea,jsonb,text,text,text,text,uuid,bigint,bigint,boolean,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_monitor_open_exposure(uuid,text,bigint)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_open_exposure(uuid,uuid,text,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_close_exposure(uuid,text,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_request_recovery(uuid,uuid,tstzrange,timestamptz,text,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_report_runtime_fault(uuid,text,uuid,bigint,uuid,bigint,bigint,text,text,text)'),
('verdify_experiment_v2_component_executor_login','verdify_experiment_component_executor','public.fn_experiment_v2_safe_startup_attestation(text,uuid)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_configure(uuid,text,text,text,text,text,text,uuid,text,bigint,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_lock_design(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_launch_lock(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,tstzrange,text,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_launch_approve_day1(uuid,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_proof_begin(uuid,text,tstzrange,text,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_proof_open_aggressive(uuid,uuid,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_proof_begin_baseline_after(uuid,uuid,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_proof_finish(uuid,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_proof_resolve_emergency(uuid,uuid,text,bigint,text,text,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_proof_begin_emergency_recovery(uuid,uuid,text,bigint,tstzrange,text,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_proof_retry_emergency_recovery(uuid,uuid,uuid,text,bigint,tstzrange,text,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_proof_finish_emergency_recovery(uuid,uuid,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_proof_attempt_status(uuid)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_proof_resolve_startup_rollover(uuid,uuid,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_terminalize_legacy_expired_recovery(uuid,uuid,bigint,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_direct_launch_commit(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_register_state(uuid,text,smallint,bytea,bytea,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_record_approval(uuid,text,text,integer,text,text,tstzrange,timestamptz,text,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_transition(uuid,text,text,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_set_admission(uuid,text,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_record_facility_safe_closure(uuid,text,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_create_work(uuid,text,text,tstzrange,timestamptz,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_request_recovery(uuid,uuid,tstzrange,timestamptz,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_complete(uuid,text,text)'),
('verdify_experiment_v2_lifecycle_login','verdify_experiment_lifecycle','public.fn_experiment_v2_api_status(uuid)')
   ) expected(login_name,duty_name,signature)
   LEFT JOIN pg_proc p ON p.oid=to_regprocedure(expected.signature)
   WHERE p.oid IS NULL OR pg_get_userbyid(p.proowner)<>'verdify_experiment_v2_owner'
     OR NOT has_function_privilege(expected.login_name,p.oid,'EXECUTE')
 ) THEN RAISE EXCEPTION 'refusing restored function ownership/grant drift'; END IF;
END;
$guard$;
-- This file never creates/grants/replaces any object or role.
