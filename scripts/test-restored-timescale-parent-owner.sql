-- Blocking C0-aware successor for a current paired logical restore.
-- Exercise a real restored hypertable without replaying immutable migration 217
-- or rotating its sealed runtime boundary receipts. All mutations roll back.
\set ON_ERROR_STOP on
BEGIN;

DO $precondition$
BEGIN
    IF current_database() <> 'verdify_rehearsal'
       OR (current_setting('listen_addresses') <> ''
          AND NOT (current_setting('cluster_name') = 'verdify-cnpg-rehearsal'
                   AND current_setting('server_version_num')::integer = 160013
                   AND inet_client_addr() IS NULL AND NOT pg_is_in_recovery()))
       OR (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb')
          IS DISTINCT FROM '2.25.2'
       OR (SELECT count(*) FROM public.schema_migrations
            WHERE filename = 'db/migrations/217-runtime-role-boundary.sql') <> 1
       OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2 THEN
        RAISE EXCEPTION 'restored-owner successor requires socket-only current C0/Timescale paired restore';
    END IF;
END;
$precondition$;

CREATE ROLE test_672_restored_rogue NOLOGIN;
CREATE ROLE test_672_restored_reader NOLOGIN;

-- Require one real user-owned parent with both physical chunk forms and an
-- existing explicit ACL. A missing candidate is an unsupported state, not a
-- reason to shrink the adversarial fixture to a synthetic table.
CREATE TEMP TABLE test_672_real_parent ON COMMIT DROP AS
SELECT h.schema_name, h.table_name, h.id AS hypertable_id,
       h.compressed_hypertable_id, parent.oid AS parent_oid,
       parent.relowner AS original_owner,
       count(*) FILTER (WHERE chunk.hypertable_id = h.id) AS regular_chunks,
       count(*) FILTER (WHERE chunk.hypertable_id = h.compressed_hypertable_id)
           AS compressed_chunks
  FROM _timescaledb_catalog.hypertable h
  JOIN pg_class parent
    ON parent.oid = to_regclass(format('%I.%I', h.schema_name, h.table_name))
  JOIN _timescaledb_catalog.chunk chunk
    ON chunk.hypertable_id IN (h.id, h.compressed_hypertable_id)
   AND NOT chunk.dropped
 WHERE h.schema_name <> '_timescaledb_internal'
   AND parent.relowner = (SELECT datdba FROM pg_database
                           WHERE datname = current_database())
   AND parent.relacl IS NOT NULL
 GROUP BY h.schema_name, h.table_name, h.id, h.compressed_hypertable_id,
          parent.oid, parent.relowner
HAVING count(*) FILTER (WHERE chunk.hypertable_id = h.id) > 0
   AND count(*) FILTER (WHERE chunk.hypertable_id = h.compressed_hypertable_id) > 0
 ORDER BY h.schema_name, h.table_name
 LIMIT 1;

DO $candidate$
BEGIN
    IF (SELECT count(*) FROM test_672_real_parent) <> 1 THEN
        RAISE EXCEPTION 'unsupported restored owner state: no real parent with regular and compressed chunks plus an explicit ACL'
            USING HINT = 'Inspect the current paired backup and qualify a real mixed-chunk hypertable; do not replace this with a synthetic-only fixture.';
    END IF;
END;
$candidate$;

-- Add an adversarial explicit grant, then snapshot the actual restored parent,
-- all of its physical chunks, every column ACL, and both sealed C0 receipts.
DO $grant_reader$
DECLARE p record;
BEGIN
    SELECT * INTO STRICT p FROM test_672_real_parent;
    EXECUTE format('GRANT SELECT ON %I.%I TO test_672_restored_reader',
                   p.schema_name, p.table_name);
END;
$grant_reader$;

CREATE TEMP TABLE test_672_relation_before ON COMMIT DROP AS
SELECT relation.oid, relation.relowner, relation.relacl,
       chunk.hypertable_id = p.compressed_hypertable_id AS compressed
  FROM test_672_real_parent p
  JOIN pg_class relation ON relation.oid = p.parent_oid
  LEFT JOIN _timescaledb_catalog.chunk chunk ON false
UNION ALL
SELECT relation.oid, relation.relowner, relation.relacl,
       chunk.hypertable_id = p.compressed_hypertable_id AS compressed
  FROM test_672_real_parent p
  JOIN _timescaledb_catalog.chunk chunk
    ON chunk.hypertable_id IN (p.hypertable_id, p.compressed_hypertable_id)
   AND NOT chunk.dropped
  JOIN pg_class relation
    ON relation.oid = to_regclass(format('%I.%I', chunk.schema_name, chunk.table_name));

CREATE TEMP TABLE test_672_column_acl_before ON COMMIT DROP AS
SELECT attribute.attrelid, attribute.attnum, attribute.attacl
  FROM pg_attribute attribute
  JOIN test_672_relation_before relation ON relation.oid = attribute.attrelid
 WHERE attribute.attnum > 0 AND NOT attribute.attisdropped;

CREATE TEMP TABLE test_672_receipts_before ON COMMIT DROP AS
SELECT login_name, boundary_sha256, captured_at
  FROM public.runtime_ordinary_login_attestation_receipts;

DO $poison_and_repair$
DECLARE p record;
BEGIN
    SELECT * INTO STRICT p FROM test_672_real_parent;
    IF (SELECT count(*) FROM test_672_relation_before)
       <> 1 + p.regular_chunks + p.compressed_chunks THEN
        RAISE EXCEPTION 'restored owner snapshot missed a live chunk relation';
    END IF;
    EXECUTE format('ALTER TABLE %I.%I OWNER TO test_672_restored_rogue',
                   p.schema_name, p.table_name);
    IF EXISTS (
        SELECT 1 FROM test_672_relation_before before_row
        JOIN pg_class after_row USING (oid)
        WHERE after_row.relowner <> 'test_672_restored_rogue'::regrole
    ) OR NOT has_table_privilege('test_672_restored_reader',
                                  p.parent_oid, 'SELECT') THEN
        RAISE EXCEPTION 'supported parent owner poison did not reach every real regular/compressed chunk and retain ACL';
    END IF;

    -- The same supported primitive used by migration 217; repeat for
    -- idempotence. Never ALTER an extension-managed chunk directly.
    EXECUTE format('REASSIGN OWNED BY test_672_restored_rogue TO %I',
                   pg_get_userbyid(p.original_owner));
    EXECUTE format('REASSIGN OWNED BY test_672_restored_rogue TO %I',
                   pg_get_userbyid(p.original_owner));
END;
$poison_and_repair$;

DO $exact_recovery$
BEGIN
    IF EXISTS (
        (SELECT oid, relowner, relacl FROM test_672_relation_before
         EXCEPT ALL
         SELECT relation.oid, relation.relowner, relation.relacl
           FROM pg_class relation
           JOIN test_672_relation_before before_row USING (oid))
        UNION ALL
        (SELECT relation.oid, relation.relowner, relation.relacl
           FROM pg_class relation
           JOIN test_672_relation_before before_row USING (oid)
         EXCEPT ALL
         SELECT oid, relowner, relacl FROM test_672_relation_before)
    ) OR EXISTS (
        (SELECT attrelid, attnum, attacl FROM test_672_column_acl_before
         EXCEPT ALL
         SELECT attribute.attrelid, attribute.attnum, attribute.attacl
           FROM pg_attribute attribute
           JOIN test_672_column_acl_before before_row
             USING (attrelid, attnum))
        UNION ALL
        (SELECT attribute.attrelid, attribute.attnum, attribute.attacl
           FROM pg_attribute attribute
           JOIN test_672_column_acl_before before_row
             USING (attrelid, attnum)
         EXCEPT ALL
         SELECT attrelid, attnum, attacl FROM test_672_column_acl_before)
    ) OR EXISTS (
        (SELECT * FROM test_672_receipts_before
         EXCEPT ALL
         SELECT login_name, boundary_sha256, captured_at
           FROM public.runtime_ordinary_login_attestation_receipts)
        UNION ALL
        (SELECT login_name, boundary_sha256, captured_at
           FROM public.runtime_ordinary_login_attestation_receipts
         EXCEPT ALL
         SELECT * FROM test_672_receipts_before)
    ) THEN
        RAISE EXCEPTION 'real restored owner/ACL or sealed C0 receipt differs after supported repair';
    END IF;
END;
$exact_recovery$;

SELECT jsonb_build_object(
    'fixture', 'restored-timescale-parent-owner-v1',
    'timescaledb_version', (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'),
    'parent', (SELECT format('%I.%I', schema_name, table_name) FROM test_672_real_parent),
    'regular_chunks', (SELECT regular_chunks FROM test_672_real_parent),
    'compressed_chunks', (SELECT compressed_chunks FROM test_672_real_parent),
    'objects_exact', (SELECT count(*) FROM test_672_relation_before),
    'column_acl_rows_exact', (SELECT count(*) FROM test_672_column_acl_before),
    'sealed_c0_receipts_exact', (SELECT count(*) FROM test_672_receipts_before));
ROLLBACK;
\echo 'PASS: real restored Timescale parent/chunk owner and ACL repair; sealed C0 receipts unchanged'
