\set ON_ERROR_STOP on
BEGIN READ ONLY; SET LOCAL statement_timeout='30s'; SET LOCAL lock_timeout='2s'; SET LOCAL search_path=pg_catalog,pg_temp;
DO $identity$ BEGIN
 IF current_database()<>'verdify_rehearsal' OR current_setting('cluster_name')<>'verdify-cnpg-s2-integration'
 OR current_setting('server_version_num')::int<>160013 OR inet_client_addr() IS NOT NULL
 OR pg_is_in_recovery() OR current_user<>'postgres' OR session_user<>'postgres'
 OR (SELECT oid FROM pg_database WHERE datname=current_database())<>16447
 OR (SELECT system_identifier::text FROM pg_control_system())<>'7693399121498484768'
 THEN RAISE EXCEPTION '382 metadata refuses changed integration identity'; END IF;
END $identity$;

SELECT jsonb_build_object('kind','ledger_projection','rows',jsonb_agg(jsonb_build_array(source,filename,seq,sha256,stamp_method) ORDER BY source,filename)) FROM public.schema_migrations;
SELECT jsonb_build_object('kind','trigger_projection','rows',jsonb_agg(jsonb_build_object('relation',format('%I.%I',n.nspname,c.relname),'name',t.tgname,'definition',pg_get_triggerdef(t.oid,true),'native',to_jsonb(t),'function',jsonb_build_object('oid',p.oid,'name',p.proname,'owner_oid',p.proowner,'owner',pg_get_userbyid(p.proowner),'language',l.lanname,'security_definer',p.prosecdef,'config',p.proconfig,'acl',p.proacl,'prosrc_sha256',encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'))) ORDER BY n.nspname,c.relname,t.tgname)) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_language l ON l.oid=p.prolang WHERE t.oid IN (174448,176982,176983,174465);
COMMIT;
