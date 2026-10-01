-- #643: read-only crop-zone topology; immutable authentic268 predecessor.
DO $preflight$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=268
   AND sha256='aed9c4e562ff0420e315d14211224d1fff6469b9e0d2a541aedbc0bc562447e0' AND stamp_method='runner')
   OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=269)
   OR public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login') IS DISTINCT FROM decode('fe79f986d58ba6deec513312441b5ba5d579168d3e7e28d5721bb5771150af81','hex')
   OR public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login') IS DISTINCT FROM decode('15e4eff5d86ff58bf3fc98075dfc4613b5fd2a418bf3be9251fd7e6b1634a96e','hex')
   OR public.fn_mcp_runtime_boundary_digest() IS DISTINCT FROM decode('81836c70a76578da82b77899da5d1cafee4597ea819e35ee68a3fd6cf669fa45','hex')
   OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
   OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r WHERE r.login_name='verdify_api_runtime_login' AND r.boundary_sha256=decode('fe79f986d58ba6deec513312441b5ba5d579168d3e7e28d5721bb5771150af81','hex'))
   OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r WHERE r.login_name='verdify_ingestor_runtime_login' AND r.boundary_sha256=decode('15e4eff5d86ff58bf3fc98075dfc4613b5fd2a418bf3be9251fd7e6b1634a96e','hex'))
   OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
      WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
   OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt r
      WHERE r.boundary_sha256=public.fn_mcp_runtime_boundary_digest())
 THEN RAISE EXCEPTION '#643 requires authentic unchanged268 predecessor'; END IF;
END $preflight$;
CREATE TEMP TABLE role643_lab_topology_predecessor_seals ON COMMIT DROP AS
SELECT login_name, boundary_sha256 FROM public.runtime_ordinary_login_attestation_receipts
UNION ALL SELECT 'verdify_mcp_runtime_login', boundary_sha256 FROM public.mcp_runtime_boundary_receipt;
-- #643: exact crop-zone topology fields required by the public zone renderer.
-- Keep canonical crop.zone_id -> linked shelf zone -> legacy zone precedence.
-- No public table grant, write capability, credential or ordinary boundary change.
DO $guard$
BEGIN
  IF current_user <> 'verdify'
     OR NOT EXISTS (
       SELECT 1 FROM pg_roles
       WHERE rolname='verdify_lab_publisher_runtime' AND NOT rolcanlogin
         AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
         AND NOT rolreplication AND NOT rolbypassrls
     )
     OR NOT EXISTS (
       SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
       WHERE n.nspname='verdify_lab_publisher_runtime' AND r.rolname='verdify'
     )
     OR NOT EXISTS (
       SELECT 1 FROM pg_auth_members m
       JOIN pg_roles login ON login.oid=m.member
       JOIN pg_roles duty ON duty.oid=m.roleid
       WHERE login.rolname='verdify_lab_publisher_runtime_login'
         AND login.rolcanlogin AND NOT login.rolsuper AND NOT login.rolcreatedb
         AND NOT login.rolcreaterole AND NOT login.rolreplication AND NOT login.rolbypassrls
         AND (SELECT count(*) FROM pg_auth_members own WHERE own.member=login.oid)=1
         AND duty.rolname='verdify_lab_publisher_runtime'
         AND NOT m.admin_option AND m.inherit_option AND m.set_option
     ) THEN
    RAISE EXCEPTION 'expected six-workload lab boundary is unavailable';
  END IF;
END
$guard$;

CREATE VIEW verdify_lab_publisher_runtime.positions WITH (security_barrier=true)
AS SELECT id, greenhouse_id, shelf_id FROM public.positions;
REVOKE ALL ON verdify_lab_publisher_runtime.positions FROM PUBLIC;
GRANT SELECT ON verdify_lab_publisher_runtime.positions TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.shelves WITH (security_barrier=true)
AS SELECT id, zone_id FROM public.shelves;
REVOKE ALL ON verdify_lab_publisher_runtime.shelves FROM PUBLIC;
GRANT SELECT ON verdify_lab_publisher_runtime.shelves TO verdify_lab_publisher_runtime;

-- Never refresh any predecessor receipt to accept a role-design side effect.
DO $postflight$
BEGIN
 IF EXISTS(SELECT 1 FROM pg_temp.role643_lab_topology_predecessor_seals s
   WHERE s.boundary_sha256 IS DISTINCT FROM CASE WHEN s.login_name='verdify_mcp_runtime_login'
      THEN public.fn_mcp_runtime_boundary_digest()
      ELSE public.fn_runtime_ordinary_boundary_digest(s.login_name) END)
 THEN RAISE EXCEPTION '#643 changed an existing sealed runtime boundary'; END IF;
END $postflight$;
