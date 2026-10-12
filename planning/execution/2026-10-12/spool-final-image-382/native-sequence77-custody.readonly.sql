\set ON_ERROR_STOP on
BEGIN READ ONLY; SET LOCAL statement_timeout='60s';
DO $g$ BEGIN IF current_user<>'postgres' OR session_user<>'postgres' OR inet_client_addr() IS NOT NULL OR current_database()<>'verdify_rehearsal' OR current_setting('cluster_name')<>'verdify-cnpg-s2-integration' OR pg_is_in_recovery() OR current_setting('server_version_num')::int<>160013 OR (SELECT oid FROM pg_database WHERE datname=current_database())<>16447 OR (SELECT system_identifier::text FROM pg_control_system())<>'7693399121498484768' THEN RAISE EXCEPTION '382 native witness authority refused'; END IF; END $g$;
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='S' AND n.nspname!~'^pg_' AND n.nspname<>'information_schema';
SELECT format('SELECT jsonb_build_object(''name'',%L,''last_value'',last_value,''is_called'',is_called) FROM %I.%I;',format('%I.%I',n.nspname,c.relname),n.nspname,c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='S' AND n.nspname!~'^pg_' AND n.nspname<>'information_schema' ORDER BY n.nspname,c.relname
\gexec
SELECT jsonb_build_object('kind','qualified_rows','rows',(SELECT jsonb_agg(jsonb_build_array(login_name,encode(boundary_sha256,'hex'),qualification_sha256) ORDER BY login_name) FROM public.cnpg_qualified_runtime_receipts));
COMMIT;
