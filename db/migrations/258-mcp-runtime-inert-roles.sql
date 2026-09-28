-- #371: reserve stable catalog identities for a future ordinary MCP login.
-- This phase is inert: neither role can log in, has a password, belongs to
-- another role, owns an object, or receives an object privilege.  A later
-- sealed grant/cutover migration can use their committed OIDs when computing
-- the existing API and ingestor boundary successors.  PostgreSQL catalog OIDs
-- allocated inside a rolled-back CREATE ROLE cannot be reused as an exact
-- production ACL digest.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations,
           public.runtime_ordinary_login_attestation_receipts
    IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migrations
         WHERE source = 'db/migrations'
           AND filename = 'db/migrations/257-route-only-crop-band-publication.sql'
           AND seq = 257
           AND sha256 = '48dc31ad941bc55a353e9a3514692f20e64e11806faca6d83a19aa771b0e1ac4'
           AND stamp_method = 'runner'
    ) OR EXISTS (
        SELECT 1 FROM public.schema_migrations
         WHERE source = 'db/migrations' AND seq >= 258
    ) THEN
        RAISE EXCEPTION 'inert MCP role reservation requires exact ledger 257';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
         WHERE rolname IN ('verdify_mcp_runtime', 'verdify_mcp_runtime_login')
    ) THEN
        RAISE EXCEPTION 'MCP role name already exists outside this migration';
    END IF;
    IF (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2
       OR EXISTS (
           SELECT 1 FROM (VALUES
               ('verdify_api_runtime_login',
                '444063bd61ccb69f02888ede5f2c2338d7882b954af7141e267cdb53b4ed9c7e'),
               ('verdify_ingestor_runtime_login',
                '86660529322d02ce6e735d329f6c5e320eeb53f9a8b2890a9a285eaf852f88f5')
           ) expected(login_name, digest)
           LEFT JOIN public.runtime_ordinary_login_attestation_receipts receipt
             ON receipt.login_name = expected.login_name
           WHERE encode(receipt.boundary_sha256, 'hex') IS DISTINCT FROM expected.digest
              OR encode(public.fn_runtime_ordinary_boundary_digest(expected.login_name), 'hex')
                 IS DISTINCT FROM expected.digest
       ) THEN
        RAISE EXCEPTION 'inert MCP role reservation refuses ordinary boundary drift';
    END IF;
END;
$preflight$;

CREATE ROLE verdify_mcp_runtime
    NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE verdify_mcp_runtime_login
    NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE verdify_mcp_runtime SET search_path = pg_catalog, public, pg_temp;
ALTER ROLE verdify_mcp_runtime_login SET search_path = pg_catalog, public, pg_temp;
COMMENT ON ROLE verdify_mcp_runtime IS
    'Inert MCP duty reservation. No grants or membership until a separately sealed successor.';
COMMENT ON ROLE verdify_mcp_runtime_login IS
    'Inert MCP login reservation. NOLOGIN until a separately sealed credential bootstrap.';

DO $postflight$
BEGIN
    IF (SELECT count(*) FROM pg_catalog.pg_roles
         WHERE rolname IN ('verdify_mcp_runtime', 'verdify_mcp_runtime_login')
           AND rolcanlogin IS FALSE AND rolinherit IS FALSE
           AND rolsuper IS FALSE AND rolcreatedb IS FALSE AND rolcreaterole IS FALSE
           AND rolreplication IS FALSE AND rolbypassrls IS FALSE
           AND rolconfig = ARRAY['search_path=pg_catalog, public, pg_temp']) <> 2
       OR EXISTS (
           SELECT 1 FROM pg_catalog.pg_auth_members m
            JOIN pg_catalog.pg_roles granted ON granted.oid = m.roleid
            JOIN pg_catalog.pg_roles member ON member.oid = m.member
           WHERE granted.rolname IN ('verdify_mcp_runtime', 'verdify_mcp_runtime_login')
              OR member.rolname IN ('verdify_mcp_runtime', 'verdify_mcp_runtime_login')
       )
       OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex')
          IS DISTINCT FROM '444063bd61ccb69f02888ede5f2c2338d7882b954af7141e267cdb53b4ed9c7e'
       OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex')
          IS DISTINCT FROM '86660529322d02ce6e735d329f6c5e320eeb53f9a8b2890a9a285eaf852f88f5'
    THEN
        RAISE EXCEPTION 'inert MCP role reservation changed authority or existing boundary';
    END IF;
END;
$postflight$;
