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
-- Exact sequence enumeration/state query extracted from the accepted metadata
-- helper; no nextval/setval, row export, study fixture or copied receipt mutation.
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind='S' AND n.nspname!~'^pg_' AND n.nspname<>'information_schema';
SELECT format('SELECT jsonb_build_object(''name'',%L,''last_value'',last_value,''is_called'',is_called) FROM %I.%I;',
 format('%I.%I',n.nspname,c.relname),n.nspname,c.relname)
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind='S' AND n.nspname!~'^pg_' AND n.nspname<>'information_schema'
 ORDER BY n.nspname,c.relname
\gexec
ROLLBACK;
