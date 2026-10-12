\set ON_ERROR_STOP on
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='30s'; SET LOCAL lock_timeout='2s';
SET LOCAL search_path=pg_catalog,public,pg_temp;
DO $native$
BEGIN
 IF current_database()<>'verdify_rehearsal' OR current_setting('cluster_name')<>'verdify-cnpg-s2-integration'
    OR current_setting('server_version_num')<>'160013' OR pg_is_in_recovery()
    OR inet_client_addr() IS NOT NULL OR current_user<>'postgres' OR session_user<>'postgres'
    OR (SELECT oid FROM pg_database WHERE datname=current_database())<>16447
    OR (SELECT system_identifier::text FROM pg_control_system())<>'7693399121498484768'
    OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=274
       AND filename='db/migrations/274-qualified-physical-crop-band-publication.sql'
       AND sha256='0b939df1a79e3ce71835e59aedb5d1898805cd43cae72b79fa905f77067a8e4f' AND stamp_method='runner')
    OR (SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations') NOT IN (274,275)
 THEN RAISE EXCEPTION 'refusing actual native identity or274/275 ledger drift'; END IF;
END;
$native$;
-- Metadata only; source214 roles must already exist. No verifier is exported.
SELECT jsonb_build_object(
 'kind','two-role-starting-posture',
 'database_oid',(SELECT oid FROM pg_database WHERE datname=current_database()),
 'system_identifier',(SELECT system_identifier::text FROM pg_control_system()),
 'ledger_max',(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations'),
 'roles',(SELECT jsonb_agg(jsonb_build_object(
   'name',r.rolname,'can_login',r.rolcanlogin,'inherit',r.rolinherit,
   'superuser',r.rolsuper,'createdb',r.rolcreatedb,'createrole',r.rolcreaterole,
   'replication',r.rolreplication,'bypassrls',r.rolbypassrls,
   'password_is_null',a.rolpassword IS NULL,
   'memberships',(SELECT coalesce(jsonb_agg(jsonb_build_object('role',d.rolname,
      'admin_option',m.admin_option) ORDER BY d.rolname),'[]'::jsonb)
      FROM pg_auth_members m JOIN pg_roles d ON d.oid=m.roleid WHERE m.member=r.oid)
 ) ORDER BY r.rolname) FROM pg_roles r JOIN pg_authid a ON a.oid=r.oid
 WHERE r.rolname IN ('verdify_experiment_v2_owner','verdify_experiment_component_executor',
    'verdify_experiment_lifecycle','verdify_experiment_v2_component_executor_login',
    'verdify_experiment_v2_lifecycle_login')));
SELECT jsonb_build_object('kind','existing-component-bindings','experiments',(
 SELECT coalesce(jsonb_agg(jsonb_build_object('experiment_id',e.experiment_id,
   'status',e.status,'execution_phase',e.execution_phase,'admission_state',e.admission_state,
   'component_enabled',e.component_enabled,'lease_generation',e.lease_generation,
   'device_ids',(SELECT coalesce(jsonb_agg(device_id ORDER BY device_id),'[]'::jsonb) FROM (
      SELECT device_id FROM public.experiment_v2_runtime_generations g WHERE g.experiment_id=e.experiment_id
      UNION SELECT device_id FROM public.experiment_v2_exposures x WHERE x.experiment_id=e.experiment_id
   ) devices)) ORDER BY e.experiment_id),'[]'::jsonb)
 FROM public.control_experiments e WHERE e.protocol_version=2 AND e.transport_kind='legacy_components_v1'));
ROLLBACK;
