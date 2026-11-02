-- 282 (VD-66, #1179), dashboard migration M2: the dashboard login and its sessions.
-- Creates the NOLOGIN duty verdify_dashboard_runtime, its login with no password and
-- the guardrails on the role (CONNECTION LIMIT 8, statement_timeout 10s and
-- idle_in_transaction_session_timeout 15s: unbounded panel queries OOM-killed the
-- shared database on 2026-07-14/15, migration 203), the duty's schema with
-- fn_attest_login(), and dashboard_session. PUBLIC's existing CONNECT and USAGE on
-- public serve the login, so neither ACL the MCP digest hashes changes.
-- It grants only on the objects it creates, so every runtime receipt keeps its
-- predecessor's value. The ordinary digest hashes each grantee's OID, and an OID
-- allocated in a rolled-back qualification is never reused (258's note), so the
-- reads and writes on existing objects follow in a successor measured once this
-- file is applied, as 259 followed 258. No grant here reaches setpoint_changes.
-- PENDING VD-66: predecessor 281 and the three seals are all-zero placeholders.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
        AND seq=281 AND filename='db/migrations/281-irrigation-minutes-by-zone.sql'
        AND sha256='0000000000000000000000000000000000000000000000000000000000000000'
        AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=282)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '0000000000000000000000000000000000000000000000000000000000000000' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '0000000000000000000000000000000000000000000000000000000000000000' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '0000000000000000000000000000000000000000000000000000000000000000'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('0000000000000000000000000000000000000000000000000000000000000000','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('0000000000000000000000000000000000000000000000000000000000000000','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('0000000000000000000000000000000000000000000000000000000000000000','hex'))
        OR EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname IN ('verdify_dashboard_runtime','verdify_dashboard_runtime_login'))
        OR to_regnamespace('verdify_dashboard_runtime') IS NOT NULL
        OR to_regclass('public.dashboard_session') IS NOT NULL
    THEN RAISE EXCEPTION '282 refuses an unqualified predecessor'; END IF;
END $predecessor$;

CREATE ROLE verdify_dashboard_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE verdify_dashboard_runtime_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8;
GRANT verdify_dashboard_runtime TO verdify_dashboard_runtime_login WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;
CREATE SCHEMA verdify_dashboard_runtime AUTHORIZATION verdify;
REVOKE ALL ON SCHEMA verdify_dashboard_runtime FROM PUBLIC;
GRANT USAGE ON SCHEMA verdify_dashboard_runtime TO verdify_dashboard_runtime;
ALTER ROLE verdify_dashboard_runtime_login SET search_path=verdify_dashboard_runtime,pg_catalog,public,pg_temp;
ALTER ROLE verdify_dashboard_runtime_login SET statement_timeout='10s';
ALTER ROLE verdify_dashboard_runtime_login SET idle_in_transaction_session_timeout='15s';

-- 276's identity checks plus the guardrails; in place of 276's per-duty denials,
-- this login's: no write on setpoint_changes (the NOTIFY fast path past the
-- dispatcher's fences) or setpoint_plan, and nothing on the experiment tables.
-- A login with no role settings attests false, never NULL.
CREATE FUNCTION verdify_dashboard_runtime.fn_attest_login() RETURNS boolean
 LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp AS $attest$
 SELECT current_user=session_user AND current_user='verdify_dashboard_runtime_login'
 AND r.rolcanlogin AND r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
 AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls
 AND r.rolconnlimit=8
 AND coalesce(r.rolconfig @> ARRAY['statement_timeout=10s','idle_in_transaction_session_timeout=15s'],false)
 AND NOT has_database_privilege(current_user,current_database(),'CREATE')
 AND NOT has_schema_privilege(current_user,'public','CREATE')
 AND NOT has_schema_privilege(current_user,'verdify_dashboard_runtime','CREATE')
 AND (SELECT count(*) FROM pg_auth_members m WHERE m.member=r.oid)=1
 AND EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles d ON d.oid=m.roleid
   WHERE m.member=r.oid AND d.rolname='verdify_dashboard_runtime' AND NOT m.admin_option
   AND m.inherit_option AND m.set_option AND NOT d.rolcanlogin AND NOT d.rolinherit
   AND NOT d.rolsuper AND NOT d.rolcreatedb AND NOT d.rolcreaterole
   AND NOT d.rolreplication AND NOT d.rolbypassrls)
 AND (SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='verdify_dashboard_runtime'))=0
 AND (SELECT count(*) FROM pg_auth_members WHERE roleid=(SELECT oid FROM pg_roles WHERE rolname='verdify_dashboard_runtime'))=1
 AND NOT has_table_privilege(current_user,'public.setpoint_changes','INSERT,UPDATE,DELETE,TRUNCATE')
 AND NOT has_any_column_privilege(current_user,'public.setpoint_changes','INSERT,UPDATE')
 AND NOT has_table_privilege(current_user,'public.setpoint_plan','INSERT,UPDATE,DELETE,TRUNCATE')
 AND NOT has_any_column_privilege(current_user,'public.setpoint_plan','INSERT,UPDATE')
 AND NOT has_table_privilege(current_user,'public.control_experiments','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 AND NOT has_any_column_privilege(current_user,'public.control_experiments','SELECT,INSERT,UPDATE,REFERENCES')
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
REVOKE ALL ON FUNCTION verdify_dashboard_runtime.fn_attest_login() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION verdify_dashboard_runtime.fn_attest_login() TO verdify_dashboard_runtime;

CREATE TABLE public.dashboard_session (
    token_sha256 bytea PRIMARY KEY,
    username     text NOT NULL,
    display_name text,
    role         text NOT NULL CHECK (role IN ('operator', 'viewer')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);
COMMENT ON TABLE public.dashboard_session IS
    'Server-side dashboard sessions (VD-66): the SHA-256 of the session token with the Authentik user and its role. The operator API writes them and deletes expired rows hourly.';
GRANT SELECT, INSERT, UPDATE, DELETE ON public.dashboard_session TO verdify_dashboard_runtime;

-- No receipt moves: the digests and receipts must still equal the predecessor's.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '0000000000000000000000000000000000000000000000000000000000000000' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '0000000000000000000000000000000000000000000000000000000000000000' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '0000000000000000000000000000000000000000000000000000000000000000'
        OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
            WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
    THEN RAISE EXCEPTION '282 refuses an unqualified successor'; END IF;
END $successor$;
