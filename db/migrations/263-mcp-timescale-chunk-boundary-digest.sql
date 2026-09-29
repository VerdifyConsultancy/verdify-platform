-- 263: Keep the MCP ordinary authority receipt stable when Timescale creates a
-- new native or compressed chunk with its physical hypertable ACL. Static objects
-- and every exceptional chunk retain exact catalog identity, so new privileges drift.
-- The ledger runner owns the transaction. No prior migration is edited.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations, public.mcp_runtime_boundary_receipt
    IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
BEGIN
    IF (SELECT count(*) FROM public.mcp_runtime_boundary_receipt) <> 1
       OR NOT EXISTS (
           SELECT 1 FROM public.mcp_runtime_boundary_receipt receipt
            WHERE receipt.singleton
              AND encode(receipt.boundary_sha256, 'hex') IN (
                  'c34f6091839412a8578c1061a0bddae987fe65d8cf8cf5551fddca63924cfff3',
                  'c6e6952976cbc27f8342ae2304fb69ecdbec682c46ed1ddaeb8b7a00894d6bd7')
              AND receipt.boundary_sha256 =
                  public.fn_mcp_runtime_boundary_digest())
       OR NOT EXISTS (SELECT 1 FROM public.schema_migrations
                      WHERE source='db/migrations' AND seq=262
                        AND filename='db/migrations/262-fixed-panel-native-callback-ledger.sql'
                        AND sha256='6b9ebfb1ee429fac2a31c07253014ccfbc1832e08307f7f0e0aad867fc46020e'
                        AND stamp_method='runner')
       OR EXISTS (SELECT 1 FROM public.schema_migrations
                  WHERE source='db/migrations' AND seq >= 263) THEN
        RAISE EXCEPTION '263 refuses unexpected MCP receipt or migration ledger';
    END IF;
END;
$preflight$;

