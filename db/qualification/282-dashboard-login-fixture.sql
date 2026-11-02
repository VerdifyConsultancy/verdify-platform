-- Run only inside an explicit rollback-only transaction after exact282, as the
-- superuser owner. Native checks of M2 on the production catalog: the two roles,
-- their settings and single membership, the duty's schema, attestor and sessions
-- table, no dashboard grant on any existing object, then every runtime login's
-- attestation under SET SESSION AUTHORIZATION (the dashboard login has no
-- password until the R1 wiring PR seals one). The session row and every
-- authorization change end with the rollback.
DO $catalog$
DECLARE
    owner_oid constant oid := (SELECT datdba FROM pg_database WHERE datname = current_database());
    duty constant regrole := 'verdify_dashboard_runtime'::regrole;
    login constant regrole := 'verdify_dashboard_runtime_login'::regrole;
BEGIN
    IF (SELECT count(*) FROM pg_roles WHERE oid = duty
          AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolcreatedb
          AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls AND rolconfig IS NULL) <> 1
       OR (SELECT count(*) FROM pg_roles WHERE oid = login
          AND rolcanlogin AND rolinherit AND NOT rolsuper AND NOT rolcreatedb
          AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls AND rolconnlimit = 8
          AND rolconfig @> ARRAY['search_path=verdify_dashboard_runtime, pg_catalog, public, pg_temp',
                                 'statement_timeout=10s', 'idle_in_transaction_session_timeout=15s']
          AND cardinality(rolconfig) = 3) <> 1
       OR EXISTS (SELECT 1 FROM pg_authid WHERE oid IN (duty, login) AND rolpassword IS NOT NULL)
       OR (SELECT count(*) FROM pg_auth_members m WHERE m.roleid IN (duty, login) OR m.member IN (duty, login)) <> 1
       OR NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.roleid = duty AND m.member = login
                      AND NOT m.admin_option AND m.inherit_option AND m.set_option)
    THEN RAISE EXCEPTION '282 native role check failed'; END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'verdify_dashboard_runtime' AND nspowner = owner_oid)
       OR (SELECT array_agg(format('%s:%s', a.grantee::regrole, a.privilege_type)
                            ORDER BY a.grantee::regrole::text, a.privilege_type)
           FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
           WHERE n.nspname = 'verdify_dashboard_runtime' AND a.grantee <> owner_oid)
          IS DISTINCT FROM ARRAY['verdify_dashboard_runtime:USAGE']
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid = 'verdify_dashboard_runtime.fn_attest_login()'::regprocedure
             AND p.proowner = owner_oid AND NOT p.prosecdef AND p.provolatile = 's'
             AND p.proconfig = ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT array_agg(format('%s:%s', a.grantee::regrole, a.privilege_type)
                            ORDER BY a.grantee::regrole::text, a.privilege_type)
           FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
           WHERE p.oid = 'verdify_dashboard_runtime.fn_attest_login()'::regprocedure AND a.grantee <> owner_oid)
          IS DISTINCT FROM ARRAY['verdify_dashboard_runtime:EXECUTE']
       OR NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.oid = 'public.dashboard_session'::regclass
                      AND c.relowner = owner_oid AND c.relkind = 'r' AND NOT c.relrowsecurity)
       OR (SELECT array_agg(format('%s:%s', a.grantee::regrole, a.privilege_type)
                            ORDER BY a.grantee::regrole::text, a.privilege_type)
           FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
           WHERE c.oid = 'public.dashboard_session'::regclass AND a.grantee <> owner_oid)
          IS DISTINCT FROM ARRAY['verdify_dashboard_runtime:DELETE', 'verdify_dashboard_runtime:INSERT',
                                 'verdify_dashboard_runtime:SELECT', 'verdify_dashboard_runtime:UPDATE']
    THEN RAISE EXCEPTION '282 native object check failed'; END IF;

    -- 282 grants on nothing it did not create: no sealed ACL holds a dashboard OID.
    IF EXISTS (SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
               WHERE a.grantee IN (duty, login) AND c.oid <> 'public.dashboard_session'::regclass)
       OR EXISTS (SELECT 1 FROM pg_attribute t CROSS JOIN LATERAL aclexplode(t.attacl) a
                  WHERE t.attacl IS NOT NULL AND a.grantee IN (duty, login))
       OR EXISTS (SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
                  WHERE a.grantee IN (duty, login)
                    AND p.oid <> 'verdify_dashboard_runtime.fn_attest_login()'::regprocedure)
       OR EXISTS (SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
                  WHERE a.grantee IN (duty, login) AND n.nspname <> 'verdify_dashboard_runtime')
       OR EXISTS (SELECT 1 FROM pg_database d CROSS JOIN LATERAL aclexplode(d.datacl) a
                  WHERE a.grantee IN (duty, login))
       OR EXISTS (SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
                  WHERE a.grantee IN (duty, login) OR d.defaclrole IN (duty, login))
    THEN RAISE EXCEPTION '282 granted on an existing object'; END IF;
END $catalog$;

-- With 282's objects in place the api, ingestor and MCP logins still attest, and
-- the dashboard login attests, keeps its sessions and is refused the setpoint
-- fast path and the experiment table. SET SESSION AUTHORIZATION needs the
-- superuser owner; the rollback undoes it.
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $api$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() IS NOT TRUE THEN
        RAISE EXCEPTION '282 api login does not attest';
    END IF;
END $api$;
RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $ingestor$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() IS NOT TRUE THEN
        RAISE EXCEPTION '282 ingestor login does not attest';
    END IF;
END $ingestor$;
RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION verdify_mcp_runtime_login;
DO $mcp$
BEGIN
    IF public.fn_mcp_runtime_attest_ordinary_login() IS NOT TRUE THEN
        RAISE EXCEPTION '282 MCP login does not attest';
    END IF;
END $mcp$;
RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;
DO $dashboard$
BEGIN
    IF verdify_dashboard_runtime.fn_attest_login() IS NOT TRUE THEN
        RAISE EXCEPTION '282 dashboard login does not attest';
    END IF;
    INSERT INTO public.dashboard_session (token_sha256, username, display_name, role, expires_at)
    VALUES (sha256(convert_to('282 native qualification', 'UTF8')), '282-qualification', '282 qualification',
            'viewer', now() + interval '1 hour');
    UPDATE public.dashboard_session SET role = 'operator', expires_at = now() - interval '1 second'
     WHERE username = '282-qualification';
    DELETE FROM public.dashboard_session WHERE username = '282-qualification' AND expires_at <= now();
    IF NOT FOUND THEN
        RAISE EXCEPTION '282 dashboard login could not expire its session';
    END IF;
    BEGIN
        INSERT INTO public.setpoint_changes (ts, parameter, value, source)
        VALUES (now(), '282-qualification', 0, 'manual');
        RAISE EXCEPTION '282 dashboard login inserted into setpoint_changes';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        PERFORM 1 FROM public.control_experiments LIMIT 1;
        RAISE EXCEPTION '282 dashboard login read control_experiments';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $dashboard$;
RESET SESSION AUTHORIZATION;

SELECT json_build_object(
    'checks', json_build_array('roles_settings_membership', 'schema_attestor_sessions_acl',
                               'no_grant_on_existing_objects', 'logins_attest', 'session_round_trip',
                               'setpoint_changes_insert_denied', 'control_experiments_read_denied'),
    'all_passed', true,
    'attested', json_build_object('api', true, 'ingestor', true, 'mcp', true, 'dashboard', true));
