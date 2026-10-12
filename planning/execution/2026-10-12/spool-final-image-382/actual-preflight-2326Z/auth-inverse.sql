\set ON_ERROR_STOP on
BEGIN;
SET LOCAL log_statement='none';SET LOCAL log_min_error_statement='panic';SET LOCAL log_min_duration_statement=-1;SET LOCAL log_min_duration_sample=-1;SET LOCAL log_statement_sample_rate=0;SET LOCAL log_duration=off;SET LOCAL log_parameter_max_length=0;SET LOCAL log_parameter_max_length_on_error=0;SET LOCAL pg_stat_statements.track_utility=off;
DO $guard$ BEGIN
 IF current_database()<>'verdify_rehearsal' OR current_setting('cluster_name')<>'verdify-cnpg-s2-integration'
 OR current_setting('server_version_num')<>'160013' OR pg_is_in_recovery()
 OR inet_client_addr() IS NOT NULL OR current_user<>'postgres' OR session_user<>'postgres'
 OR (SELECT oid FROM pg_database WHERE datname=current_database())<>16447
 OR (SELECT system_identifier::text FROM pg_control_system())<>'7693399121498484768'
 OR (SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')<>275
 OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=275 AND sha256='f4f8da110236461680f41770bad766a7e28b1a6db1982ecc632e93930a375dc4')
 OR (SELECT count(*) FROM public.cnpg_s2_275_runtime_receipts)<>3
 THEN RAISE EXCEPTION 'refusing clone identity/admission drift'; END IF;
 IF EXISTS(SELECT 1 FROM pg_extension WHERE extname~'(audit|auto_explain)')
 OR COALESCE(current_setting('pgaudit.log',true),'none') NOT IN ('','none')
 OR EXISTS(SELECT 1 FROM regexp_split_to_table(current_setting('shared_preload_libraries'),',') v WHERE trim(v) NOT IN ('timescaledb','pg_stat_statements'))
 THEN RAISE EXCEPTION 'refusing overriding audit retention'; END IF;
 IF EXISTS(SELECT 1 FROM (VALUES ('verdify_ingestor_runtime_login','verdify_ingestor_runtime'),('verdify_experiment_v2_equipment_source_collector_login','verdify_experiment_equipment_source_collector')) expected(login,duty)
 LEFT JOIN pg_authid l ON l.rolname=expected.login LEFT JOIN pg_authid d ON d.rolname=expected.duty
 WHERE l.oid IS NULL OR d.oid IS NULL OR NOT l.rolcanlogin OR NOT l.rolinherit
 OR l.rolsuper OR l.rolcreatedb OR l.rolcreaterole OR l.rolreplication OR l.rolbypassrls
 OR d.rolcanlogin OR d.rolinherit OR d.rolsuper OR d.rolcreatedb OR d.rolcreaterole OR d.rolreplication OR d.rolbypassrls
 OR (SELECT count(*) FROM pg_auth_members WHERE member=l.oid)<>1
 OR NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=l.oid AND roleid=d.oid AND NOT admin_option AND inherit_option AND set_option)
 OR l.rolpassword IS NULL)
 THEN RAISE EXCEPTION 'refusing login/password/membership drift; no repair'; END IF;
 IF EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend') THEN RAISE EXCEPTION 'refusing another target client';END IF;
 IF public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login')<>decode('6bae416d783bd87bf9ebf1cea3c9feecc392bdb14644acd78abc6961405b0480','hex') THEN RAISE EXCEPTION 'ordinary boundary drift';END IF;
END $guard$;
ALTER ROLE verdify_ingestor_runtime_login PASSWORD NULL;
ALTER ROLE verdify_experiment_v2_equipment_source_collector_login PASSWORD NULL;
COMMIT;