CREATE OR REPLACE FUNCTION public.fn_mcp_runtime_boundary_digest()
RETURNS bytea
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
WITH managed(oid) AS (
    SELECT oid FROM pg_catalog.pg_roles
     WHERE rolname IN ('verdify_mcp_runtime', 'verdify_mcp_runtime_login')
), generated_chunks AS MATERIALIZED (
    SELECT c.oid AS chunk_oid, generated.parent_oid, generated.boundary_matches_parent
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      JOIN LATERAL (
          SELECT parent.oid AS parent_oid,
                 CASE WHEN h.schema_name = 'public' THEN 'native'
                      ELSE 'compressed' END AS chunk_kind,
                 c.relacl IS NOT DISTINCT FROM physical_parent.relacl
                 AND c.relowner = physical_parent.relowner
                 AND c.relrowsecurity = physical_parent.relrowsecurity
                 AND c.relforcerowsecurity = physical_parent.relforcerowsecurity
                 AND NOT EXISTS (
                     SELECT 1 FROM pg_catalog.pg_attribute child_column
                     LEFT JOIN pg_catalog.pg_attribute parent_column
                       ON parent_column.attrelid = physical_parent.oid
                      AND parent_column.attname = child_column.attname
                      AND parent_column.attnum > 0
                      AND NOT parent_column.attisdropped
                    WHERE child_column.attrelid = c.oid
                      AND child_column.attnum > 0
                      AND NOT child_column.attisdropped
                      AND child_column.attacl IS DISTINCT FROM parent_column.attacl
                 ) AS boundary_matches_parent
            FROM _timescaledb_catalog.chunk ch
            JOIN _timescaledb_catalog.hypertable h ON h.id = ch.hypertable_id
            JOIN _timescaledb_catalog.hypertable original
              ON original.schema_name = 'public'
             AND (original.id = h.id OR original.compressed_hypertable_id = h.id)
            JOIN pg_catalog.pg_class parent
              ON parent.relnamespace = 'public'::regnamespace
             AND parent.relname = original.table_name
            JOIN pg_catalog.pg_namespace physical_schema
              ON physical_schema.nspname = h.schema_name
            JOIN pg_catalog.pg_class physical_parent
              ON physical_parent.relnamespace = physical_schema.oid
             AND physical_parent.relname = h.table_name
           WHERE n.nspname = '_timescaledb_internal'
             AND c.relkind = 'r'
             AND NOT ch.dropped
             AND ch.schema_name = n.nspname
             AND ch.table_name = c.relname
             AND ((h.schema_name = 'public'
                   AND c.relname ~ '^_hyper_[0-9]+_[0-9]+_chunk$')
               OR (h.schema_name = '_timescaledb_internal'
                   AND c.relname ~ '^compress_hyper_[0-9]+_[0-9]+_chunk$'))
      ) generated ON TRUE
), entries(entry) AS (
    SELECT pg_catalog.format(
        'role|%s|%s|%s|%s|%s|%s|%s|%s|%s',
        r.rolname, r.rolcanlogin, r.rolinherit, r.rolsuper,
        r.rolcreatedb, r.rolcreaterole, r.rolreplication,
        r.rolbypassrls, coalesce(r.rolconfig::text, ''))
      FROM pg_catalog.pg_roles r WHERE r.oid IN (SELECT oid FROM managed)
    UNION ALL
    SELECT pg_catalog.format('member|%s|%s|%s|%s|%s|%s',
        m.roleid, m.member, m.grantor,
        m.admin_option, m.inherit_option, m.set_option)
      FROM pg_catalog.pg_auth_members m
     WHERE m.roleid IN (SELECT oid FROM managed)
        OR m.member IN (SELECT oid FROM managed)
    UNION ALL
    SELECT pg_catalog.format('database|%s|%s', d.datdba, d.datacl::text)
      FROM pg_catalog.pg_database d WHERE d.datname = pg_catalog.current_database()
    UNION ALL
    SELECT pg_catalog.format('schema|%s|%s', n.nspowner, n.nspacl::text)
      FROM pg_catalog.pg_namespace n WHERE n.nspname = 'public'
    UNION ALL
    SELECT pg_catalog.format('relation|%s|%s|%s|%s|%s|%s|%s|%s',
        n.nspname, c.relname,
        c.relkind, c.relowner,
        c.relrowsecurity, c.relforcerowsecurity, c.reloptions::text,
        CASE WHEN c.relkind IN ('v','m')
             THEN pg_catalog.pg_get_viewdef(c.oid, true) ELSE '' END)
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      LEFT JOIN generated_chunks generated ON generated.chunk_oid = c.oid
     WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND (generated.parent_oid IS NULL OR NOT generated.boundary_matches_parent)
       AND (generated.parent_oid IS NOT NULL
            OR c.relowner IN (SELECT oid FROM managed)
            OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(c.relacl) a
                        WHERE a.grantee = 0
                           OR a.grantee IN (SELECT oid FROM managed)))
    UNION ALL
    SELECT pg_catalog.format('relation-acl|%s|%s|%s|%s|%s',
        c.oid::regclass::text,
        a.grantee, a.grantor, a.privilege_type, a.is_grantable)
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      LEFT JOIN generated_chunks generated ON generated.chunk_oid = c.oid
      CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
     WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND (generated.parent_oid IS NULL OR NOT generated.boundary_matches_parent)
       AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM managed))
    UNION ALL
    SELECT pg_catalog.format('column-acl|%s|%s|%s|%s|%s|%s',
        a.attrelid::regclass::text,
        a.attname, x.grantee, x.grantor,
        x.privilege_type, x.is_grantable)
      FROM pg_catalog.pg_attribute a
      JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      LEFT JOIN generated_chunks generated ON generated.chunk_oid = c.oid
      CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) x
     WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND (generated.parent_oid IS NULL OR NOT generated.boundary_matches_parent)
       AND a.attnum > 0 AND NOT a.attisdropped
       AND (x.grantee = 0 OR x.grantee IN (SELECT oid FROM managed))
    UNION ALL
    SELECT pg_catalog.format('function|%s|%s|%s',
        p.oid::regprocedure, p.proowner, pg_catalog.pg_get_functiondef(p.oid))
      FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND (p.proowner IN (SELECT oid FROM managed)
            OR p.oid = 'public.fn_mcp_runtime_boundary_digest()'::regprocedure
            OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a
                        WHERE a.grantee = 0
                           OR a.grantee IN (SELECT oid FROM managed)))
    UNION ALL
    SELECT pg_catalog.format('function-acl|%s|%s|%s|%s|%s',
        p.oid::regprocedure, a.grantee, a.grantor, a.privilege_type, a.is_grantable)
      FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
      CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) a
     WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM managed))
    UNION ALL
    SELECT pg_catalog.format('default-acl|%s|%s|%s|%s|%s|%s',
        d.defaclrole, d.defaclnamespace, d.defaclobjtype,
        a.grantee, a.privilege_type, a.is_grantable)
      FROM pg_catalog.pg_default_acl d
      CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) a
     WHERE a.grantee = 0 OR a.grantee IN (SELECT oid FROM managed)
)
SELECT public.digest(
    pg_catalog.convert_to(coalesce(pg_catalog.string_agg(entry, E'\n' ORDER BY entry), ''), 'UTF8'),
    'sha256')
  FROM (SELECT DISTINCT entry FROM entries) distinct_entries
$body$;

-- A reviewed successor digest is filled from a rollback-only production
-- rehearsal. Never recapture an arbitrary live ACL into the receipt.
UPDATE public.mcp_runtime_boundary_receipt
   SET boundary_sha256 = decode('81836c70a76578da82b77899da5d1cafee4597ea819e35ee68a3fd6cf669fa45', 'hex')
 WHERE singleton;

DO $postflight$
BEGIN
    IF encode(public.fn_mcp_runtime_boundary_digest(), 'hex')
       IS DISTINCT FROM '81836c70a76578da82b77899da5d1cafee4597ea819e35ee68a3fd6cf669fa45'
       OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt
            WHERE singleton AND boundary_sha256 =
                  public.fn_mcp_runtime_boundary_digest()) <> 1 THEN
        RAISE EXCEPTION '263 MCP successor digest did not match reviewed projection';
    END IF;
END;
$postflight$;
