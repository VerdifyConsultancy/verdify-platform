-- PREPARED ONLY. Retry is the sole production execution owner.
-- Owner binds current DB Pod UID/container and exact deployed source before/after.
-- Metadata only: no passwords/verifiers, Secret data, query text, product rows,
-- SET ROLE, authenticated probes, fixture, grant, setter or full-COPY scan.
\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '1s';
SELECT jsonb_build_object('section','identity','at',statement_timestamp(),
 'database',current_database(),'database_oid',(SELECT oid FROM pg_database WHERE datname=current_database()),
 'executor',current_user,'session_user',session_user,'server_version_num',current_setting('server_version_num'),
 'read_only',current_setting('transaction_read_only'),
 'schema_max',(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations'),
 'selected_ledger',(SELECT jsonb_agg(jsonb_build_object('filename',filename,'sha256',sha256,'applied_by',applied_by) ORDER BY filename)
  FROM public.schema_migrations WHERE source='db/migrations' AND seq IN (217,259,268,275)));
WITH expected(duty,login,role_name) AS (VALUES
    ('api','verdify_api_runtime_login','verdify_api_runtime'),
    ('ingestor','verdify_ingestor_runtime_login','verdify_ingestor_runtime'),
    ('mcp','verdify_mcp_runtime_login','verdify_mcp_runtime'),
    ('planner','verdify_planner_runtime_login','verdify_planner_runtime'),
    ('setpoint_server','verdify_setpoint_server_runtime_login','verdify_setpoint_server_runtime'),
    ('ha_backfill','verdify_ha_backfill_runtime_login','verdify_ha_backfill_runtime'),
    ('vision','verdify_vision_runtime_login','verdify_vision_runtime'),
    ('lab_publisher','verdify_lab_publisher_runtime_login','verdify_lab_publisher_runtime'),
    ('grafana','verdify_grafana_runtime_login','verdify_grafana_runtime')
), metadata AS (
 SELECT e.*,r.oid AS login_oid,r.rolcanlogin,r.rolinherit,r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolbypassrls,
  d.oid AS duty_oid,d.rolcanlogin AS duty_can_login,d.rolinherit AS duty_inherit,
  d.rolsuper AS duty_super,d.rolcreatedb AS duty_create_db,d.rolcreaterole AS duty_create_role,
  d.rolreplication AS duty_replication,d.rolbypassrls AS duty_bypass_rls,
  CASE WHEN r.oid IS NOT NULL THEN has_database_privilege(r.oid,current_database(),'CREATE') END AS can_create_database_schema,
  CASE WHEN r.oid IS NOT NULL THEN has_schema_privilege(r.oid,'public','CREATE') END AS can_create_in_public,
  (SELECT count(*) FROM pg_auth_members m WHERE m.member=r.oid) AS membership_count,
  (SELECT jsonb_agg(jsonb_build_object('role',g.rolname,'admin',m.admin_option,'inherit',m.inherit_option,'set',m.set_option) ORDER BY g.rolname)
   FROM pg_auth_members m JOIN pg_roles g ON g.oid=m.roleid WHERE m.member=r.oid) AS memberships,
  (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE c.relowner IN (r.oid,d.oid) AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%') AS owned_relations,
  (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE p.proowner IN (r.oid,d.oid) AND n.nspname NOT IN ('pg_catalog','information_schema')) AS owned_functions,
  (SELECT count(*) FROM pg_namespace n WHERE n.nspowner IN (r.oid,d.oid)) AS owned_schemas
 FROM expected e LEFT JOIN pg_roles r ON r.rolname=e.login LEFT JOIN pg_roles d ON d.rolname=e.role_name
)
SELECT jsonb_build_object('section','nine-role-posture','roles',jsonb_agg(to_jsonb(metadata) ORDER BY duty)) FROM metadata;
-- This snapshot identifies authenticated existing sessions, not a new login test.
-- A short-lived CronJob may have zero sessions; absence gives no duty-execution credit.
SELECT jsonb_build_object('section','existing-session-role-pod-ip-join',
 'clients',coalesce(jsonb_agg(to_jsonb(s) ORDER BY s.usename,s.client_addr::text),'[]'::jsonb))
FROM (SELECT usename,client_addr,backend_type,state,count(*) AS count,
 min(backend_start) AS oldest_backend,max(backend_start) AS newest_backend
 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()
 AND (usename IN ('verdify_api_runtime_login','verdify_ingestor_runtime_login','verdify_mcp_runtime_login','verdify_planner_runtime_login','verdify_setpoint_server_runtime_login','verdify_ha_backfill_runtime_login','verdify_vision_runtime_login','verdify_lab_publisher_runtime_login','verdify_grafana_runtime_login') OR usename='verdify')
 GROUP BY usename,client_addr,backend_type,state) s;
ROLLBACK;
